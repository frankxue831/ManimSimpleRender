"""Explicit partial caption intervals on an integer-fps scene clock.

The author marks both boundaries. This never inspects rendered objects;
scene_production.captions can bind the explicit record to an existing video.
"""

import json
import math
from pathlib import Path

from app.render.subtitle_draft import coverage_stats, render_draft


class CaptionEvents:
    def __init__(self, fps):
        if type(fps) is not int or fps <= 0 or fps > 1000:
            raise ValueError("fps must be an integer between 1 and 1000")
        self.fps = fps
        self.events = []
        self._open = None
        self._ids = set()

    def _frame(self, seconds):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ValueError("scene time must be a finite nonnegative number")
        try:
            scaled = seconds * self.fps
            if not math.isfinite(seconds) or not math.isfinite(scaled) or seconds < 0:
                raise ValueError("scene time must be a finite nonnegative number in frame range")
            frame = round(scaled)
        except OverflowError as exc:
            raise ValueError("scene time is outside the supported frame range") from exc
        if abs(scaled - frame) > 1e-5:
            raise ValueError("scene time must be on a frame boundary")
        return frame

    def start(self, event_id, text, seconds):
        if self._open is not None:
            raise ValueError("an event is already open")
        frame = self._frame(seconds)
        if isinstance(event_id, str) and event_id in self._ids:
            raise ValueError("duplicate event id")
        render_draft(
            {
                "fps": self.fps,
                "duration_frames": 1,
                "events": [{"id": event_id, "start_frame": 0, "end_frame": 1, "text": text}],
            }
        )
        if self.events and frame < self.events[-1]["end_frame"]:
            raise ValueError("events overlap or are out of order")
        self._open = {"id": event_id, "text": text, "start_frame": frame}
        self._ids.add(event_id)

    def end(self, event_id, seconds):
        if self._open is None or event_id != self._open["id"]:
            raise ValueError("end does not match an open event")
        frame = self._frame(seconds)
        if frame <= self._open["start_frame"]:
            raise ValueError("end must be later than start")
        self.events.append({**self._open, "end_frame": frame})
        self._open = None

    def _build_record(self, duration_frames):
        if self._open is not None:
            raise ValueError("event remains open")
        record = {
            "fps": self.fps,
            "duration_frames": duration_frames,
            "events": [dict(event) for event in self.events],
        }
        render_draft(record)
        record["uncovered_frame_ranges"] = coverage_stats(record)["uncovered_frame_ranges"]
        record["notice"] = "Uncovered ranges were not annotated; this does not mean no captions."
        return record

    def stage(self, directory, duration_frames):
        record = self._build_record(duration_frames)
        path = Path(directory) / "events.json"
        with path.open("x", encoding="utf-8", newline="\n") as file:
            json.dump(record, file, ensure_ascii=False, indent=2)
            file.write("\n")
