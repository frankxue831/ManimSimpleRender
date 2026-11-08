"""Bounded production of trusted Manim scenes; not a sandbox."""

import ast
from contextlib import contextmanager, nullcontext
import hashlib
import io
import json
import math
import os
import re
from pathlib import Path
import struct
import sys
import subprocess
import tempfile
import threading
import time
import tokenize

from app.render.aggregation import JobOutcome
from app.render.command_builder import build_command
from app.render.diagnostics import DiagnosticTimeline, exception_type
from app.render.implementation import write_implementation_manifest
from app.render.jobs import RenderJob
from app.render.quality import known_quality_flags
from app.render.runner import RenderRunner
from app.scenes.discovery import _discover_from_tree


class _ProductionInterrupted(BaseException):
    """Marker shared with the signal adapter without importing the agent."""


def _record_implementation(run, operation, diagnostics):
    if not write_implementation_manifest(
        run / "implementation.json", operation=operation, run_id=run.name
    ):
        diagnostics.event("implementation_manifest_unavailable")
        try:
            print("Warning: implementation manifest unavailable.", file=sys.stderr)
        except (OSError, ValueError):
            pass


def _font_failure_hint(log_path):
    """Recognize a final font exception; never echo arbitrary subprocess log text.

    This is an untrusted-log hint, not a certified diagnosis or automatic repair.
    Keep the original log for errors outside these known scene-font failures.
    """
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 65536))
            text = handle.read(65536).decode("utf-8", errors="replace")
    except OSError:
        return None
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    if last.startswith("ValueError: SCENE_FONT has missing glyphs: "):
        return "selected font lacks required glyphs; choose a covering font with SCENE_FONT"
    if last.startswith("ValueError: SCENE_FONT is not installed: "):
        return "selected font is not installed; choose an installed font with SCENE_FONT"
    return None


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("times must be finite nonnegative numbers, excluding bool")
    return float(value)


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def load_timeline(raw):
    if len(raw) > 65536:
        raise ValueError("timeline exceeds 64 KiB")
    data = json.loads(raw, object_pairs_hook=pairs)
    if type(data) is not dict or set(data) != {"version", "segments"}:
        raise ValueError("expected version and segments")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("unsupported timeline version")
    segments = data["segments"]
    if type(segments) is not list or not 1 <= len(segments) <= 64:
        raise ValueError("expected 1..64 segments")
    previous = 0
    count = 0
    for segment in segments:
        if (
            type(segment) is not dict
            or not {"label", "start", "end"} <= set(segment)
            or set(segment) - {"label", "start", "end", "samples"}
        ):
            raise ValueError("invalid segment fields")
        if type(segment["label"]) is not str or not 1 <= len(segment["label"]) <= 200:
            raise ValueError("label must contain 1..200 characters")
        start, end = number(segment["start"]), number(segment["end"])
        if start != previous or not start < end <= 600:
            raise ValueError("segments must be contiguous from zero and end within 600s")
        samples = segment.get("samples", [])
        if type(samples) is not list:
            raise ValueError("samples must be a list")
        times = [number(t) for t in samples]
        if times != sorted(set(times)) or any(not start < t < end for t in times):
            raise ValueError("samples must be unique, ordered, strictly inside segment")
        count += 2 + len(times)
        previous = end
    if count > 256:
        raise ValueError("at most 256 samples")
    return data


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def tool(command, timeout, *, cwd=None):
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        cwd=cwd,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-2000:])
    return result.stdout


def video_duration(stream, video, timeout):
    """Measure selected video presentation span, never audio/container duration."""
    try:
        duration = number(float(stream["duration"]))
        if duration > 0:
            return duration, "stream.duration"
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    try:
        numerator, denominator = map(int, stream["time_base"].split("/"))
        duration = number(float(stream["duration_ts"]) * numerator / denominator)
        if duration > 0:
            return duration, "stream.duration_ts*time_base"
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        pass
    # Some formats omit stream duration. Decode frame timestamps rather than
    # falling back to a container that may be extended by an audio track.
    metadata = json.loads(
        tool(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time,pkt_duration_time,duration_time",
                "-of",
                "json",
                str(video),
            ],
            timeout,
        )
    )
    intervals = []
    for frame in metadata["frames"]:
        start = number(float(frame["best_effort_timestamp_time"]))
        span = number(float(frame.get("duration_time", frame.get("pkt_duration_time"))))
        if span <= 0:
            raise ValueError("missing positive video frame duration")
        intervals.append((start, start + span))
    if not intervals:
        raise ValueError("no video frame timestamps")
    duration = max(end for _, end in intervals) - min(start for start, _ in intervals)
    if duration <= 0:
        raise ValueError("invalid video presentation duration")
    return duration, "decoded frame presentation span"


