import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SubtitleDraftTests(unittest.TestCase):
    def run_draft(self, record):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "app.render.subtitle_draft", str(path)],
                capture_output=True,
                text=True,
            )

    def test_export_actual_frame_boundaries_and_text(self):
        result = self.run_draft(
            {
                "fps": 15,
                "duration_frames": 45,
                "events": [
                    {"id": "question", "start_frame": 0, "end_frame": 15, "text": "哪个边界？"},
                    {"id": "answer", "start_frame": 15, "end_frame": 45, "text": "移动 hi。"},
                ],
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "1\n00:00:00,000 --> 00:00:01,000\n哪个边界？\n\n"
            "2\n00:00:01,000 --> 00:00:03,000\n移动 hi。\n",
        )
        self.assertIn("draft", result.stderr.lower())

    def test_rejects_overlap_without_partial_output(self):
        result = self.run_draft(
            {
                "fps": 15,
                "duration_frames": 45,
                "events": [
                    {"id": "a", "start_frame": 0, "end_frame": 30, "text": "甲"},
                    {"id": "b", "start_frame": 15, "end_frame": 45, "text": "乙"},
                ],
            }
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("overlap", result.stderr.lower())


class InvalidRecordsTests(unittest.TestCase):
    def invoke(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "app.render.subtitle_draft", str(path)],
                capture_output=True,
                text=True,
            )

    def test_invalid_input_produces_error_and_no_srt(self):
        good = {"id": "a", "start_frame": 0, "end_frame": 15, "text": "中文 e\u0301"}
        examples = [
            {"fps": 15, "duration_frames": 30},
            {"fps": 15, "duration_frames": 30, "events": []},
            {"fps": 0, "duration_frames": 30, "events": [good]},
            {"fps": 15, "duration_frames": 30, "events": [dict(good, end_frame=31)]},
            {"fps": 15, "duration_frames": 30, "events": [dict(good, text="\u200b")]},
            {"fps": 15, "duration_frames": 30, "events": [dict(good, text="甲\u2028乙")]},
            {
                "fps": 15,
                "duration_frames": 30,
                "events": [good, dict(good, start_frame=15, end_frame=30)],
            },
            {"fps": 10000, "duration_frames": 30, "events": [dict(good, end_frame=1)]},
        ]
        for record in examples:
            with self.subTest(record=record):
                result = self.invoke(record)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("error:", result.stderr)

    def test_gaps_are_allowed_and_fractional_frames_are_rounded(self):
        result = self.invoke(
            {
                "fps": 15,
                "duration_frames": 20,
                "events": [{"id": "a", "start_frame": 1, "end_frame": 2, "text": "中文 e\u0301"}],
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("00:00:00,067 --> 00:00:00,133", result.stdout)
        self.assertIn("中文 e\u0301", result.stdout)


class OutputFileTests(unittest.TestCase):
    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events, output = root / "events.json", root / "draft.srt"
            events.write_text(
                json.dumps(
                    {
                        "fps": 15,
                        "duration_frames": 15,
                        "events": [{"id": "a", "start_frame": 0, "end_frame": 15, "text": "甲"}],
                    }
                )
            )
            output.write_text("old content")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.render.subtitle_draft",
                    str(events),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), "old content")
            self.assertIn("exists", result.stderr.lower())

    def test_new_output_is_created_from_valid_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events, output = root / "events.json", root / "draft.srt"
            events.write_text(
                json.dumps(
                    {
                        "fps": 15,
                        "duration_frames": 15,
                        "events": [{"id": "a", "start_frame": 0, "end_frame": 15, "text": "甲"}],
                    }
                )
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.render.subtitle_draft",
                    str(events),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("甲", output.read_text())
            self.assertEqual(result.stdout, "")


class UnicodeFailureTests(unittest.TestCase):
    def test_invalid_unicode_never_creates_output(self):
        for text in ("\ud800", "\u0301\u0302"):
            with self.subTest(text=repr(text)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                events, output = root / "events.json", root / "out.srt"
                events.write_text(
                    json.dumps(
                        {
                            "fps": 15,
                            "duration_frames": 15,
                            "events": [
                                {"id": "a", "start_frame": 0, "end_frame": 15, "text": text}
                            ],
                        }
                    )
                )
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "app.render.subtitle_draft",
                        str(events),
                        "--output",
                        str(output),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("error", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
