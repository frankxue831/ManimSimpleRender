"""Layered evidence uses native frame bounds, not guessed absolute times."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch
from scene_production import revision as rev
from scene_production.frame_trace import FrameTrace
import tests.test_scene_revision as fixtures


class RevisionAuditTests(unittest.TestCase):
    setUpBase = fixtures.RevisionTests.setUp
    make_run = fixtures.RevisionTests.make_run
    baseline = fixtures.RevisionTests.baseline

    # Only the new tests are collected here; inherited fixture helpers are reused.
    def setUp(self):
        self.setUpBase()
        self.source.write_text(
            "x=1\n# scene:start intro\na=1\n# scene:end intro\n"
            "# scene:start target\nb=2\n# scene:end target\n"
            "# scene:start tail\nc=3\n# scene:end tail\n"
        )
        t = {
            "version": 1,
            "segments": [
                {"label": s, "start": i, "end": i + 1}
                for i, s in enumerate(["intro", "target", "tail"])
            ],
        }
        self.timeline.write_text(json.dumps(t))
        m = json.loads(self.manifest.read_text())
        m["segments"].append(
            {"id": "tail", "captions": ["tail"], "objects": ["caption"], "depends_on": []}
        )
        self.manifest.write_text(json.dumps(m))
        self.receipt = self.make_run(self.run, self.source.read_bytes())
        self.attach(self.receipt, [30, 30, 30], [b"A", b"B", b"C"])

    def attach(self, receipt, counts, values):
        d = json.loads(receipt.read_text())
        c = FrameTrace("30/1", d["source_sha256"])
        for name, n, value in zip(["intro", "target", "tail"], counts, values):
            c.section(name)
            c.add_frame(value * (16 * 16 * 4), (16, 16, 4), n)
        p = receipt.parent / "frame-trace.json"
        p.write_text(json.dumps(c.finish()))
        d["frame_trace"] = {"path": str(p), "sha256": rev.digest(p)}
        d["probe"]["streams"][0].update(nb_frames=str(sum(counts)), r_frame_rate="30/1")
        receipt.write_text(json.dumps(d))

    def candidate(self, counts=[30, 30, 30], values=[b"A", b"D", b"C"]):
        base = self.baseline()
        rev.apply(rev.locate(base, "target", "change target"), {"target": "b=5\n"})
        if counts[1] != 30:
            t = json.loads(self.timeline.read_text())
            delta = (counts[1] - 30) / 30
            t["segments"][1]["end"] += delta
            t["segments"][2]["start"] += delta
            t["segments"][2]["end"] += delta
            self.timeline.write_text(json.dumps(t))
        run = self.root / "candidate"
        run.mkdir()
        (run / "video.mp4").write_bytes(b"newvideo")
        p = self.make_run(run, self.source.read_bytes())
        self.attach(p, counts, values)
        return base, p

    def test_target_duration_shift_maps_unchanged_tail_by_native_id(self):
        base, p = self.candidate([30, 45, 30])

        def frames(video, start, end, fps):
            return [("0", "1/30", "size", "same")] * int((end - start) * fps)

        with patch.object(rev, "frame_sequence", side_effect=frames):
            out = rev.audit(base, p, ["target"])
        self.assertEqual(out["status"], "unchanged_non_target")
        self.assertEqual(out["sections"][-1]["before_frames"], [60, 90])
        self.assertEqual(out["sections"][-1]["after_frames"], [75, 105])
        self.assertTrue(out["human_review_required"])
        self.assertEqual(out["baseline"]["snapshot_sha256"], rev.digest(base))
        self.assertEqual(out["candidate"]["receipt_sha256"], rev.digest(p))
        self.assertTrue(out["non_target_source_unchanged"])
        self.assertTrue(out["dependencies_unchanged"])

    def test_raw_change_is_not_waived_by_identical_encoded_mock(self):
        base, p = self.candidate(values=[b"A", b"D", b"X"])
        with patch.object(rev, "frame_sequence", return_value=["same"] * 30):
            out = rev.audit(base, p, ["target"])
        self.assertEqual(out["status"], "needs_review")
        self.assertEqual(out["sections"][-1]["classification"], "render_output_changed")
        self.assertFalse(out["non_target_raw_equal"])

    def test_encoding_only_difference_is_separate_and_not_silently_passed(self):
        base, p = self.candidate()
        with patch.object(
            rev, "frame_sequence", side_effect=[["a"] * 30, ["a"] * 30, ["a"] * 30, ["b"] * 30]
        ):
            out = rev.audit(base, p, ["target"])
        self.assertTrue(out["non_target_raw_equal"])
        self.assertFalse(out["non_target_decoded_equal"])
        self.assertEqual(out["status"], "needs_review")
        self.assertEqual(out["sections"][-1]["classification"], "encoded_output_changed")

    def test_missing_or_wrong_native_ids_and_changed_trace_rejected(self):
        base, p = self.candidate()
        d = json.loads(p.read_text())
        trace = Path(d["frame_trace"]["path"])
        obj = json.loads(trace.read_text())
        obj["sections"][-1]["id"] = "wrong"
        trace.write_text(json.dumps(obj))
        d["frame_trace"]["sha256"] = rev.digest(trace)
        p.write_text(json.dumps(d))
        with self.assertRaisesRegex(ValueError, "ID"):
            rev.audit(base, p, ["target"])

    def test_source_mutation_during_decode_is_rejected(self):
        base, p = self.candidate()

        def frames(*args):
            self.source.write_text(self.source.read_text().replace("b=5", "b=6"))
            return ["same"] * 30

        with patch.object(rev, "frame_sequence", side_effect=frames):
            with self.assertRaisesRegex(ValueError, "current source"):
                rev.audit(base, p, ["target"])

    def test_fractional_second_boundaries_accept_only_float_representation_error(self):
        observed = {
            "frame_rate": "24",
            "sections": [{"id": "x", "start_frame": 0, "end_frame": 31}],
        }
        declared = [{"label": "x", "start": 0, "end": 31 / 24}]
        self.assertTrue(rev._inspection_alignment(declared, observed)[0]["equal"])
        declared[0]["end"] += 0.001 / 24
        self.assertFalse(rev._inspection_alignment(declared, observed)[0]["equal"])
        declared[0]["end"] = 32 / 24
        self.assertFalse(rev._inspection_alignment(declared, observed)[0]["equal"])

    def test_self_consistent_trace_receipt_change_during_decode_is_rejected(self):
        base, p = self.candidate()
        changed = False

        def frames(*args):
            nonlocal changed
            if not changed:
                d = json.loads(p.read_text())
                trace = Path(d["frame_trace"]["path"])
                obj = json.loads(trace.read_text())
                obj["frames"][-1] = "f" * 64
                trace.write_text(json.dumps(obj))
                d["frame_trace"]["sha256"] = rev.digest(trace)
                p.write_text(json.dumps(d))
                changed = True
            return ["same"] * 30

        with patch.object(rev, "frame_sequence", side_effect=frames):
            with self.assertRaisesRegex(ValueError, "changed during"):
                rev.audit(base, p, ["target"])

    def test_native_preview_metadata_rejects_wrong_type_range_or_time(self):
        d = json.loads(self.receipt.read_text())
        for index, native_time in [
            (True, "1/30"),
            (0.5, "1/60"),
            ("1", "1/30"),
            (-1, "-1/30"),
            (90, "3"),
            (1, "1/15"),
            (1, None),
        ]:
            d["frames"] = [{"native_frame_index": index, "native_time": native_time}]
            self.receipt.write_text(json.dumps(d))
            with self.subTest(index=index, native_time=native_time):
                with self.assertRaisesRegex(ValueError, "native preview"):
                    rev.recorded(self.receipt)

    def test_capture_rejects_native_order_that_disagrees_with_source_markers(self):
        d = json.loads(self.receipt.read_text())
        trace = Path(d["frame_trace"]["path"])
        obj = json.loads(trace.read_text())
        obj["sections"][0]["id"] = "target"
        obj["sections"][1]["id"] = "intro"
        trace.write_text(json.dumps(obj))
        d["frame_trace"]["sha256"] = rev.digest(trace)
        self.receipt.write_text(json.dumps(d))
        with self.assertRaisesRegex(ValueError, "native section IDs"):
            rev.capture(self.manifest, self.receipt, self.root / "versions")
