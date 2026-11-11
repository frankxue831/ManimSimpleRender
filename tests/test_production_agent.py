"""Machine protocol boundaries; media execution is covered by production tests."""

from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

from app.render.cleanup import RenderCleanupState, set_cleanup_state
from unittest.mock import patch


class AgentProtocolTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            importlib.util.find_spec("scene_production.agent"), "agent protocol is not implemented"
        )
        self.agent = importlib.import_module("scene_production.agent")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "attempt"

    def invoke(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = self.agent.main(argv)
        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len([event for event in events if event["event"] == "terminal"]), 1)
        self.assertEqual(events[-1]["event"], "terminal")
        self.assertTrue(all(event["run_token"] == events[0]["run_token"] for event in events))
        self.assertEqual(stderr.getvalue(), "")
        return code, events

    def arguments(self, operation="render"):
        args = [operation, "--timeline", "timeline.json", "--output-dir", str(self.output)]
        if operation == "render":
            return args + ["--scene-file", "scene.py", "--scene", "Demo"]
        return args + ["--from-run", "old/result.json"]

    @staticmethod
    def successful_api(**kwargs):
        run = Path(kwargs["output_dir"]) / "scene-fixture"
        run.mkdir(parents=True)
        result = {"status": "success", "run_dir": str(run), "frames": []}
        if "from_run" in kwargs:
            result["operation"] = "reinspect"
        else:
            result["render_cleanup_state"] = "complete"
        for name, data in (
            ("source.py", b"source"),
            ("timeline.json", b"{}"),
            ("video.mp4", b"vid"),
        ):
            (run / name).write_bytes(data)
            key = {"source.py": "source", "timeline.json": "timeline", "video.mp4": "video"}[name]
            result[key + "_sha256"] = hashlib.sha256(data).hexdigest()
            if key == "video":
                result[key] = str(run / name)
        frame = run / "frame.png"
        frame.write_bytes(b"frame")
        result["frames"] = [{"path": str(frame), "sha256": hashlib.sha256(b"frame").hexdigest()}]
        (run / "result.json").write_text(json.dumps(result))
        return result

    def test_describe_has_executable_structured_invocations_without_environment(self):
        with patch.dict(os.environ, {"SECRET_TEST": "DO-NOT-SERIALIZE"}):
            code, events = self.invoke(["describe"])
        self.assertEqual(code, 0)
        description = events[-1]["description"]
        for command in description["commands"].values():
            invocation = command["invocation"]
            self.assertEqual(set(invocation), {"args", "cwd", "env", "shell"})
            self.assertIsNone(invocation["env"])
            self.assertIs(invocation["shell"], False)
            self.assertEqual(
                invocation["args"][:3], [sys.executable, "-m", "scene_production.agent"]
            )
        self.assertNotIn("DO-NOT-SERIALIZE", json.dumps(events))

    def test_parse_failures_are_terminal_machine_readable_without_raw_argv(self):
        for argv in ([], ["bad-secret-command"], ["render"], ["render", "--timeout", "SECRET"]):
            with self.subTest(argv=argv):
                code, events = self.invoke(argv)
                self.assertEqual(code, 2)
                self.assertEqual(events[-1]["error"]["code"], "invalid_arguments")
                self.assertNotIn("SECRET", json.dumps(events))
                self.assertNotIn("bad-secret", json.dumps(events))

    def test_success_persists_token_events_and_verified_artifacts(self):
        with patch("scene_production.render", side_effect=self.successful_api):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 0)
        terminal = events[-1]
        self.assertEqual(terminal["status"], "succeeded")
        self.assertFalse(terminal["semantic_acceptance"])
        self.assertEqual(json.loads((self.output / "attempt.json").read_text()), terminal)
        journal = [
            json.loads(line) for line in (self.output / "events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events, journal)
        self.assertEqual(
            terminal["artifacts"]["video"]["sha256"], hashlib.sha256(b"vid").hexdigest()
        )

    def test_phase_timing_does_not_add_protocol_events_or_private_input(self):
        with patch("scene_production.render", side_effect=self.successful_api):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 0)
        self.assertEqual([item["event"] for item in events], ["started", "terminal"])
        self.assertTrue(all(item["timestamp"].endswith("Z") for item in events))
        self.assertGreaterEqual(events[-1]["elapsed_seconds"], events[0]["elapsed_seconds"])
        self.assertEqual(events[-1]["phase"], "complete")
        timeline = [
            json.loads(line)
            for line in (self.output / "diagnostics.jsonl").read_text().splitlines()
        ]
        self.assertTrue(all(item["run_token"] == events[0]["run_token"] for item in timeline))
        finished = [item["phase"] for item in timeline if item["event"] == "phase_finished"]
        self.assertEqual(
            finished, ["initial_persistence", "started_delivery", "operation", "verification"]
        )
        self.assertFalse(events[-1]["diagnostics"]["timeline_write_failed"])
        self.assertNotIn(str(self.output), json.dumps(timeline))

    def test_timeline_unavailable_is_visible_without_changing_success(self):
        real_open = os.open

        def open_file(path, *args, **kwargs):
            if Path(path).name == "diagnostics.jsonl":
                raise OSError("private mount secret")
            return real_open(path, *args, **kwargs)

        with (
            patch("app.render.diagnostics.os.open", side_effect=open_file),
            patch("scene_production.render", side_effect=self.successful_api),
        ):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 0)
        self.assertTrue(events[-1]["diagnostics"]["timeline_write_failed"])
        self.assertNotIn("private mount", json.dumps(events))

    def test_unknown_records_interrupted_phase_without_claiming_cleanup(self):
        with patch("scene_production.render", side_effect=KeyboardInterrupt):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 130)
        self.assertEqual(events[-1]["phase"], "operation")
        timeline = [
            json.loads(line)
            for line in (self.output / "diagnostics.jsonl").read_text().splitlines()
        ]
        failed = [item for item in timeline if item["event"] == "phase_failed"]
        self.assertEqual(failed[-1]["phase"], "operation")
        self.assertEqual(failed[-1]["exception_type"], "KeyboardInterrupt")
        self.assertFalse(events[-1]["retry_safe"])

    def test_existing_directory_is_never_claimed_or_its_logs_associated(self):
        self.output.mkdir()
        marker = self.output / "attempt.json"
        marker.write_text("old attempt")
        with patch("scene_production.render") as api:
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]["error"]["code"], "output_exists")
        self.assertEqual(events[-1]["diagnostics"], {})
        self.assertEqual(marker.read_text(), "old attempt")
        api.assert_not_called()

    def test_transient_failed_receipt_does_not_end_or_retry_operation(self):
        def api(**kwargs):
            result = self.successful_api(**kwargs)
            path = Path(result["run_dir"]) / "result.json"
            failed = dict(result, status="failed", completed_artifact_version=1)
            path.write_text(json.dumps(failed))
            state = json.loads((self.output / "attempt.json").read_text())
            self.assertEqual(state["status"], "running")
            path.write_text(json.dumps(result))
            return result

        with patch("scene_production.render", side_effect=api) as call:
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 0)
        self.assertEqual(call.call_count, 1)
        self.assertEqual([event["event"] for event in events], ["started", "terminal"])

    def test_api_exception_is_sanitized_and_only_attempt_logs_are_linked(self):
        def api(**kwargs):
            print("private-output-secret")
            print("private-error-secret", file=sys.stderr)
            raise set_cleanup_state(
                RuntimeError("private-exception-secret"), RenderCleanupState.NOT_STARTED
            )

        with patch("scene_production.render", side_effect=api):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 1)
        self.assertNotIn("private-", json.dumps(events))
        self.assertIn("private-output-secret", (self.output / "api.log").read_text())
        self.assertIn("private-exception-secret", (self.output / "api.log").read_text())
        self.assertNotIn("private-", (self.output / "diagnostics.jsonl").read_text())
        self.assertEqual(events[-1]["error"]["code"], "operation_failed")
        self.assertFalse(events[-1]["retry_safe"])

    def test_interruption_is_unknown_not_retryable_failed(self):
        with patch("scene_production.render", side_effect=KeyboardInterrupt):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 130)
        self.assertEqual(events[-1]["status"], "unknown")
        self.assertFalse(events[-1]["process_cleanup_verified"])
        self.assertEqual(json.loads((self.output / "attempt.json").read_text()), events[-1])

    def test_signal_interruption_retains_production_reason_and_unknown_protocol(self):
        source = self.root / "scene.py"
        source.write_text(
            "from manim import Scene\nclass Demo(Scene):\n def construct(self): pass\n"
        )
        timeline = self.root / "timeline.json"
        timeline.write_text('{"version":1,"segments":[{"label":"intro","start":0,"end":1}]}')
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.output = self.root / str(signum)
            arguments = [
                "render",
                "--timeline",
                str(timeline),
                "--output-dir",
                str(self.output),
                "--scene-file",
                str(source),
                "--scene",
                "Demo",
            ]
            with self.subTest(signum=signum), patch("scene_production.RenderRunner") as runner:
                runner.return_value.run_job.side_effect = lambda *args: signal.raise_signal(signum)
                code, events = self.invoke(arguments)
            self.assertEqual(code, 128 + signum)
            self.assertEqual([e["event"] for e in events], ["started", "terminal"])
            terminal = events[-1]
            self.assertEqual(terminal["status"], "unknown")
            self.assertFalse(terminal["retry_safe"])
            self.assertFalse(terminal["process_cleanup_verified"])
            self.assertEqual(terminal["diagnostics"]["failure_reason"], "interrupted")
            self.assertEqual(terminal["diagnostics"]["stage"], "render")
            receipt = json.loads(Path(terminal["diagnostics"]["receipt"]).read_text())
            self.assertEqual(receipt["failure_reason"], "interrupted")
            self.assertFalse(receipt["process_cleanup_verified"])

    def test_success_result_with_changed_artifact_is_rejected(self):
        def api(**kwargs):
            result = self.successful_api(**kwargs)
            Path(result["video"]).write_bytes(b"modified")
            return result

        with patch("scene_production.render", side_effect=api):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]["error"]["code"], "verification_failed")

    def test_receipt_outside_attempt_is_rejected(self):
        def api(**kwargs):
            return self.successful_api(output_dir=self.root / "other")

        with patch("scene_production.render", side_effect=api):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]["error"]["code"], "verification_failed")

    def test_inspect_dispatches_to_public_reinspection_only(self):
        with (
            patch("scene_production.reinspection.reinspect", side_effect=self.successful_api),
            patch("scene_production.render", side_effect=AssertionError("must not render")),
        ):
            code, events = self.invoke(self.arguments("inspect"))
        self.assertEqual(code, 0)
        self.assertEqual(events[-1]["operation"], "inspect")

    def test_stale_running_attempt_is_not_reused(self):
        self.output.mkdir()
        (self.output / "attempt.json").write_text('{"status":"running"}')
        code, events = self.invoke(self.arguments())
        self.assertEqual(code, 1)
        self.assertFalse(events[-1]["retry_safe"])
        self.assertEqual(
            json.loads((self.output / "attempt.json").read_text())["status"], "running"
        )

    def test_cli_subprocess_usage_error_has_no_traceback(self):
        result = subprocess.run(
            [sys.executable, "-m", "scene_production.agent", "render"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        event = json.loads(result.stdout)
        self.assertEqual(event["event"], "terminal")
        self.assertEqual(result.stderr, "")

    def test_real_early_api_failure_keeps_traceback_only_in_private_capture(self):
        arguments = self.arguments()
        arguments[arguments.index("--scene-file") + 1] = str(self.root / "PRIVATE_MISSING.py")
        result = subprocess.run(
            [sys.executable, "-m", "scene_production.agent", *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([item["event"] for item in events], ["started", "terminal"])
        self.assertEqual(events[-1]["error"]["code"], "input_or_tool_missing")
        self.assertEqual(events[-1]["phase"], "operation")
        self.assertIn("PRIVATE_MISSING.py", (self.output / "api.log").read_text())
        self.assertNotIn("PRIVATE_MISSING.py", result.stdout)
        self.assertNotIn("PRIVATE_MISSING.py", (self.output / "diagnostics.jsonl").read_text())

    def test_foreign_exception_receipt_is_not_associated(self):
        other = self.root / "old" / "result.json"
        other.parent.mkdir()
        other.write_text('{"stage":"render","error":"old secret"}')
        error = set_cleanup_state(RuntimeError("secret"), RenderCleanupState.COMPLETE)
        error.receipt_path = str(other)
        with patch("scene_production.render", side_effect=error):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 1)
        self.assertNotIn("receipt", events[-1]["diagnostics"])
        self.assertNotIn("old secret", json.dumps(events))

    def test_owned_failure_receipt_and_fixed_font_hint(self):
        def fail(**kwargs):
            run = Path(kwargs["output_dir"]) / "scene-failure"
            run.mkdir(parents=True)
            receipt = run / "result.json"
            receipt.write_text('{"status":"failed","stage":"render","error":"secret"}')
            (run / "render.log").write_text("ValueError: SCENE_FONT is not installed: secret-font")
            error = set_cleanup_state(RuntimeError("secret"), RenderCleanupState.COMPLETE)
            error.receipt_path = str(receipt)
            raise error

        with patch("scene_production.render", side_effect=fail):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 1)
        diagnostics = events[-1]["diagnostics"]
        self.assertEqual(diagnostics["stage"], "render")
        self.assertIn("selected font is not installed", diagnostics["font_hint"])
        self.assertNotIn("secret", json.dumps(events))

    def test_symlinked_artifact_is_rejected(self):
        def api(**kwargs):
            result = self.successful_api(**kwargs)
            video = Path(result["video"])
            target = video.with_name("other.mp4")
            video.rename(target)
            video.symlink_to(target)
            return result

        with patch("scene_production.render", side_effect=api):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]["error"]["code"], "verification_failed")

    def test_returned_success_must_match_persisted_receipt(self):
        def api(**kwargs):
            result = self.successful_api(**kwargs)
            path = Path(result["run_dir"]) / "result.json"
            path.write_text(json.dumps(dict(result, status="failed")))
            return result

        with patch("scene_production.render", side_effect=api):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]["error"]["code"], "verification_failed")

    def test_terminal_persistence_failure_returns_unknown_not_success(self):
        real_write = self.agent._atomic_write

        def fail_terminal(path, content):
            if '"event": "terminal"' in content:
                raise OSError("disk secret")
            return real_write(path, content)

        with (
            patch("scene_production.render", side_effect=self.successful_api),
            patch.object(self.agent, "_atomic_write", side_effect=fail_terminal),
        ):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 3)
        self.assertEqual(events[-1]["status"], "unknown")
        self.assertEqual(events[-1]["error"]["code"], "persistence_failed")
        self.assertNotIn("disk secret", json.dumps(events))
        self.assertEqual(
            json.loads((self.output / "attempt.json").read_text())["status"], "running"
        )

    def test_failed_initial_persistence_never_starts_api(self):
        with (
            patch.object(self.agent, "_atomic_write", side_effect=OSError("disk")),
            patch("scene_production.render") as api,
        ):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 3)
        self.assertEqual(events[-1]["status"], "unknown")
        api.assert_not_called()

    def test_atomic_replace_failure_preserves_previous_state(self):
        state = self.root / "state.json"
        state.write_text("old")
        with patch.object(self.agent.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.agent._atomic_write(state, "new")
        self.assertEqual(state.read_text(), "old")
        self.assertEqual(list(self.root.glob(".agent-*")), [])

    def test_native_stdout_and_stderr_do_not_corrupt_cli_protocol(self):
        code = (
            "import os, sys, scene_production\n"
            "from scene_production.agent import main\n"
            "def fail(**kwargs):\n"
            " os.write(1, b'native-secret-out\\n')\n"
            " os.write(2, b'native-secret-err\\n')\n"
            " raise RuntimeError('private')\n"
            "scene_production.render = fail\n"
            "sys.exit(main(sys.argv[1:]))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, *self.arguments()],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 3)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([event["event"] for event in events], ["started", "terminal"])
        self.assertNotIn("secret", result.stdout + result.stderr)
        self.assertIn("native-secret-out", (self.output / "api.log").read_text())
        self.assertIn("native-secret-err", (self.output / "api.log").read_text())

    def test_sigterm_is_unknown_with_single_terminal(self):
        code = (
            "import os, signal, sys, scene_production\n"
            "from scene_production.agent import main\n"
            "def stop(**kwargs):\n"
            " os.kill(os.getpid(), signal.SIGTERM)\n"
            "scene_production.render = stop\n"
            "sys.exit(main(sys.argv[1:]))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, *self.arguments()],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 143)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([event["event"] for event in events], ["started", "terminal"])
        self.assertEqual(events[-1]["status"], "unknown")
        self.assertEqual(result.stderr, "")

    def test_public_render_and_reinspect_complete_after_interim_failed_receipts(self):
        import struct
        from app.render.aggregation import JobOutcome
        from app.render.runner import JobResult

        source = self.root / "scene.py"
        source.write_text(
            "from manim import Scene\nclass Demo(Scene):\n def construct(self): pass\n"
        )
        timeline = self.root / "timeline.json"
        timeline.write_text('{"version":1,"segments":[{"label":"a","start":0,"end":1}]}')
        observed = []

        def runner(job, command, event, log, env):
            media = Path(command[command.index("--media_dir") + 1])
            media.mkdir(parents=True)
            (media / "Demo.mp4").write_bytes(b"video")
            return JobResult(
                job, 0, JobOutcome.SUCCESS, render_cleanup_state=RenderCleanupState.COMPLETE
            )

        def tool(command, timeout, *, cwd=None):
            if command[0] == "ffprobe":
                receipts = list((self.output / "production").glob("*/result.json"))
                observed.append(json.loads(receipts[0].read_text())["status"])
                self.assertEqual(
                    json.loads((self.output / "attempt.json").read_text())["status"], "running"
                )
                return json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "duration": "1",
                                "avg_frame_rate": "30/1",
                                "width": 16,
                                "height": 16,
                            }
                        ]
                    }
                ).encode()
            if command[-1] != "-":
                (Path(cwd or ".") / command[-1]).write_bytes(
                    b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 16, 16)
                )
            return b""

        with (
            patch("scene_production.RenderRunner.run_job", side_effect=runner),
            patch("scene_production.tool", side_effect=tool),
        ):
            code, first = self.invoke(
                [
                    "render",
                    "--scene-file",
                    str(source),
                    "--scene",
                    "Demo",
                    "--timeline",
                    str(timeline),
                    "--output-dir",
                    str(self.output),
                ]
            )
            self.assertEqual(code, 0)
            old = first[-1]["artifacts"]["receipt"]["path"]
            self.output = self.root / "inspection-attempt"
            code, second = self.invoke(
                [
                    "inspect",
                    "--from-run",
                    old,
                    "--timeline",
                    str(timeline),
                    "--output-dir",
                    str(self.output),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(second[-1]["status"], "succeeded")
        self.assertEqual(observed, ["failed", "failed"])
        self.assertNotEqual(first[0]["run_token"], second[0]["run_token"])

    def test_terminal_transport_failure_downgrades_persisted_state_to_unknown(self):
        class BrokenTerminal(io.StringIO):
            def write(self, text):
                if '"event": "terminal"' in text:
                    raise BrokenPipeError("private transport detail")
                return super().write(text)

        output = BrokenTerminal()
        with (
            redirect_stdout(output),
            patch("scene_production.render", side_effect=self.successful_api),
        ):
            code = self.agent.main(self.arguments())
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue())["event"], "started")
        state = json.loads((self.output / "attempt.json").read_text())
        self.assertEqual(state["status"], "unknown")
        self.assertEqual(state["error"]["code"], "transport_failed")

    def test_failed_started_transport_does_not_execute_api(self):
        class BrokenStarted(io.StringIO):
            def write(self, text):
                if '"event": "started"' in text:
                    raise BrokenPipeError("private")
                return super().write(text)

        output = BrokenStarted()
        with redirect_stdout(output), patch("scene_production.render") as api:
            code = self.agent.main(self.arguments())
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue())["status"], "unknown")
        api.assert_not_called()

    def test_sigint_is_unknown_with_single_terminal(self):
        code = (
            "import os, signal, sys, scene_production\n"
            "from scene_production.agent import main\n"
            "def stop(**kwargs):\n"
            " os.kill(os.getpid(), signal.SIGINT)\n"
            "scene_production.render = stop\n"
            "sys.exit(main(sys.argv[1:]))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, *self.arguments()],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 130)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([event["event"] for event in events], ["started", "terminal"])
        self.assertEqual(events[-1]["status"], "unknown")
        self.assertEqual(result.stderr, "")

    def test_log_flush_failure_restores_protocol_descriptors(self):
        code = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            "from scene_production.agent import _private_output\n"
            "class Log:\n"
            " def __init__(self): self.file = open(sys.argv[1], 'w')\n"
            " def __enter__(self): return self\n"
            " def __exit__(self, *args): self.file.close()\n"
            " def fileno(self): return self.file.fileno()\n"
            " def flush(self): raise OSError('disk full')\n"
            "saved = os.dup(1)\n"
            "before = os.fstat(1)\n"
            "try:\n"
            " with patch.object(Path, 'open', return_value=Log()):\n"
            "  try:\n"
            "   with _private_output(Path(sys.argv[1])): pass\n"
            "  except OSError: pass\n"
            " after = os.fstat(1)\n"
            " restored = (before.st_ino, before.st_dev) == (after.st_ino, after.st_dev)\n"
            "finally:\n"
            " os.dup2(saved, 1)\n"
            " os.close(saved)\n"
            "print('restored' if restored else 'lost')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.root / "broken.log")],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "restored")

    def test_closed_stdout_pipe_exits_unknown_without_shutdown_traceback(self):
        process = subprocess.Popen(
            [sys.executable, "-m", "scene_production.agent", "describe"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        process.stdout.close()
        try:
            code = process.wait(timeout=10)
            error = process.stderr.read()
        finally:
            process.stderr.close()
            if process.poll() is None:
                process.kill()
                process.wait()
        self.assertEqual(code, 3)
        self.assertEqual(error, "")

    def test_frame_trace_failure_retains_owned_diagnostic_stage(self):
        def fail(**kwargs):
            run = Path(kwargs["output_dir"]) / "scene-failure"
            run.mkdir(parents=True)
            receipt = run / "result.json"
            receipt.write_text('{"status":"failed","stage":"frame_trace","error":"secret"}')
            error = set_cleanup_state(
                ValueError("secret trace validation detail"), RenderCleanupState.COMPLETE
            )
            error.receipt_path = str(receipt)
            raise error

        with patch("scene_production.render", side_effect=fail):
            code, events = self.invoke(self.arguments())
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]["status"], "failed")
        self.assertEqual(events[-1]["diagnostics"].get("stage"), "frame_trace")
        self.assertEqual(json.loads((self.output / "attempt.json").read_text()), events[-1])
        self.assertNotIn("secret", json.dumps(events))