def _verify_file(value, root, expected):
    if type(value) is not str or not Path(value).is_absolute():
        raise ValueError("recorded artifact path must be absolute")
    path = Path(value).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("recorded artifact must resolve inside run root")
    if type(expected) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("missing or invalid recorded SHA-256")
    if digest(path) != expected:
        raise ValueError("recorded artifact SHA-256 mismatch: " + str(path))
    return path


def _write_receipt(run, receipt):
    temporary = run / ".result.json.tmp"
    temporary.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    temporary.replace(run / "result.json")


def _failure(run, receipt, stage, exc, diagnostics=None):
    receipt.update(
        status="failed",
        stage=receipt.get("stage", stage),
        error=str(exc),
        exception_type=exception_type(exc),
    )
    if isinstance(exc, (_ProductionInterrupted, KeyboardInterrupt, SystemExit)):
        receipt["failure_reason"] = "interrupted"
        receipt["process_cleanup_verified"] = False
    else:
        receipt.setdefault(
            "failure_reason",
            "tool_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "exception",
        )
    if diagnostics is not None:
        diagnostics.event(
            "production_failed",
            phase=receipt["stage"],
            exception_type=exception_type(exc),
            reason=receipt["failure_reason"],
        )
    try:
        _write_receipt(run, receipt)
    except BaseException as persistence:
        if diagnostics is not None:
            diagnostics.event(
                "failure_receipt_unavailable", exception_type=exception_type(persistence)
            )
        raise persistence from exc
    exc.receipt_path = str(run / "result.json")


def _preview_frame_index(sample, trace):
    from .frame_clock import frame_coordinate

    coordinate, nearest, error, limit = frame_coordinate(sample, trace["frame_rate"])
    index = nearest if error <= limit else math.ceil(coordinate)
    return min(index, trace["frame_count"] - 1)


@contextmanager
def _production_phase(receipt, diagnostics, name):
    """Keep the legacy failure stage and the diagnostic timeline in agreement."""
    receipt["stage"] = name
    with diagnostics.phase(name) if diagnostics is not None else nullcontext():
        yield


def _inspect_video(video, data, run, tool_timeout, receipt, diagnostics=None):
    """One complete checker for render/reinspection, using sequential frame extraction."""
    with _production_phase(receipt, diagnostics, "inspection_integrity"):
        _verify_file(str(video), run, receipt["video_sha256"])
    with _production_phase(receipt, diagnostics, "probe"):
        probe = json.loads(
            tool(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(video),
                ],
                tool_timeout,
            )
        )
        stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    trace = None
    if "frame_trace" in receipt:
        from .frame_trace import read_trace

        with _production_phase(receipt, diagnostics, "frame_trace"):
            entry = receipt["frame_trace"]
            _verify_file(entry["path"], run, entry["sha256"])
            trace = read_trace(entry["path"], receipt["source_sha256"], stream)
    with _production_phase(receipt, diagnostics, "probe"):
        duration, duration_method = video_duration(stream, video, tool_timeout)
        numerator, denominator = map(int, stream["avg_frame_rate"].split("/"))
        fps = numerator / denominator
        if not math.isfinite(fps) or fps <= 0 or duration <= 0:
            raise ValueError("invalid video metadata")
        tolerance = 2 / fps + 0.05
        if abs(data["segments"][-1]["end"] - duration) > tolerance:
            raise ValueError("timeline duration disagrees with video")
    with _production_phase(receipt, diagnostics, "decode"):
        tool(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-f",
                "null",
                "-",
            ],
            tool_timeout,
        )
    with _production_phase(receipt, diagnostics, "frames"):
        frames = []
        plan = []
        (run / "frames").mkdir()
        for index, segment in enumerate(data["segments"]):
            start, end = segment["start"], min(segment["end"], duration)
            if end <= start:
                raise ValueError("segment outside video")
            times = sorted(
                set(
                    [start + (end - start) * 0.1, start + (end - start) * 0.9]
                    + segment.get("samples", [])
                )
            )
            for sample in times:
                if not start < sample < end:
                    raise ValueError("sample outside actual video")
                frame = run / "frames" / ("%03d.png" % len(plan))
                plan.append((index, sample, frame))
        for index, sample, frame in plan:
            if trace is not None:
                from fractions import Fraction

                native_rate = Fraction(trace["frame_rate"])
                native_index = _preview_frame_index(sample, trace)
                # Index selection avoids ambiguous timestamp seeking at a boundary
                # and remains valid for short final sections with no later PTS.
                command = [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(video),
                    "-map",
                    "0:v:0",
                    "-vf",
                    f"select=eq(n\\,{native_index})",
                    "-frames:v",
                    "1",
                    "-n",
                    frame.name,
                ]
            else:
                command = [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-ss",
                    str(sample),
                    "-i",
                    str(video),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-n",
                    frame.name,
                ]
            # image2 interprets percent sequences even in directory components.
            # Keep user-selected directories out of its output pattern. The frames
            # directory is new for this run; -n alone is not a path-safety boundary.
            tool(command, tool_timeout, cwd=frame.parent)
            header = frame.read_bytes()[:24]
            if (
                len(header) != 24
                or header[:8] != b"\x89PNG\r\n\x1a\n"
                or struct.unpack(">II", header[16:24]) != (stream["width"], stream["height"])
            ):
                raise RuntimeError("invalid PNG or changed resolution")
            item = {"path": str(frame), "time": sample, "segment": index, "sha256": digest(frame)}
            if trace is not None:
                item.update(
                    native_frame_index=native_index,
                    native_time=str(Fraction(native_index, native_rate)),
                    time_notice=(
                        "time is requested seconds; native_frame_index is extracted exactly"
                    ),
                )
            frames.append(item)
    with _production_phase(receipt, diagnostics, "inspection_integrity"):
        for path, expected in (
            (video, receipt["video_sha256"]),
            (run / "source.py", receipt["source_sha256"]),
            (run / "timeline.json", receipt["timeline_sha256"]),
        ):
            _verify_file(str(path), run, expected)
        if "frame_trace" in receipt:
            entry = receipt["frame_trace"]
            _verify_file(entry["path"], run, entry["sha256"])
    return dict(
        probe=probe,
        frames=frames,
        duration_tolerance=tolerance,
        video_duration_seconds=duration,
        video_duration_method=duration_method,
        inspection="Machine decode and frame extraction only; no semantic visual assessment.",
    )


