"""Optional real-Manim regression for an encoder thread stranded by hook errors."""

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scene_production import render


@unittest.skipUnless(
    importlib.util.find_spec("manim") and shutil.which("ffmpeg"),
    "requires existing Manim and ffmpeg",
)
class CaptureProcessTests(unittest.TestCase):
    def test_frame_before_first_section_fails_as_render_error_not_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scene.py"
            source.write_text(
                "from manim import Scene\nclass BeforeSection(Scene):\n"
                " def construct(self):\n  self.wait(1/30)\n"
                '  self.next_section("later")\n  self.wait(.2)\n'
            )
            timeline = root / "timeline.json"
            timeline.write_text('{"version":1,"segments":[{"label":"later","start":0,"end":1}]}')
            with self.assertRaisesRegex(RuntimeError, "render failed") as caught:
                render(
                    source,
                    "BeforeSection",
                    timeline,
                    root / "runs",
                    record_scene_frames=True,
                    timeout=30,
                )
            self.assertNotIn("timed out", str(caught.exception))
            receipt = next((root / "runs").glob("*/result.json"))
            data = json.loads(receipt.read_text())
            self.assertEqual((data["status"], data["stage"]), ("failed", "render"))
            self.assertIn("call next_section", (receipt.parent / "render.log").read_text())
