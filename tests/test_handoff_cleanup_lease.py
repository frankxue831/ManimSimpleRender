"""Simulated cleanup waits retain leases without bypassing authority changes."""

import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from scene_production.handoff import runner
from scene_production.handoff.ledger import LedgerError
from tests import test_handoff_runner


class CleanupLeaseTests(unittest.TestCase):
    def run_cleanup(self, change=None, never_exits=False):
        fixture = test_handoff_runner.RunnerIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        clock = [1000.0]
        fixture.ledger.clock = lambda: clock[0]
        renewals = []
        heartbeat = fixture.ledger.heartbeat
        signal_calls = []
        changed = []

        class Process:
            pid = 12345
            returncode = None
            stdout = io.BytesIO()
            stderr = io.BytesIO()

            def wait(self, timeout):
                clock[0] += timeout
                if change and not changed and clock[0] >= 1030:
                    changed.append(True)
                    if change == "revoke":
                        fixture.ledger.revoke_authorization("auth", "test:revoked")
                    else:
                        clock[0] += 61
                        fixture.ledger.reconcile()
                if not never_exits and clock[0] >= 1072:
                    self.returncode = -15
                    return self.returncode
                raise subprocess.TimeoutExpired("test", timeout)

            def poll(self):
                return self.returncode

        process = Process()
        persist = runner._persist_supervised

        def fail_process_evidence(path, *args):
            if path.name == "process.json":
                clock[0] += 19
                raise OSError("injected initial error")
            return persist(path, *args)

        def renew(*args, **kwargs):
            result = heartbeat(*args, **kwargs)
            renewals.append(clock[0])
            return result

        with (
            patch.object(runner.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(runner.subprocess, "Popen", return_value=process),
            patch.object(
                runner, "_signal_group", side_effect=lambda *a, **k: signal_calls.append(k)
            ),
            patch.object(runner, "_persist_supervised", side_effect=fail_process_evidence),
            patch.object(fixture.ledger, "heartbeat", side_effect=renew),
        ):
            if change == "fence":
                with self.assertRaises(LedgerError):
                    runner.run_task(fixture.ledger, "task", "worker", 1, poll_interval=1)
                result = None
            else:
                result = runner.run_task(fixture.ledger, "task", "worker", 1, poll_interval=1)
        outputs = list((Path(fixture.ledger.directory) / "outputs").iterdir())
        self.assertEqual(len(outputs), 1)
        self.assertEqual([call["force"] for call in signal_calls], [False, True])
        return fixture, result, outputs[0], renewals, clock[0]

    def test_slow_cleanup_renews_lease_and_preserves_exit_evidence(self):
        fixture, result, output, renewals, end = self.run_cleanup()
        self.assertGreaterEqual(end, 1072)
        self.assertGreater(len(renewals), 40)
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["exit_observed"])
        self.assertFalse(result["descendants_cleanup_verified"])
        self.assertTrue(json.loads((output / "exit.json").read_text())["interrupted"])
        self.assertEqual(fixture.ledger.reconcile(), [])
        self.assertEqual(fixture.ledger.get_task("task")["fence"], 1)

    def test_revocation_during_cleanup_does_not_renew_or_adopt_success(self):
        fixture, result, output, renewals, _ = self.run_cleanup("revoke")
        self.assertTrue(all(t < 1030 for t in renewals))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("evidence_error", result)
        self.assertFalse((output / "exit.json").exists())
        self.assertEqual(fixture.ledger.get_task("task")["status"], "unknown")

    def test_fencing_during_cleanup_rejects_stale_persistence_and_finish(self):
        fixture, _, output, renewals, _ = self.run_cleanup("fence")
        self.assertTrue(all(t < 1030 for t in renewals))
        self.assertFalse((output / "exit.json").exists())
        self.assertEqual(fixture.ledger.get_task("task")["fence"], 2)

    def test_cleanup_deadline_is_not_extended_by_renewal(self):
        _, result, output, _, end = self.run_cleanup(never_exits=True)
        self.assertEqual(end, 1019 + runner.DEFAULT_SUPERVISION_CLEANUP_TIMEOUT + 2)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["exit_observed"])
        self.assertTrue((output / "exit.json").exists())
