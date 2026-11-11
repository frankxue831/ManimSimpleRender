"""Real-Manim regressions for native frame tracing and encoder cleanup."""

import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scene_production import render


@unittest.skipUnless(
    importlib.util.find_spec("manim") and shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "requires existing Manim, ffmpeg and ffprobe",
)
class ProductionCommandProcessTests(unittest.TestCase):
    def test_untraced_render_disables_update_check_and_keeps_output(self):
        # This path also works with older installed Manim: the private writer
        # compatibility guard must remain exclusive to opt-in frame tracing.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scene.py"
            source.write_text(
                "import sys\nfrom manim import Scene, config\n"
                "assert not config.notify_outdated_version\n"
                'print("retained stdout sentinel")\n'
                'print("retained stderr sentinel", file=sys.stderr)\n'
                "class Ordinary(Scene):\n def construct(self):\n  self.wait(1)\n"
            )
            timeline = root / "timeline.json"
            timeline.write_text('{"version":1,"segments":[{"label":"intro","start":0,"end":1}]}')
            result = render(source, "Ordinary", timeline, root / "runs", quality="ql", timeout=30)
            self.assertEqual(result["status"], "success")
            log = (Path(result["run_dir"]) / "render.log").read_text()
            self.assertIn("retained stdout sentinel", log)
            self.assertIn("retained stderr sentinel", log)


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

    @unittest.skipUnless(shutil.which("ffprobe"), "requires ffprobe for video inspection")
    def test_traced_render_preserves_native_sections_and_repeated_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scene.py"
            source.write_text(
                "from manim import RED, RIGHT, Scene, Square\n"
                "from scene_production.frame_clock import hold_until_frame\n"
                "class Traced(Scene):\n"
                " def construct(self):\n"
                '  self.next_section("intro")\n'
                "  square = Square()\n  self.add(square)\n"
                "  self.play(square.animate.shift(RIGHT), run_time=.2)\n"
                "  hold_until_frame(self, 6)\n"
                '  self.next_section("outro")\n'
                "  square.set_color(RED)\n  hold_until_frame(self, 12)\n"
            )
            timeline = root / "timeline.json"
            timeline.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "segments": [
                            {"label": "intro", "start": 0, "end": 0.4, "samples": [0.2]},
                            {"label": "outro", "start": 0.4, "end": 0.8, "samples": [0.6]},
                        ],
                    }
                )
            )
            receipt = render(
                source,
                "Traced",
                timeline,
                root / "runs",
                quality="ql",
                record_scene_frames=True,
                timeout=30,
            )
            self.assertEqual(receipt["status"], "success")
            trace = json.loads(Path(receipt["frame_trace"]["path"]).read_text())
            self.assertEqual(trace["frame_rate"], "15")
            self.assertEqual(trace["frame_count"], 12)
            self.assertEqual(
                trace["sections"],
                [
                    {"id": "intro", "start_frame": 0, "end_frame": 6},
                    {"id": "outro", "start_frame": 6, "end_frame": 12},
                ],
            )
            self.assertEqual(len(set(trace["frames"][3:6])), 1)
            self.assertEqual(len(set(trace["frames"][6:12])), 1)
            self.assertNotEqual(trace["frames"][5], trace["frames"][6])
