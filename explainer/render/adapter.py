"""Render a storyboard to an mp4: verify -> codegen -> render (via the app core).

Reuses the hardened render pipeline (``app.render``) to run the generated scene,
so process-group kill, merged streams, and MANIM_NOT_FOUND handling come for
free. The generated scene imports ``explainer.codegen.code_compat`` at render
time, so the manim subprocess is given ``PYTHONPATH`` = repo root.
"""

import glob
import hashlib
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
import shutil
import sys
import tempfile
import traceback

from app.render.aggregation import JobOutcome
from app.render.command_builder import build_command, manim_available
from app.render.diagnostics import DiagnosticTimeline, exception_type, utc_timestamp
from app.render.implementation import write_implementation_manifest
from app.render.jobs import RenderJob
from app.render.quality import quality_flag_for_height
from app.render.runner import RenderRunner

from explainer.cite.verifier import verify_storyboard
from explainer.codegen.manim_codegen import generate_scene
from explainer.storyboard.model import load_storyboard

_HERE = os.path.dirname(os.path.abspath(__file__))  # explainer/render
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))  # repo root
SCENE_NAME = "ExplainerVideo"


def _log_notice(on_log, message):
    """Diagnostic notices must not turn a successful render into a failed one."""
    try:
        if on_log is None:
            print(message, file=sys.stderr)
        else:
            on_log(message)
    except Exception:
        pass


def _diagnostic_directory(out_path, on_log):
    try:
        parent = os.path.join(os.path.dirname(os.path.abspath(out_path)), ".explainer-logs")
        os.makedirs(parent, mode=0o700, exist_ok=True)
        directory = tempfile.mkdtemp(prefix="run-", dir=parent)
    except (OSError, ValueError):
        _log_notice(on_log, "Warning: persistent render diagnostics unavailable.")
        return None
    _log_notice(on_log, "Render diagnostics: " + directory)
    return directory


