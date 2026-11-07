"""Audited abandonment never turns unknown execution into success or retry."""

import copy
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scene_production.handoff import Ledger, LedgerError

IDENTITY = dict(
    version=1, platform="darwin", host="a" * 64, boot="11111111-2222-4333-8444-555555555555"
)
REBOOT = dict(IDENTITY, boot="71db42cc-f47b-4266-96d6-ea01d6a3305c")
PROBE = "scene_production.handoff.execution_identity.local_execution_identity"


def resolve_race(path, request, queue):
    with Ledger(path) as ledger:
        try:
            queue.put(ledger.resolve_unknown(request)["resolution_id"])
        except LedgerError:
            queue.put("rejected")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "workflow"
        self.ledger = Ledger.create(self.path)
        self.addCleanup(self.ledger.close)
        self.probe = patch(PROBE, return_value=IDENTITY)
        self.identity = self.probe.start()
        self.addCleanup(self.probe.stop)
        self.ledger.add_authorization(
            dict(
                authorization_id="auth",
                source_kind="user_message",
                source_ref="test:1",
                original_text="Prepare local test",
                question_text="",
                allowed_actions=["render"],
                allowed_resources=["fixture"],
                restrictions=[],
            )
        )
        for name, deps in (("old", []), ("next", []), ("dependent", ["old"])):
            self.ledger.create_task(
                dict(
                    task_id=name,
                    title=name,
                    owner=name,
                    kind="render",
                    action="render",
                    resource="fixture",
                    authorization_id="auth",
                    depends_on=deps,
                    payload={},
                )
            )
        self.ledger.clock = lambda: 100

    def unknown(self, *, started=False, legacy=False, finished=False):
        claim = self.ledger.claim if legacy else self.ledger.claim_local_render
        attempt = claim("old", "worker", 1, lease_seconds=1)
        if started:
            self.ledger.begin_execution(attempt["attempt_id"], attempt["fence"])
        if finished:
            self.ledger.finish(attempt["attempt_id"], attempt["fence"], "unknown", {})
        else:
            self.ledger.clock = lambda: 102
            self.ledger.reconcile()
        self.attempt = self.ledger.get_attempt(attempt["attempt_id"])
        self.request = dict(
            resolution_id="audit-1",
            task_id="old",
            attempt_id=attempt["attempt_id"],
            expected_fence=self.ledger.get_task("old")["fence"],
            operator="test-operator",
            source_ref="test:review",
            reason="Retire unknown test without accepting its result",
            evidence_refs=["test:inspection"],
            decision="abandon",
        )
        return self.attempt

    def test_never_started_releases_slot_and_keeps_unknown_evidence(self):
        attempt = self.unknown()
        for event in self.ledger.pending_events("next"):
            self.ledger.acknowledge(event["event_id"], "next")
        result = self.ledger.resolve_unknown(self.request)
        self.assertEqual(result["proof"], {"kind": "fenced_before_spawn_intent"})
        self.assertEqual(self.ledger.get_attempt(attempt["attempt_id"]), attempt)
        self.assertEqual(self.ledger.get_task("old")["status"], "abandoned")
        self.assertTrue(self.ledger.get_task("next")["eligible"])
        self.assertFalse(self.ledger.get_task("dependent")["eligible"])
        self.assertEqual(len(self.ledger.pending_events("next")), 1)
        self.assertEqual(self.ledger.pending_events("next")[0]["type"], "ready")
        with Ledger(self.path) as reopened:
            self.assertEqual(reopened.get_resolution("audit-1"), result)
            self.assertEqual(reopened.resolve_unknown(self.request), result)
            self.assertEqual(reopened.list_resolutions("old"), [result])
            self.assertEqual(
                reopened.get_handoff("dependent")["predecessors"][0]["resolutions"], [result]
            )
        for operation in (self.ledger.begin_execution, self.ledger.heartbeat):
            with self.assertRaises(LedgerError):
                operation(attempt["attempt_id"], attempt["fence"])
        with self.assertRaises(LedgerError):
            self.ledger.finish(attempt["attempt_id"], attempt["fence"], "succeeded", {})
        with self.assertRaises(LedgerError):
            self.ledger.claim("old", "replacement", 1)

    def test_spawn_intent_is_blocked_even_with_claimed_exit_or_no_process_file(self):
        attempt = self.unknown(started=True)
        directory = self.path / "outputs" / attempt["attempt_id"]
        directory.mkdir(parents=True)
        (directory / "exit.json").write_text('{"exit_observed":true,"status":"succeeded"}')
        with self.assertRaises(LedgerError):
            self.ledger.resolve_unknown(self.request)
        self.assertFalse(self.ledger.get_task("next")["eligible"])
        self.assertEqual(self.ledger.list_resolutions(), [])

    def test_same_host_reboot_only_abandons_and_never_reactivates_authorization(self):
        self.unknown(started=True)
        self.ledger.revoke_authorization("auth", "test:revoked")
        self.identity.return_value = REBOOT
        result = self.ledger.resolve_unknown(self.request)
        self.assertEqual(result["proof"]["kind"], "same_host_reboot")
        self.assertEqual(self.ledger.get_attempt(self.attempt["attempt_id"]), self.attempt)
        self.assertEqual(result["authorization"]["status"], "revoked")
        self.assertFalse(self.ledger.get_task("next")["eligible"])
        self.assertFalse(self.ledger.get_task("dependent")["eligible"])

    def test_unsupported_legacy_and_foreign_execution_remain_blocked(self):
        self.unknown(legacy=True)
        self.identity.return_value = REBOOT
        with self.assertRaises(LedgerError):
            self.ledger.resolve_unknown(self.request)

    def test_identity_failure_same_boot_different_host_and_malformed_stay_blocked(self):
        self.unknown(started=True)
        for identity in (
            None,
            IDENTITY,
            dict(REBOOT, host="b" * 64),
            dict(REBOOT, version=True),
            dict(REBOOT, boot="bad"),
            dict(REBOOT, platform="linux"),
        ):
            with self.subTest(identity=identity):
                self.identity.return_value = identity
                with self.assertRaises(LedgerError):
                    self.ledger.resolve_unknown(self.request)
                self.assertEqual(self.ledger.get_task("old")["status"], "unknown")

    def test_launch_gate_refuses_replay_changed_boot_revoked_and_duplicate(self):
        attempt = self.ledger.claim_local_render("old", "worker", 1)
        self.identity.return_value = REBOOT
        with self.assertRaises(LedgerError):
            self.ledger.begin_execution(attempt["attempt_id"], attempt["fence"])
        self.identity.return_value = IDENTITY
        self.ledger.begin_execution(attempt["attempt_id"], attempt["fence"])
        with self.assertRaises(LedgerError):
            self.ledger.begin_execution(attempt["attempt_id"], attempt["fence"])

    def test_gate_denies_revocation_and_lease_expiry_during_identity_probe(self):
        attempt = self.ledger.claim_local_render("old", "worker", 1, lease_seconds=1)

        def delayed():
            self.ledger.clock = lambda: 102
            return IDENTITY

        self.identity.side_effect = delayed
        with self.assertRaises(LedgerError):
            self.ledger.begin_execution(attempt["attempt_id"], attempt["fence"])
        self.assertEqual(
            self.ledger.get_attempt(attempt["attempt_id"])["execution_guard"]["state"],
            "not_started",
        )
        self.ledger.clock = lambda: 100
        self.ledger.revoke_authorization("auth", "test:revoked")
        with self.assertRaises(LedgerError):
            self.ledger.begin_execution(attempt["attempt_id"], attempt["fence"])

    def test_running_attempt_or_invented_stop_assertion_never_resolves(self):
        attempt = self.unknown(finished=True)
        for update in (
            {"execution_stopped": True},
            {"decision": "succeeded"},
            {"expected_fence": True},
            {"expected_fence": 987},
            {"evidence_refs": []},
            {"operator": ""},
            {"task_id": "next"},
        ):
            with self.subTest(update=update), self.assertRaises(LedgerError):
                self.ledger.resolve_unknown(dict(self.request, **update))
        self.assertEqual(self.ledger.get_attempt(attempt["attempt_id"]), attempt)
        result = self.ledger.resolve_unknown(self.request)
        with self.assertRaises(LedgerError):
            self.ledger.resolve_unknown(dict(self.request, reason="different context"))
        self.assertEqual(self.ledger.list_resolutions(), [result])

    def test_atomic_rollback_and_competing_resolvers(self):
        self.unknown()
        original = self.ledger._event

        def fail(*args):
            original(*args)
            raise RuntimeError("simulated crash")

        with patch.object(self.ledger, "_event", side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.ledger.resolve_unknown(self.request)
        self.assertEqual(self.ledger.list_resolutions(), [])
        self.assertEqual(self.ledger.get_task("old")["status"], "unknown")
        self.assertFalse(self.ledger.get_task("next")["eligible"])
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        workers = [
            ctx.Process(target=resolve_race, args=(self.path, request, queue))
            for request in (self.request, dict(self.request, resolution_id="audit-2"))
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(15)
            self.assertEqual(worker.exitcode, 0)
        outcomes = [queue.get(timeout=2) for _ in workers]
        self.assertEqual(outcomes.count("rejected"), 1)
        self.assertEqual(len(self.ledger.list_resolutions()), 1)
        self.assertEqual(self.ledger.get_task("old")["fence"], self.request["expected_fence"] + 1)
        self.assertEqual(
            len([e for e in self.ledger.pending_events("old") if e["type"] == "abandoned"]), 1
        )

    def test_malformed_guard_and_manual_attempt_are_not_recoverable(self):
        self.unknown()
        original = copy.deepcopy(self.attempt)
        for guard in (
            None,
            dict(version=True, state="not_started", identity=None),
            dict(version=1, state="unexpected", identity=None),
        ):
            attempt = dict(original, execution_guard=guard)
            self.ledger._put("attempt", attempt["attempt_id"], attempt)
            self.ledger.db.commit()
            with self.assertRaises(LedgerError):
                self.ledger.resolve_unknown(self.request)
        task = self.ledger._get("task", "old")
        task["kind"] = "manual"
        self.ledger._put("task", "old", task)
        self.ledger._put("attempt", original["attempt_id"], original)
        self.ledger.db.commit()
        with self.assertRaises(LedgerError):
            self.ledger.resolve_unknown(self.request)

    def test_cli_restart_resolves_once_and_queries_immutable_audit(self):
        self.unknown()
        input_path = Path(self.tmp.name) / "resolution.json"
        input_path.write_text(json.dumps(self.request))

        def call(*args):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scene_production.handoff",
                    "--workflow",
                    str(self.path),
                    *args,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=Path(__file__).resolve().parents[1],
                shell=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)["result"]

        result = call("resolve-unknown", "--input", str(input_path))
        self.assertEqual(call("resolve-unknown", "--input", str(input_path)), result)
        self.assertEqual(call("resolution", "--id", "audit-1"), result)
        self.assertEqual(call("resolutions", "--task-id", "old"), [result])
        self.assertEqual(
            [t["task_id"] for t in call("tasks", "--owner", "next", "--eligible")], ["next"]
        )
        self.assertEqual(call("attempt", "--id", self.attempt["attempt_id"]), self.attempt)

    def test_running_not_started_claim_cannot_be_abandoned(self):
        attempt = self.ledger.claim_local_render("old", "worker", 1)
        request = dict(
            resolution_id="audit",
            task_id="old",
            attempt_id=attempt["attempt_id"],
            expected_fence=attempt["fence"],
            operator="operator",
            source_ref="test:1",
            reason="not terminal",
            evidence_refs=["test:1"],
            decision="abandon",
        )
        with self.assertRaises(LedgerError):
            self.ledger.resolve_unknown(request)
        self.assertEqual(self.ledger.get_task("old")["status"], "running")

    def test_stale_runner_cannot_launch_after_never_started_resolution(self):
        from scene_production.handoff.runner import run_task

        source = Path(self.tmp.name) / "scene.py"
        source.write_text("# trusted test source\n")
        task = self.ledger._get("task", "old")
        task["payload"] = dict(scene_file=str(source), timeline=str(source), scene="Demo")
        self.ledger._put("task", "old", task)
        self.ledger.db.commit()
        begin = self.ledger.begin_execution

        def race(attempt_id, fence):
            self.ledger.clock = lambda: 200
            self.ledger.reconcile()
            self.ledger.resolve_unknown(
                dict(
                    resolution_id="race",
                    task_id="old",
                    attempt_id=attempt_id,
                    expected_fence=self.ledger.get_task("old")["fence"],
                    operator="operator",
                    source_ref="test:race",
                    reason="Abandon before launch",
                    evidence_refs=["test:race"],
                    decision="abandon",
                )
            )
            return begin(attempt_id, fence)

        with (
            patch.object(self.ledger, "begin_execution", side_effect=race),
            patch("scene_production.handoff.runner.subprocess.Popen") as launch,
        ):
            with self.assertRaises(LedgerError):
                run_task(self.ledger, "old", "worker", 1)
            launch.assert_not_called()
        self.assertEqual(self.ledger.get_task("old")["status"], "abandoned")
        self.assertTrue(self.ledger.get_task("next")["eligible"])


if __name__ == "__main__":
    unittest.main()
