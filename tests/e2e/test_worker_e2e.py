"""Tier A worker e2e: RenderWorker.run() over the REAL runner + fake manim.

Drives the production RenderWorker synchronously over the real RenderRunner and
the fake-manim shim, so progress, result codes, cancellation, disk logging, and
summary tallying are exercised end to end without real manim. An isolated
fresh-interpreter probe also starts a real QThread to verify exception fencing.
Needs PyQt5 and skips cleanly on the Qt-free CI job.
Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest tests.e2e.test_worker_e2e
"""

import os
import subprocess
import sys
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtWidgets import QApplication

    _HAS_QT = True
except ImportError:  # the Qt-free e2e-pure CI job
    _HAS_QT = False

if _HAS_QT:
    from app.render.jobs import build_jobs
    from app.render.worker import RenderWorker
    from app.scenes.discovery import discover_scenes
    from tests.support import fixtures, output_assertions

    _app = QApplication.instance() or QApplication([])

FAKE_MANIM = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_manim.py")]


@unittest.skipUnless(_HAS_QT, "PyQt5 not installed (Qt-free job)")
class WorkerE2ETests(unittest.TestCase):
    def _tmp(self):
        import tempfile

        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def _jobs(self, names):
        root = self._tmp()
        for index, name in enumerate(names):
            fixtures.write_scene(root, f"s{index}.py", name)
        return build_jobs(discover_scenes(root))

    def test_unexpected_runner_exception_does_not_abort_python(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        code = """
from PyQt5.QtCore import QCoreApplication
from app.render.jobs import RenderJob
from app.render.worker import RenderWorker
class Boom:
    def run_job(self, *args, **kwargs):
        raise RuntimeError('boom')
app = QCoreApplication([])
worker = RenderWorker([RenderJob('a.py', 'A')], '-ql', '', '/tmp', runner=Boom())
worker.start()
assert worker.wait(5000)
print('worker stopped')
"""
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=repo_root, capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("worker stopped", proc.stdout)

    def test_full_batch_success(self):
        jobs = self._jobs(["SceneA", "SceneB", "SceneC"])
        self.assertEqual(len(jobs), 3)
        out = self._tmp()
        worker = RenderWorker(jobs, "-ql", "", out, base_command=FAKE_MANIM)
        progress, summaries = [], []
        worker.progress.connect(lambda c, t: progress.append((c, t)))
        worker.finished_summary.connect(summaries.append)
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE": "success"}):
            worker.run()
        self.assertEqual(summaries[0].succeeded, 3)
        self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])
        self.assertEqual(len(output_assertions.find_videos(out)), 3)

    def test_partial_batch_with_cancel(self):
        jobs = self._jobs(["SceneA", "SceneB", "SceneC"])  # sorted s0,s1,s2 -> A,B,C
        out = self._tmp()
        worker = RenderWorker(jobs, "-ql", "", out, base_command=FAKE_MANIM)
        summaries = []
        worker.finished_summary.connect(summaries.append)
        # A succeeds, B fails (both fast), then cancel trips while C is hanging.
        threading.Timer(0.8, worker.request_cancel).start()
        modes = "SceneA=success,SceneB=fail,SceneC=hang"
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE_MAP": modes}):
            worker.run()
        summary = summaries[0]
        self.assertEqual(summary.succeeded, 1)
        self.assertEqual(summary.failed, 1)
        self.assertGreaterEqual(summary.cancelled, 1)
        self.assertTrue(summary.terminal_message().startswith("Cancelled:"))

    def test_manim_not_found_terminal_message(self):
        jobs = self._jobs(["OnlyScene"])
        worker = RenderWorker(
            jobs, "-ql", "", self._tmp(), base_command=["no-such-manim-binary-xyz123"]
        )
        summaries = []
        worker.finished_summary.connect(summaries.append)
        worker.run()
        self.assertEqual(summaries[0].manim_not_found, 1)
        self.assertIn("manim not found", summaries[0].terminal_message().lower())


if __name__ == "__main__":
    unittest.main()
