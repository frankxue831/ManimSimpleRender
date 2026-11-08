"""Failure isolation and timing semantics for the non-authoritative sidecar."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.render.diagnostics import DiagnosticTimeline


class TimelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "diagnostics.jsonl"

    def read(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_wall_clock_reversal_does_not_change_phase_duration(self):
        with (
            patch("app.render.diagnostics.time.monotonic", side_effect=[10, 11, 11, 14, 14]),
            patch("app.render.diagnostics.utc_timestamp", side_effect=["later", "earlier"]),
            DiagnosticTimeline(self.path, context={"run_token": "example"}) as timeline,
        ):
            with timeline.phase("render"):
                # Records are flushed before control enters the operation.
                self.assertEqual(self.read()[0]["event"], "phase_started")
        start, end = self.read()
        self.assertEqual((start["timestamp"], end["timestamp"]), ("later", "earlier"))
        self.assertEqual(end["duration_seconds"], 3)
        self.assertEqual(end["elapsed_seconds"], 4)
        self.assertEqual(end["run_token"], "example")
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_exception_kept_and_private_details_excluded(self):
        error = RuntimeError("secret /private/scene.py")
        with DiagnosticTimeline(self.path) as timeline:
            with self.assertRaises(RuntimeError) as caught:
                with timeline.phase("render"):
                    raise error
        self.assertIs(caught.exception, error)
        self.assertEqual(self.read()[-1]["exception_type"], "RuntimeError")
        self.assertNotIn("secret", self.path.read_text())
        self.assertIsNone(timeline.current_phase)
        self.assertEqual(timeline.last_phase, "render")

    def test_existing_file_or_symlink_is_never_overwritten(self):
        self.path.write_text("previous")
        target = self.path.with_name("link.jsonl")
        target.symlink_to(self.path)
        for path in (self.path, target):
            with DiagnosticTimeline(path, warn=False) as timeline:
                with timeline.phase("render"):
                    pass
            self.assertTrue(timeline.failed)
        self.assertEqual(self.path.read_text(), "previous")

    def test_disk_failure_warns_once_and_keeps_operation_exception(self):
        class Broken(io.StringIO):
            def write(self, value):
                raise OSError("private mount detail")

        stderr = io.StringIO()
        with DiagnosticTimeline(self.path) as timeline:
            timeline._file.close()
            timeline._file = Broken()
            with patch("sys.stderr", stderr), self.assertRaises(KeyboardInterrupt):
                with timeline.phase("render"):
                    raise KeyboardInterrupt()
            self.assertTrue(timeline.failed)
            self.assertEqual(timeline.failure_type, "OSError")
        self.assertEqual(stderr.getvalue().count("Warning:"), 1)
        self.assertNotIn("private", stderr.getvalue())

    def test_nested_failure_records_both_phases_without_claiming_success(self):
        with DiagnosticTimeline(self.path) as timeline:
            with self.assertRaises(ValueError):
                with timeline.phase("operation"):
                    with timeline.phase("probe"):
                        raise ValueError("private")
        records = self.read()
        self.assertEqual([row["sequence"] for row in records], [1, 2, 3, 4])
        self.assertEqual([row["phase"] for row in records[-2:]], ["probe", "operation"])
        self.assertTrue(all(row["event"] == "phase_failed" for row in records[-2:]))
