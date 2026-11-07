"""Opt-in local task handoff CLI. JSON output is not evidence of permission."""

import argparse
import json
import sqlite3
import sys

from scene_production import pairs

from .ledger import Ledger, LedgerError


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid_arguments")


def _parser():
    parser = _Parser(allow_abbrev=False)
    parser.add_argument("--workflow")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    commands.add_parser("describe")
    commands.add_parser("init")
    tasks = commands.add_parser("tasks")
    tasks.add_argument("--owner")
    tasks.add_argument("--eligible", action="store_true")
    commands.add_parser("reconcile")
    for name in ("authorization-add", "task-add", "resolve-unknown"):
        commands.add_parser(name).add_argument("--input", required=True)
    for name in ("task", "handoff", "authorization", "attempt", "resolution"):
        commands.add_parser(name).add_argument("--id", required=True)
    commands.add_parser("attempts").add_argument("--task-id")
    commands.add_parser("resolutions").add_argument("--task-id")
    revoke = commands.add_parser("authorization-revoke")
    revoke.add_argument("--id", required=True)
    revoke.add_argument("--source-ref", required=True)
    events = commands.add_parser("events")
    events.add_argument("--owner", required=True)
    ack = commands.add_parser("ack")
    ack.add_argument("--event-id", required=True)
    ack.add_argument("--owner", required=True)
    for name in ("claim-manual", "run"):
        claim = commands.add_parser(name)
        claim.add_argument("--task-id", required=True)
        claim.add_argument("--worker", required=True)
        claim.add_argument("--authorization-revision", required=True, type=int)
        claim.add_argument("--lease-seconds", default=60, type=float)
    finish = commands.add_parser("finish-manual")
    finish.add_argument("--attempt-id", required=True)
    finish.add_argument("--fence", type=int, required=True)
    finish.add_argument("--status", choices=("succeeded", "failed", "unknown"), required=True)
    finish.add_argument("--input", required=True)
    heartbeat = commands.add_parser("heartbeat-manual")
    heartbeat.add_argument("--attempt-id", required=True)
    heartbeat.add_argument("--fence", type=int, required=True)
    heartbeat.add_argument("--lease-seconds", default=60, type=float)
    return parser