def _artifact_binding(path):
    """Observe file bytes; reject a file that changes during observation."""
    path = os.path.abspath(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        before = os.fstat(handle.fileno())
        size = 0
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(handle.fileno())
    current = os.stat(path)

    def identity(stat):
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    if (
        identity(before) != identity(after)
        or identity(after) != identity(current)
        or size != after.st_size
    ):
        raise OSError("published artifact changed during observation")
    return {"path": path, "size_bytes": size, "sha256": digest.hexdigest()}


def _source_binding(path):
    """A diagnostic snapshot failure must not prevent media publication."""
    try:
        return _artifact_binding(path)
    except (OSError, ValueError, TypeError):
        return None


def _write_result_receipt(directory, out_path, srt_path, expected, on_log):
    """Keep private output identity after temporary media is removed.

    This optional receipt describes the files observed after publication, not
    immutable output storage or a completion/recovery protocol.
    """
    if directory is None:
        return False
    temporary = None
    try:
        artifacts = {"video": _artifact_binding(out_path)}
        if srt_path is not None:
            artifacts["subtitles"] = _artifact_binding(srt_path)
        for kind, artifact in artifacts.items():
            source = expected.get(kind)
            if source is None or any(
                artifact[field] != source[field] for field in ("size_bytes", "sha256")
            ):
                raise OSError("published artifact differs from rendered source")
        receipt = {
            "schema_version": 1,
            "operation": "explainer_render",
            "run_id": os.path.basename(directory),
            "status": "succeeded",
            "observed_at": utc_timestamp(),
            "artifacts": artifacts,
        }
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".result-",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(receipt, handle, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, os.path.join(directory, "result.json"))
        return True
    except (OSError, ValueError, TypeError):
        _log_notice(on_log, "Warning: persistent render artifact binding unavailable.")
        return False
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class _RenderLog:
    """Keep child output locally, without changing the caller's streaming callback."""

    def __init__(self, directory, on_log):
        self._file = None
        self._on_log = on_log
        self.failed = directory is None
        if directory is not None:
            try:
                descriptor = os.open(
                    os.path.join(directory, "render.log"),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                try:
                    self._file = os.fdopen(descriptor, "w", encoding="utf-8")
                except BaseException:
                    os.close(descriptor)
                    raise
            except (OSError, ValueError):
                self._failure()

    def _failure(self):
        if not self.failed:
            self.failed = True
            _log_notice(self._on_log, "Warning: persistent render output log is incomplete.")

    def record(self, line):
        if self._file is not None and not self.failed:
            try:
                timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                self._file.write(f"{timestamp} {line}\n")
                self._file.flush()
            except (OSError, ValueError):
                self._failure()

    def write(self, line):
        self.record(line)
        if self._on_log is not None:
            self._on_log(line)

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            except (OSError, ValueError):
                self._failure()
            self._file = None


def resolution_to_quality(resolution, fallback="-qm"):
    """Map a theme resolution like ``'1080p30'`` to a manim quality flag.

    Returns ``-ql/-qm/-qh/-qp/-qk`` from the shared quality ladder
    (``app.render.quality``); unknown or empty resolutions return ``fallback``
    (pass ``None`` to detect unknowns, as the CLI does).
    """
    height = str(resolution or "").split("p", 1)[0].strip()
    if not height.isdecimal():
        return fallback
    return quality_flag_for_height(int(height), fallback=fallback)


class CitationGateError(Exception):
    """Raised when one or more cited code facts fail verification."""

    def __init__(self, failures):
        self.failures = failures
        super().__init__("citation gate failed:\n  " + "\n  ".join(str(f) for f in failures))


class RenderError(Exception):
    """Raised when the manim render cannot run, or does not produce a video."""

    def __init__(self, outcome, workdir=None, detail=""):
        self.outcome = outcome
        self.workdir = workdir
        if workdir is None:
            message = f"render failed ({outcome}): {detail}"
        else:
            message = f"render failed ({outcome}); generated scene kept at {workdir}"
        super().__init__(message)


def render_storyboard(storyboard_path, out_path, quality=None, lang="en", on_log=None):
    """Verify, generate, and render ``storyboard_path`` to ``out_path`` (an mp4).

    ``quality`` is a manim flag (``-ql/-qm/-qh/-qp/-qk``); when ``None`` it is derived
    from the storyboard's ``theme.resolution`` (so a ``1080p30`` theme renders at
    ``-qh`` by default). Raises :class:`CitationGateError` if any cite fails, or
    :class:`RenderError` if manim is not installed (nothing is written) or the
    render produces no video (the generated scene is kept for debugging).

    After preflight, private diagnostics are retained beside the output in
    ``.explainer-logs/run-*/``. The path is announced through ``on_log`` (stderr
    when absent). Raw child output may contain private data. Diagnostic storage
    failure warns but does not change the rendering outcome.
    """
    storyboard = load_storyboard(storyboard_path)
    if quality is None:
        quality = resolution_to_quality(storyboard.theme.resolution)

    failures = verify_storyboard(storyboard)
    if failures:
        raise CitationGateError(failures)

    source = generate_scene(storyboard, scene_name=SCENE_NAME, lang=lang)

    # The light ``.[verify]`` install has no manim: ``python -m manim`` would fail
    # on import and leave a "generated scene kept at" workdir behind. Say what is
    # missing and how to get it, before anything is written.
    if not manim_available():
        raise RenderError(
            "manim_not_installed",
            detail=f"manim is not importable from {sys.executable}; install the "
            f'render stack with: pip install -e ".[explainer]" (see README, Quick start)',
        )

    workdir = tempfile.mkdtemp(prefix="explainer_")
    log = timeline = None
    diagnostic_dir = None
    try:
        diagnostic_dir = _diagnostic_directory(out_path, on_log)
        log = _RenderLog(diagnostic_dir, on_log)
        if diagnostic_dir is not None:
            timeline = DiagnosticTimeline(
                os.path.join(diagnostic_dir, "diagnostics.jsonl"),
                context={
                    "operation": "explainer_render",
                    "run_id": os.path.basename(diagnostic_dir),
                },
            )
            implementation_available = write_implementation_manifest(
                os.path.join(diagnostic_dir, "implementation.json"),
                operation="explainer_render",
                run_id=os.path.basename(diagnostic_dir),
            )
            timeline.event("implementation_manifest", available=implementation_available)
            if not implementation_available:
                _log_notice(on_log, "Warning: render implementation manifest unavailable.")
        phase = timeline.phase if timeline is not None else lambda name: nullcontext()
        with phase("prepare"):
            scene_file = os.path.join(workdir, "explainer_scene.py")
            with open(scene_file, "w", encoding="utf-8") as handle:
                handle.write(source)

            media_dir = os.path.join(workdir, "media")
            job = RenderJob(scene_file, SCENE_NAME)
            command = build_command(job, quality, media_dir)

            env = os.environ.copy()
            previous = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = REPO_ROOT + (os.pathsep + previous if previous else "")
        with phase("render"):
            result = RenderRunner().run_job(
                job,
                command,
                cancel_event=None,
                on_log=log.write,
                env=env,
            )
            if getattr(result, "diagnostic", ""):
                log.write(result.diagnostic)
            if timeline is not None:
                timeline.event(
                    "render_result",
                    outcome=result.outcome.value,
                    return_code=getattr(result, "return_code", None),
                )
            if result.outcome != JobOutcome.SUCCESS:
                raise RenderError(result.outcome, workdir)

        with phase("artifact"):
            produced = glob.glob(os.path.join(media_dir, "videos", "*", "*", SCENE_NAME + ".mp4"))
            if not produced:
                raise RenderError("no_output_mp4", workdir)

        with phase("publication"):
            os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
            expected = {"video": _source_binding(produced[0])} if diagnostic_dir else {}
            shutil.copyfile(produced[0], out_path)
            srt = os.path.splitext(produced[0])[0] + ".srt"
            srt_path = None
            if os.path.exists(srt):
                srt_path = os.path.splitext(out_path)[0] + ".srt"
                if diagnostic_dir:
                    expected["subtitles"] = _source_binding(srt)
                shutil.copyfile(srt, srt_path)
        receipt_available = _write_result_receipt(
            diagnostic_dir, out_path, srt_path, expected, on_log
        )
        if timeline is not None:
            timeline.event("artifact_binding", available=receipt_available)
            timeline.event("operation_finished", status="succeeded")
    except BaseException as exc:
        if log is not None:
            log.record("".join(traceback.format_exception(exc)))
        if timeline is not None:
            timeline.event("operation_failed", error_type=exception_type(exc))
        if not isinstance(exc, RenderError):
            shutil.rmtree(workdir, ignore_errors=True)
        # RenderError keeps the generated scene/media. The persistent diagnostics
        # survive both this path and cleanup of unexpected errors.
        try:
            exc.diagnostics_dir = diagnostic_dir
        except Exception:
            pass
        raise
    finally:
        if log is not None:
            log.close()
        if timeline is not None:
            timeline.event("logging_finished", output_log_complete=not log.failed)
            timeline.close()
    shutil.rmtree(workdir, ignore_errors=True)
    return out_path
