"""Production timelines remain diagnostic evidence, never terminal authority."""

from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from app.render.aggregation import JobOutcome
from app.render.process_session import _ProcessSession
from app.render.runner import JobResult, RenderRunner
from scene_production.reinspection import reinspect
from tests import test_scene_production as fixtures


class ProductionDiagnosticsTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    runner = fixtures.PipelineTests.runner
    invoke = fixtures.PipelineTests.invoke
    receipt = fixtures.PipelineTests.receipt
    fake_tool = staticmethod(fixtures.PipelineTests.fake_tool)

    def events(self, root=None):
        path = next((root or self.output).glob("*/diagnostics.jsonl"))
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_complete_timeline_and_private_line_buffered_raw_log(self):
        original = self.runner()
        outer = self

        class Streaming:
            def run_job(self, job, command, event, log, env):
                log("PRIVATE_CHILD_OUTPUT")
                path = next(outer.output.glob("*/render.log"))
                outer.assertIn("PRIVATE_CHILD_OUTPUT", path.read_text())
                if os.name == "posix":
                    outer.assertEqual(path.stat().st_mode & 0o777, 0o600)
                return original.run_job(job, command, event, log, env)

        with patch("scene_production.tool", side_effect=self.fake_tool):
            result = self.invoke(runner=Streaming())
        events = self.events()
        self.assertEqual(result["status"], "success")
        self.assertNotIn("stage", result)
        phases = [item["phase"] for item in events if item["event"] == "phase_started"]
        self.assertEqual(
            phases,
            [
                "prepare",
                "render",
                "provenance",
                "artifact",
                "inspection_integrity",
                "probe",
                "probe",
                "decode",
                "frames",
                "inspection_integrity",
                "publication",
            ],
        )
        finished = [item for item in events if item["event"] == "phase_finished"]
        self.assertEqual([item["phase"] for item in finished], phases)
        self.assertTrue(all(item["duration_seconds"] >= 0 for item in finished))
        self.assertEqual(events[-1]["event"], "production_finished")
        self.assertTrue(all(item["timestamp"].endswith("Z") for item in events))
        self.assertEqual([item["sequence"] for item in events], list(range(1, len(events) + 1)))
        self.assertEqual(len({item["run_id"] for item in events}), 1)
        manifest = json.loads((Path(result["run_dir"]) / "implementation.json").read_text())
        self.assertEqual(manifest["run_id"], events[0]["run_id"])
        self.assertEqual(manifest["operation"], "render")
        self.assertEqual(manifest["implementation"]["source"]["status"], "observed")
        text = json.dumps(events)
        self.assertNotIn("PRIVATE_CHILD_OUTPUT", text)
        self.assertNotIn(str(self.root), text)
        if os.name == "posix":
            self.assertEqual(
                next(self.output.glob("*/diagnostics.jsonl")).stat().st_mode & 0o777, 0o600
            )

    def test_tool_timeout_has_safe_phase_and_reason(self):
        with patch(
            "scene_production.tool", side_effect=subprocess.TimeoutExpired("PRIVATE_ARG", 0.01)
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.invoke(runner=self.runner())
        events = self.events()
        failed = [item for item in events if item["event"] == "phase_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["phase"], "probe")
        self.assertEqual(failed[0]["exception_type"], "TimeoutExpired")
        self.assertEqual(events[-1]["event"], "production_failed")
        self.assertEqual(events[-1]["reason"], "tool_timeout")
        self.assertEqual(self.receipt()["failure_reason"], "tool_timeout")
        self.assertNotIn("PRIVATE_ARG", json.dumps(events))

    def test_missing_executable_retains_runner_diagnostic_privately(self):
        class Missing:
            def run_job(self, job, *args):
                return JobResult(job, None, JobOutcome.MANIM_NOT_FOUND, "PRIVATE_LAUNCH_ERROR")

        with self.assertRaises(RuntimeError):
            self.invoke(runner=Missing())
        self.assertEqual(self.receipt()["failure_reason"], "executable_not_found")
        self.assertIn("PRIVATE_LAUNCH_ERROR", next(self.output.glob("*/render.log")).read_text())
        self.assertNotIn("PRIVATE_LAUNCH_ERROR", json.dumps(self.events()))

    def test_delayed_timer_callback_after_api_return_does_not_cancel_success(self):
        class DelayedTimer:
            def __init__(self, timeout, callback):
                self.callback = callback

            def start(self):
                pass

            def cancel(self):
                # Simulate a callback already dispatched when cancel() runs.
                self.callback()

            def join(self):
                pass

        event = threading.Event()
        with (
            patch("scene_production.threading.Timer", DelayedTimer),
            patch("scene_production.tool", side_effect=self.fake_tool),
        ):
            result = self.invoke(runner=self.runner(), cancel_event=event)
        self.assertEqual(result["status"], "success")
        self.assertFalse(event.is_set())

    def test_deadline_during_post_exit_drain_is_cleanup_timeout(self):
        callbacks = []

        class ManualTimer:
            def __init__(self, timeout, callback):
                callbacks.append(callback)

            def start(self):
                pass

            def cancel(self):
                pass

            def join(self):
                pass

        process = Mock()
        process.poll.return_value = process.wait.return_value = 0
        reader = Mock()
        reader.is_alive.return_value = False
        session = _ProcessSession(process, 123, queue.Queue(), reader, 1, 1)

        def drain(*args):
            callbacks[0]()
            return True

        with (
            patch("scene_production.threading.Timer", ManualTimer),
            patch("app.render.runner._ProcessSession.start", return_value=session),
            patch.object(session, "_drain_until", side_effect=drain),
            patch.object(session, "_group_is_active", return_value=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup timed out"):
                self.invoke(runner=RenderRunner())
        self.assertEqual(self.receipt()["failure_reason"], "render_cleanup_timeout")
        self.assertEqual(self.receipt()["status"], "failed")
        result = next(item for item in self.events() if item["event"] == "render_result")
        self.assertEqual(result["outcome"], "success")
        self.assertTrue(result["leader_exit_observed_before_deadline"])
        self.assertEqual(self.events()[-1]["reason"], "render_cleanup_timeout")

    def test_production_interruption_receipt_does_not_certify_cleanup(self):
        from scene_production.agent import _Interrupted

        for error in (KeyboardInterrupt(), SystemExit(), _Interrupted(signal.SIGTERM)):
            self.output = self.root / type(error).__name__
            runner = Mock()
            runner.run_job.side_effect = error
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)):
                    self.invoke(runner=runner)
                receipt = self.receipt()
                self.assertEqual(receipt["failure_reason"], "interrupted")
                self.assertFalse(receipt["process_cleanup_verified"])
                self.assertEqual(receipt["stage"], "render")
                self.assertEqual(self.events()[-1]["reason"], "interrupted")

    def test_diagnostic_creation_failure_preserves_success_and_warns_without_detail(self):
        real_open = os.open

        def fail_timeline(path, *args, **kwargs):
            if Path(path).name == "diagnostics.jsonl":
                raise PermissionError("PRIVATE_DISK_ERROR")
            return real_open(path, *args, **kwargs)

        warning = io.StringIO()
        with (
            patch("app.render.diagnostics.os.open", side_effect=fail_timeline),
            patch("scene_production.tool", side_effect=self.fake_tool),
            redirect_stderr(warning),
        ):
            result = self.invoke(runner=self.runner())
        self.assertEqual(result["status"], "success")
        self.assertIn("diagnostic timeline unavailable", warning.getvalue())
        self.assertNotIn("PRIVATE_DISK_ERROR", warning.getvalue())

    def test_implementation_manifest_failure_warns_without_changing_render_result(self):
        warning = io.StringIO()
        with (
            patch("scene_production.write_implementation_manifest", return_value=False),
            patch("scene_production.tool", side_effect=self.fake_tool),
            redirect_stderr(warning),
        ):
            result = self.invoke(runner=self.runner())
        self.assertEqual(result["status"], "success")
        self.assertIn("implementation manifest unavailable", warning.getvalue())
        self.assertIn("implementation_manifest_unavailable", [e["event"] for e in self.events()])

    def test_reinspection_has_copy_and_check_phases_without_rendering(self):
        with patch("scene_production.tool", side_effect=self.fake_tool):
            original = self.invoke(runner=self.runner())
            target = self.root / "reinspect"
            result = reinspect(Path(original["run_dir"]) / "result.json", self.timeline, target)
        events = self.events(target)
        phases = [item["phase"] for item in events if item["event"] == "phase_started"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(phases[0], "copy")
        self.assertEqual(phases[-1], "publication")
        self.assertNotIn("render", phases)
        self.assertTrue(all(item["operation"] == "reinspect" for item in events))
        manifest = json.loads((Path(result["run_dir"]) / "implementation.json").read_text())
        self.assertEqual(manifest["operation"], "reinspect")
        self.assertEqual(manifest["run_id"], events[0]["run_id"])

    def test_reinspection_manifest_failure_preserves_success(self):
        with patch("scene_production.tool", side_effect=self.fake_tool):
            original = self.invoke(runner=self.runner())
        target = self.root / "reinspect"
        warning = io.StringIO()
        with (
            patch("scene_production.write_implementation_manifest", return_value=False),
            patch("scene_production.tool", side_effect=self.fake_tool),
            redirect_stderr(warning),
        ):
            result = reinspect(Path(original["run_dir"]) / "result.json", self.timeline, target)
        self.assertEqual(result["status"], "success")
        self.assertIn("implementation manifest unavailable", warning.getvalue())
        self.assertIn(
            "implementation_manifest_unavailable", [e["event"] for e in self.events(target)]
        )

    def test_failure_receipt_write_error_remains_distinct_from_original_failure(self):
        failure = subprocess.TimeoutExpired("PRIVATE_ARG", 1)

        def failed_receipt(run, receipt):
            if "failure_reason" in receipt:
                raise PermissionError("PRIVATE_DISK_ERROR")

        with (
            patch("scene_production.tool", side_effect=failure),
            patch("scene_production._write_receipt", side_effect=failed_receipt),
        ):
            with self.assertRaises(PermissionError) as caught:
                self.invoke(runner=self.runner())
        self.assertIs(caught.exception.__cause__, failure)
        events = self.events()
        self.assertEqual(events[-2]["reason"], "tool_timeout")
        self.assertEqual(events[-1]["event"], "failure_receipt_unavailable")
        self.assertEqual(events[-1]["exception_type"], "PermissionError")
        self.assertNotIn("PRIVATE_", json.dumps(events))
