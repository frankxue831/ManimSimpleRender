"""Tier A hardening hooks: lock in specific fixes through the real runner/pipeline.

These are the automation-harness Phase 5 "hooks" -- targeted end-to-end assertions
tying a fix to the real seam, complementing the unit tests. Qt-free; runs on a bare
python3 via the fake-manim shim. (Other Phase-5 hooks already live as unit tests:
shlex round-trip in test_command_builder, progress->reset on cancel in test_controller,
absolute output in test_controller, the doctor subprocess probe in test_env_doctor_broken,
and the ASCII guard.)
Run: python3 -m unittest tests.e2e.test_hardening_e2e
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

from app.render.aggregation import JobOutcome
from app.render.command_builder import build_command
from app.render.jobs import build_jobs, media_output_collisions
from app.render.runner import RenderRunner
from app.scenes.discovery import discover_scenes
from tests.support import fixtures, output_assertions

FAKE_MANIM = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_manim.py")]


class HardeningHookTests(unittest.TestCase):
    def _tmp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def test_same_basename_scenes_alias_one_output_and_are_flagged(self):
        # BL-05: a/scene.py and b/scene.py both defining Demo alias one Manim output
        # path. This test calls RenderRunner directly (bypasses RenderController
        # batch preflight) to show the underlying alias; product-level rejection
        # of colliding jobs is owned by RenderController. media_output_collisions
        # only detects the collision set for that preflight.
        root = fixtures.duplicate_name_tree(self._tmp())
        jobs = build_jobs([s for s in discover_scenes(root) if s.scene_class == "Demo"])
        self.assertEqual(len(jobs), 2)
        self.assertEqual(len(media_output_collisions(jobs)), 1)  # detection fires

        out = self._tmp()
        runner = RenderRunner()
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE": "success"}):
            for job in jobs:
                cmd = build_command(job, "-ql", out, "", base_command=FAKE_MANIM)
                self.assertEqual(
                    runner.run_job(job, cmd, None, lambda _l: None).outcome, JobOutcome.SUCCESS
                )
        # Both keyed on basename "scene" + class "Demo" -> one aliased mp4 survives.
        self.assertEqual(len(output_assertions.find_videos(out)), 1)

    def test_carriage_return_progress_collapses_through_runner(self):
        # BL-28: the fake shim's CR progress ticks must reach the log as one collapsed
        # final value, not one line per tick.
        root = fixtures.simple_scene_tree(self._tmp())
        job = build_jobs(discover_scenes(root))[0]
        cmd = build_command(job, "-ql", self._tmp(), "", base_command=FAKE_MANIM)
        logs = []
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE": "crlf"}):
            RenderRunner().run_job(job, cmd, cancel_event=None, on_log=logs.append)
        progress_lines = [line for line in logs if line.startswith("Rendering ")]
        self.assertEqual(progress_lines, ["Rendering 100%"])  # collapsed to the final tick


if __name__ == "__main__":
    unittest.main()
