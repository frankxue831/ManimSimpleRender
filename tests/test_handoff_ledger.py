"""Durability, fencing and delivery contract tests."""

import multiprocessing
from pathlib import Path
import tempfile
import unittest

from scene_production.handoff import Ledger, LedgerError


def competing_claim(path, output):
    with Ledger(path) as ledger:
        try:
            ledger.claim("draft", "worker", 1)
            output.put("claimed")
        except LedgerError:
            output.put("blocked")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "workflow"
        self.ledger = Ledger.create(self.path)
        self.addCleanup(self.ledger.close)
        self.ledger.add_authorization(
            dict(
                authorization_id="auth",
                source_ref="message:1",
                original_text="Do it",
                question_text="",
                source_kind="user_message",
                allowed_actions=["draft"],
                allowed_resources=["scene"],
                restrictions=[],
            )
        )
        self.task("draft")

    def task(self, task_id, deps=None):
        return self.ledger.create_task(
            dict(
                task_id=task_id,
                title=task_id,
                owner="alice",
                action="draft",
                resource="scene",
                authorization_id="auth",
                kind="manual",
                depends_on=deps or [],
                payload={},
            )
        )

    def test_competing_process_claims(self):
        ctx = multiprocessing.get_context("spawn")
        output = ctx.Queue()
        workers = [ctx.Process(target=competing_claim, args=(self.path, output)) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(15)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(sorted(output.get(timeout=2) for _ in workers), ["blocked", "claimed"])

    def test_restart_and_ack(self):
        events = self.ledger.pending_events("alice")
        self.assertEqual(events, self.ledger.pending_events("alice"))
        with Ledger(self.path) as reopened:
            self.assertEqual(reopened.get_task("draft")["status"], "pending")
            event = reopened.acknowledge(events[0]["event_id"], "alice")
            self.assertEqual(event, reopened.acknowledge(event["event_id"], "alice"))
            self.assertNotIn(event, reopened.pending_events("alice"))

    def test_expired_fenced_never_retried(self):
        self.ledger.clock = lambda: 100
        attempt = self.ledger.claim("draft", "worker", 1, 1)
        self.ledger.clock = lambda: 102
        with self.assertRaises(LedgerError):
            self.ledger.finish(attempt["attempt_id"], attempt["fence"], "succeeded", {})
        self.assertEqual(len(self.ledger.reconcile()), 1)
        self.assertEqual(self.ledger.reconcile(), [])
        with self.assertRaises(LedgerError):
            self.ledger.claim("draft", "worker", 1)
        self.assertEqual(self.ledger.get_task("draft")["status"], "unknown")

    def test_dependency_and_revocation(self):
        self.task("review", ["draft"])
        self.assertFalse(self.ledger.get_task("review")["eligible"])
        attempt = self.ledger.claim("draft", "worker", 1)
        self.ledger.revoke_authorization("auth", "message:2")
        with self.assertRaises(LedgerError):
            self.ledger.finish(attempt["attempt_id"], attempt["fence"], "succeeded", {})
        self.assertFalse(self.ledger.get_task("review")["eligible"])

    def test_success_unlocks_but_does_not_complete_review(self):
        self.task("review", ["draft"])
        attempt = self.ledger.claim("draft", "worker", 1)
        self.ledger.finish(
            attempt["attempt_id"], attempt["fence"], "succeeded", {"artifacts": ["scene.py"]}
        )
        self.assertTrue(self.ledger.get_task("review")["eligible"])
        self.assertEqual(self.ledger.get_task("review")["status"], "pending")
        self.assertEqual(len(self.ledger.get_handoff("review")["predecessors"]), 1)

    def test_failed_parent_and_future_dependencies(self):
        with self.assertRaises(LedgerError):
            self.task("cycle", ["cycle"])
        self.task("review", ["draft"])
        attempt = self.ledger.claim("draft", "worker", 1)
        self.ledger.finish(attempt["attempt_id"], attempt["fence"], "failed", {})
        self.assertFalse(self.ledger.get_task("review")["eligible"])
        with self.assertRaises(LedgerError):
            self.ledger.claim("draft", "worker", 1)

    def test_ready_after_last_parent_once(self):
        self.task("second")
        self.task("review", ["draft", "second"])
        for index, parent in enumerate(("draft", "second")):
            attempt = self.ledger.claim(parent, "worker", 1)
            self.ledger.finish(attempt["attempt_id"], attempt["fence"], "succeeded", {})
            ready = [
                e
                for e in self.ledger.pending_events("alice")
                if e["type"] == "ready" and e["task_id"] == "review"
            ]
            self.assertEqual(len(ready), index)
            with self.assertRaises(LedgerError):
                self.ledger.finish(attempt["attempt_id"], attempt["fence"], "succeeded", {})
        self.assertEqual(ready[0]["task_id"], "review")
        self.assertEqual(ready[0]["authorization_revision"], 1)
        self.assertIn("created_at", ready[0])
        ack = self.ledger.acknowledge(ready[0]["event_id"], "alice")
        self.assertIsNotNone(ack["acknowledged_at"])

    def test_rollback_has_no_phantom_event(self):
        attempt = self.ledger.claim("draft", "worker", 1)
        before = self.ledger.pending_events("alice")
        original = self.ledger._event

        def failing_event(*args):
            original(*args)
            raise RuntimeError("simulated interruption")

        self.ledger._event = failing_event
        with self.assertRaises(RuntimeError):
            self.ledger.finish(attempt["attempt_id"], attempt["fence"], "succeeded", {})
        self.assertEqual(self.ledger.pending_events("alice"), before)
        self.assertEqual(self.ledger.get_task("draft")["status"], "running")

    def test_strict_inputs(self):
        for value in (None, [], True):
            with self.assertRaises(LedgerError):
                self.ledger.add_authorization(value)
            with self.assertRaises(LedgerError):
                self.ledger.create_task(value)
        with self.assertRaises(LedgerError):
            self.ledger.claim("draft", "worker", True)
        auth = self.ledger.get_authorization("auth")
        auth.update(authorization_id="agent-auth", source_kind="agent_message")
        with self.assertRaises(LedgerError):
            self.ledger.add_authorization(auth)

    def test_failed_render_releases_ready_notification(self):
        def render(name, deps):
            self.ledger.create_task(
                dict(
                    task_id=name,
                    title=name,
                    owner="renderer",
                    action="draft",
                    resource="scene",
                    authorization_id="auth",
                    kind="render",
                    depends_on=deps,
                    payload={},
                )
            )

        render("busy", [])
        busy = self.ledger.claim("busy", "worker", 1)
        render("next", ["draft"])
        draft = self.ledger.claim("draft", "worker", 1)
        self.ledger.finish(draft["attempt_id"], draft["fence"], "succeeded", {})

        def ready():
            return [
                e
                for e in self.ledger.pending_events("renderer")
                if e["task_id"] == "next" and e["type"] == "ready"
            ]

        self.assertEqual(ready(), [])
        self.ledger.finish(busy["attempt_id"], busy["fence"], "failed", {})
        self.assertEqual(len(ready()), 1)
        self.task("unrelated")
        self.assertEqual(len(ready()), 1)
        self.assertTrue(self.ledger.get_task("next")["eligible"])

    def test_initial_ready_once(self):
        ready = [
            e
            for e in self.ledger.pending_events("alice")
            if e["type"] == "ready" and e["task_id"] == "draft"
        ]
        self.assertEqual(len(ready), 1)
