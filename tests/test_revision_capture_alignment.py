"""Opt-in capture rejects already-misaligned baselines without writing a store."""

import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scene_production import revision as rev
from scene_production.frame_trace import FrameTrace
from tests import test_revision_audit as fixtures


class CaptureAlignmentTests(unittest.TestCase):
    setUpBase = fixtures.RevisionAuditTests.setUpBase
    make_run = fixtures.RevisionAuditTests.make_run
    attach = fixtures.RevisionAuditTests.attach
    setUp = fixtures.RevisionAuditTests.setUp

    def capture(self, store=None, **kwargs):
        return rev.capture(self.manifest, self.receipt, store or self.root / "versions", **kwargs)

    def test_aligned_capture_retains_legacy_snapshot_schema(self):
        path = Path(self.capture(require_aligned=True))
        result = json.loads(path.read_text())
        self.assertEqual(result["receipt"], str(self.receipt.resolve()))
        self.assertEqual(
            set(result),
            {
                "version",
                "manifest",
                "source",
                "timeline",
                "files",
                "receipt",
                "receipt_sha256",
                "video_sha256",
                "segments",
                "notice",
            },
        )

    def test_misaligned_native_bounds_refuse_before_creating_store(self):
        self.attach(self.receipt, [29, 31, 30], [b"A", b"B", b"C"])
        with self.assertRaisesRegex(ValueError, "intro: declared.*observed.*fresh canonical"):
            self.capture(require_aligned=True)
        self.assertFalse((self.root / "versions").exists())

    def test_refusal_preserves_existing_store(self):
        store = self.root / "versions"
        store.mkdir()
        sentinel = store / "existing"
        sentinel.write_bytes(b"preserve")
        self.attach(self.receipt, [29, 31, 30], [b"A", b"B", b"C"])
        with self.assertRaisesRegex(ValueError, "inspection windows"):
            self.capture(store, require_aligned=True)
        self.assertEqual(list(store.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_untraced_capture_is_legacy_only(self):
        data = json.loads(self.receipt.read_text())
        del data["frame_trace"]
        self.receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "requires a native frame trace"):
            self.capture(require_aligned=True)
        self.assertFalse((self.root / "versions").exists())
        self.assertTrue(Path(self.capture()).is_file())

    def test_legacy_capture_still_allows_alignment_to_be_reviewed_later(self):
        self.attach(self.receipt, [29, 31, 30], [b"A", b"B", b"C"])
        self.assertTrue(Path(self.capture()).is_file())

    def test_opt_in_keeps_input_and_trace_integrity_checks(self):
        trace = self.run / "frame-trace.json"
        trace.write_text(trace.read_text() + " ")
        with self.assertRaises(ValueError):
            self.capture(require_aligned=True)
        self.assertFalse((self.root / "versions").exists())

    def test_float_representation_tolerance_but_not_fractional_frame_drift(self):
        rate = 24
        data = json.loads(self.receipt.read_text())
        collector = FrameTrace(str(rate), data["source_sha256"])
        for section in ["intro", "target", "tail"]:
            collector.section(section)
            collector.add_frame(b"A" * (16 * 16 * 4), (16, 16, 4), 31)
        trace = self.run / "frame-trace.json"
        trace.write_text(json.dumps(collector.finish()))
        data["frame_trace"]["sha256"] = rev.digest(trace)
        data["probe"]["streams"][0].update(
            nb_frames="93", r_frame_rate="24/1", avg_frame_rate="24/1"
        )
        timeline = json.loads(self.timeline.read_text())
        for i, section in enumerate(timeline["segments"]):
            section.update(start=i * 31 / rate, end=(i + 1) * 31 / rate)
        self.timeline.write_text(json.dumps(timeline))
        (self.run / "timeline.json").write_bytes(self.timeline.read_bytes())
        data["timeline_sha256"] = rev.digest(self.timeline)
        self.receipt.write_text(json.dumps(data))
        self.assertTrue(Path(self.capture(require_aligned=True)).is_file())
        timeline["segments"][-1]["end"] += 0.001 / rate
        self.timeline.write_text(json.dumps(timeline))
        (self.run / "timeline.json").write_bytes(self.timeline.read_bytes())
        data["timeline_sha256"] = rev.digest(self.timeline)
        self.receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "tail: declared"):
            self.capture(self.root / "drifted", require_aligned=True)
        self.assertFalse((self.root / "drifted").exists())

    def test_cli_propagates_flag_and_reports_refusal_without_snapshot(self):
        args = [
            "revision",
            "capture",
            "--manifest",
            str(self.manifest),
            "--run",
            str(self.receipt),
            "--store",
            str(self.root / "versions"),
            "--require-aligned",
        ]
        self.attach(self.receipt, [29, 31, 30], [b"A", b"B", b"C"])
        stderr, stdout = io.StringIO(), io.StringIO()
        with patch("sys.argv", args), contextlib.redirect_stderr(stderr):
            with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
                rev.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("observed", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertFalse((self.root / "versions").exists())