def _input(path):
    with open(path, "rb") as handle:
        data = handle.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("input_too_large")
    value = json.loads(data, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("input_must_be_object")
    return value


def describe():
    return {
        "protocol_version": 1,
        "transport": "local CLI, one JSON result; no remote service",
        "commands": [
            "init",
            "authorization-add",
            "authorization",
            "authorization-revoke",
            "task-add",
            "task",
            "tasks",
            "handoff",
            "attempt",
            "attempts",
            "claim-manual",
            "heartbeat-manual",
            "finish-manual",
            "run",
            "events",
            "ack",
            "reconcile",
            "resolve-unknown",
            "resolution",
            "resolutions",
        ],
        "authorization_required": [
            "authorization_id",
            "source_kind=user_message",
            "source_ref",
            "original_text",
            "question_text",
            "allowed_actions",
            "allowed_resources",
            "restrictions",
        ],
        "task_required": [
            "task_id",
            "title",
            "owner",
            "kind=manual|render",
            "action",
            "resource",
            "authorization_id",
            "depends_on",
            "payload",
        ],
        "resolution_required": [
            "resolution_id",
            "task_id",
            "attempt_id",
            "expected_fence=current task fence, positive integer",
            "operator",
            "source_ref",
            "reason",
            "evidence_refs=nonempty list of strings",
            "decision=abandon",
        ],
        "trust": "Stored user-message provenance and local owner/worker/operator names "
        "are caller asserted, not authenticated. "
        "Verify the original source, current restrictions and runtime permission before claiming.",
        "notifications": "events is a durable local pull inbox. Polling is not delivery or ACK. "
        "ack records the named owner's explicit receipt, not review approval.",
        "consumer": "On startup, reconnect and each processing cycle, scan tasks --owner NAME "
        "--eligible even when events is empty. ACK and claim are separate transactions. "
        "Fetch handoff, verify original authorization and current restrictions, then claim. "
        "Owner filters select records, not authenticated access. A scan does not reserve work. "
        "Also scan owned tasks without --eligible to inspect blocked dependencies.",
        "manual_lease": "heartbeat-manual renews a running manual attempt using its attempt_id "
        "and fence before lease expiry. It rejects render tasks and stale attempts. "
        "run maintains render heartbeats; no automatic manual heartbeat is installed.",
        "recovery": "reconcile fences expired attempts as unknown, never retries. "
        "resolve-unknown accepts only audited abandonment of supported unknown render tasks, "
        "never success, retry or restored authority; original unknown attempts remain unchanged. "
        "Slot release requires a fenced unused launch gate, or an independently verified "
        "same-host changed boot identity. Operator assertions alone cannot release it. "
        "Manual, legacy and same-boot started unknowns remain blocked.",
        "review": "Task completion is not semantic acceptance, publication approval "
        "or release eligibility.",
        "exit_codes": {
            "0": "request succeeded",
            "1": "request rejected or render failed",
            "3": "render unknown",
        },
    }


def _dispatch(ledger, args):
    command = args.command
    if command == "authorization-add":
        return ledger.add_authorization(_input(args.input))
    if command == "authorization":
        return ledger.get_authorization(args.id)
    if command == "authorization-revoke":
        return ledger.revoke_authorization(args.id, args.source_ref)
    if command == "task-add":
        return ledger.create_task(_input(args.input))
    if command == "task":
        return ledger.get_task(args.id)
    if command == "tasks":
        return ledger.list_tasks(owner=args.owner, eligible_only=args.eligible)
    if command == "handoff":
        return ledger.get_handoff(args.id)
    if command == "attempt":
        return ledger.get_attempt(args.id)
    if command == "attempts":
        return ledger.list_attempts(args.task_id)
    if command == "resolve-unknown":
        return ledger.resolve_unknown(_input(args.input))
    if command == "resolution":
        return ledger.get_resolution(args.id)
    if command == "resolutions":
        return ledger.list_resolutions(args.task_id)
    if command == "events":
        return ledger.pending_events(args.owner)
    if command == "ack":
        return ledger.acknowledge(args.event_id, args.owner)
    if command == "reconcile":
        return ledger.reconcile()
    if command == "claim-manual":
        if ledger.get_task(args.task_id)["kind"] != "manual":
            raise LedgerError("Use run for render tasks")
        return ledger.claim(
            args.task_id, args.worker, args.authorization_revision, args.lease_seconds
        )
    if command == "finish-manual":
        attempt = ledger.get_attempt(args.attempt_id)
        if ledger.get_task(attempt["task_id"])["kind"] != "manual":
            raise LedgerError("Render outcomes require the runner")
        return ledger.finish(args.attempt_id, args.fence, args.status, _input(args.input))
    if command == "heartbeat-manual":
        attempt = ledger.get_attempt(args.attempt_id)
        if ledger.get_task(attempt["task_id"])["kind"] != "manual":
            raise LedgerError("Render heartbeats require the runner")
        return ledger.heartbeat(args.attempt_id, args.fence, args.lease_seconds)
    if command == "run":
        from .runner import run_task

        return run_task(
            ledger,
            args.task_id,
            args.worker,
            args.authorization_revision,
            lease_seconds=args.lease_seconds,
        )
    raise ValueError("invalid_command")


def main(argv=None):
    code = 0
    try:
        args = _parser().parse_args(argv)
        if args.command == "describe":
            result = describe()
        else:
            if not args.workflow:
                raise ValueError("workflow_required")
            opener = Ledger.create if args.command == "init" else Ledger
            with opener(args.workflow) as ledger:
                result = (
                    {"workflow": str(ledger.directory)}
                    if args.command == "init"
                    else _dispatch(ledger, args)
                )
        response = {"protocol_version": 1, "ok": True, "result": result}
        if args.command == "run":
            code = {"succeeded": 0, "failed": 1, "unknown": 3}[result["status"]]
            response["ok"] = code == 0
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
        # Paths, original text and exception strings may contain private data.
        response = {"protocol_version": 1, "ok": False, "error": "request_rejected"}
        code = 1
    print(json.dumps(response, ensure_ascii=True, allow_nan=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
