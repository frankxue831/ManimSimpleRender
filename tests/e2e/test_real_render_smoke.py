"""Tier B real-render smoke: a genuine manim render through the production path.

Renders a minimal scene with REAL manim + ffmpeg via the production build_command
(default base = manim_base_command) and the real RenderRunner -- no shim -- and
asserts an actual non-empty .mp4 lands at manim's real output path. Gated on manim +
ffmpeg, so it skips cleanly where they are absent (the per-PR CI / bare python3) and
runs on the nightly (Tier B) workflow and a healthy dev machine. Uses a Square + a
short wait, so no LaTeX is required.
Run: .venv/bin/python -m unittest tests.e2e.test_real_render_smoke
"""

import importlib.util
import os
import shutil
import tempfile
import unittest

from app.render.aggregation import JobOutcome
from app.render.command_builder import build_command
from app.render.jobs import build_jobs
from app.render.runner import RenderRunner
from app.scenes.discovery import discover_scenes
from tests.support import output_assertions

_MANIM_MISSING = importlib.util.find_spec("manim") is None
_FFMPEG_MISSING = shutil.which("ffmpeg") is None

_SMOKE_SCENE = (
    "from manim import *\n"
    "\n"
    "class SmokeScene(Scene):\n"
    "    def construct(self):\n"
    "        self.add(Square())\n"
    "        self.wait(0.1)\n"
)


@unittest.skipIf(_MANIM_MISSING or _FFMPEG_MISSING, "real-render smoke needs manim + ffmpeg")
class RealRenderSmokeTests(unittest.TestCase):
    def test_renders_minimal_scene_to_nonempty_mp4(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out:
            with open(os.path.join(root, "smoke.py"), "w", encoding="utf-8") as handle:
                handle.write(_SMOKE_SCENE)
            jobs = build_jobs(discover_scenes(root))
            self.assertEqual(len(jobs), 1)
            cmd = build_command(jobs[0], "-ql", out, "")  # production base -> real manim
            logs = []
            result = RenderRunner().run_job(jobs[0], cmd, cancel_event=None, on_log=logs.append)
            self.assertEqual(result.outcome, JobOutcome.SUCCESS, "\n".join(logs[-15:]))
            self.assertEqual(result.return_code, 0)
            path = output_assertions.media_output_path(out, "smoke.py", "SmokeScene", "480p15")
            output_assertions.assert_nonempty_video(self, path)


if __name__ == "__main__":
    unittest.main()
