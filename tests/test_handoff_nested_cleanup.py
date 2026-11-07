"""The outer wrapper must allow the existing render core to stop its session."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from scene_production.handoff import runner
from tests import test_handoff_runner


@unittest.skipUnless(hasattr(os, "killpg"), "POSIX render-session regression")
class NestedCleanupTests(unittest.TestCase):
    def test_revocation_allows_inner_render_runner_to_force_its_group(self):
        fixture = test_handoff_runner.RunnerIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        real_popen = subprocess.Popen
        marker = None
        inner_pid = None
        script = r"""
import sys, threading
from pathlib import Path
from scene_production import agent
from app.render.runner import RenderRunner
from app.render.jobs import RenderJob
def api(args, attempt):
    child = "import os,sys,signal,time; from pathlib import Path; " \
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); " \
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(10)"
    RenderRunner(terminate_timeout=1).run_job(
        RenderJob('fixture', 'Test'),
        [sys.executable, '-c', child, str(attempt.parent / 'inner.pid')],
        threading.Event(), lambda line: None)
    raise RuntimeError('fixture should be interrupted')
agent._call_api = api
sys.exit(agent.main(sys.argv[1:]))
"""

        def launch(args, **kwargs):
            nonlocal marker
            marker = Path(args[args.index("--output-dir") + 1]).parent / "inner.pid"
            return real_popen([sys.executable, "-c", script, *args[3:]], **kwargs)

        original_heartbeat = fixture.ledger.heartbeat

        def revoke(*args, **kwargs):
            nonlocal inner_pid
            if marker is not None and inner_pid is None:
                deadline = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertTrue(marker.exists(), "inner render must have installed TERM handler")
                inner_pid = int(marker.read_text())
                fixture.ledger.revoke_authorization("auth", "test:revoke-nested-render")
            return original_heartbeat(*args, **kwargs)

        try:
            with (
                patch.object(runner.subprocess, "Popen", side_effect=launch),
                patch.object(fixture.ledger, "heartbeat", side_effect=revoke),
            ):
                result = runner.run_task(fixture.ledger, "task", "worker", 1)
            self.assertEqual(result["status"], "unknown")
            self.assertIsNotNone(inner_pid)
            with self.assertRaises(ProcessLookupError):
                os.kill(inner_pid, 0)
            self.assertFalse(result.get("descendants_cleanup_verified", False))
        finally:
            if inner_pid is not None:
                try:
                    os.killpg(inner_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    unittest.main()
