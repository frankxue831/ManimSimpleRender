"""Local, fenced render supervision. Callers must independently verify consent.

Recovery only proposes an outcome; it never changes a fenced ledger attempt.
Private evidence is not authenticated against a malicious local writer.
"""

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from scene_production import agent

LIMIT = 2 * 1024 * 1024


def _read(path):
    return agent._read_json(path)


def _bounded_bytes(path):
    with path.open("rb") as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("output limit")
    return data


def _events(data):
    if len(data) > LIMIT or not data.endswith(b"\n"):
        raise ValueError("incomplete protocol")
    events = [json.loads(line, object_pairs_hook=agent.pairs) for line in data.splitlines()]
    if any(not isinstance(event, dict) for event in events):
        raise ValueError("invalid event")
    return events


def verify_result(attempt, data, returncode):
    """Validate transport, durable protocol, receipt and all retained hashes."""
    events = _events(data)
    if len(events) != 2:
        raise ValueError("invalid event count")
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
        raise ValueError("inconsistent protocol")
    if _read(attempt / "attempt.json") != terminal:
        raise ValueError("state mismatch")
    journal = _bounded_bytes(attempt / "events.jsonl")
    if _events(journal) != events:
        raise ValueError("journal mismatch")
    if terminal["status"] == "succeeded":
        artifacts = terminal["artifacts"]
        receipt = agent._regular(artifacts["receipt"]["path"], attempt)
        if agent._verified_result(_read(receipt), attempt, "render") != artifacts:
            raise ValueError("artifact mismatch")
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
    _arguments(task["payload"], Path("unused"))
    claim = ledger.claim(task_id, worker_id, authorization_revision, lease_seconds=lease_seconds)
    attempt_id, fence = claim["attempt_id"], claim["fence"]
    directory = Path(ledger.directory).resolve() / "outputs" / attempt_id
    process = None
    interrupted = False
    result = {"status": "unknown", "exit_observed": False}
    threads = []
    overflow = threading.Event()
    try:
        directory.parent.mkdir(mode=0o700, exist_ok=True)
        if directory.parent.resolve() != directory.parent:
            raise ValueError("symlinked output root")
        directory.mkdir(mode=0o700)
        args = _arguments(task["payload"], directory / "agent")
        ledger.heartbeat(attempt_id, fence, lease_seconds=lease_seconds)
        process = subprocess.Popen(
            args,
            cwd=Path(agent.__file__).resolve().parent.parent,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        agent._atomic_write(
            directory / "process.json",
            agent._json({"pid": process.pid, "attempt_id": attempt_id, "fence": fence}),
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
                            overflow.set()
            except (OSError, ValueError):
                overflow.set()
            finally:
                pipe.close()

        for pipe, name in ((process.stdout, "protocol.log"), (process.stderr, "stderr.log")):
            thread = threading.Thread(target=drain, args=(pipe, name), daemon=True)
            thread.start()
            threads.append(thread)
        next_heartbeat = time.monotonic()
        while process.poll() is None:
            if overflow.is_set():
                raise ValueError("output limit")
            if time.monotonic() >= next_heartbeat:
                ledger.heartbeat(attempt_id, fence, lease_seconds=lease_seconds)
                next_heartbeat = time.monotonic() + lease_seconds / 3
            time.sleep(poll_interval)
        for thread in threads:
            thread.join(timeout=2)
        if any(t.is_alive() for t in threads) or overflow.is_set():
            raise ValueError("incomplete output")
        result = {"status": "unknown", "exit_observed": True, "returncode": process.returncode}
        result["status"] = verify_result(
            directory / "agent", _bounded_bytes(directory / "protocol.log"), process.returncode
        )
        result.update(_handoff_result(directory))
    except BaseException:
        interrupted = True
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            result = {
                "status": "unknown",
                "exit_observed": process.poll() is not None,
                "returncode": process.returncode,
                "descendants_cleanup_verified": False,
            }
    for thread in threads:
        thread.join(timeout=2)
    if directory.exists():
        try:
            agent._atomic_write(
                directory / "exit.json", agent._json(dict(result, interrupted=interrupted))
            )
            os.chmod(directory / "exit.json", 0o600)
        except OSError:
            result = {"status": "unknown", "exit_observed": False}
    ledger.finish(attempt_id, fence, result["status"], result)
    return result
