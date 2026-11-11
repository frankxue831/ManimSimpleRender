"""Strict timeline and failed-production contracts."""

import json
from pathlib import Path
import tempfile
import unittest
from scene_production import digest, load_timeline, render
from app.render.runner import JobResult
from app.render.aggregation import JobOutcome


class ProductionTests(unittest.TestCase):
    def test_render_disables_optional_update_check_without_changing_verbosity(self):
        commands = []

        class Failed:
            def run_job(self, job, command, event, log, env):
                commands.append(command)
                log("render stderr remains available")
                return JobResult(job, 1, JobOutcome.FAILED)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scene.py"
            source.write_text(
                "from manim import Scene\nclass Demo(Scene):\n def construct(self): pass\n"
            )
            timeline = root / "timeline.json"
            timeline.write_text('{"version":1,"segments":[{"label":"intro","start":0,"end":1}]}')
            for traced in (False, True):
                with self.subTest(traced=traced):
                    output = root / str(traced)
                    with self.assertRaisesRegex(RuntimeError, "render failed"):
                        render(
                            source,
                            "Demo",
                            timeline,
                            output,
                            runner=Failed(),
                            record_scene_frames=traced,
                        )
                    self.assertIn("--silent", commands[-1])
                    self.assertNotIn("--verbosity", commands[-1])
                    self.assertIn(
                        "render stderr remains available",
                        next(output.glob("*/render.log")).read_text(),
                    )

    def test_timeline(self):
        base = {"version": 1, "segments": [{"label": "intro", "start": 0, "end": 1}]}
        self.assertEqual(load_timeline(json.dumps(base).encode()), base)
        for value in (True, -1, 0, float("nan"), float("inf")):
            base["segments"][0]["end"] = value
            with self.assertRaises(ValueError):
                load_timeline(json.dumps(base).encode())
        with self.assertRaises(ValueError):
            load_timeline(b'{"version":1,"version":1,"segments":[]}')

    def test_failed_runs_preserved(self):
        class Failed:
            def run_job(self, job, *args):
                return JobResult(job, 1, JobOutcome.FAILED)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scene.py"
            source.write_text(
                "from manim import Scene\nclass Demo(Scene):\n def construct(self): pass\n"
            )
            timeline = root / "timeline.json"
            timeline.write_text('{"version":1,"segments":[{"label":"intro","start":0,"end":1}]}')
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    render(source, "Demo", timeline, root / "out", runner=Failed())
            receipts = list((root / "out").glob("*/result.json"))
            self.assertEqual(len(receipts), 2)
            for receipt in receipts:
                self.assertEqual(json.loads(receipt.read_text())["stage"], "render")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "scene.py"
        self.source.write_text(
            "from manim import Scene\nclass Demo(Scene):\n def construct(self): pass\n"
        )
        self.timeline = self.root / "timeline.json"
        self.timeline.write_text('{"version":1,"segments":[{"label":"intro","start":0,"end":1}]}')
        self.output = self.root / "partial_movie_files" / "out"

    def runner(self, mode="success", fixture=None):
        source = self.source

        class Runner:
            def run_job(self, job, command, event, log, env):
                if mode == "timeout":
                    event.wait(2)
                if mode == "mutate":
                    source.write_text(source.read_text() + "\n# changed")
                media = Path(command[command.index("--media_dir") + 1])
                if mode not in ("missing", "timeout"):
                    path = media / ("partial_movie_files" if mode == "partial" else "videos")
                    path.mkdir(parents=True)
                    (path / "Demo.mp4").write_bytes(
                        fixture.read_bytes() if fixture else b"synthetic video"
                    )
                return JobResult(job, 0, JobOutcome.SUCCESS)

        return Runner()

    def invoke(self, **kwargs):
        return render(self.source, "Demo", self.timeline, self.output, **kwargs)

    def receipt(self):
        return json.loads(next(self.output.glob("*/result.json")).read_text())

    @staticmethod
    def fake_tool(command, timeout, *, cwd=None):
        import struct

        if command[0] == "ffprobe":
            return json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "duration": "1",
                            "avg_frame_rate": "30/1",
                            "width": 16,
                            "height": 16,
                        }
                    ],
                    "format": {"duration": "4"},
                }
            ).encode()
        if command[-1] != "-":
            (Path(cwd or ".") / command[-1]).write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 16, 16)
            )
        return b""

    def test_success_original_resolution_hashes_and_unrelated_ancestor(self):
        from unittest.mock import patch
        from scene_production import digest

        with patch("scene_production.tool", side_effect=self.fake_tool):
            result = self.invoke(runner=self.runner())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["video_duration_seconds"], 1)
        self.assertEqual(result["video_sha256"], digest(result["video"]))
        self.assertEqual(result["source_sha256"], digest(self.source))
        self.assertEqual(result["timeline_sha256"], digest(self.timeline))
        self.assertEqual([f["time"] for f in result["frames"]], [0.1, 0.9])
        for frame in result["frames"]:
            self.assertEqual(frame["sha256"], digest(frame["path"]))

    def test_missing_partial_and_mutated_source_fail_closed(self):
        for mode, stage in (
            ("missing", "artifact"),
            ("partial", "artifact"),
            ("mutate", "provenance"),
        ):
            with self.subTest(mode=mode):
                self.output = self.root / mode
                with self.assertRaises(RuntimeError):
                    self.invoke(runner=self.runner(mode))
                self.assertEqual(self.receipt()["status"], "failed")
                self.assertEqual(self.receipt()["stage"], stage)

    def test_timeout_and_preexisting_cancellation(self):
        import threading

        for cancelled in (False, True):
            self.output = self.root / str(cancelled)
            event = threading.Event()
            if cancelled:
                event.set()
            message = "render cancelled" if cancelled else "render timed out"
            with self.assertRaisesRegex(RuntimeError, message):
                self.invoke(runner=self.runner("timeout"), timeout=0.02, cancel_event=event)
            self.assertEqual(self.receipt()["stage"], "render")
            self.assertEqual(
                self.receipt()["failure_reason"], "cancelled" if cancelled else "render_timeout"
            )

    def test_each_inspection_failure_has_failed_stage(self):
        from unittest.mock import patch
        import subprocess

        for stage in ("probe", "decode", "frames"):
            for failure in (
                FileNotFoundError("missing tool"),
                RuntimeError("nonzero exit"),
                subprocess.TimeoutExpired("tool", 0.01),
            ):
                self.output = self.root / (stage + type(failure).__name__)

                def failing(command, timeout, *, cwd=None):
                    actual = (
                        "probe"
                        if command[0] == "ffprobe"
                        else ("decode" if command[-1] == "-" else "frames")
                    )
                    if actual == stage:
                        raise failure
                    return self.fake_tool(command, timeout, cwd=cwd)

                with self.subTest(stage=stage, failure=type(failure).__name__):
                    with patch("scene_production.tool", side_effect=failing):
                        with self.assertRaises(type(failure)):
                            self.invoke(runner=self.runner())
                    receipt = self.receipt()
                    self.assertEqual(receipt["status"], "failed")
                    self.assertEqual(receipt["stage"], stage)
                    self.assertEqual(receipt["completed_artifact_version"], 1)
                    self.assertEqual(receipt["video_sha256"], digest(receipt["video"]))
                    self.assertNotIn("frames", receipt)
                    self.assertNotIn("probe", receipt)

    def test_wrong_png_dimensions_rejected(self):
        from unittest.mock import patch

        def wrong(command, timeout, *, cwd=None):
            result = self.fake_tool(command, timeout, cwd=cwd)
            if command[0] == "ffmpeg" and command[-1] != "-":
                (Path(cwd or ".") / command[-1]).write_bytes(b"not png")
            return result

        with patch("scene_production.tool", side_effect=wrong):
            with self.assertRaises(RuntimeError):
                self.invoke(runner=self.runner())
        self.assertEqual(self.receipt()["stage"], "frames")

    def test_cli_exit_and_forwarding(self):
        from unittest.mock import patch
        from scene_production.__main__ import main

        argv = [
            "scene_production",
            "--scene-file",
            str(self.source),
            "--scene",
            "Demo",
            "--timeline",
            str(self.timeline),
            "--output-dir",
            str(self.output),
        ]
        with (
            patch("sys.argv", argv),
            patch("builtins.print"),
            patch("scene_production.__main__.render", return_value={"status": "success"}) as call,
        ):
            self.assertEqual(main(), 0)
            self.assertEqual(call.call_args.kwargs["scene"], "Demo")
        with (
            patch("sys.argv", argv),
            patch("builtins.print"),
            patch("scene_production.__main__.render", side_effect=RuntimeError("failed")),
        ):
            self.assertEqual(main(), 1)

    def test_tool_subprocess_failure_contract(self):
        from unittest.mock import patch
        import subprocess
        from scene_production import tool

        for error in (FileNotFoundError(), subprocess.TimeoutExpired("x", 1)):
            with patch("scene_production.subprocess.run", side_effect=error):
                with self.assertRaises(type(error)):
                    tool(["x"], 1)
        with patch(
            "scene_production.subprocess.run",
            return_value=subprocess.CompletedProcess(["x"], 1, b"", b"decode failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "decode failed"):
                tool(["x"], 1)

    def test_real_video_stream_shorter_than_audio_rejected(self):
        import shutil
        import subprocess

        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg and ffprobe required")
        fixture = self.root / "fixture.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=32x32:r=30:d=0.5",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                "1",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                str(fixture),
            ],
            check=True,
            timeout=15,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        with self.assertRaisesRegex(ValueError, "duration disagrees"):
            self.invoke(runner=self.runner(fixture=fixture))
        self.assertEqual(self.receipt()["stage"], "probe")
        # Same real file succeeds when timeline matches video rather than audio.
        self.timeline.write_text('{"version":1,"segments":[{"label":"intro","start":0,"end":0.5}]}')
        result = self.invoke(runner=self.runner(fixture=fixture))
        self.assertEqual(result["status"], "success")
        import struct

        for frame in result["frames"]:
            self.assertEqual(
                struct.unpack(">II", Path(frame["path"]).read_bytes()[16:24]), (32, 32)
            )

    def test_duration_fallback_uses_video_frames(self):
        from unittest.mock import patch
        from scene_production import video_duration

        self.assertEqual(
            video_duration({"duration_ts": 15, "time_base": "1/30"}, "unused", 1),
            (0.5, "stream.duration_ts*time_base"),
        )
        frames = {
            "frames": [
                {"best_effort_timestamp_time": "0", "pkt_duration_time": "0.25"},
                {"best_effort_timestamp_time": "0.25", "pkt_duration_time": "0.25"},
            ]
        }
        with patch("scene_production.tool", return_value=json.dumps(frames).encode()):
            self.assertEqual(video_duration({}, "unused", 1)[0], 0.5)
        with patch("scene_production.tool", return_value=b'{"frames":[]}'):
            with self.assertRaises(ValueError):
                video_duration({}, "unused", 1)

    def test_timeline_rejections_before_run_creation(self):
        import copy

        base = {"version": 1, "segments": [{"label": "intro", "start": 0, "end": 1}]}
        variants = []
        for field, value in (
            ("start", 0.1),
            ("end", 601),
            ("samples", [1]),
            ("samples", [-1]),
            ("samples", [0.5, 0.5]),
            ("samples", [0.7, 0.2]),
            ("samples", [True]),
            ("samples", "wrong"),
            ("extra", 1),
            ("label", ""),
        ):
            data = copy.deepcopy(base)
            data["segments"][0][field] = value
            variants.append(data)
        variants += [
            {"version": True, "segments": base["segments"]},
            {"version": 1, "segments": []},
            {"version": 1, "segments": base["segments"] * 65},
            {"version": 1, "segments": base["segments"] * 2},
        ]
        for data in variants:
            with self.subTest(data=data):
                self.timeline.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    self.invoke(runner=self.runner())
                self.assertFalse(self.output.exists())
        with self.assertRaises(ValueError):
            load_timeline(b" " * 65537)
        base["segments"][0]["samples"] = [i / 300 for i in range(1, 299)]
        with self.assertRaises(ValueError):
            load_timeline(json.dumps(base).encode())
