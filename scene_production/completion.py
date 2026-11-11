"""Pure interpretation of one captured scene-agent stdout stream and process exit.

This checks protocol evidence only. It never opens receipts, hashes artifacts,
launches processes, retries work, or establishes visual/semantic acceptance.
"""

from dataclasses import dataclass
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re


@dataclass(frozen=True)
class Completion:
    """A protocol outcome, not an independent verification of retained files.

    ``terminal`` is provided only for internally consistent protocol evidence.
    ``run_token`` comes from the actual started event, or the sole terminal for
    describe/pre-start outcomes. Neither token source authenticates the capture.
    Every outcome still requires a separate, explicit decision before new work.
    """

    status: str
    reason: str
    run_token: str | None = None
    terminal: dict | None = None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("non-finite JSON number")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON number")
    return result


def _token(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _absolute_path(value):
    # Pure path operations also support captures made on another platform.
    return (
        isinstance(value, str)
        and "\x00" not in value
        and (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute())
    )


def _artifacts(value):
    """Check manifest shape, NOT existence, containment, content or hashes."""
    if not isinstance(value, dict):
        return False
    required = {"receipt", "source", "timeline", "video"}
    frames = {key for key in value if re.fullmatch(r"frame_[0-9]{3}", key)}
    if (
        not required.issubset(value)
        or not 1 <= len(frames) <= 256
        or frames != {"frame_%03d" % index for index in range(len(frames))}
        or set(value) - required - frames - {"frame_trace"}
    ):
        return False
    return all(
        isinstance(item, dict)
        and _absolute_path(item.get("path"))
        and isinstance(item.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is not None
        for item in value.values()
    )


def read_completion(
    stdout: str | bytes,
    returncode: int | None,
    *,
    expected_operation: str,
    expected_token: str | None = None,
) -> Completion:
    """Interpret the complete stdout captured from ONE scene-agent invocation.

    Pass the process return code observed by wait/run, or None if exit was not
    observed. Never substitute a terminal's exit_code for that observation.
    Render/inspect success requires exactly started then terminal; the token is
    captured from that actual started event, not inferred from the terminal or a
    receipt. An optional independently saved expected_token must also match.

    Describe has one terminal and no started event. A terminal-only usage error
    has operation=None because parsing did not finish; recognized pre-claim
    failures also need no started event. All other absent/extra/reordered events,
    invalid JSON (including duplicate keys), unterminated lines and conflicting
    protocol evidence produce unknown. Unknown is never a retry instruction.

    Additional diagnostic fields are not completion/liveness evidence. The
    returned manifest is the producer's claim, not a fresh artifact recheck.
    No filesystem access or subprocess work occurs in this function.
    """
    if expected_operation not in ("describe", "render", "inspect"):
        return Completion("unknown", "invalid_expected_operation")
    if expected_token is not None and not _token(expected_token):
        return Completion("unknown", "invalid_expected_token")
    if type(returncode) is not int:
        return Completion("unknown", "process_exit_unobserved_or_invalid")
    if isinstance(stdout, bytes):
        try:
            stdout = stdout.decode("utf-8", errors="strict")
        except UnicodeError:
            return Completion("unknown", "invalid_utf8")
    if not isinstance(stdout, str):
        return Completion("unknown", "invalid_capture")
    if not stdout.endswith("\n"):
        return Completion("unknown", "incomplete_jsonl")
    try:
        # Split only on the JSONL delimiter. splitlines() also splits valid JSON
        # string characters such as U+2028, and can hide other framing defects.
        events = [
            json.loads(line, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
            for line in stdout.split("\n")[:-1]
        ]
    except (ValueError, RecursionError):
        return Completion("unknown", "invalid_jsonl")
    if len(events) not in (1, 2) or any(not isinstance(item, dict) for item in events):
        return Completion("unknown", "invalid_event_sequence")
    started = events[0] if len(events) == 2 else None
    terminal = events[-1]
    if terminal.get("event") != "terminal" or (
        started is not None
        and (
            started.get("event") != "started"
            or started.get("status") != "running"
            or not _absolute_path(started.get("attempt_dir"))
            or any(key in started for key in ("exit_code", "error", "artifacts", "description"))
        )
    ):
        return Completion("unknown", "invalid_event_sequence")
    if any(
        type(item.get("protocol_version")) is not int
        or item["protocol_version"] != 1
        or item.get("retry_safe") is not False
        or not _token(item.get("run_token"))
        or ("semantic_acceptance" in item and item["semantic_acceptance"] is not False)
        or (
            "process_cleanup_verified" in item
            and type(item["process_cleanup_verified"]) is not bool
        )
        for item in events
    ):
        return Completion("unknown", "invalid_protocol_fields")
    token = (started if started is not None else terminal)["run_token"]
    if any(item["run_token"] != token for item in events) or (
        expected_token is not None and token != expected_token
    ):
        return Completion("unknown", "token_mismatch", token)
    if type(terminal.get("exit_code")) is not int or terminal["exit_code"] != returncode:
        return Completion("unknown", "exit_mismatch", token)
    status = terminal.get("status")
    error = terminal.get("error")
    error_code = error.get("code") if isinstance(error, dict) else None
    usage = (
        started is None
        and status == "failed"
        and returncode == 2
        and error_code == "invalid_arguments"
        and "operation" in terminal
        and terminal["operation"] is None
    )
    if not usage and any(item.get("operation") != expected_operation for item in events):
        return Completion("unknown", "operation_mismatch", token)
    if expected_operation == "describe" and started is not None:
        return Completion("unknown", "invalid_event_sequence", token)
    if status == "succeeded" and returncode == 0:
        if "error" in terminal or "process_cleanup_verified" in terminal:
            return Completion("unknown", "conflicting_success", token)
        if expected_operation == "describe":
            if not isinstance(terminal.get("description"), dict) or "artifacts" in terminal:
                return Completion("unknown", "invalid_description", token)
        elif (
            started is None
            or "description" in terminal
            or terminal.get("semantic_acceptance") is not False
            or terminal.get("verification_scope") != "retained_artifact_bytes"
            or not _artifacts(terminal.get("artifacts"))
        ):
            return Completion("unknown", "invalid_success_evidence", token)
        return Completion("succeeded", "terminal_succeeded", token, terminal)
    if any(key in terminal for key in ("artifacts", "description", "verification_scope")):
        return Completion("unknown", "conflicting_terminal", token)
    if usage:
        return Completion("failed", "terminal_failed", token, terminal)
    if status == "failed" and returncode == 1:
        errors = (
            ("output_exists", "output_unavailable")
            if started is None and expected_operation != "describe"
            else (
                "input_or_tool_missing",
                "invalid_input",
                "operation_failed",
                "verification_failed",
            )
        )
        if error_code in errors and expected_operation != "describe":
            return Completion("failed", "terminal_failed", token, terminal)
    if status == "unknown" and (
        (returncode == 3 and error_code in ("persistence_failed", "transport_failed"))
        or (
            returncode in (130, 143)
            and error_code == "interrupted"
            and terminal.get("process_cleanup_verified") is False
        )
    ):
        return Completion("unknown", "terminal_unknown", token, terminal)
    return Completion("unknown", "invalid_terminal_outcome", token)
