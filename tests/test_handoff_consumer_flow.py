"""No-send consumer contract through independent CLI subprocesses.

This simulates a caller's shell boundary, not the actual Grok Bot service.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConsumerFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workflow = self.root / "workflow"
        self.call("init")
        self.call(
            "authorization-add",
            "--input",
            self.data(
                "authorization.json",
                {
                    "authorization_id": "synthetic-fixture",
                    "source_kind": "user_message",
                    "source_ref": "test-only:user-2",
                    "original_text": "Yes, prepare a local draft.",
                    "question_text": "May I prepare and inspect this local test fixture?",
                    "allowed_actions": ["prepare", "inspect"],
                    "allowed_resources": ["fixture-v1"],
                    "restrictions": ["Never send externally."],
                },
            ),
        )

    def call(self, *args, code=0):
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "scene_production.handoff",
                "--workflow",
                str(self.workflow),
                *args,
            ],
            cwd=ROOT,
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        self.assertEqual(process.stderr, "")
        return json.loads(process.stdout)

    def data(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        return str(path)

    def task(self, task_id, owner, deps=(), action="prepare"):
        return self.call(
            "task-add",
            "--input",
            self.data(
                task_id + ".json",
                {
                    "task_id": task_id,
                    "title": task_id,
                    "owner": owner,
                    "kind": "manual",
                    "action": action,
                    "resource": "fixture-v1",
                    "authorization_id": "synthetic-fixture",
                    "depends_on": list(deps),
                    "payload": {},
                },
            ),
        )["result"]

    def test_dropped_poll_restart_duplicate_ack_and_late_revocation(self):
        call, data = self.call, self.data
        self.task("prepare", "producer")
        self.task("inspect", "consumer", ["prepare"], action="inspect")
        claimed = call(
            "claim-manual",
            "--task-id",
            "prepare",
            "--worker",
            "producer",
            "--authorization-revision",
            "1",
        )["result"]
        call(
            "finish-manual",
            "--attempt-id",
            claimed["attempt_id"],
            "--fence",
            str(claimed["fence"]),
            "--status",
            "succeeded",
            "--input",
            data("result.json", {"artifact_ref": "test-only:fixture-v1", "reviewed": False}),
        )

        # Losing a poll result does not mark delivery or discard an event.
        first = call("events", "--owner", "consumer")["result"]
        second = call("events", "--owner", "consumer")["result"]
        self.assertEqual(first, second)
        ready = [event for event in second if event["type"] == "ready"]
        self.assertEqual(len(ready), 1)
        event_id = ready[0]["event_id"]

        # A fresh interpreter recovers original question and upstream result.
        handoff = call("handoff", "--id", "inspect")["result"]
        self.assertEqual(handoff["authorization"]["source_ref"], "test-only:user-2")
        self.assertIn("May I", handoff["authorization"]["question_text"])
        self.assertEqual(
            handoff["predecessors"][0]["attempts"][0]["result"],
            {"artifact_ref": "test-only:fixture-v1", "reviewed": False},
        )
        call("ack", "--event-id", event_id, "--owner", "wrong-consumer", code=1)
        accepted = call("ack", "--event-id", event_id, "--owner", "consumer")["result"]
        repeated = call("ack", "--event-id", event_id, "--owner", "consumer")["result"]
        self.assertEqual(accepted, repeated)
        self.assertFalse(
            any(e["event_id"] == event_id for e in call("events", "--owner", "consumer")["result"])
        )

        # Neither the cached ready event nor ACK confers execution authority.
        call(
            "authorization-revoke",
            "--id",
            "synthetic-fixture",
            "--source-ref",
            "test-only:user-3",
        )
        call(
            "claim-manual",
            "--task-id",
            "inspect",
            "--worker",
            "consumer",
            "--authorization-revision",
            "1",
            code=1,
        )
        self.assertEqual(call("attempts", "--task-id", "inspect")["result"], [])

    def test_ack_then_crash_recovers_from_owned_task_scan_without_duplicate_claim(self):
        self.task("work", "consumer")
        self.task("later", "consumer", ["work"])
        self.task("unrelated", "other-consumer")
        inbox = self.call("events", "--owner", "consumer")["result"]
        self.assertTrue(any(e["type"] == "ready" and e["task_id"] == "work" for e in inbox))
        for event in inbox:
            self.assertEqual(event["owner"], "consumer")
            self.call("ack", "--event-id", event["event_id"], "--owner", "consumer")

        # The ACK process exits before any claim. Restart cannot rely on its inbox.
        self.assertEqual(self.call("events", "--owner", "consumer")["result"], [])
        candidates = self.call("tasks", "--owner", "consumer", "--eligible")["result"]
        self.assertEqual([task["task_id"] for task in candidates], ["work"])
        self.assertEqual(self.call("events", "--owner", "consumer")["result"], [])
        owned = self.call("tasks", "--owner", "consumer")["result"]
        self.assertEqual([task["task_id"] for task in owned], ["work", "later"])
        self.assertEqual(owned[1]["blocked_reason"], "dependency_blocked")
        other = self.call("tasks", "--owner", "other-consumer", "--eligible")["result"]
        self.assertEqual([task["task_id"] for task in other], ["unrelated"])
        handoff = self.call("handoff", "--id", candidates[0]["task_id"])["result"]
        self.assertEqual(handoff["authorization"]["source_ref"], "test-only:user-2")

        args = ("--task-id", "work", "--worker", "consumer", "--authorization-revision", "1")
        claimed = self.call("claim-manual", *args)["result"]
        # Reprocessing a cached scan cannot claim or execute the same task again.
        self.call("claim-manual", *args, code=1)
        self.assertEqual(self.call("attempts", "--task-id", "work")["result"], [claimed])
        self.assertEqual(self.call("tasks", "--owner", "consumer", "--eligible")["result"], [])
        self.call(
            "finish-manual",
            "--attempt-id",
            claimed["attempt_id"],
            "--fence",
            str(claimed["fence"]),
            "--status",
            "succeeded",
            "--input",
            self.data("result.json", {"note": "Test-only local completion"}),
        )
        next_cycle = self.call("tasks", "--owner", "consumer", "--eligible")["result"]
        self.assertEqual([task["task_id"] for task in next_cycle], ["later"])
        self.call("claim-manual", *args, code=1)
        self.assertEqual(len(self.call("attempts", "--task-id", "work")["result"]), 1)

    def test_manual_heartbeat_persists_between_interpreters_and_rejects_terminal_attempt(self):
        self.task("work", "consumer")
        claimed = self.call(
            "claim-manual",
            "--task-id",
            "work",
            "--worker",
            "consumer",
            "--authorization-revision",
            "1",
            "--lease-seconds",
            "60",
        )["result"]
        args = ("--attempt-id", claimed["attempt_id"], "--fence", str(claimed["fence"]))
        renewed = self.call("heartbeat-manual", *args, "--lease-seconds", "120")["result"]
        self.assertGreater(renewed["lease_until"], claimed["lease_until"])
        self.assertEqual(self.call("attempt", "--id", claimed["attempt_id"])["result"], renewed)
        self.call(
            "heartbeat-manual",
            "--attempt-id",
            claimed["attempt_id"],
            "--fence",
            str(claimed["fence"] + 1),
            code=1,
        )
        self.assertEqual(self.call("attempt", "--id", claimed["attempt_id"])["result"], renewed)
        finished = self.call(
            "finish-manual",
            *args,
            "--status",
            "succeeded",
            "--input",
            self.data("result.json", {"note": "Test-only local completion"}),
        )["result"]
        self.call("heartbeat-manual", *args, code=1)
        self.assertEqual(self.call("attempt", "--id", claimed["attempt_id"])["result"], finished)

    def test_bad_recovery_input_returns_only_generic_error_across_process_boundary(self):
        rejected = self.call(
            "resolve-unknown",
            "--input",
            self.data("private-recovery.json", {"reason": "never-echo-private-reason"}),
            code=1,
        )
        self.assertEqual(
            rejected, {"protocol_version": 1, "ok": False, "error": "request_rejected"}
        )
        self.assertEqual(self.call("resolutions")["result"], [])


if __name__ == "__main__":
    unittest.main()
