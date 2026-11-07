import json
import tempfile
import unittest
from pathlib import Path


class CaptionEventAPITests(unittest.TestCase):
    def test_one_event_stages_uncovered_ranges(self):
        from app.render.caption_events import CaptionEvents

        events = CaptionEvents(15)
        events.start("first", "测试中文", 1.0)
        events.end("first", 2.0)
        with tempfile.TemporaryDirectory() as folder:
            events.stage(Path(folder), 45)
            record = json.loads((Path(folder) / "events.json").read_text())
            self.assertEqual(record["events"][0]["start_frame"], 15)
            self.assertEqual(record["events"][0]["end_frame"], 30)
            self.assertEqual(record["uncovered_frame_ranges"], [[0, 15], [30, 45]])
            self.assertFalse((Path(folder) / "draft.srt").exists())

    def test_invalid_state_and_frame_are_rejected(self):
        from app.render.caption_events import CaptionEvents

        events = CaptionEvents(15)
        with self.assertRaisesRegex(ValueError, "frame"):
            events.start("a", "文本", 0.01)
        events.start("a", "文本", 0)
        with self.assertRaisesRegex(ValueError, "open"):
            events.start("b", "文本", 1)
        with self.assertRaisesRegex(ValueError, "match"):
            events.end("b", 1)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "open"):
                events.stage(Path(folder), 30)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_existing_output_is_not_overwritten(self):
        from app.render.caption_events import CaptionEvents

        events = CaptionEvents(15)
        events.start("a", "文本", 0)
        events.end("a", 1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "events.json").write_text("existing")
            with self.assertRaises(FileExistsError):
                events.stage(path, 30)
            self.assertEqual((path / "events.json").read_text(), "existing")
            self.assertFalse((path / "draft.srt").exists())


class ErrorPathTests(unittest.TestCase):
    def test_duplicate_id_overlap_invalid_text_and_bounds(self):
        from app.render.caption_events import CaptionEvents

        events = CaptionEvents(15)
        with self.assertRaises(ValueError):
            events.start("bad", "\u200b", 0)
        events.start("a", "可见", 0)
        with self.assertRaises(ValueError):
            events.end("a", 0)
        events.end("a", 1)
        with self.assertRaises(ValueError):
            events.start("a", "重复", 1)
        with self.assertRaises(ValueError):
            events.start("b", "重叠", 0)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                events.stage(Path(folder), 14)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_empty_or_unsupported_timebase_fails(self):
        from app.render.caption_events import CaptionEvents

        with self.assertRaises(ValueError):
            CaptionEvents(29.97)
        events = CaptionEvents(15)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                events.stage(Path(folder), 30)
            self.assertEqual(list(Path(folder).iterdir()), [])


class StrictCaptionRecordsTests(unittest.TestCase):
    def test_explicit_half_open_intervals_include_adjacent_events(self):
        from app.render.caption_events import CaptionEvents

        events = CaptionEvents(30)
        events.start("one", "第一句", 0)
        events.end("one", 1 / 30)
        events.start("two", "第二句", 1 / 30)
        events.end("two", 2 / 30)
        record = events._build_record(3)
        self.assertEqual(record["uncovered_frame_ranges"], [[2, 3]])
        record["events"][0]["text"] = "local edit"
        self.assertEqual(events.events[0]["text"], "第一句")

    def test_unsupported_and_nonfinite_clock_values_fail(self):
        from app.render.caption_events import CaptionEvents

        for fps in (True, False, 0, -1, 1001, 15.0, "15"):
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                CaptionEvents(fps)
        events = CaptionEvents(15)
        for seconds in (True, -1, float("inf"), float("nan"), 10**400, "0"):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                events.start("one", "text", seconds)

    def test_strict_json_rejects_unknown_fields_and_false_coverage(self):
        from app.render.subtitle_draft import load_events

        base = {
            "fps": 15,
            "duration_frames": 30,
            "events": [{"id": "a", "start_frame": 0, "end_frame": 15, "text": "text"}],
        }
        for change in (
            {"fps": True},
            {"duration_frames": 30.0},
            {"events": []},
            {"uncovered_frame_ranges": [[True, 30]]},
            {"notice": float("nan")},
            {"unknown": 1},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                load_events(json.dumps(dict(base, **change)))
        for raw in ('{"fps":15,"fps":30}', '{"fps": NaN}', " " * (2 * 1024 * 1024 + 1)):
            with self.assertRaises(ValueError):
                load_events(raw)

    def test_input_is_utf8_not_implicitly_detected_utf16(self):
        from app.render.subtitle_draft import load_events

        record = {
            "fps": 15,
            "duration_frames": 15,
            "events": [{"id": "a", "start_frame": 0, "end_frame": 15, "text": "text"}],
        }
        with self.assertRaises((ValueError, UnicodeError)):
            load_events(json.dumps(record).encode("utf-16"))


if __name__ == "__main__":
    unittest.main()
