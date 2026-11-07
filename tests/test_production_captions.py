"""Caption sidecars bind explicit annotations, never infer screen content."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scene_production import digest


class CaptionBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "source.py").write_text("# historical trusted source\n")
        (self.run / "timeline.json").write_text(
            json.dumps({"version": 1, "segments": [{"label": "demo", "start": 0, "end": 2}]})
        )
        (self.run / "video.mp4").write_bytes(b"fake video; tools are mocked in unit tests")
        self.receipt = {
            "status": "success",
            "run_dir": str(self.run),
            "source": str(self.root / "original.py"),
            "source_sha256": digest(self.run / "source.py"),
            "timeline_sha256": digest(self.run / "timeline.json"),
            "video": str(self.run / "video.mp4"),
            "video_sha256": digest(self.run / "video.mp4"),
        }
        self.origin = self.run / "result.json"
        self.write_receipt()
        self.record = {
            "fps": 15,
            "duration_frames": 30,
            "events": [
                {"id": "first", "start_frame": 3, "end_frame": 12, "text": "作者标记"},
                {"id": "second", "start_frame": 15, "end_frame": 30, "text": "第二句"},
            ],
        }
        self.events = self.root / "input-events.json"
        self.write_events()
        self.probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "avg_frame_rate": "15/1",
                    "r_frame_rate": "15/1",
                    "nb_read_frames": "30",
                    "nb_frames": "30",
                    "width": 16,
                    "height": 16,
                }
            ]
        }

    def write_receipt(self):
        self.origin.write_text(json.dumps(self.receipt))

    def write_events(self):
        self.events.write_text(json.dumps(self.record))

    def bind(self, output=None):
        from scene_production.captions import bind

        with patch("scene_production.captions.tool", return_value=json.dumps(self.probe).encode()):
            return bind(self.origin, self.events, output or self.root / "sidecars")

    def test_success_keeps_original_unchanged_and_snapshots_events(self):
        from scene_production.captions import verify_sidecar

        before = {p.name: p.read_bytes() for p in self.run.iterdir()}
        result = self.bind()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["cues"][0]["event_pointer"], "/events/0")
        self.assertEqual(
            result["coverage"], {"covered_frames": 24, "uncovered_frame_ranges": [[0, 3], [12, 15]]}
        )
        self.assertEqual(result["video"]["sha256"], self.receipt["video_sha256"])
        self.assertIn("author", result["notice"])
        self.events.unlink()
        self.assertEqual(verify_sidecar(result["manifest_path"]), result)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.run.iterdir()})

    def test_repeated_bind_uses_exclusive_directory(self):
        first, second = self.bind(), self.bind()
        self.assertNotEqual(first["manifest_path"], second["manifest_path"])
        self.assertEqual(Path(first["events"]["path"]).read_bytes(), self.events.read_bytes())

    def test_failed_receipt_and_reinspection_are_rejected(self):
        for change in (
            {"status": "failed", "completed_artifact_version": 1},
            {"operation": "reinspect"},
            {"run_dir": str(self.root)},
        ):
            old = copy.deepcopy(self.receipt)
            self.receipt.update(change)
            self.write_receipt()
            with self.assertRaises(ValueError):
                self.bind()
            self.receipt = old

    def test_fps_frame_count_geometry_and_missing_probe_are_rejected(self):
        baseline = copy.deepcopy(self.probe)
        for change in (
            {"avg_frame_rate": "30000/1001"},
            {"r_frame_rate": "30/1"},
            {"nb_read_frames": "29"},
            {"nb_frames": "31"},
            {"nb_read_frames": "N/A"},
            {"width": 0},
        ):
            self.probe = copy.deepcopy(baseline)
            self.probe["streams"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.bind()

    def test_original_mutations_invalidate_binding(self):
        from scene_production.captions import verify_sidecar

        result = self.bind()
        for name in ("result.json", "video.mp4", "source.py", "timeline.json"):
            path = self.run / name
            raw = path.read_bytes()
            path.write_bytes(raw + b" ")
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify_sidecar(result["manifest_path"])
            path.write_bytes(raw)

    def test_sidecar_bytes_and_derived_fields_are_checked(self):
        from scene_production.captions import verify_sidecar

        result = self.bind()
        manifest = Path(result["manifest_path"])
        original = manifest.read_bytes()
        for name in ("events", "srt", "probe"):
            path = Path(result[name]["path"])
            raw = path.read_bytes()
            path.write_bytes(raw + b" ")
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify_sidecar(manifest)
            path.write_bytes(raw)
        for change in (
            {"schema_version": 2},
            {"schema_version": True},
            {"unknown": 1},
            {"fps": 30},
            {"coverage": {}},
            {"cues": []},
            {"width": 1},
        ):
            data = json.loads(original)
            data.update(change)
            manifest.write_text(json.dumps(data))
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_sidecar(manifest)
        manifest.write_bytes(original)

    def test_self_consistent_srt_tampering_is_still_rejected(self):
        from scene_production.captions import verify_sidecar

        result = self.bind()
        srt = Path(result["srt"]["path"])
        srt.write_text("unrelated SRT")
        data = json.loads(Path(result["manifest_path"]).read_text())
        data["srt"]["sha256"] = digest(srt)
        Path(result["manifest_path"]).write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "SRT"):
            verify_sidecar(result["manifest_path"])

    def test_boundaries_duplicates_unknown_fields_and_false_coverage_fail(self):
        good = copy.deepcopy(self.record)
        for change in ({"duration_frames": 29}, {"extra": True}, {"uncovered_frame_ranges": []}):
            self.record = dict(good, **change)
            self.write_events()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.bind()
        self.events.write_text('{"fps":15,"fps":15}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.bind()

    def test_output_inside_origin_and_symlink_escape_are_rejected(self):
        with self.assertRaises(ValueError):
            self.bind(self.run / "sidecars")
        outside = self.root / "external.mp4"
        outside.write_bytes((self.run / "video.mp4").read_bytes())
        (self.run / "video.mp4").unlink()
        (self.run / "video.mp4").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.bind()

    def test_source_mutated_during_probe_is_rejected(self):
        from scene_production.captions import bind

        def mutate(*args):
            (self.run / "source.py").write_text("changed")
            return json.dumps(self.probe).encode()

        with patch("scene_production.captions.tool", side_effect=mutate):
            with self.assertRaises(ValueError):
                bind(self.origin, self.events, self.root / "out")
        self.assertEqual(list((self.root / "out").rglob("manifest.json")), [])

    def test_text_revision_requires_same_video_and_only_one_text(self):
        from scene_production.captions import verify_text_revision

        first = self.bind()
        self.record["events"][0]["text"] = "只改一句"
        self.write_events()
        second = self.bind()
        result = verify_text_revision(first["manifest_path"], second["manifest_path"])
        self.assertEqual(result["changed_event_pointer"], "/events/0/text")
        self.record["events"][1]["text"] = "又改一句"
        self.write_events()
        third = self.bind()
        with self.assertRaises(ValueError):
            verify_text_revision(first["manifest_path"], third["manifest_path"])
        with self.assertRaises(ValueError):
            verify_text_revision(first["manifest_path"], first["manifest_path"])

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required"
    )
    def test_real_video_probe(self):
        from scene_production.captions import bind, verify_sidecar

        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=16x16:r=15:d=2",
                "-c:v",
                "libx264",
                "-y",
                str(self.run / "video.mp4"),
            ],
            check=True,
            timeout=30,
        )
        self.receipt["video_sha256"] = digest(self.run / "video.mp4")
        self.write_receipt()
        result = bind(self.origin, self.events, self.root / "real")
        self.assertEqual(verify_sidecar(result["manifest_path"])["duration_frames"], 30)

    def test_text_revision_rejects_changed_timing_event_ids_and_video(self):
        from scene_production.captions import verify_text_revision

        first = self.bind()
        original_record = copy.deepcopy(self.record)
        for field, value in (("id", "renamed"), ("start_frame", 4)):
            self.record = copy.deepcopy(original_record)
            self.record["events"][0][field] = value
            self.record["events"][0]["text"] = "changed text"
            self.write_events()
            next_result = self.bind()
            with self.assertRaises(ValueError):
                verify_text_revision(first["manifest_path"], next_result["manifest_path"])
        # A second valid origin with new video bytes is not a pure text revision.
        previous_run = self.run
        self.run = self.root / "other-run"
        shutil.copytree(previous_run, self.run)
        self.origin = self.run / "result.json"
        self.receipt["run_dir"] = str(self.run)
        self.receipt["video"] = str(self.run / "video.mp4")
        (self.run / "video.mp4").write_bytes(b"newly encoded video")
        self.receipt["video_sha256"] = digest(self.run / "video.mp4")
        self.write_receipt()
        self.record = original_record
        self.record["events"][0]["text"] = "changed text"
        self.write_events()
        after = self.bind()
        with self.assertRaisesRegex(ValueError, "identity"):
            verify_text_revision(first["manifest_path"], after["manifest_path"])

    def test_current_source_is_not_executed_or_required(self):
        from scene_production.captions import verify_sidecar

        path = Path(self.receipt["source"])
        path.write_text('raise RuntimeError("must not execute current source")')
        first = self.bind()
        path.write_text("modified current source does not replace retained source")
        self.assertEqual(verify_sidecar(first["manifest_path"]), first)

    def test_excessive_event_duration_and_timeout_fail_before_output(self):
        from scene_production.captions import bind

        for timeout in (True, 0, -1, 3601, float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                bind(self.origin, self.events, self.root / "out", timeout)
        self.record["duration_frames"] = 31
        self.write_events()
        with self.assertRaisesRegex(ValueError, "frame count"):
            self.bind()
        self.assertFalse((self.root / "out").exists())

    def test_probe_timeout_never_publishes_success(self):
        from scene_production.captions import bind

        with patch(
            "scene_production.captions.tool", side_effect=subprocess.TimeoutExpired("ffprobe", 1)
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                bind(self.origin, self.events, self.root / "out", 1)
        self.assertFalse((self.root / "out").exists())

    def test_near_limit_event_snapshot_produces_verifiable_manifest(self):
        from scene_production.captions import verify_sidecar

        self.record["events"] = [dict(self.record["events"][0], text="x" * (2 * 1024 * 1024 - 200))]
        self.write_events()
        result = self.bind()
        self.assertEqual(verify_sidecar(result["manifest_path"])["fps"], 15)

    def test_original_frame_trace_is_validated_with_the_same_video(self):
        from scene_production.frame_trace import FrameTrace

        trace = FrameTrace("15", self.receipt["source_sha256"])
        trace.section("first")
        trace.add_frame(b"\x00" * (16 * 16 * 4), (16, 16, 4), 30)
        path = self.run / "frame-trace.json"
        data = trace.finish()
        path.write_text(json.dumps(data))
        self.receipt["frame_trace"] = {"path": str(path), "sha256": digest(path)}
        self.write_receipt()
        self.assertEqual(self.bind()["duration_frames"], 30)
        data["source_sha256"] = "0" * 64
        path.write_text(json.dumps(data))
        self.receipt["frame_trace"]["sha256"] = digest(path)
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, "trace source"):
            self.bind()