def render(
    scene_file,
    scene,
    timeline,
    output_dir,
    quality="qm",
    timeout=120,
    tool_timeout=30,
    cancel_event=None,
    runner=None,
    record_scene_frames=False,
):
    """Return success only after machine inspection; failures retain result.json."""
    if type(record_scene_frames) is not bool:
        raise ValueError("record_scene_frames must be boolean")
    for value in (timeout, tool_timeout):
        if not 0 < number(value) <= 3600:
            raise ValueError("timeouts must be in (0, 3600]")
    flag = "-" + quality.lstrip("-")
    if flag not in known_quality_flags():
        raise ValueError("unknown quality")
    source = Path(scene_file).resolve()
    raw_source = source.read_bytes()
    with Path(timeline).open("rb") as f:
        raw = f.read(65537)
    data = load_timeline(raw)
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw_source).readline)
    tree = ast.parse(raw_source.decode(encoding), str(source))
    if scene not in {
        s.scene_class for s in _discover_from_tree(str(source), tree, str(source.parent))
    }:
        raise ValueError(
            "scene not recognized by AST discovery (imported base classes are not followed)"
        )
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="scene-", dir=root))
    receipt = {"status": "failed", "run_dir": str(run)}
    with DiagnosticTimeline(
        run / "diagnostics.jsonl", context={"operation": "render", "run_id": run.name}
    ) as diagnostics:
        diagnostics.event("production_started")
        try:
            _record_implementation(run, "render", diagnostics)
            with _production_phase(receipt, diagnostics, "prepare"):
                (run / "source.py").write_bytes(raw_source)
                (run / "timeline.json").write_bytes(raw)
                event = cancel_event if cancel_event is not None else threading.Event()
                deadline_fired_at = None
                completed_at = None
                completion_lock = threading.Lock()

                def deadline():
                    nonlocal deadline_fired_at
                    # Serialize the deadline with observed API completion. A
                    # delayed timer cannot turn a returned result into timeout.
                    with completion_lock:
                        if completed_at is None and not event.is_set():
                            deadline_fired_at = time.monotonic()
                            event.set()

                timer = threading.Timer(timeout, deadline)
                timer.daemon = True
                job = RenderJob(str(source), scene)
                command = build_command(job, flag, str(run / "media"))
                command += ["--disable_caching"]
                if record_scene_frames:
                    if command[:3] != [sys.executable, "-m", "manim"]:
                        raise ValueError(
                            "frame tracing requires the normal Python Manim environment"
                        )
                    command = [
                        sys.executable,
                        "-I",
                        str(Path(__file__).with_name("launcher.py")),
                        "capture",
                        *command[3:],
                    ]
                env = os.environ.copy()
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                if record_scene_frames:
                    env["SCENE_PRODUCTION_FRAME_TRACE"] = str(run / "frame-trace.json")
                else:
                    env.pop("SCENE_PRODUCTION_FRAME_TRACE", None)
                env["PYTHONPATH"] = str(source.parent) + os.pathsep + env.get("PYTHONPATH", "")
            with _production_phase(receipt, diagnostics, "render"):
                # The run is exclusive. Restrict raw child output and flush each
                # line so a silent/hung render's last output remains inspectable.
                descriptor = os.open(
                    run / "render.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "w", encoding="utf-8", buffering=1) as log:
                    timer.start()
                    try:
                        outcome = (runner or RenderRunner(terminate_timeout=1)).run_job(
                            job, command, event, lambda line: log.write(str(line) + "\n"), env
                        )
                        with completion_lock:
                            completed_at = time.monotonic()
                            cancelled_at_completion = event.is_set()
                    finally:
                        timer.cancel()
                        timer.join()
                    if outcome.diagnostic:
                        log.write(outcome.diagnostic + "\n")
                timed_out = deadline_fired_at is not None and deadline_fired_at <= completed_at
                observed_exit = getattr(outcome, "leader_exit_observed_at", None)
                exit_before_deadline = None
                if (
                    type(observed_exit) in (int, float)
                    and math.isfinite(observed_exit)
                    and deadline_fired_at is not None
                ):
                    exit_before_deadline = observed_exit < deadline_fired_at
                diagnostics.event(
                    "render_result",
                    outcome=outcome.outcome.value,
                    return_code=outcome.return_code,
                    leader_exit_observed_before_deadline=exit_before_deadline,
                )
                if timed_out or (cancelled_at_completion and deadline_fired_at is None):
                    if timed_out:
                        # The budget includes output draining and process-tree
                        # cleanup. A leader exit alone cannot certify success.
                        receipt["failure_reason"] = (
                            "render_cleanup_timeout" if exit_before_deadline else "render_timeout"
                        )
                        raise RuntimeError(
                            "render cleanup timed out"
                            if exit_before_deadline
                            else "render timed out"
                        )
                    receipt["failure_reason"] = "cancelled"
                    raise RuntimeError("render cancelled")
                if outcome.outcome != JobOutcome.SUCCESS or outcome.return_code != 0:
                    receipt["failure_reason"] = (
                        "executable_not_found"
                        if outcome.outcome == JobOutcome.MANIM_NOT_FOUND
                        else (
                            "cancelled"
                            if outcome.outcome == JobOutcome.CANCELLED
                            else "render_failed"
                        )
                    )
                    message = "render failed: " + str(outcome)
                    hint = _font_failure_hint(run / "render.log")
                    if hint:
                        message += "\nfont hint (from render log): " + hint
                    raise RuntimeError(message)
            with _production_phase(receipt, diagnostics, "provenance"):
                if source.read_bytes() != raw_source:
                    raise RuntimeError("source changed during render")
            with _production_phase(receipt, diagnostics, "artifact"):
                videos = [
                    p
                    for p in (run / "media").rglob(scene + ".mp4")
                    if "partial_movie_files" not in p.relative_to(run / "media").parts
                    and p.is_file()
                    and p.stat().st_size > 0
                ]
                if len(videos) != 1:
                    raise RuntimeError("expected exactly one final nonempty video")
                video = videos[0]
                source_sha = hashlib.sha256(raw_source).hexdigest()
                timeline_sha = hashlib.sha256(raw).hexdigest()
                video_sha = digest(video)
                for path, expected in (
                    (video, video_sha),
                    (run / "source.py", source_sha),
                    (run / "timeline.json", timeline_sha),
                ):
                    _verify_file(str(path), run, expected)
                if event.is_set():
                    receipt["failure_reason"] = "cancelled"
                    raise RuntimeError("render cancelled before recording artifact")
                if source.read_bytes() != raw_source:
                    raise RuntimeError("source changed before recording artifact")
                receipt.update(
                    completed_artifact_version=1,
                    video=str(video),
                    video_sha256=video_sha,
                    source=str(source),
                    source_sha256=source_sha,
                    timeline_sha256=timeline_sha,
                    command=command,
                )
                if record_scene_frames:
                    trace_path = run / "frame-trace.json"
                    receipt["frame_trace"] = {"path": str(trace_path), "sha256": digest(trace_path)}
                _write_receipt(run, receipt)
            inspection = _inspect_video(video, data, run, tool_timeout, receipt, diagnostics)
            with _production_phase(receipt, diagnostics, "publication"):
                receipt.update(
                    status="success",
                    provenance=(
                        "Source checked before/after render; transient changes cannot be excluded. "
                        "Helpers, imports and environment not snapshotted. "
                        "Trusted code, not sandboxed."
                    ),
                )
                receipt.update(inspection)
                receipt.pop("stage", None)
                _write_receipt(run, receipt)
        except BaseException as exc:
            _failure(run, receipt, diagnostics.last_phase or "prepare", exc, diagnostics)
            raise
        diagnostics.event("production_finished")
    return receipt
