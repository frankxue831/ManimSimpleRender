"""Agent-facing CLI and manual dependency handoff without external messaging."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scene_production.handoff.__main__ import main
from scene_production.handoff.ledger import Ledger


class HandoffCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workflow = self.root / "workflow"
        self.call("init")

    def call(self, *args, expected=0):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--workflow", str(self.workflow), *args])
        self.assertEqual(code, expected, output.getvalue())
        return json.loads(output.getvalue())

    def input(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data))
        return str(path)

    def authorize(self):
        return self.call(
            "authorization-add",
            "--input",
            self.input(
                "authorization.json",
                {
                    "authorization_id": "approval-v1",
                    "source_kind": "user_message",
                    "source_ref": "example:user-message-2",
                    "original_text": "Yes, do the draft.",
                    "question_text": "May I prepare this local draft and review?",
                    "allowed_actions": ["draft", "review", "render"],
                    "allowed_resources": ["demo-v1"],
                    "restrictions": ["Do not publish."],
                },
            ),
        )

    def task(self, name, depends=(), kind="manual", owner=None):
        return self.call(
            "task-add",
            "--input",
            self.input(
                name + ".json",
                {
                    "task_id": name,
                    "title": name,
                    "owner": owner or name + "-owner",
                    "kind": kind,
                    "action": "draft" if kind == "manual" else "render",
                    "resource": "demo-v1",
                    "authorization_id": "approval-v1",
                    "depends_on": list(depends),
                    "payload": {},
                },
            ),
        )

    def test_manual_handoff_returns_original_question_and_upstream_result(self):
        self.authorize()
        self.task("draft")
        self.task("review", ["draft"])
        blocked = self.call("task", "--id", "review")["result"]
        self.assertFalse(blocked["eligible"])
        attempt = self.call(
            "claim-manual",
            "--task-id",
            "draft",
            "--worker",
            "writer",
            "--authorization-revision",
            "1",
        )["result"]
        self.call(
            "finish-manual",
            "--attempt-id",
            attempt["attempt_id"],
            "--fence",
            str(attempt["fence"]),
            "--status",
            "succeeded",
            "--input",
            self.input("result.json", {"note": "Draft prepared, not reviewed."}),
        )
        handoff = self.call("handoff", "--id", "review")["result"]
        self.assertTrue(handoff["task"]["eligible"])
        self.assertEqual(handoff["authorization"]["source_ref"], "example:user-message-2")
        self.assertIn("May I", handoff["authorization"]["question_text"])
        self.assertEqual(
            handoff["predecessors"][0]["attempts"][0]["result"]["note"],
            "Draft prepared, not reviewed.",
        )
        current = self.call("handoff", "--id", "draft")["result"]["attempts"][0]
        self.assertEqual(current["attempt_id"], attempt["attempt_id"])
        self.assertEqual(self.call("attempt", "--id", attempt["attempt_id"])["result"], current)
        self.assertEqual(self.call("attempts", "--task-id", "draft")["result"], [current])

    def test_invalid_input_does_not_echo_private_values(self):
        path = self.root / "private.json"
        path.write_text('{"secret":"never-echo", "secret":"other"}')
        result = self.call("authorization-add", "--input", str(path), expected=1)
        self.assertEqual(result["error"], "request_rejected")
        self.assertNotIn("never-echo", str(result))
        self.call("init", expected=1)

    def test_task_scan_filters_owner_and_current_eligibility(self):
        self.authorize()
        self.task("draft", owner="writer")
        self.task("later", ["draft"], owner="writer")
        self.task("other", owner="another-writer")
        self.assertEqual(len(self.call("tasks")["result"]), 3)
        owned = self.call("tasks", "--owner", "writer")["result"]
        self.assertEqual([task["task_id"] for task in owned], ["draft", "later"])
        eligible = self.call("tasks", "--eligible")["result"]
        self.assertEqual([task["task_id"] for task in eligible], ["draft", "other"])
        candidates = self.call("tasks", "--owner", "writer", "--eligible")["result"]
        self.assertEqual([task["task_id"] for task in candidates], ["draft"])
        self.assertTrue(candidates[0]["eligible"])
        self.assertEqual(self.call("tasks", "--owner", "missing")["result"], [])
        self.call(
            "claim-manual",
            "--task-id",
            "draft",
            "--worker",
            "writer",
            "--authorization-revision",
            "1",
        )
        self.assertEqual(self.call("tasks", "--owner", "writer", "--eligible")["result"], [])
        self.assertEqual(len(self.call("tasks", "--owner", "writer")["result"]), 2)

    def test_manual_heartbeat_renews_lease_and_rejects_stale_or_expired_fence(self):
        # Inject only time; every command still opens and mutates the real ledger.
        now = [100]
        with patch(
            "scene_production.handoff.__main__.Ledger",
            side_effect=lambda path: Ledger(path, clock=lambda: now[0]),
        ):
            self.authorize()
            self.task("draft")
            attempt = self.call(
                "claim-manual",
                "--task-id",
                "draft",
                "--worker",
                "writer",
                "--authorization-revision",
                "1",
                "--lease-seconds",
                "20",
            )["result"]
            args = ("--attempt-id", attempt["attempt_id"], "--fence", str(attempt["fence"]))
            now[0] = 110
            renewed = self.call("heartbeat-manual", *args)["result"]
            self.assertEqual(renewed["lease_until"], 170)
            now[0] = 120
            renewed = self.call("heartbeat-manual", *args, "--lease-seconds", "90")["result"]
            self.assertEqual(renewed["lease_until"], 210)
            self.assertEqual(self.call("attempt", "--id", attempt["attempt_id"])["result"], renewed)
            self.call(
                "heartbeat-manual",
                "--attempt-id",
                attempt["attempt_id"],
                "--fence",
                str(attempt["fence"] + 1),
                expected=1,
            )
            self.assertEqual(self.call("attempt", "--id", attempt["attempt_id"])["result"], renewed)
            now[0] = 210
            self.call("heartbeat-manual", *args, expected=1)
            self.call("reconcile")
            self.call("heartbeat-manual", *args, expected=1)
            self.assertEqual(
                self.call("attempt", "--id", attempt["attempt_id"])["result"]["status"], "unknown"
            )

    def test_manual_heartbeat_rejects_invalid_duration_and_revocation(self):
        self.authorize()
        self.task("draft")
        attempt = self.call(
            "claim-manual",
            "--task-id",
            "draft",
            "--worker",
            "writer",
            "--authorization-revision",
            "1",
        )["result"]
        args = ("--attempt-id", attempt["attempt_id"], "--fence", str(attempt["fence"]))
        for duration in ("0", "-1", "nan", "inf"):
            with self.subTest(duration=duration):
                self.call("heartbeat-manual", *args, "--lease-seconds", duration, expected=1)
        self.assertEqual(self.call("attempt", "--id", attempt["attempt_id"])["result"], attempt)
        self.call("authorization-revoke", "--id", "approval-v1", "--source-ref", "test:user-3")
        self.call("heartbeat-manual", *args, expected=1)
        self.assertEqual(self.call("attempt", "--id", attempt["attempt_id"])["result"], attempt)

    def test_recovery_queries_and_rejected_input_preserve_generic_errors(self):
        self.assertEqual(self.call("resolutions")["result"], [])
        self.assertEqual(self.call("resolutions", "--task-id", "missing")["result"], [])
        rejected = {"protocol_version": 1, "ok": False, "error": "request_rejected"}
        response = self.call(
            "resolve-unknown",
            "--input",
            self.input("private-recovery.json", {"source_ref": "never-echo-private-source"}),
            expected=1,
        )
        self.assertEqual(response, rejected)
        duplicate = self.root / "duplicate-recovery.json"
        duplicate.write_text('{"reason":"never-echo-private-reason","reason":"other"}')
        self.assertEqual(
            self.call("resolve-unknown", "--input", str(duplicate), expected=1), rejected
        )
        self.assertEqual(
            self.call("resolution", "--id", "never-echo-private-id", expected=1), rejected
        )
        self.assertEqual(self.call("resolutions")["result"], [])

    def test_run_exit_code_preserves_failed_or_unknown_outcome(self):
        for status, code in (("succeeded", 0), ("failed", 1), ("unknown", 3)):
            with (
                self.subTest(status=status),
                patch("scene_production.handoff.runner.run_task", return_value={"status": status}),
            ):
                response = self.call(
                    "run",
                    "--task-id",
                    "render",
                    "--worker",
                    "worker",
                    "--authorization-revision",
                    "1",
                    expected=code,
                )
                self.assertEqual(response["result"]["status"], status)
                self.assertEqual(response["ok"], status == "succeeded")

    def test_cli_cannot_manually_claim_heartbeat_or_finish_render(self):
        self.authorize()
        self.task("render", kind="render")
        self.call(
            "claim-manual",
            "--task-id",
            "render",
            "--worker",
            "writer",
            "--authorization-revision",
            "1",
            expected=1,
        )
        with Ledger(self.workflow) as ledger:
            attempt = ledger.claim("render", "worker", 1)
        self.call(
            "heartbeat-manual",
            "--attempt-id",
            attempt["attempt_id"],
            "--fence",
            str(attempt["fence"]),
            expected=1,
        )
        self.assertEqual(self.call("attempt", "--id", attempt["attempt_id"])["result"], attempt)
        self.call(
            "finish-manual",
            "--attempt-id",
            attempt["attempt_id"],
            "--fence",
            "1",
            "--status",
            "succeeded",
            "--input",
            self.input("fake.json", {"claim": "fake success"}),
            expected=1,
        )

    def test_revocation_and_pending_ack_survive_open_per_command(self):
        self.authorize()
        self.task("draft")
        events = self.call("events", "--owner", "draft-owner")["result"]
        self.assertEqual(events, self.call("events", "--owner", "draft-owner")["result"])
        event_id = events[0]["event_id"]
        self.call("ack", "--event-id", event_id, "--owner", "wrong", expected=1)
        self.call("ack", "--event-id", event_id, "--owner", "draft-owner")
        self.call("ack", "--event-id", event_id, "--owner", "draft-owner")
        self.call(
            "authorization-revoke", "--id", "approval-v1", "--source-ref", "example:user-message-3"
        )
        self.assertFalse(self.call("task", "--id", "draft")["result"]["eligible"])
        self.assertEqual(
            self.call("authorization", "--id", "approval-v1")["result"]["revocation_source_ref"],
            "example:user-message-3",
        )


if __name__ == "__main__":
    unittest.main()
