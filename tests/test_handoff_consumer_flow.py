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
    def test_dropped_poll_restart_duplicate_ack_and_late_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / "workflow"

            def call(*args, code=0):
                process = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scene_production.handoff",
                        "--workflow",
                        str(workflow),
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

            def data(name, value):
                path = root / name
                path.write_text(json.dumps(value))
                return str(path)

            call("init")
            call(
                "authorization-add",
                "--input",
                data(
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
            for task_id, owner, action, deps in (
                ("prepare", "producer", "prepare", []),
                ("inspect", "consumer", "inspect", ["prepare"]),
            ):
                call(
                    "task-add",
                    "--input",
                    data(
                        task_id + ".json",
                        {
                            "task_id": task_id,
                            "title": task_id,
                            "owner": owner,
                            "kind": "manual",
                            "action": action,
                            "resource": "fixture-v1",
                            "authorization_id": "synthetic-fixture",
                            "depends_on": deps,
                            "payload": {},
                        },
                    ),
                )
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
                any(
                    e["event_id"] == event_id
                    for e in call("events", "--owner", "consumer")["result"]
                )
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


if __name__ == "__main__":
    unittest.main()
