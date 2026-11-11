"""Requested FPS never replaces observed media or weakens failure cleanup."""

import json
import threading
import unittest
from unittest.mock import patch

from app.render.aggregation import JobOutcome
from app.render.cleanup import RenderCleanupState
from app.render.runner import JobResult
from scene_production import render
from tests import test_scene_production as production_fixtures
from tests import test_traced_production as traced_fixtures


class ExplicitFpsFailureTests(unittest.TestCase):
    setUp = production_fixtures.PipelineTests.setUp
    receipt = production_fixtures.PipelineTests.receipt

    def test_prepared_command_and_intent_survive_each_runner_failure(self):
        for outcome, state in (
            (JobOutcome.MANIM_NOT_FOUND, RenderCleanupState.NOT_STARTED),
            (JobOutcome.CANCELLED, RenderCleanupState.COMPLETE),
            (JobOutcome.FAILED, RenderCleanupState.COMPLETE),
            (JobOutcome.FAILED, RenderCleanupState.UNKNOWN),
        ):
            for traced in (False, True):
                with self.subTest(outcome=outcome, cleanup=state, traced=traced):
                    output = self.root / (outcome.value + state.value + str(traced))
                    commands = []

                    class Failed:
                        def run_job(self, job, command, *args):
                            commands.append(command)
                            return JobResult(
                                job,
                                None if state == RenderCleanupState.NOT_STARTED else 1,
                                outcome,
                                render_cleanup_state=state,
                            )

                    with self.assertRaises(RuntimeError) as caught:
                        render(
                            self.source,
                            "Demo",
                            self.timeline,
                            output,
                            fps=30,
                            runner=Failed(),
                            record_scene_frames=traced,
                        )
                    record = json.loads(next(output.glob("*/result.json")).read_text())
                    self.assertEqual(record["requested_fps"], 30)
                    self.assertEqual(record["command"], commands[0])
                    self.assertEqual(record["command"].count("--fps"), 1)
                    self.assertEqual(
                        record["command"][-4:], ["--fps", "30", "--disable_caching", "--silent"]
                    )
                    self.assertEqual(record["render_cleanup_state"], state.value)
                    self.assertEqual(caught.exception.render_cleanup_state, state)
                    self.assertNotIn("completed_artifact_version", record)

    def test_explicit_timeout_retains_prepared_command(self):
        runner = production_fixtures.PipelineTests.runner(self, "timeout")
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            render(
                self.source, "Demo", self.timeline, self.output, fps=30, runner=runner, timeout=0.01
            )
        record = self.receipt()
        self.assertEqual(record["requested_fps"], 30)
        self.assertEqual(record["failure_reason"], "render_timeout")
        self.assertIn("--fps", record["command"])
        self.assertEqual(record["render_cleanup_state"], "complete")

    def test_explicit_cancellation_retains_prepared_command(self):
        event = threading.Event()
        event.set()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            render(
                self.source,
                "Demo",
                self.timeline,
                self.output,
                fps=30,
                runner=production_fixtures.PipelineTests.runner(self),
                cancel_event=event,
            )
        self.assertEqual(self.receipt()["requested_fps"], 30)
        self.assertEqual(self.receipt()["failure_reason"], "cancelled")

    def test_observed_override_is_not_replaced_with_requested_intent(self):
        def observed(command, timeout, *, cwd=None):
            result = production_fixtures.PipelineTests.fake_tool(command, timeout, cwd=cwd)
            if command[0] == "ffprobe":
                record = json.loads(result)
                record["streams"][0]["avg_frame_rate"] = "60/1"
                return json.dumps(record).encode()
            return result

        with patch("scene_production.tool", side_effect=observed):
            result = render(
                self.source,
                "Demo",
                self.timeline,
                self.output,
                fps=30,
                runner=production_fixtures.PipelineTests.runner(self),
            )
        self.assertEqual(result["requested_fps"], 30)
        self.assertEqual(result["probe"]["streams"][0]["avg_frame_rate"], "60/1")

    def test_observed_trace_probe_mismatch_still_fails(self):
        def observed(command, timeout, *, cwd=None):
            result = traced_fixtures.TracedProductionTests.fake_tool(command, timeout, cwd=cwd)
            if command[0] == "ffprobe":
                record = json.loads(result)
                record["streams"][0].update(avg_frame_rate="60/1", r_frame_rate="60/1")
                return json.dumps(record).encode()
            return result

        with patch("scene_production.tool", side_effect=observed):
            with self.assertRaises(ValueError):
                render(
                    self.source,
                    "Demo",
                    self.timeline,
                    self.output,
                    fps=30,
                    runner=traced_fixtures.TracedProductionTests.runner(self),
                    record_scene_frames=True,
                )
        record = self.receipt()
        self.assertEqual(record["requested_fps"], 30)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["stage"], "frame_trace")
