"""Explicit target edits, immutable baselines, and whole-interval media checks."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scene_production import revision as rev


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "scene.py"
        self.source.write_text(
            "x=1\n# scene:start intro\na=1\n# scene:end intro\n"
            "# scene:start target\nb=2\n# scene:end target\n"
        )
        self.timeline = self.root / "timeline.json"
        self.timeline.write_text(
            json.dumps(
                {
                    "version": 1,
                    "segments": [
                        {"label": "intro", "start": 0, "end": 1},
                        {"label": "target", "start": 1, "end": 2},
                    ],
                }
            )
        )
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source": "scene.py",
                    "timeline": "timeline.json",
                    "scene": "Demo",
                    "dependencies": [],
                    "brief": {
                        "audience": "developers",
                        "goal": "understand",
                        "sources": ["source"],
                        "pitfalls": ["not automatic"],
                    },
                    "segments": [
                        {"id": i, "captions": ["caption"], "objects": ["object"], "depends_on": []}
                        for i in ["intro", "target"]
                    ],
                }
            )
        )
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "video.mp4").write_bytes(b"video")
        self.receipt = self.make_run(self.run, self.source.read_bytes())

    def make_run(self, run, source):
        (run / "source.py").write_bytes(source)
        (run / "timeline.json").write_bytes(self.timeline.read_bytes())
        d = {
            "status": "success",
            "run_dir": str(run),
            "source": str(self.source),
            "source_sha256": rev.digest(run / "source.py"),
            "timeline_sha256": rev.digest(run / "timeline.json"),
            "video": str(run / "video.mp4"),
            "video_sha256": rev.digest(run / "video.mp4"),
            "command": ["python", "-m", "manim", str(self.source), "Demo"],
            "probe": {
                "streams": [
                    {"codec_type": "video", "avg_frame_rate": "30/1", "width": 16, "height": 16}
                ]
            },
        }
        (run / "result.json").write_text(json.dumps(d))
        return run / "result.json"

    def baseline(self):
        return rev.capture(self.manifest, self.receipt, self.root / "versions")

    def test_target_lookup_apply_stale_and_unexpected_scope(self):
        base = self.baseline()
        with self.assertRaisesRegex(ValueError, "target"):
            rev.locate(base, "missing", "feedback")
        plan = rev.locate(base, "target", "change b")
        self.assertEqual(plan["context"]["objects"], ["object"])
        self.assertIn("b=2", plan["context"]["code"])
        result = rev.apply(plan, {"target": "b=3\n"})
        self.assertIn("b=3", self.source.read_text())
        with self.assertRaisesRegex(ValueError, "stale"):
            rev.apply(plan, {"target": "b=4\n"})
        self.source.write_text(self.source.read_text().replace("a=1", "a=9"))
        with self.assertRaisesRegex(ValueError, "outside"):
            rev.check_scope(base, ["target"])
        self.assertNotEqual(result["before_source_sha256"], result["after_source_sha256"])

    def test_marker_injection_and_changed_dependency_are_rejected(self):
        base = self.baseline()
        plan = rev.locate(base, "target", "feedback")
        with self.assertRaises(ValueError):
            rev.apply(plan, {"target": "# scene:end target\nb=3\n"})
        self.assertIn("b=2", self.source.read_text())
        self.manifest.write_text(self.manifest.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "manifest"):
            rev.locate(base, "target", "feedback")

    def test_temp_file_and_crlf_are_preserved(self):
        self.source.write_bytes(self.source.read_bytes().replace(b"\n", b"\r\n"))
        self.make_run(self.run, self.source.read_bytes())
        base = self.baseline()
        plan = rev.locate(base, "target", "feedback")
        sentinel = self.source.with_suffix(".py.revision-tmp")
        sentinel.write_text("user data")
        rev.apply(plan, {"target": "b=3\r\n"})
        self.assertEqual(sentinel.read_text(), "user data")
        self.assertTrue(self.source.read_bytes().startswith(b"x=1\r\n"))

    def test_changed_preview_is_rejected(self):
        preview = self.run / "preview.png"
        preview.write_bytes(b"original")
        d = json.loads(self.receipt.read_text())
        d["frames"] = [{"path": str(preview), "time": 1.5, "sha256": rev.digest(preview)}]
        self.receipt.write_text(json.dumps(d))
        base = self.baseline()
        preview.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            rev.locate(base, "target", "feedback")

    def test_relative_presentation_timing_is_retained(self):
        from fractions import Fraction
        from types import SimpleNamespace

        raw = "#tb 0: 1/30\n0, 0, 0, 1, 12, abc\n0, 2, 2, 1, 12, def\n"
        with patch.object(rev.subprocess, "run", return_value=SimpleNamespace(stdout=raw)):
            seq = rev.frame_sequence("video", 0, "0.1", Fraction(20))
        self.assertEqual(seq[1], ("1/15", "1/30", "12", "def"))

    def test_nominal_frame_grid_handles_small_container_rounding(self):
        base = self.baseline()
        plan = rev.locate(base, "target", "feedback")
        rev.apply(plan, {"target": "b=3\n"})
        run = self.root / "candidate"
        run.mkdir()
        (run / "video.mp4").write_bytes(b"newvideo")
        receipt = self.make_run(run, self.source.read_bytes())
        for p in [self.receipt, receipt]:
            d = json.loads(p.read_text())
            d["probe"]["streams"][0].update(avg_frame_rate="331776/11059", r_frame_rate="30/1")
            p.write_text(json.dumps(d))
        # Capture a new legitimate baseline after metadata correction, without changing inputs.
        self.source.write_bytes((self.run / "source.py").read_bytes())
        base = self.baseline()
        rev.apply(rev.locate(base, "target", "feedback"), {"target": "b=3\n"})
        with patch.object(rev, "frame_sequence", return_value=["same"] * 30):
            self.assertTrue(rev.verify(base, receipt, ["target"])["non_target_media_equal"])

    def test_video_geometry_change_is_rejected(self):
        base = self.baseline()
        plan = rev.locate(base, "target", "feedback")
        rev.apply(plan, {"target": "b=3\n"})
        run = self.root / "candidate"
        run.mkdir()
        (run / "video.mp4").write_bytes(b"newvideo")
        receipt = self.make_run(run, self.source.read_bytes())
        d = json.loads(receipt.read_text())
        d["probe"]["streams"][0]["width"] = 32
        receipt.write_text(json.dumps(d))
        with patch.object(rev, "frame_sequence", return_value=["same"] * 30):
            with self.assertRaisesRegex(ValueError, "geometry"):
                rev.verify(base, receipt, ["target"])

    def test_declared_downstream_state_requires_acknowledgement(self):
        m = json.loads(self.manifest.read_text())
        m["segments"][1]["depends_on"] = ["intro"]
        self.manifest.write_text(json.dumps(m))
        base = self.baseline()
        plan = rev.locate(base, "intro", "change intro")
        self.assertEqual(plan["dependent_segments"], ["target"])
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            rev.apply(plan, {"intro": "a=4\n"})
        plan = rev.locate(base, "intro", "change intro", acknowledge=["target"])
        rev.apply(plan, {"intro": "a=4\n"})

    def test_wrong_rendered_scene_class_is_rejected(self):
        d = json.loads(self.receipt.read_text())
        d["command"][-1] = "OtherScene"
        self.receipt.write_text(json.dumps(d))
        with self.assertRaisesRegex(ValueError, "Scene class"):
            self.baseline()

    def test_actual_dependency_change_rejected(self):
        helper = self.root / "helper.py"
        helper.write_text("VALUE=1\n")
        m = json.loads(self.manifest.read_text())
        m["dependencies"] = ["helper.py"]
        self.manifest.write_text(json.dumps(m))
        base = self.baseline()
        helper.write_text("VALUE=2\n")
        with self.assertRaisesRegex(ValueError, "dependency"):
            rev.locate(base, "target", "feedback")

    def test_non_target_media_mismatch_and_guarded_restore(self):
        base = self.baseline()
        plan = rev.locate(base, "target", "feedback")
        rev.apply(plan, {"target": "b=3\n"})
        run = self.root / "candidate"
        run.mkdir()
        (run / "video.mp4").write_bytes(b"newvideo")
        receipt = self.make_run(run, self.source.read_bytes())
        with patch.object(rev, "frame_sequence", return_value=["same"] * 30):
            out = rev.verify(base, receipt, ["target"])
        self.assertTrue(out["non_target_media_equal"])
        with patch.object(rev, "frame_sequence", side_effect=[["a"] * 30, ["b"] * 30]):
            with self.assertRaisesRegex(ValueError, "media"):
                rev.verify(base, receipt, ["target"])
        self.source.write_text(self.source.read_text() + "# unsaved\n")
        with self.assertRaisesRegex(ValueError, "current"):
            rev.restore(base, receipt)
        self.source.write_bytes((run / "source.py").read_bytes())
        rev.restore(base, receipt)
        self.assertIn("b=2", self.source.read_text())
        rev.apply(plan, {"target": "b=3\n"})
        self.assertEqual(rev.digest(self.source), rev.digest(run / "source.py"))

    def test_apply_rechecks_inputs_before_replacing_source(self):
        base = self.baseline()
        plan = rev.locate(base, "target", "change")
        original = rev.tempfile.mkstemp

        def concurrent(*args, **kwargs):
            self.source.write_text(self.source.read_text().replace("a=1", "a=999"))
            return original(*args, **kwargs)

        with patch.object(rev.tempfile, "mkstemp", side_effect=concurrent):
            with self.assertRaisesRegex(ValueError, "stale baseline"):
                rev.apply(plan, {"target": "b=5\n"})
        self.assertIn("a=999", self.source.read_text())

    def test_source_marker_order_matches_manifest(self):
        self.source.write_text(
            "# scene:start target\nb=2\n# scene:end target\n"
            "# scene:start intro\na=1\n# scene:end intro\n"
        )
        with self.assertRaisesRegex(ValueError, "order"):
            rev.manifest(self.manifest)

    def test_marker_looking_lines_inside_an_edited_caption_are_not_markers(self):
        base = self.baseline()
        plan = rev.locate(base, "target", "show source markers as caption text")
        body = (
            'caption = """\n# scene:start illustration\n'
            'not a region\n# scene:end illustration\n"""\n'
        )
        rev.apply(plan, {"target": body})
        self.assertEqual(list(rev.regions(self.source.read_text())), ["intro", "target"])
        rev.check_scope(base, ["target"])

    def test_relocated_end_marker_cannot_swallow_protected_source(self):
        self.source.write_text(self.source.read_text() + "# '''\ntail=3\n")
        self.receipt = self.make_run(self.run, self.source.read_bytes())
        base = self.baseline()
        plan = rev.locate(base, "target", "change target only")
        original = self.source.read_bytes()
        body = "b=5\n# scene:end target\nx='''\n"
        with self.assertRaisesRegex(ValueError, "scope|marker"):
            rev.apply(plan, {"target": body})
        self.assertEqual(self.source.read_bytes(), original)
