"""Raw RGBA identities and observed native sections are strict local evidence."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scene_production.frame_trace import FrameTrace, validate_trace, read_trace


class FrameTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "trace.json"
        self.source_sha = "a" * 64
        self.stream = {
            "width": 2,
            "height": 1,
            "r_frame_rate": "30/1",
            "avg_frame_rate": "30/1",
            "nb_frames": "3",
        }

    def make(self):
        c = FrameTrace("30/1", self.source_sha)
        c.section("first")
        c.add_frame(b"12345678", (1, 2, 4), 2)
        c.section("second")
        c.add_frame(b"87654321", (1, 2, 4), 1)
        d = c.finish()
        self.path.write_text(json.dumps(d))
        return d

    def test_repeat_hashes_and_contiguous_observed_sections(self):
        d = self.make()
        out = validate_trace(d, self.source_sha, self.stream)
        self.assertEqual(out["frame_count"], 3)
        self.assertEqual(out["frames"][:2], [hashlib.sha256(b"12345678").hexdigest()] * 2)
        self.assertEqual(
            [(s["start_frame"], s["end_frame"]) for s in out["sections"]], [(0, 2), (2, 3)]
        )
        self.assertEqual(read_trace(self.path, self.source_sha, self.stream), d)

    def test_invalid_capture_inputs_rejected(self):
        c = FrameTrace("30/1", self.source_sha)
        with self.assertRaisesRegex(ValueError, "section"):
            c.add_frame(b"12345678", (1, 2, 4))
        c.section("first")
        with self.assertRaises(ValueError):
            c.section("second")
        with self.assertRaises(ValueError):
            c.add_frame(b"bad", (1, 2, 4))
        c.add_frame(b"12345678", (1, 2, 4))
        with self.assertRaises(ValueError):
            c.section("first")
        with self.assertRaises(ValueError):
            c.add_frame(b"12345678", (2, 1, 4))

    def test_tampered_identity_count_geometry_order_and_hashes(self):
        d = self.make()
        for edit in [
            lambda x: x.update(source_sha256="b" * 64),
            lambda x: x.update(frame_count=4),
            lambda x: x.update(geometry=[2, 1, 4]),
            lambda x: x.update(geometry=[True, 2, 4]),
            lambda x: x.update(frame_rate=True),
            lambda x: x.update(frame_rate="0/0"),
            lambda x: x["sections"][1].update(start_frame=1),
            lambda x: x["sections"][1].update(id="first"),
            lambda x: x["frames"].__setitem__(0, "bad"),
        ]:
            value = json.loads(json.dumps(d))
            edit(value)
            with self.assertRaises(ValueError):
                validate_trace(value, self.source_sha, self.stream)

    def test_duplicate_json_keys_and_bounded_file(self):
        self.path.write_text('{"version":1,"version":1}')
        with self.assertRaises(ValueError):
            read_trace(self.path, self.source_sha, self.stream)
