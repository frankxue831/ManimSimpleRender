"""Bounded test-only consumer/contender; not an installed worker or daemon."""

import json
import os
from pathlib import Path
import sys
import time

from scene_production.handoff import Ledger
from scene_production.handoff.__main__ import main


def execute(mode, workflow, evidence):
    evidence = Path(evidence)
    if mode == "ack":
        # Controlled normal exit after ACK, before any claim. Drain created as
        # well as ready events: ACKing only ready does not empty the inbox.
        with Ledger(workflow) as ledger:
            events = ledger.pending_events("producer")
            for event in events:
                ledger.acknowledge(event["event_id"], "producer")
            result = {"acked": events, "attempts": ledger.list_attempts("render")}
        print(json.dumps(result))
        return 0
    if mode != "contend":
        raise ValueError("unsupported fixture mode")
    (evidence / "ready").write_text("ready\n", encoding="ascii")
    # Parent starts both interpreters before releasing this pipe barrier. This
    # synchronizes callers, not SQLite transactions, and adds no product hook.
    if sys.stdin.readline() != "go\n":
        raise RuntimeError("fixture barrier was not released")
    started = time.monotonic_ns()
    code = main(
        [
            "--workflow",
            workflow,
            "run",
            "--task-id",
            "render",
            "--worker",
            evidence.name,
            "--authorization-revision",
            "1",
        ]
    )
    (evidence / "invocation.json").write_text(
        json.dumps({"pid": os.getpid(), "start_ns": started, "end_ns": time.monotonic_ns()}),
        encoding="utf-8",
    )
    return code


if __name__ == "__main__":
    sys.exit(execute(*sys.argv[1:]))
