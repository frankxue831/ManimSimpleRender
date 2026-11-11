"""Render validated concept input using the existing headless runner."""

import hashlib
import json
import math
from pathlib import Path
import tempfile
import threading

from app.render.cleanup import (
    RenderCleanupState,
    cleanup_state,
    result_cleanup_state,
    set_cleanup_state,
)
from app.render.aggregation import JobOutcome
from app.render.command_builder import build_command
from app.render.jobs import RenderJob
from app.render.runner import RenderRunner
from .model import validate
from .manim_codegen import generate_scene

LIMIT = 64 * 1024


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def render(input_path, output_dir, timeout=60, runner=None) -> dict:
    """Return an artifact receipt, retaining cleanup evidence on every error."""
    cleanup = {"state": RenderCleanupState.NOT_STARTED}
    try:
        return _render(input_path, output_dir, timeout, runner, cleanup)
    except BaseException as exc:
        if not isinstance(exc, Exception):
            cleanup["state"] = RenderCleanupState.UNKNOWN
        set_cleanup_state(exc, cleanup["state"])
        raise


def _render(input_path, output_dir, timeout, runner, _cleanup):
    if type(timeout) not in (int, float) or not 0 < timeout <= 120 or not math.isfinite(timeout):
        raise ValueError("timeout must be a finite number in (0, 120]")
    with Path(input_path).open("rb") as source:
        raw = source.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("input exceeds 64 KiB")
    try:
        data = validate(json.loads(raw.decode("utf-8")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid concept input: " + str(exc)) from exc
    scene_source = generate_scene(data)

    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="concept-", dir=str(root)))
    scene_path = run_dir / "scene.py"
    log_path = run_dir / "render.log"
    receipt_path = run_dir / "result.json"
    media_dir = run_dir / "media"
    _cleanup["state"] = RenderCleanupState.NOT_STARTED
    stage = "prepare"
    try:
        log_path.touch()
        (run_dir / "input.json").write_bytes(raw)
        scene_path.write_text(scene_source, encoding="utf-8")
        media_dir.mkdir()
        job = RenderJob(str(scene_path), "ConceptScene")
        command = build_command(job, "-ql", str(media_dir))
        executor = RenderRunner(terminate_timeout=1) if runner is None else runner
        cancel_event = threading.Event()
        timer = threading.Timer(float(timeout), cancel_event.set)
        timer.daemon = True
        stage = "render"
        with log_path.open("a", encoding="utf-8") as log_file:

            def on_log(line):
                log_file.write(str(line).rstrip("\n") + "\n")
                log_file.flush()

            try:
                timer.start()
                _cleanup["state"] = RenderCleanupState.UNKNOWN
                try:
                    outcome = executor.run_job(job, command, cancel_event, on_log, None)
                except BaseException as runner_error:
                    _cleanup["state"] = cleanup_state(
                        getattr(runner_error, "render_cleanup_state", None)
                    )
                    raise
                _cleanup["state"] = result_cleanup_state(outcome)
                # Persist supervision details before the cleanup/publication gate.
                # Unknown cleanup is exactly when an operator needs this evidence.
                if getattr(outcome, "diagnostic", ""):
                    on_log(outcome.diagnostic)
            finally:
                timer.cancel()
                if timer.ident is not None:
                    timer.join()
            if _cleanup["state"] == RenderCleanupState.UNKNOWN or (
                outcome.outcome == JobOutcome.SUCCESS
                and _cleanup["state"] != RenderCleanupState.COMPLETE
            ):
                raise RuntimeError("render cleanup is unverified")
            if cancel_event.is_set():
                raise RuntimeError("render timed out or was cancelled")
            if outcome.outcome != JobOutcome.SUCCESS or outcome.return_code != 0:
                detail = getattr(outcome, "diagnostic", "")
                raise RuntimeError(
                    "render failed (return code %r): %s" % (outcome.return_code, detail)
                )

        stage = "artifact"
        videos = [
            p
            for p in media_dir.rglob("ConceptScene.mp4")
            if "partial_movie_files" not in p.relative_to(media_dir).parts
            and p.is_file()
            and p.stat().st_size > 0
        ]
        if len(videos) != 1:
            raise RuntimeError("expected exactly one nonempty final ConceptScene.mp4")
        durations = []
        steps = []
        for index, step in enumerate(data["steps"]):
            start = math.fsum(durations)
            durations.append(step["duration"])
            steps.append(
                {
                    "index": index,
                    "start": start,
                    "end": math.fsum(durations),
                    "caption": step["caption"],
                }
            )
        result = {
            "status": "success",
            "render_cleanup_state": _cleanup["state"].value,
            "video": str(videos[0].resolve()),
            "scene": str(scene_path),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "requested_font": data["font"],
            "steps": steps,
            "log": str(log_path),
            "return_code": outcome.return_code,
        }
        stage = "receipt"
        _write_json(receipt_path, result)
        return result
    except BaseException as exc:
        if not isinstance(exc, Exception):
            _cleanup["state"] = RenderCleanupState.UNKNOWN
        set_cleanup_state(exc, _cleanup["state"])
        error = str(exc) or type(exc).__name__
        failed = {"status": "failed", "error": error, "stage": stage, "log": str(log_path)}
        failed["render_cleanup_state"] = _cleanup["state"].value
        try:
            log_path.touch(exist_ok=True)
            _write_json(receipt_path, failed)
        except BaseException as receipt_error:
            if not isinstance(exc, Exception):
                raise exc from receipt_error
            if not isinstance(receipt_error, OSError):
                raise
            replacement = RuntimeError(
                "failed at %s: %s; cannot save failure receipt: %s" % (stage, error, receipt_error)
            )
            set_cleanup_state(replacement, _cleanup["state"])
            raise replacement from exc
        if not isinstance(exc, Exception):
            raise
        replacement = RuntimeError("failed at %s: %s (receipt: %s)" % (stage, error, receipt_path))
        set_cleanup_state(replacement, _cleanup["state"])
        raise replacement from exc
