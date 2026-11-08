"""Local, fenced render supervision. Callers must independently verify consent.

Recovery only proposes an outcome; it never changes a fenced ledger attempt.
Private evidence is not authenticated against a malicious local writer.
"""

import json
import math
import os
from contextlib import contextmanager, nullcontext
from pathlib import Path
import subprocess
import sys
import threading
import time

from scene_production import agent
from app.render.diagnostics import DiagnosticTimeline, exception_type
from app.render.process_session import _signal_group
from app.render.runner import DEFAULT_SUPERVISION_CLEANUP_TIMEOUT

LIMIT = 2 * 1024 * 1024


class _EvidenceError(ValueError):
    """An internal, fixed diagnostic code, never arbitrary exception text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _read(path):
    return agent._read_json(path)


def _bounded_bytes(path):
    with path.open("rb") as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("output limit")
    return data


def _persist_supervised(path, value, heartbeat, interval):
    """One writer, with lease renewal on the SQLite-owning calling thread.

    If authority changes, finish this same writer before returning the error.
    There is never a competing correction writer. Evidence is not permission.
    Unbounded OS/storage stalls are outside the local recovery guarantee.
    """
    errors = []

    def write():
        try:
            agent._atomic_write(path, value)
            os.chmod(path, 0o600)
        except BaseException as exc:
            errors.append(exc)

    heartbeat()
    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    heartbeat_error = None
    while writer.is_alive():
        try:
            heartbeat()
        except Exception as exc:
            heartbeat_error = exc
        writer.join(timeout=interval)
    if heartbeat_error is not None:
        raise heartbeat_error
    if errors:
        raise errors[0]
    heartbeat()


def _events(data):
    if len(data) > LIMIT or not data.endswith(b"\n"):
        raise _EvidenceError("protocol_incomplete")
    try:
        events = [json.loads(line, object_pairs_hook=agent.pairs) for line in data.splitlines()]
    except ValueError:
        raise _EvidenceError("protocol_invalid_json") from None
    if any(not isinstance(event, dict) for event in events):
        raise _EvidenceError("protocol_invalid_event")
    return events


def verify_result(attempt, data, returncode):
    """Validate transport, durable protocol, receipt and all retained hashes."""
    events = _events(data)
    if len(events) != 2:
        raise _EvidenceError("protocol_event_count")
    started, terminal = events
    if (
        started.get("event") != "started"
        or started.get("status") != "running"
        or started.get("attempt_dir") != str(attempt)
        or terminal.get("event") != "terminal"
        or not started.get("run_token")
        or terminal.get("run_token") != started["run_token"]
        or any(e.get("protocol_version") != 1 or e.get("operation") != "render" for e in events)
        or terminal.get("exit_code") != returncode
        or (terminal.get("status"), returncode) not in (("succeeded", 0), ("failed", 1))
    ):
        raise _EvidenceError("protocol_mismatch")
    if _read(attempt / "attempt.json") != terminal:
        raise _EvidenceError("state_mismatch")
    journal = _bounded_bytes(attempt / "events.jsonl")
    if _events(journal) != events:
        raise _EvidenceError("journal_mismatch")
    if terminal["status"] == "succeeded":
        artifacts = terminal["artifacts"]
        receipt = agent._regular(artifacts["receipt"]["path"], attempt)
        if agent._verified_result(_read(receipt), attempt, "render") != artifacts:
            raise _EvidenceError("artifact_mismatch")
    return terminal["status"]


def reconcile_evidence(directory):
    """Read-only recovery proposal requiring recorded observed process exit."""
    try:
        directory = Path(directory).resolve(strict=True)
        evidence = _read(directory / "exit.json")
        if not isinstance(evidence, dict):
            return {"status": "unknown"}
        if evidence.get("exit_observed") is not True or evidence.get("interrupted"):
            return {"status": "unknown"}
        status = verify_result(
            directory / "agent", _bounded_bytes(directory / "protocol.log"), evidence["returncode"]
        )
        return dict(evidence, status=status, **_handoff_result(directory))
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "unknown"}


def _handoff_result(directory):
    terminal = _read(directory / "agent" / "attempt.json")
    process = _read(directory / "process.json")
    result = {
        "attempt_id": process["attempt_id"],
        "fence": process["fence"],
        "directory": str(directory),
        "run_token": terminal["run_token"],
        "process_evidence": str(directory / "process.json"),
        "exit_evidence": str(directory / "exit.json"),
        "semantic_acceptance": False,
    }
    if terminal["status"] == "succeeded":
        result.update(artifacts=terminal["artifacts"], verification_scope="retained_artifact_bytes")
    else:
        error = terminal.get("error", {})
        if isinstance(error, dict) and error.get("code") in {
            "invalid_arguments",
            "output_exists",
            "output_unavailable",
            "verification_failed",
            "operation_failed",
            "input_or_tool_missing",
            "invalid_input",
        }:
            result["agent_error_code"] = error["code"]
        diagnostics = terminal.get("diagnostics", {})
        if isinstance(diagnostics, dict) and diagnostics.get("stage") in agent.STAGES:
            result["agent_stage"] = diagnostics["stage"]
        reason = diagnostics.get("failure_reason") if isinstance(diagnostics, dict) else None
        if isinstance(reason, str) and reason in {
            "render_timeout",
            "render_cleanup_timeout",
            "interrupted",
            "cancelled",
            "executable_not_found",
            "render_failed",
            "tool_timeout",
            "exception",
        }:
            result["agent_failure_reason"] = reason
    return result


def _arguments(payload, attempt):
    allowed = {
        "scene_file",
        "scene",
        "timeline",
        "quality",
        "timeout",
        "tool_timeout",
        "record_scene_frames",
    }
    if set(payload) - allowed:
        raise ValueError("unsupported render parameter")
    args = [sys.executable, "-m", "scene_production.agent", "render"]
    for name in ("scene_file", "scene", "timeline"):
        value = payload[name]
        if not isinstance(value, str) or not value:
            raise ValueError("invalid render parameter")
        if name != "scene":
            value = str(Path(value).resolve(strict=True))
        args.extend(["--" + name.replace("_", "-"), value])
    quality = payload.get("quality", "qm")
    if quality not in ("ql", "qm", "qh", "qp", "qk"):
        raise ValueError("invalid quality")
    args.extend(["--quality", quality, "--output-dir", str(attempt)])
    for name, default in (("timeout", 120), ("tool_timeout", 30)):
        value = payload.get(name, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("invalid timeout")
        args.extend(["--" + name.replace("_", "-"), str(value)])
    if not isinstance(payload.get("record_scene_frames", False), bool):
        raise ValueError("invalid frame option")
    if payload.get("record_scene_frames"):
        args.append("--record-scene-frames")
    return args


def run_task(
    ledger, task_id, worker_id, authorization_revision, *, lease_seconds=60, poll_interval=0.1
):
    """Claim once, supervise one fixed module invocation, never retry.

    Claim/heartbeat enforce live authorization and fencing. A stored authorization
    is evidence only, not a replacement for the caller checking its source.
    """
    task = ledger.get_task(task_id)
    if task["kind"] != "render" or task.get("action") != "render":
        raise ValueError("render task required")
    # Validate before claim; no caller-selected executable, cwd or output root.
    if (
        isinstance(poll_interval, bool)
        or not isinstance(poll_interval, (int, float))
        or not math.isfinite(poll_interval)
        or poll_interval <= 0
    ):
        raise ValueError("poll_interval must be finite and positive")
    _arguments(task["payload"], Path("unused"))
    claim_started = time.monotonic()
    claim = ledger.claim_local_render(
        task_id, worker_id, authorization_revision, lease_seconds=lease_seconds
    )
    claim_elapsed = time.monotonic() - claim_started
    attempt_id, fence = claim["attempt_id"], claim["fence"]
    directory = Path(ledger.directory).resolve() / "outputs" / attempt_id
    process = None
    interrupted = False
    result = {"status": "unknown", "exit_observed": False}
    threads = []
    overflow = threading.Event()
    capture_errors = []
    timeline = None
    current_phase = "output_setup"

    @contextmanager
    def phase(name):
        nonlocal current_phase
        current_phase = name
        with timeline.phase(name) if timeline is not None else nullcontext():
            yield

    def diagnostic(exc):
        return {
            "diagnostic_phase": current_phase,
            "diagnostic_code": (
                exc.code if isinstance(exc, _EvidenceError) else current_phase + "_failed"
            ),
            "exception_type": exception_type(exc),
        }

    def heartbeat():
        return ledger.heartbeat(attempt_id, fence, lease_seconds=lease_seconds)

    try:
        directory.parent.mkdir(mode=0o700, exist_ok=True)
        if directory.parent.resolve() != directory.parent:
            raise ValueError("symlinked output root")
        directory.mkdir(mode=0o700)
        timeline = DiagnosticTimeline(
            directory / "diagnostics.jsonl",
            context={"task_id": task_id, "attempt_id": attempt_id, "fence": fence},
        )
        timeline.event("claim_finished", duration_seconds=claim_elapsed)
        with phase("prepare"):
            args = _arguments(task["payload"], directory / "agent")
            ledger.heartbeat(attempt_id, fence, lease_seconds=lease_seconds)
        with phase("launch"):
            # Commit the one-way launch gate before Popen. A fenced worker cannot
            # launch after a never-started attempt has been abandoned by recovery.
            ledger.begin_execution(attempt_id, fence)
            process = subprocess.Popen(
                args,
                cwd=Path(agent.__file__).resolve().parent.parent,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            timeline.event("process_started", pid=process.pid)
        with phase("process_evidence"):
            _persist_supervised(
                directory / "process.json",
                agent._json({"pid": process.pid, "attempt_id": attempt_id, "fence": fence}),
                heartbeat,
                min(poll_interval, lease_seconds / 6),
            )

        def drain(pipe, name):
            try:
                with (directory / name).open("xb") as log:
                    os.chmod(log.name, 0o600)
                    size = 0
                    while True:
                        chunk = pipe.read(65536)
                        if not chunk:
                            break
                        log.write(chunk[: max(0, LIMIT - size)])
                        size += len(chunk)
                        if size > LIMIT:
                            if not capture_errors:
                                capture_errors.append("output_limit")
                            overflow.set()
            except (OSError, ValueError):
                capture_errors.append("output_capture_failed")
                overflow.set()
            finally:
                pipe.close()

        with phase("agent_wait"):
            for pipe, name in ((process.stdout, "protocol.log"), (process.stderr, "stderr.log")):
                thread = threading.Thread(target=drain, args=(pipe, name), daemon=True)
                thread.start()
                threads.append(thread)
            next_heartbeat = time.monotonic()
            while process.poll() is None:
                if overflow.is_set():
                    raise _EvidenceError(capture_errors[0])
                if time.monotonic() >= next_heartbeat:
                    ledger.heartbeat(attempt_id, fence, lease_seconds=lease_seconds)
                    next_heartbeat = time.monotonic() + lease_seconds / 3
                time.sleep(min(poll_interval, lease_seconds / 6))
            timeline.event("process_exited", returncode=process.returncode)
        with phase("drain"):
            drain_deadline = time.monotonic() + 4
            while any(thread.is_alive() for thread in threads):
                if time.monotonic() >= drain_deadline:
                    raise _EvidenceError("output_drain_timeout")
                ledger.heartbeat(attempt_id, fence, lease_seconds=lease_seconds)
                time.sleep(min(poll_interval, lease_seconds / 6))
            if overflow.is_set():
                raise _EvidenceError(capture_errors[0])
        verified = []
        verification_error = []

        def verify():
            try:
                status = verify_result(
                    directory / "agent",
                    _bounded_bytes(directory / "protocol.log"),
                    process.returncode,
                )
                verified.append(
                    dict(
                        status=status,
                        exit_observed=True,
                        returncode=process.returncode,
                        **_handoff_result(directory),
                    )
                )
            except BaseException as exc:
                verification_error.append(exc)

        with phase("verification"):
            verifier = threading.Thread(target=verify, daemon=True)
            verifier.start()
            while verifier.is_alive():
                ledger.heartbeat(attempt_id, fence, lease_seconds=lease_seconds)
                verifier.join(timeout=min(poll_interval, lease_seconds / 6))
            if verification_error:
                raise verification_error[0]
            if not verified:
                raise _EvidenceError("verification_incomplete")
            ledger.heartbeat(attempt_id, fence, lease_seconds=lease_seconds)
            result = verified[0]

    except BaseException as exc:
        interrupted = True
        result.update(diagnostic(exc))
        if timeline is not None:
            timeline.event("attempt_interrupted", **diagnostic(exc))
        if process is not None:
            # Escalate the captured group even when the leader already exited.
            # This remains best-effort: detached descendants are not covered.
            with phase("cleanup"):
                try:
                    _signal_group(process.pid, force=False, timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                try:
                    # The agent's RenderRunner owns a separate render session. Give
                    # its cooperative cleanup the shared core's conservative bound
                    # before killing the wrapper that is supervising that session.
                    process.wait(timeout=DEFAULT_SUPERVISION_CLEANUP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    _signal_group(process.pid, force=True, timeout=2)
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                result.update(
                    status="unknown",
                    exit_observed=process.poll() is not None,
                    returncode=process.returncode,
                    descendants_cleanup_verified=False,
                )
    for thread in threads:
        thread.join(timeout=2)
    if process is not None:
        # An acquisition failure may precede the reader threads. Only close
        # streams that no reader owns; closing a live reader can block on its lock.
        for pipe in (process.stdout, process.stderr)[len(threads) :]:
            if pipe is not None:
                pipe.close()
    if directory.exists():
        try:
            with phase("exit_evidence"):
                _persist_supervised(
                    directory / "exit.json",
                    agent._json(dict(result, interrupted=interrupted)),
                    heartbeat,
                    min(poll_interval, lease_seconds / 6),
                )
        except Exception as exc:
            # The authoritative ledger may not adopt success after revocation,
            # expiry or failed evidence persistence. Never race a second writer.
            failure = diagnostic(exc)
            if "diagnostic_code" in result:
                result["evidence_error"] = failure
            else:
                result = dict(status="unknown", exit_observed=result["exit_observed"], **failure)
    try:
        if timeline is not None:
            result["diagnostics_log"] = str(directory / "diagnostics.jsonl")
            if timeline.failed:
                result["diagnostics_incomplete"] = True
        with phase("ledger_finish"):
            ledger.finish(attempt_id, fence, result["status"], result)
        if timeline is not None:
            timeline.event("attempt_finished", status=result["status"])
    finally:
        if timeline is not None:
            timeline.close()
    return result
