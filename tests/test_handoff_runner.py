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
        from scene_production.handoff.ledger import Ledger

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

    def run_child(self, mode):
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
                exit_code=1, run_token='abc', operation='render')
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
            return run_task(self.ledger, "task", "worker", 1, poll_interval=0.005)

    def test_success_and_recovery(self):
        self.assertEqual(self.run_child("success")["status"], "succeeded")
        directory = next((self.ledger.directory / "outputs").iterdir())
        self.assertEqual(reconcile_evidence(directory)["status"], "succeeded")
        (directory / "exit.json").unlink()
        self.assertEqual(reconcile_evidence(directory)["status"], "unknown")

    def test_failed_exit(self):
        self.assertEqual(self.run_child("failed")["status"], "failed")

    def test_invalid_terminal_and_changed_artifacts(self):
        for mode in ("tamper", "mismatch", "truncated", "intermediate", "wrong_exit", "overflow"):
            with self.subTest(mode=mode):
                if self.ledger.get_task("task")["status"] != "pending":
                    self.tearDown()
                    self.setUp()
                self.assertEqual(self.run_child(mode)["status"], "unknown")
                for log in (self.ledger.directory / "outputs").glob("*/*.log"):
                    self.assertLessEqual(log.stat().st_size, 2 * 1024 * 1024)

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

    def test_persistence_failure_is_unknown(self):
        from unittest.mock import patch
        from scene_production import agent

        original = agent._atomic_write

        def fail_exit(path, value):
            if path.name == "exit.json":
                raise OSError("private detail")
            return original(path, value)

        with patch.object(agent, "_atomic_write", side_effect=fail_exit):
            self.assertEqual(self.run_child("success")["status"], "unknown")

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
