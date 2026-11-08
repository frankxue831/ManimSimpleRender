"""Opt-in JSONL adapter for the public synchronous production APIs.

Trusted local code only. Tokens and hashes identify attempts/bytes, not consent,
authenticity or semantic acceptance. Never infer termination from result.json.
"""

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import sys
import tempfile
import threading
import time
import traceback

from app.render.diagnostics import DiagnosticTimeline, utc_timestamp

from . import _ProductionInterrupted, _font_failure_hint, digest, pairs

VERSION = 1
STAGES = {
    "prepare",
    "render",
    "provenance",
    "artifact",
    "copy",
    "probe",
    "decode",
    "frames",
    "frame_trace",
    "inspection_integrity",
    "publication",
}


class _UsageError(Exception):
    pass


class _Interrupted(_ProductionInterrupted):
    def __init__(self, signum):
        self.signum = signum


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's original message can contain arbitrary argv, including secrets.
        raise _UsageError()


def _parser():
    parser = _Parser(add_help=False, allow_abbrev=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("describe", add_help=False, allow_abbrev=False)
    for operation in ("render", "inspect"):
        command = commands.add_parser(operation, add_help=False, allow_abbrev=False)
        names = ["timeline", "output-dir"]
        names += ["scene-file", "scene"] if operation == "render" else ["from-run"]
        for name in names:
            command.add_argument("--" + name, required=True)
        command.add_argument("--tool-timeout", type=float, default=30)
        if operation == "render":
            command.add_argument("--quality", default="qm", choices=["ql", "qm", "qh", "qp", "qk"])
            command.add_argument("--timeout", type=float, default=120)
            command.add_argument("--record-scene-frames", action="store_true")
    return parser


def describe_protocol():
    prefix = [sys.executable, "-m", "scene_production.agent"]
    templates = {
        "describe": [],
        "render": [
            "--scene-file",
            "SCENE_FILE",
            "--scene",
            "SCENE",
            "--timeline",
            "TIMELINE",
            "--output-dir",
            "NEW_ATTEMPT_DIRECTORY",
        ],
        "inspect": [
            "--from-run",
            "RENDER_RUN/result.json",
            "--timeline",
            "TIMELINE",
            "--output-dir",
            "NEW_ATTEMPT_DIRECTORY",
        ],
    }
    return {
        "protocol_version": VERSION,
        "transport": "JSONL only on stdout, including argument errors; use describe for help",
        "commands": {
            name: {
                "invocation": {
                    "args": prefix + [name] + arguments,
                    "cwd": str(Path(__file__).resolve().parent.parent),
                    "env": None,
                    "shell": False,
                }
            }
            for name, arguments in templates.items()
        },
        "invocation_usage": "Replace placeholders, then subprocess.run(**invocation).",
        "environment_policy": "env:null inherits the environment; values are never serialized",
        "render_options": {
            "quality": ["ql", "qm", "qh", "qp", "qk"],
            "timeout": 120,
            "tool_timeout": 30,
            "record_scene_frames": False,
        },
        "inspect_options": {"tool_timeout": 30},
        "output_policy": "New exclusive directory; parent must exist. Never reuse an attempt.",
        "events": ["started", "terminal"],
        "timing": "Events include observed UTC timestamp and process-local monotonic "
        "elapsed_seconds. Private diagnostics.jsonl records phases; it is not a "
        "completion, liveness or retry protocol.",
        "terminal_statuses": ["succeeded", "failed", "unknown"],
        "exit_codes": {
            "0": "succeeded",
            "1": "failed",
            "2": "invalid arguments",
            "3": "unknown or persistence/transport failure",
            "130": "interrupted",
            "143": "terminated",
        },
        "completion_rule": "Require process exit 0 and exactly one terminal succeeded event with "
        "the expected run_token. A receipt status is never a liveness signal.",
        "recovery_rule": "Missing terminal, interrupted transport, or stale running state means "
        "unknown. Inspect ownership and processes; never automatically retry.",
        "trust": "Trusted scene code/toolchain only; no sandbox or authorization substitute. "
        "Hashes and tokens do not authenticate receipts or grant permission.",
        "inspection": "Machine inspection only; no semantic visual acceptance. Reinspection "
        "does not apply source edits and does not execute Manim.",
        "persistence": "Each state/journal replacement is atomic, not a multi-file transaction. "
        "SIGKILL, disk failure or power loss can leave incomplete attempts.",
    }


def _atomic_write(path, text):
    fd, temporary = tempfile.mkstemp(prefix=".agent-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n"


@contextmanager
def _private_output(path):
    """Capture Python and native API output, never mixing it into stdout JSONL."""
    with path.open("x", encoding="utf-8") as log:
        os.chmod(path, 0o600)
        saved = []
        try:
            for stream in (sys.stdout, sys.stderr):
                try:
                    descriptor = stream.fileno()
                except (AttributeError, io.UnsupportedOperation):
                    continue
                stream.flush()
                backup = os.dup(descriptor)
                saved.append((descriptor, backup))
                os.dup2(log.fileno(), descriptor)
            with redirect_stdout(log), redirect_stderr(log):
                yield
        finally:
            try:
                log.flush()
            finally:
                for descriptor, backup in reversed(saved):
                    os.dup2(backup, descriptor)
                    os.close(backup)


@contextmanager
def _interrupts():
    previous = {}

    def interrupt(signum, frame):
        raise _Interrupted(signum)

    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, interrupt)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _read_json(path):
    with path.open("rb") as handle:
        data = handle.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("receipt too large")
    return json.loads(data, object_pairs_hook=pairs)


def _regular(value, root):
    path = Path(value)
    if not path.is_absolute() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("expected absolute regular file")
    resolved = path.resolve(strict=True)
    if resolved != path or not resolved.is_relative_to(root):
        raise ValueError("artifact outside owned root or symlinked")
    return path


def _run_root(value, attempt):
    path = Path(value)
    root = attempt / "production"
    if (
        not path.is_absolute()
        or path.parent != root
        or path.resolve(strict=True) != path
        or not path.is_dir()
        or not path.name.startswith("scene-")
    ):
        raise ValueError("run outside owned production directory")
    return path


def _verified_result(result, attempt, operation):
    if not isinstance(result, dict) or result.get("status") != "success":
        raise ValueError("API did not return success")
    if result.get("operation", "render") != ("render" if operation == "render" else "reinspect"):
        raise ValueError("wrong operation")
    run = _run_root(result["run_dir"], attempt)
    receipt = _regular(str(run / "result.json"), run)
    before = digest(receipt)
    if _read_json(receipt) != result:
        raise ValueError("returned and recorded receipts differ")
    artifacts = {"receipt": {"path": str(receipt), "sha256": before}}

    def verify(name, value, expected):
        path = _regular(value, run)
        actual = digest(path)
        if actual != expected or path.stat().st_size == 0:
            raise ValueError("artifact hash mismatch or empty artifact")
        artifacts[name] = {"path": str(path), "sha256": actual}

    for name, value in (
        ("source", str(run / "source.py")),
        ("timeline", str(run / "timeline.json")),
        ("video", result["video"]),
    ):
        verify(name, value, result[name + "_sha256"])
    frames = result["frames"]
    if not isinstance(frames, list) or not 1 <= len(frames) <= 256:
        raise ValueError("missing or excessive inspected frames")
    for index, frame in enumerate(frames):
        verify("frame_%03d" % index, frame["path"], frame["sha256"])
    if "frame_trace" in result:
        trace = result["frame_trace"]
        verify("frame_trace", trace["path"], trace["sha256"])
    if digest(receipt) != before:
        raise ValueError("receipt changed during verification")
    return artifacts


def _diagnostics(attempt, exc=None):
    if attempt is None:
        return {}
    result = {
        name: str(attempt / filename)
        for name, filename in (
            ("attempt_state", "attempt.json"),
            ("events", "events.jsonl"),
            ("api_log", "api.log"),
            ("timeline", "diagnostics.jsonl"),
        )
    }
    # Never accept a foreign path from an exception or associate old output logs.
    try:
        path = Path(exc.receipt_path)
        run = _run_root(str(path.parent), attempt)
        if path.name != "result.json":
            return result
        _regular(str(path), run)
        result["receipt"] = str(path)
        receipt = _read_json(path)
        if receipt.get("stage") in STAGES:
            result["stage"] = receipt["stage"]
        if receipt.get("failure_reason") in {
            "render_timeout",
            "render_cleanup_timeout",
            "interrupted",
            "cancelled",
            "tool_timeout",
            "render_failed",
            "executable_not_found",
            "exception",
        }:
            result["failure_reason"] = receipt["failure_reason"]
        log = run / "render.log"
        _regular(str(log), run)
        result["render_log"] = str(log)
        hint = _font_failure_hint(log)
        if hint:
            result["font_hint"] = hint
    except (AttributeError, OSError, ValueError, TypeError, KeyError):
        pass
    return result


def _call_api(args, attempt):
    options = vars(args).copy()
    operation = options.pop("operation")
    options["output_dir"] = str(attempt / "production")
    if operation == "render":
        from . import render

        return render(**options)
    from .reinspection import reinspect

    return reinspect(**options)


def main(argv=None):
    """One CLI invocation, not a thread-safe in-process service or retry engine."""
    token = secrets.token_hex(16)
    began = time.monotonic()
    timeline = None
    stream, attempt, operation, events = sys.stdout, None, None, []
    code, status, phase, payload = 1, "failed", "arguments", {}

    def event(kind, **fields):
        return dict(
            protocol_version=VERSION,
            event=kind,
            run_token=token,
            operation=operation,
            retry_safe=False,
            timestamp=utc_timestamp(),
            elapsed_seconds=round(max(0, time.monotonic() - began), 6),
            phase=phase,
            **fields,
        )

    def persist(value):
        if attempt is not None:
            _atomic_write(attempt / "attempt.json", _json(value))
            _atomic_write(
                attempt / "events.jsonl", "".join(_json(item) for item in events + [value])
            )

    def emit(value):
        stream.write(_json(value))
        stream.flush()

    try:
        args = _parser().parse_args(argv)
        operation = args.operation
        if operation == "describe":
            payload = {"description": describe_protocol()}
        else:
            phase = "claim"
            requested = Path(args.output_dir).absolute()
            target = requested.parent.resolve(strict=True) / requested.name
            target.mkdir(mode=0o700, exist_ok=False)
            attempt = target
            timeline = DiagnosticTimeline(
                attempt / "diagnostics.jsonl",
                context={"run_token": token, "operation": operation},
                warn=False,
            )
            phase = "persistence"
            started = event("started", status="running", attempt_dir=str(attempt))
            with timeline.phase("initial_persistence"):
                persist(started)
            events.append(started)
            phase = "transport"
            with timeline.phase("started_delivery"):
                emit(started)
            phase = "operation"
            with _interrupts(), _private_output(attempt / "api.log"):
                try:
                    with timeline.phase("operation"):
                        result = _call_api(args, attempt)
                    phase = "verification"
                    with timeline.phase("verification"):
                        artifacts = _verified_result(result, attempt, operation)
                    timeline.event("production_run", run_id=Path(result["run_dir"]).name)
                except BaseException:
                    # Keep early validation failures and verifier details in the
                    # private capture, never in the protocol or timing sidecar.
                    try:
                        traceback.print_exc()
                    except Exception:
                        pass
                    raise
            payload = {
                "artifacts": artifacts,
                "semantic_acceptance": False,
                "verification_scope": "retained_artifact_bytes",
                "diagnostics": _diagnostics(attempt),
            }
        code, status, phase = 0, "succeeded", "complete"
    except _UsageError:
        code, payload = 2, {"error": {"code": "invalid_arguments", "hint": "Run describe."}}
    except (_Interrupted, KeyboardInterrupt, SystemExit) as exc:
        code = 128 + getattr(exc, "signum", signal.SIGINT)
        status = "unknown"
        payload = {
            "error": {"code": "interrupted"},
            "process_cleanup_verified": False,
            "diagnostics": _diagnostics(attempt, exc),
        }
    except Exception as exc:
        error = "operation_failed"
        if phase == "claim":
            error = "output_exists" if isinstance(exc, FileExistsError) else "output_unavailable"
        elif phase == "verification":
            error = "verification_failed"
        elif phase == "persistence":
            code, status, error = 3, "unknown", "persistence_failed"
        elif phase == "transport":
            code, status, error = 3, "unknown", "transport_failed"
        elif isinstance(exc, FileNotFoundError):
            error = "input_or_tool_missing"
        elif isinstance(exc, ValueError):
            error = "invalid_input"
        payload = {"error": {"code": error}, "diagnostics": _diagnostics(attempt, exc)}
    # This sidecar covers the API and retained-byte verification, not the final
    # protocol commit/delivery below. Logging cannot change the API outcome.
    if timeline is not None:
        timeline.event("operation_outcome", status=status, exit_code=code, phase=phase)
        timeline.close()
        payload.setdefault("diagnostics", _diagnostics(attempt))[
            "timeline_write_failed"
        ] = timeline.failed
    terminal = event("terminal", status=status, exit_code=code, **payload)
    try:
        persist(terminal)
    except Exception:
        code = 3
        phase = "persistence"
        terminal = event(
            "terminal",
            status="unknown",
            exit_code=code,
            error={"code": "persistence_failed"},
            diagnostics=_diagnostics(attempt),
        )
        # Best-effort correction if only one of the two atomic replacements failed.
        try:
            persist(terminal)
        except Exception:
            pass
    try:
        emit(terminal)
    except (OSError, ValueError):
        # A partial/full line may have reached the caller: do not emit another one.
        phase = "transport"
        unknown = event(
            "terminal",
            status="unknown",
            exit_code=3,
            error={"code": "transport_failed"},
            diagnostics=_diagnostics(attempt),
        )
        try:
            persist(unknown)
        except Exception:
            pass
        return 3
    return code


if __name__ == "__main__":
    exit_code = main()
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        # Avoid a second flush/traceback during interpreter shutdown after a
        # disconnected caller. No event can be delivered over a closed pipe.
        exit_code = 3
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
    sys.exit(exit_code)
