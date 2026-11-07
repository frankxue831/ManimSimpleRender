"""Image2 never receives a user-controlled directory as its output pattern."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import scene_production as production
import tests.test_scene_production as fixtures


class ProductionImagePathTests(unittest.TestCase):
    def test_frame_output_uses_relative_name_and_explicit_cwd(self):
        fixture = fixtures.PipelineTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.output = fixture.root / "take-%03d" / "out"
        calls = []

        def observe(command, timeout, *, cwd=None):
            calls.append((command, cwd))
            return fixture.fake_tool(command, timeout, cwd=cwd)

        with patch("scene_production.tool", side_effect=observe):
            result = fixture.invoke(runner=fixture.runner())
        images = [(command, cwd) for command, cwd in calls if cwd is not None]
        self.assertEqual(len(images), 2)
        for command, cwd in images:
            self.assertIn("-n", command)
            self.assertEqual(Path(command[-1]).parent, Path("."))
            self.assertNotIn("%", command[-1])
            self.assertEqual(cwd, Path(result["run_dir"]) / "frames")

    def test_tool_forwards_cwd_without_changing_parent_directory(self):
        before = Path.cwd()
        with patch(
            "scene_production.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b"ok", b""),
        ) as run:
            self.assertEqual(production.tool(["ffmpeg"], 5, cwd="isolated"), b"ok")
        self.assertEqual(run.call_args.kwargs["cwd"], "isolated")
        self.assertEqual(Path.cwd(), before)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "media tools missing")
    def test_real_percent_directory_does_not_touch_sibling_sentinel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            run = root / "take-%03d"
            run.mkdir()
            mirror = root / "take-001" / "frames"
            mirror.mkdir(parents=True)
            sentinel = mirror / "000.png"
            sentinel.write_bytes(b"user-owned sentinel")
            video = run / "video.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-n",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=16x16:r=15:d=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(video),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            (run / "source.py").write_text("# retained source\n")
            data = {"version": 1, "segments": [{"label": "one", "start": 0, "end": 1}]}
            (run / "timeline.json").write_text(json.dumps(data))
            receipt = {
                "video_sha256": production.digest(video),
                "source_sha256": production.digest(run / "source.py"),
                "timeline_sha256": production.digest(run / "timeline.json"),
            }
            checked = production._inspect_video(video, data, run, 30, receipt)
            self.assertEqual(len(checked["frames"]), 2)
            self.assertTrue(all(Path(frame["path"]).is_file() for frame in checked["frames"]))
            self.assertEqual(sentinel.read_bytes(), b"user-owned sentinel")
            self.assertEqual(list(mirror.iterdir()), [sentinel])


if __name__ == "__main__":
    unittest.main()
