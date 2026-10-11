"""Tier A pure-pipeline e2e: discovery -> jobs -> command -> real RenderRunner.

Drives the production seams end to end against a fake `manim` shim, so the actual
Popen / reader-thread / cancel / not-found classification is exercised with NO real
manim, ffmpeg, or Qt -- it runs on a bare python3. This is the high-value tier the
automation-harness plan calls Phase 1.
Run: python3 -m unittest tests.e2e.test_pipeline_e2e
"""

import os
import sys
import threading
import time
import unittest
from unittest import mock

from app.render.aggregation import JobOutcome
from app.render.command_builder import build_command
from app.render.jobs import build_jobs
from app.render.runner import RenderRunner
from app.scenes.discovery import discover_scenes, unparsable_files
from tests.support import fixtures, output_assertions

FAKE_MANIM = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_manim.py")]


def _tmp(test_case):
    import tempfile

    d = tempfile.TemporaryDirectory()
    test_case.addCleanup(d.cleanup)
    return d.name


class PipelineCommandTests(unittest.TestCase):
    def test_discovery_to_command_argv(self):
        root = fixtures.simple_scene_tree(_tmp(self))
        scenes = discover_scenes(root)
        jobs = build_jobs(scenes)
        self.assertEqual(len(jobs), 1)
        cmd = build_command(jobs[0], "-ql", "/out", "", base_command=["manim"])
        self.assertEqual(
            cmd,
            ["manim", jobs[0].file_path, "DemoScene", "-ql", "--media_dir", "/out"],
        )

    def test_duplicate_class_names_produce_two_distinct_jobs(self):
        root = fixtures.duplicate_name_tree(_tmp(self))
        scenes = [s for s in discover_scenes(root) if s.scene_class == "Demo"]
        self.assertEqual(len(scenes), 2)
        jobs = build_jobs(scenes)
        self.assertEqual(len({job.file_path for job in jobs}), 2)  # distinct owning files
        cmds = [build_command(job, "-ql", "/out", "", base_command=["manim"]) for job in jobs]
        self.assertNotEqual(cmds[0][1], cmds[1][1])  # different file positionals

    def test_engine_flag_appended_only_when_nonempty(self):
        root = fixtures.simple_scene_tree(_tmp(self))
        job = build_jobs(discover_scenes(root))[0]
        self.assertNotIn("", build_command(job, "-ql", "/out", "", base_command=["manim"]))
        self.assertEqual(
            build_command(job, "-ql", "/out", "--renderer=opengl", base_command=["manim"])[-1],
            "--renderer=opengl",
        )

    def test_mixed_tree_discovers_only_renderable_and_reports_broken(self):
        root = fixtures.mixed_tree(_tmp(self))
        self.assertEqual({s.scene_class for s in discover_scenes(root)}, {"GoodScene"})
        skipped = dict(unparsable_files(root))
        self.assertIn("broken.py", skipped)


class PipelineRunnerTests(unittest.TestCase):
    def setUp(self):
        self.runner = RenderRunner(terminate_timeout=2.0)
        self.logs = []

    def _job_and_out(self):
        root = fixtures.simple_scene_tree(_tmp(self))
        job = build_jobs(discover_scenes(root))[0]
        return job, _tmp(self)

    def test_runner_success_writes_output_file(self):
        job, out = self._job_and_out()
        cmd = build_command(job, "-ql", out, "", base_command=FAKE_MANIM)
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE": "success"}):
            result = self.runner.run_job(job, cmd, cancel_event=None, on_log=self.logs.append)
        self.assertEqual(result.outcome, JobOutcome.SUCCESS)
        self.assertEqual(result.return_code, 0)
        expected = output_assertions.media_output_path(out, job.file_path, "DemoScene", "480p15")
        output_assertions.assert_nonempty_video(self, expected)

    def test_runner_failure_classified_with_traceback(self):
        job, out = self._job_and_out()
        cmd = build_command(job, "-ql", out, "", base_command=FAKE_MANIM)
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE": "fail"}):
            result = self.runner.run_job(job, cmd, cancel_event=None, on_log=self.logs.append)
        self.assertEqual(result.outcome, JobOutcome.FAILED)
        self.assertEqual(result.return_code, 1)
        self.assertTrue(any("NameError" in line for line in self.logs))

    def test_runner_manim_not_found(self):
        job, out = self._job_and_out()
        cmd = build_command(job, "-ql", out, "", base_command=["this-manim-does-not-exist-xyz"])
        result = self.runner.run_job(job, cmd, cancel_event=None, on_log=self.logs.append)
        self.assertEqual(result.outcome, JobOutcome.MANIM_NOT_FOUND)

    def test_runner_cancel_hang_is_prompt(self):
        job, out = self._job_and_out()
        cmd = build_command(job, "-ql", out, "", base_command=FAKE_MANIM)
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        start = time.monotonic()
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE": "hang"}):
            result = self.runner.run_job(job, cmd, cancel_event=cancel, on_log=self.logs.append)
        self.assertEqual(result.outcome, JobOutcome.CANCELLED)
        self.assertLess(time.monotonic() - start, 10)

    @unittest.skipIf(sys.platform == "win32", "SIGTERM-ignore escalation is POSIX")
    def test_runner_cancel_escalates_to_kill(self):
        job, out = self._job_and_out()
        cmd = build_command(job, "-ql", out, "", base_command=FAKE_MANIM)
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        start = time.monotonic()
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE": "hang_ignore_sigterm"}):
            result = self.runner.run_job(job, cmd, cancel_event=cancel, on_log=self.logs.append)
        self.assertEqual(result.outcome, JobOutcome.CANCELLED)
        self.assertLess(time.monotonic() - start, 10)  # SIGKILL escalation fired


if __name__ == "__main__":
    unittest.main()
