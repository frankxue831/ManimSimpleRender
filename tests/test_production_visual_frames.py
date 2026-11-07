"""Caption-linked frame sampling keeps path, identity and evidence boundaries."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scene_production import digest
from scene_production import visual_frames as frames


class FrameSelectionTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "duration_frames": 30,
            "cues": [
                {"start_frame": 0, "end_frame": 10},
                {"start_frame": 20, "end_frame": 30},
            ],
        }

    def test_default_is_one_midpoint_per_explicit_cue(self):
        self.assertEqual(frames._selection(None, self.record), [5, 25])
        self.assertEqual(frames._selection([29, 0], self.record), [0, 29])

    def test_invalid_selection_fails_closed(self):
        for value in ([], [True], [1.0], [-1], [30], [1, 1], list(range(13)), "0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                frames._selection(value, self.record)

    def test_hash_response_is_strict_and_requires_geometry(self):
        for value in (b"", b"#dimensions 0: 16x16\n", b"0,0,0,1,2,bad\n"):
            with self.assertRaises(ValueError):
                frames._hashes(value)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "media tools missing")
class RealFrameSamplingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.run = self.root / "run"
        self.run.mkdir()
        self.sidecar = self.root / "captions"
        self.sidecar.mkdir()
        self.manifest = self.sidecar / "manifest.json"
        self.manifest.write_text("{}")
        video = self.run / "video.mp4"
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
                "testsrc2=s=32x24:r=15:d=1",
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
        for file in (
            self.run / "source.py",
            self.run / "result.json",
            self.sidecar / "events.json",
        ):
            file.write_text("{}")
        self.record = {
            "fps": 15,
            "duration_frames": 15,
            "video": {"path": str(video), "sha256": digest(video)},
            "source": {
                "path": str(self.run / "source.py"),
                "sha256": digest(self.run / "source.py"),
            },
            "origin_receipt": {
                "path": str(self.run / "result.json"),
                "sha256": digest(self.run / "result.json"),
            },
            "events": {
                "path": str(self.sidecar / "events.json"),
                "sha256": digest(self.sidecar / "events.json"),
            },
            "cues": [
                {
                    "id": "a",
                    "start_frame": 0,
                    "end_frame": 7,
                    "text": "Hello",
                    "event_pointer": "/events/0",
                }
            ],
        }

    def export(self, output, selection=None):
        with patch.object(frames, "verify_sidecar", return_value=self.record):
            return frames.export(self.manifest, output, selection)

    def test_real_rgb_identity_event_boundaries_and_percent_path_safety(self):
        output = self.root / "out-%03d"
        mirror = self.root / "out-001"
        mirror.mkdir()
        sentinel = mirror / "frame-001.png"
        sentinel.write_bytes(b"user-owned file")
        result = self.export(output, [0, 6, 7, 14])
        self.assertEqual([f["frame_index"] for f in result["frames"]], [0, 6, 7, 14])
        self.assertEqual([len(f["active_events"]) for f in result["frames"]], [1, 1, 0, 0])
        self.assertEqual(result["frames"][0]["active_events"][0]["event_pointer"], "/events/0")
        self.assertEqual(sentinel.read_bytes(), b"user-owned file")
        self.assertEqual(list(mirror.iterdir()), [sentinel])
        self.assertFalse(result["source_navigation"]["causal_source_mapping"])
        self.assertFalse(result["visual_acceptance"])
        self.assertFalse(result["subtitles_overlaid"])
        saved = json.loads((output / "manifest.json").read_text())
        self.assertEqual(saved, result)

    def test_existing_and_protected_outputs_are_not_modified(self):
        for output in (self.run, self.run / "new", self.sidecar / "new", self.root):
            with self.assertRaises(ValueError):
                self.export(output)
        link = self.root / "broken"
        link.symlink_to(self.root / "missing")
        with self.assertRaises(ValueError):
            self.export(link)
        self.assertTrue(link.is_symlink())

    def test_changed_identity_leaves_no_success_manifest(self):
        changed = deepcopy(self.record)
        changed["video"]["sha256"] = "0" * 64
        output = self.root / "changed"
        with patch.object(frames, "verify_sidecar", side_effect=[self.record, changed]):
            with self.assertRaisesRegex(ValueError, "changed"):
                frames.export(self.manifest, output)
        self.assertFalse((output / "manifest.json").exists())

    def test_duration_and_fps_limits_fail_before_claiming_output(self):
        for fps, count in ((24, 15), (30, 3601)):
            self.record.update(fps=fps, duration_frames=count)
            output = self.root / f"limit-{fps}"
            with self.assertRaises(ValueError):
                self.export(output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
