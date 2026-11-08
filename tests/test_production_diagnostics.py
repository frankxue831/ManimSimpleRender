"""Production timelines remain diagnostic evidence, never terminal authority."""

from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch

from app.render.aggregation import JobOutcome
from app.render.runner import JobResult
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
