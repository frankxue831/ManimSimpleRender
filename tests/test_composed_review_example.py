"""Small assembly helper contracts; mocks do not prove real-media acceptance."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from examples.composed_review import run


class ComposedReviewExampleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_existing_output_is_never_reused(self):
        with patch.object(run.silent_review, "execute") as render:
            with self.assertRaises(FileExistsError):
                run.execute(self.root)
        render.assert_not_called()

    def test_command_failure_has_diagnostic_and_no_retry(self):
        log = self.root / "command.log"
        with patch.object(
            run.subprocess, "run", return_value=subprocess.CompletedProcess([], 3)
        ) as invoke:
            with self.assertRaisesRegex(RuntimeError, "exit 3"):
                run.command(["false"], log)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(invoke.call_args.kwargs["timeout"], 120)
        self.assertTrue(log.exists())

    def test_changed_input_stops_assembly(self):
        source, overlay = self.root / "scene", self.root / "overlay"
        source.write_bytes(b"scene")
        overlay.write_bytes(b"overlay")
        with patch.object(run, "command", side_effect=lambda *args: source.write_bytes(b"changed")):
            with self.assertRaisesRegex(RuntimeError, "input changed"):
                run.compose(source, overlay, self.root / "output", self.root / "log")

    def test_probe_rejects_wrong_frame_count_or_audio(self):
        good = {
            "codec_type": "video",
            "width": 854,
            "height": 480,
            "avg_frame_rate": "15/1",
            "nb_frames": "30",
        }
        for streams in ([dict(good, nb_frames="29")], [good, {"codec_type": "audio"}], []):
            completed = subprocess.CompletedProcess([], 0, stdout=json.dumps({"streams": streams}))
            with patch.object(run.subprocess, "run", return_value=completed):
                with self.assertRaises(ValueError):
                    run.probe("unused")

    def test_fixture_does_not_claim_review_or_acceptance(self):
        record = run.fixture_assessment("assembly.json")
        self.assertEqual(record["decision"], "needs_review")
        self.assertIn("No viewing", record["scope"])

    def test_exclusive_record(self):
        path = self.root / "record.json"
        run.save(path, {"value": 1})
        with self.assertRaises(FileExistsError):
            run.save(path, {"value": 2})
        self.assertEqual(json.loads(path.read_text()), {"value": 1})

    def test_missing_or_frozen_insert_rejected(self):
        moving = [(str(i),) for i in range(15)]
        for wrong in ([("blank",)] * 15, [moving[0]] * 15):
            with patch.object(run, "frame_signatures", side_effect=[moving, wrong]):
                with self.assertRaisesRegex(RuntimeError, "differ from source"):
                    run.verify_insert("source", "final", self.root, "fixture")
        with patch.object(run, "frame_signatures", side_effect=[[("still",)] * 15] * 2):
            with self.assertRaisesRegex(RuntimeError, "visible motion"):
                run.verify_insert("source", "final", self.root, "fixture")
