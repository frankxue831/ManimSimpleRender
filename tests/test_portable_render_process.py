"""Same-host receiving render regression; not cross-machine portability proof."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scene_production import portable


@unittest.skipUnless(
    importlib.util.find_spec("manim") and shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "requires existing Manim, ffmpeg and ffprobe",
)
class PortableRenderProcessTests(unittest.TestCase):
    def test_generated_recipe_preserves_frame_bound_quality_and_explicit_fps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            producer = root / "producer"
            producer.mkdir()
            (producer / "scene.py").write_text(
                "from manim import Scene, Square\n"
                "from scene_production.frame_clock import hold_until_frame\n"
                "class PortableFrames(Scene):\n"
                " def construct(self):\n"
                "  # scene:start intro\n"
                '  self.next_section("intro")\n'
                "  self.add(Square())\n  hold_until_frame(self, 24)\n"
                "  # scene:end intro\n"
            )
            (producer / "timeline.json").write_text(
                json.dumps({"version": 1, "segments": [{"label": "intro", "start": 0, "end": 1}]})
            )
            (producer / "scene.manifest.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "brief": {
                            "audience": "developers",
                            "goal": "preserve frames",
                            "sources": ["authored fixture"],
                            "pitfalls": ["Frame counts require explicit FPS"],
                        },
                        "segments": [
                            {"id": "intro", "captions": [], "objects": ["square"], "depends_on": []}
                        ],
                        "source": "scene.py",
                        "scene": "PortableFrames",
                        "timeline": "timeline.json",
                        "dependencies": [],
                    }
                )
            )
            names = ["scene.py", "timeline.json", "scene.manifest.json"]
            spec = {
                "version": 1,
                "name": "frame-bound",
                "manifest": "scene.manifest.json",
                "render": {"quality": "ql", "fps": 24},
                "files": [
                    {
                        "path": n,
                        "role": "source" if n.endswith(".py") else "config",
                        "license": "MIT; authored fixture",
                    }
                    for n in names
                ],
                "requirements": {
                    "python_min": [3, 12],
                    "python_max": [4, 0],
                    "packages": {},
                    "tools": [],
                    "fonts": [],
                },
            }
            project = producer / "project.json"
            project.write_text(json.dumps(spec))
            bundle = root / "project.zip"
            portable.export_project(project, bundle)
            receiver = root / "receiver"
            handoff = portable.unpack_bundle(bundle, receiver)
            preflight = portable.preflight(receiver)
            self.assertTrue(preflight["ready"], preflight)
            # Only substitute the documented trusted interpreter/launcher slots.
            command = handoff["commands"]["render"]
            command[0] = sys.executable
            command[2] = str(Path(portable.__file__).with_name("launcher.py"))
            result = subprocess.run(
                command, cwd=receiver, capture_output=True, text=True, timeout=90
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            receipt = json.loads(next((receiver / "runs").glob("*/result.json")).read_text())
            self.assertEqual(receipt["status"], "success")
            self.assertEqual(receipt["requested_fps"], 24)
            trace = json.loads(Path(receipt["frame_trace"]["path"]).read_text())
            self.assertEqual(trace["frame_rate"], "24")
            self.assertEqual(trace["frame_count"], 24)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-of", "json", receipt["video"]],
                capture_output=True,
                text=True,
                check=True,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(stream["height"], 480)
            self.assertEqual(stream["avg_frame_rate"], "24/1")
