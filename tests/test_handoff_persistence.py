"""Slow evidence storage stays supervised without cross-thread SQLite access."""

import json
import threading
import time
import unittest
from unittest.mock import patch

from scene_production import agent
from tests import test_handoff_runner


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_handoff_runner.RunnerIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_slow_initial_process_evidence_keeps_lease(self):
        original = agent._atomic_write

        def delayed(path, value):
            if path.name == "process.json":
                time.sleep(0.3)
            return original(path, value)

        with patch.object(agent, "_atomic_write", side_effect=delayed):
            result = self.fixture.run_child("success", lease_seconds=0.2)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.fixture.ledger.get_task("task")["status"], "succeeded")

    def test_revocation_during_exit_write_never_records_success_or_competing_writer(self):
        original_write = agent._atomic_write
        original_heartbeat = self.fixture.ledger.heartbeat
        writing = threading.Event()
        exit_writes = []

        def delayed(path, value):
            if path.name == "exit.json":
                exit_writes.append(json.loads(value))
                writing.set()
                time.sleep(0.1)
            return original_write(path, value)

        def heartbeat(*args, **kwargs):
            if writing.is_set():
                self.fixture.ledger.revoke_authorization("auth", "test:revoked-during-write")
            return original_heartbeat(*args, **kwargs)

        with (
            patch.object(agent, "_atomic_write", side_effect=delayed),
            patch.object(self.fixture.ledger, "heartbeat", side_effect=heartbeat),
        ):
            result = self.fixture.run_child("success")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.fixture.ledger.get_task("task")["status"], "unknown")
        self.assertEqual(len(exit_writes), 1)
        # The single evidence file describes observed process completion. It is
        # neither permission nor an instruction to replace the fenced ledger.
        self.assertEqual(exit_writes[0]["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
