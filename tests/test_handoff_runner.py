"""Fail-closed transport validation without Qt or Manim."""

import json
from pathlib import Path
import tempfile
import unittest

from scene_production.handoff.runner import verify_result, reconcile_evidence, _arguments


class RunnerProtocolTests(unittest.TestCase):
    def test_failed_terminal_requires_matching_durable_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = [
                dict(
                    protocol_version=1,
                    event="started",
                    status="running",
                    run_token="token",
                    operation="render",
                    attempt_dir=str(root),
                ),
                dict(
                    protocol_version=1,
                    event="terminal",
                    status="failed",
                    exit_code=1,
                    run_token="token",
                    operation="render",
                ),
            ]
            data = ("\n".join(json.dumps(e) for e in events) + "\n").encode()
            (root / "events.jsonl").write_bytes(data)
            (root / "attempt.json").write_text(json.dumps(events[1]))
            self.assertEqual(verify_result(root, data, 1), "failed")
            for invalid in (data[:-1], data + data, data.replace(b'"token"', b'"other"', 1)):
                with self.assertRaises(ValueError):
                    verify_result(root, invalid, 1)
            with self.assertRaises(ValueError):
                verify_result(root, data, 0)

    def test_stale_files_without_exit_evidence_are_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(reconcile_evidence(tmp), {"status": "unknown"})

    def test_no_arbitrary_execution_parameters(self):
        with self.assertRaises(ValueError):
            _arguments({"executable": "/bin/sh"}, Path("unused"))


class RunnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from scene_production.handoff.ledger import Ledger

        # Identity probing has its own tests. Import before mocking Popen so a
        # lazy import cannot retain this suite's fake subprocess launcher.
        identity = patch(
            "scene_production.handoff.execution_identity.local_execution_identity",
            return_value=None,
        )
        identity.start()
        self.addCleanup(identity.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.ledger = Ledger.create(self.root / "ledger")
        self.ledger.add_authorization(
            dict(
                authorization_id="auth",
                source_kind="user_message",
                question_text="Render?",
                source_ref="message:1",
                original_text="yes",
                allowed_actions=["render"],
                allowed_resources=["scene"],
                restrictions=[],
            )
        )
        self.source = self.root / "source.py"
        self.source.write_text("# trusted test\n")
        self.create_task("task")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def create_task(self, name, **extra):
        self.ledger.create_task(
            dict(
                task_id=name,
                title="Test",
                owner="owner",
                action="render",
                resource="scene",
                authorization_id="auth",
                kind="render",
                payload=dict(scene_file=str(self.source), scene="Test", timeline=str(self.source)),
                **extra,
            )
        )

    def run_child(self, mode, **options):
        import subprocess
        import sys
        from unittest.mock import patch
        from scene_production.handoff.runner import run_task

        real_popen = subprocess.Popen
        script = r"""
import sys, json
from pathlib import Path
from scene_production import agent, digest
root = Path(sys.argv[1]); mode = sys.argv[2]
root.mkdir()
if mode == 'overflow':
    print('x' * (3 * 1024 * 1024)); sys.exit(0)
if mode == 'sleep':
    import time
    time.sleep(30)
started = dict(protocol_version=1, event='started', status='running',
               run_token='abc', operation='render', attempt_dir=str(root))
terminal = dict(protocol_version=1, event='terminal', status='failed',
                exit_code=1, run_token='abc', operation='render',
                error={'code': 'operation_failed', 'private': 'do-not-copy'},
                diagnostics={'stage': 'decode', 'private': 'do-not-copy',
                             'failure_reason': 'do-not-copy-private-reason'})
if mode in ('render_timeout', 'render_cleanup_timeout'):
    terminal['diagnostics'].update(stage='render', failure_reason=mode)
if mode in ('success', 'tamper'):
    run = root / 'production' / 'scene-test'; run.mkdir(parents=True)
    for name in ('source.py', 'timeline.json', 'video.mp4', 'frame.png'):
        (run / name).write_bytes(b'test artifact')
    receipt = dict(status='success', run_dir=str(run), video=str(run/'video.mp4'),
                   frames=[dict(path=str(run/'frame.png'), sha256=digest(run/'frame.png'))])
    for name, file in [('source','source.py'), ('timeline','timeline.json'), ('video','video.mp4')]:
        receipt[name+'_sha256'] = digest(run/file)
    (run/'result.json').write_text(json.dumps(receipt))
    terminal.update(status='succeeded', exit_code=0,
                    artifacts=agent._verified_result(receipt, root, 'render'))
    if mode == 'tamper': (run/'video.mp4').write_bytes(b'changed')
if mode == 'mismatch': terminal['run_token'] = 'foreign'
events = [started, terminal]
data = ''.join(json.dumps(e)+'\n' for e in events)
(root/'attempt.json').write_text(json.dumps(terminal))
(root/'events.jsonl').write_text(data)
if mode == 'truncated': data = data[:-10]
if mode == 'intermediate': data = json.dumps(dict(status='failed'))+'\n'
sys.stdout.write(data)
sys.exit(0 if mode == 'wrong_exit' else terminal['exit_code'])
"""

        def launch(args, **kwargs):
            self.assertEqual(args[:4], [sys.executable, "-m", "scene_production.agent", "render"])
            self.assertFalse(kwargs["shell"])
            self.assertTrue(kwargs["start_new_session"])
            output = args[args.index("--output-dir") + 1]
            return real_popen([sys.executable, "-c", script, output, mode], **kwargs)

        with patch("scene_production.handoff.runner.subprocess.Popen", side_effect=launch):
            return run_task(self.ledger, "task", "worker", 1, poll_interval=0.005, **options)

    def test_success_and_recovery(self):
        self.assertEqual(self.run_child("success")["status"], "succeeded")
        directory = next((self.ledger.directory / "outputs").iterdir())
        self.assertEqual(reconcile_evidence(directory)["status"], "succeeded")
        (directory / "exit.json").unlink()
        self.assertEqual(reconcile_evidence(directory)["status"], "unknown")

    def test_failed_exit(self):
        result = self.run_child("failed")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["agent_error_code"], "operation_failed")
        self.assertEqual(result["agent_stage"], "decode")
        self.assertNotIn("do-not-copy", json.dumps(result))
        self.assertNotIn("agent_failure_reason", result)

    def test_failed_render_timeout_preserves_allowlisted_reason(self):
        result = self.run_child("render_timeout")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["agent_stage"], "render")
        self.assertEqual(result["agent_failure_reason"], "render_timeout")
        self.assertNotIn("do-not-copy", json.dumps(result))

    def test_failed_cleanup_timeout_preserves_failure_without_adopting_success(self):
        result = self.run_child("render_cleanup_timeout")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["agent_failure_reason"], "render_cleanup_timeout")
        self.assertEqual(result["agent_stage"], "render")
        self.assertNotIn("artifacts", result)
        self.assertNotIn("do-not-copy", json.dumps(result))

    def test_invalid_terminal_and_changed_artifacts(self):
        for mode in ("tamper", "mismatch", "truncated", "intermediate", "wrong_exit", "overflow"):
            with self.subTest(mode=mode):
                if self.ledger.get_task("task")["status"] != "pending":
                    self.tearDown()
                    self.setUp()
                self.assertEqual(self.run_child(mode)["status"], "unknown")
                for log in (self.ledger.directory / "outputs").glob("*/*.log"):
                    self.assertLessEqual(log.stat().st_size, 2 * 1024 * 1024)

    def test_timeline_correlates_phases_without_private_payload(self):
        import os

        result = self.run_child("success")
        path = Path(result["diagnostics_log"])
        records = [json.loads(line) for line in path.read_text().splitlines()]
        phases = [item["phase"] for item in records if item["event"] == "phase_finished"]
        self.assertEqual(
            phases,
            [
                "prepare",
                "launch",
                "process_evidence",
                "agent_wait",
                "drain",
                "verification",
                "exit_evidence",
                "ledger_finish",
            ],
        )
        self.assertEqual(records[0]["event"], "claim_finished")
        self.assertEqual(records[-1]["event"], "attempt_finished")
        self.assertEqual(records[-1]["status"], "succeeded")
        for record in records:
            self.assertEqual(record["task_id"], "task")
            self.assertEqual(record["attempt_id"], result["attempt_id"])
            self.assertEqual(record["fence"], result["fence"])
            self.assertTrue(record["timestamp"].endswith("Z"))
            self.assertGreaterEqual(record["elapsed_seconds"], 0)
            self.assertGreaterEqual(record.get("duration_seconds", 0), 0)
        elapsed = [record["elapsed_seconds"] for record in records]
        self.assertEqual(elapsed, sorted(elapsed))
        self.assertNotIn(str(self.source), path.read_text())
        self.assertNotIn("original_text", path.read_text())
        self.assertNotIn("do-not-copy", path.read_text())
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_protocol_has_safe_failure_reason_and_phase(self):
        result = self.run_child("mismatch")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["diagnostic_phase"], "verification")
        self.assertEqual(result["diagnostic_code"], "protocol_mismatch")
        self.assertEqual(result["exception_type"], "ValueError")
        records = [
            json.loads(line) for line in Path(result["diagnostics_log"]).read_text().splitlines()
        ]
        failed = [item for item in records if item["event"] == "phase_failed"]
        self.assertEqual(failed[0]["phase"], "verification")
        self.assertEqual(failed[0]["exception_type"], "ValueError")

    def test_timeline_failure_does_not_adopt_or_discard_render_result(self):
        from unittest.mock import patch
        from app.render.diagnostics import DiagnosticTimeline

        def unavailable(path, **kwargs):
            return DiagnosticTimeline(path / "missing", **kwargs)

        with patch("scene_production.handoff.runner.DiagnosticTimeline", side_effect=unavailable):
            result = self.run_child("success")
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["diagnostics_incomplete"])

    def test_no_launch_blocked_claims(self):
        from unittest.mock import patch
        from scene_production.handoff.ledger import LedgerError
        from scene_production.handoff.runner import run_task

        with patch("scene_production.handoff.runner.subprocess.Popen") as launch:
            with self.assertRaises(LedgerError):
                run_task(self.ledger, "task", "worker", 2)
            self.create_task("dependent", depends_on=["task"])
            with self.assertRaises(LedgerError):
                run_task(self.ledger, "dependent", "worker", 1)
            self.ledger.claim("task", "other", 1)
            self.create_task("second")
            for name in ("task", "second"):
                with self.assertRaises(LedgerError):
                    run_task(self.ledger, name, "worker", 1)
            self.ledger.revoke_authorization("auth", "message:2")
            with self.assertRaises(LedgerError):
                run_task(self.ledger, "second", "worker", 1)
            launch.assert_not_called()

    def test_spawn_failure(self):
        from unittest.mock import patch
        from scene_production.handoff.runner import run_task

        with patch(
            "scene_production.handoff.runner.subprocess.Popen", side_effect=OSError("secret")
        ):
            result = run_task(self.ledger, "task", "worker", 1)
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["diagnostic_phase"], "launch")
        self.assertEqual(result["diagnostic_code"], "launch_failed")
        self.assertEqual(result["exception_type"], "OSError")
        self.assertNotIn("secret", Path(result["diagnostics_log"]).read_text())

    def test_revocation_stops_live_child(self):
        from unittest.mock import patch

        heartbeat = self.ledger.heartbeat
        calls = []

        def revoke(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                self.ledger.revoke_authorization("auth", "message:2")
            return heartbeat(*args, **kwargs)

        with patch.object(self.ledger, "heartbeat", side_effect=revoke):
            result = self.run_child("sleep")
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["exit_observed"])
        self.assertEqual(result["diagnostic_phase"], "process_evidence")
        self.assertEqual(result["evidence_error"]["diagnostic_phase"], "exit_evidence")

    def test_persistence_failure_is_unknown(self):
        from unittest.mock import patch
        from scene_production import agent

        original = agent._atomic_write

        def fail_exit(path, value):
            if path.name == "exit.json":
                raise OSError("private detail")
            return original(path, value)

        with patch.object(agent, "_atomic_write", side_effect=fail_exit):
            result = self.run_child("success")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["diagnostic_phase"], "exit_evidence")
        self.assertEqual(result["diagnostic_code"], "exit_evidence_failed")
        self.assertNotIn("private detail", json.dumps(result))

    def test_expired_fence_rejects_finish(self):
        from unittest.mock import patch
        from scene_production.handoff.ledger import LedgerError

        original = self.ledger.heartbeat
        calls = []

        def expire(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                self.ledger.clock = lambda: 1e15
            return original(*args, **kwargs)

        with patch.object(self.ledger, "heartbeat", side_effect=expire):
            with self.assertRaises(LedgerError):
                self.run_child("sleep")
        self.ledger.reconcile()
        self.assertEqual(self.ledger.get_task("task")["status"], "unknown")

    def test_symlinked_outputs_never_launch(self):
        from unittest.mock import patch
        from scene_production.handoff.runner import run_task

        foreign = self.root / "foreign"
        foreign.mkdir()
        (self.ledger.directory / "outputs").symlink_to(foreign, target_is_directory=True)
        with patch("scene_production.handoff.runner.subprocess.Popen") as launch:
            self.assertEqual(run_task(self.ledger, "task", "worker", 1)["status"], "unknown")
            launch.assert_not_called()
        self.assertEqual(list(foreign.iterdir()), [])

    def test_handoff_contains_verified_video_version(self):
        self.run_child("success")
        self.create_task("review", depends_on=["task"])
        predecessor = self.ledger.get_handoff("review")["predecessors"][0]
        result = predecessor["attempts"][0]["result"]
        video = result["artifacts"]["video"]
        from scene_production import digest

        self.assertEqual(video["sha256"], digest(Path(video["path"])))
        self.assertFalse(result["semantic_acceptance"])
        recovered = reconcile_evidence(result["directory"])
        self.assertEqual(recovered["artifacts"], result["artifacts"])
        self.assertEqual(recovered["attempt_id"], result["attempt_id"])

    def test_malformed_recovery_evidence(self):
        self.assertEqual(reconcile_evidence(self.root / "missing"), {"status": "unknown"})
        (self.root / "exit.json").write_text("[]")
        self.assertEqual(reconcile_evidence(self.root), {"status": "unknown"})

    def test_heartbeat_continues_during_slow_verification(self):
        import time
        from unittest.mock import patch
        from scene_production.handoff import runner

        original_verify = runner.verify_result
        original_heartbeat = self.ledger.heartbeat
        calls = []

        def heartbeat(*args, **kwargs):
            calls.append(time.monotonic())
            return original_heartbeat(*args, **kwargs)

        def slow_verify(*args):
            time.sleep(0.3)
            return original_verify(*args)

        with (
            patch.object(runner, "verify_result", side_effect=slow_verify),
            patch.object(self.ledger, "heartbeat", side_effect=heartbeat),
        ):
            result = self.run_child("success", lease_seconds=0.2)
        self.assertEqual(result["status"], "succeeded")
        self.assertGreater(len(calls), 5)

    def test_revocation_during_verification_prevents_success(self):
        import threading
        import time
        from unittest.mock import patch
        from scene_production.handoff import runner

        verifying = threading.Event()
        original_verify = runner.verify_result
        original_heartbeat = self.ledger.heartbeat

        def slow_verify(*args):
            verifying.set()
            time.sleep(0.05)
            return original_verify(*args)

        def heartbeat(*args, **kwargs):
            if verifying.is_set():
                self.ledger.revoke_authorization("auth", "message:3")
            return original_heartbeat(*args, **kwargs)

        with (
            patch.object(runner, "verify_result", side_effect=slow_verify),
            patch.object(self.ledger, "heartbeat", side_effect=heartbeat),
        ):
            result = self.run_child("success")
        self.assertEqual(result["status"], "unknown")

    def test_cleanup_escalates_after_leader_exit(self):
        import os
        import signal
        import subprocess
        import sys
        import time
        from unittest.mock import patch
        from scene_production.handoff.runner import run_task

        if sys.platform == "win32":
            self.skipTest("POSIX process group regression")
        ready = self.root / "child.ready"
        original = subprocess.Popen
        owned = []

        def launch(args, **kwargs):
            kwargs["start_new_session"] = False
            kwargs["process_group"] = 0
            leader = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            owned.append(leader)
            child = original(
                [
                    sys.executable,
                    "-c",
                    "import signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "open(sys.argv[1], 'w').close(); time.sleep(30)",
                    str(ready),
                ],
                process_group=leader.pid,
            )
            owned.append(child)
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "child must install TERM handler before revocation")
            return leader

        heartbeat = self.ledger.heartbeat
        calls = []

        def revoke(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                self.ledger.revoke_authorization("auth", "message:2")
            return heartbeat(*args, **kwargs)

        try:
            with (
                patch("scene_production.handoff.runner.subprocess.Popen", side_effect=launch),
                patch.object(self.ledger, "heartbeat", side_effect=revoke),
            ):
                result = run_task(self.ledger, "task", "worker", 1)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(owned[0].wait(timeout=3), -signal.SIGTERM)
            self.assertEqual(owned[1].wait(timeout=3), -signal.SIGKILL)
        finally:
            for process in owned:
                if process.poll() is None:
                    os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=3)

    def test_heartbeat_during_exit_persistence(self):
        import time
        from unittest.mock import patch
        from scene_production import agent

        original = agent._atomic_write

        def delayed(path, value):
            if path.name == "exit.json":
                time.sleep(0.3)
            return original(path, value)

        with patch.object(agent, "_atomic_write", side_effect=delayed):
            self.assertEqual(self.run_child("success", lease_seconds=0.2)["status"], "succeeded")
