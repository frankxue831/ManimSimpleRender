"""Tier B real-render smoke: a genuine manim render through the production path.

Renders a minimal scene with REAL manim + ffmpeg via the production build_command
(default base = manim_base_command) and the real RenderRunner -- no shim -- and
asserts an actual non-empty .mp4 lands at manim's real output path. Gated on manim +
ffmpeg, so it skips cleanly where they are absent. It runs in the per-PR
README quickstart job after installing the full render dependencies, as well as
in the nightly workflow and on a healthy dev machine. Uses a Square + a short
wait, so no LaTeX is required.
Run: .venv/bin/python -m unittest tests.e2e.test_real_render_smoke
"""

import importlib.util
import json
import os
import shutil
import subprocess
import textwrap
import tempfile
import unittest

from app.render.aggregation import JobOutcome
from app.render.command_builder import build_command
from app.render.jobs import RenderJob, build_jobs
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


@unittest.skipIf(
    _MANIM_MISSING
    or _FFMPEG_MISSING
    or importlib.util.find_spec("manim_voiceover") is None
    or shutil.which("ffprobe") is None,
    "voiceover smoke needs manim-voiceover + ffmpeg + ffprobe",
)
class VoiceoverRenderSmokeTests(unittest.TestCase):
    def test_voiceover_render_contains_audio_and_subcaptions_without_network(self):
        # Only speech synthesis is replaced with a local tone. Voiceover timing,
        # Scene.add_sound, file writing, muxing and subtitles all run for real.
        source = textwrap.dedent("""\
            from pathlib import Path
            import math
            import struct
            import wave
            from manim import Square
            from manim_voiceover import VoiceoverScene
            from manim_voiceover.services.base import SpeechService

            class LocalSpeech(SpeechService):
                def generate_from_text(self, text, cache_dir=None, path=None, **kwargs):
                    with wave.open(str(Path(self.cache_dir) / "tone.wav"), "wb") as out:
                        out.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                        out.writeframes(b"".join(
                            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 8000)))
                            for i in range(4000)
                        ))
                    return {"input_text": text, "original_audio": "tone.wav"}

            class Narrated(VoiceoverScene):
                def construct(self):
                    self.set_speech_service(LocalSpeech(cache_dir=Path(__file__).parent / "audio"))
                    self.add(Square())
                    with self.voiceover(text="Local narration compatibility check."):
                        self.wait(.1)
            """)
        with tempfile.TemporaryDirectory() as root:
            scene = os.path.join(root, "narrated.py")
            with open(scene, "w", encoding="utf-8") as handle:
                handle.write(source)
            # Explainer supplies an explicit job for its generated VoiceoverScene.
            command = build_command(
                RenderJob(scene, "Narrated"), "-ql", os.path.join(root, "media")
            )
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            video = output_assertions.media_output_path(
                os.path.join(root, "media"), scene, "Narrated", "480p15"
            )
            output_assertions.assert_nonempty_video(self, video)
            probe = json.loads(
                subprocess.check_output(
                    ["ffprobe", "-v", "error", "-show_streams", "-of", "json", video], timeout=15
                )
            )
            self.assertEqual({s["codec_type"] for s in probe["streams"]}, {"video", "audio"})
            audio = subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    video,
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "8000",
                    "-f",
                    "s16le",
                    "pipe:1",
                ],
                timeout=15,
            )
            self.assertGreater(len(audio), 4000)
            self.assertTrue(any(audio), "rendered audio is silent")
            with open(video.removesuffix(".mp4") + ".srt", encoding="utf-8") as handle:
                self.assertIn("Local narration compatibility check.", handle.read())


if __name__ == "__main__":
    unittest.main()
