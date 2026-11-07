"""Export an explicitly recorded frame timeline as an unverified SRT draft.

Run: python -m app.render.subtitle_draft events.json --output draft.srt
This does not inspect a video or infer unrecorded captions.
"""

import argparse
import json
import sys
import unicodedata
from pathlib import Path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key!r}")
        result[key] = value
    return result


def _clock(frame, fps):
    milliseconds = (frame * 1000 + fps // 2) // fps
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def render_draft(record):
    if type(record) is not dict or not {"fps", "duration_frames", "events"} <= set(record):
        raise ValueError("record must contain fps, duration_frames and events")
    if set(record) - {"fps", "duration_frames", "events", "uncovered_frame_ranges", "notice"}:
        raise ValueError("unknown caption record fields")
    fps, duration, events = (record.get(k) for k in ("fps", "duration_frames", "events"))
    if type(fps) is not int or not 1 <= fps <= 1000 or type(duration) is not int or duration <= 0:
        raise ValueError("fps must be an integer in 1..1000 and duration_frames a positive integer")
    if not isinstance(events, list) or not 1 <= len(events) <= 10000:
        raise ValueError("events missing or empty; no subtitle draft generated")
    output = []
    previous_end = 0
    ids = set()
    for i, event in enumerate(events, 1):
        if type(event) is not dict or set(event) != {"id", "start_frame", "end_frame", "text"}:
            raise ValueError(f"event {i}: expected id, start_frame, end_frame and text")
        ident, start, end, text = (event.get(k) for k in ("id", "start_frame", "end_frame", "text"))
        if not isinstance(ident, str) or not ident.strip() or ident in ids:
            raise ValueError(f"event {i}: missing or duplicate id")
        if any(unicodedata.category(c) == "Cs" for c in ident):
            raise ValueError(f"event {i}: invalid id encoding")
        ids.add(ident)
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise ValueError(f"event {ident}: invalid frame bounds")
        if start < previous_end:
            raise ValueError(f"event {ident}: overlap or out-of-order frames")
        if end > duration:
            raise ValueError(f"event {ident}: ends beyond duration_frames")
        if (
            not isinstance(text, str)
            or not any(
                not c.isspace() and unicodedata.category(c)[0] not in ("C", "M") for c in text
            )
            or any(
                c in "\r\n\x85" or unicodedata.category(c) in ("Zl", "Zp", "Cc", "Cs") for c in text
            )
        ):
            raise ValueError(f"event {ident}: text must be one visible line")
        start_time, end_time = _clock(start, fps), _clock(end, fps)
        if start_time == end_time:
            raise ValueError(f"event {ident}: interval is shorter than SRT precision")
        previous_end = end
        output.append(f"{i}\n{start_time} --> {end_time}\n{text}\n")
    coverage = coverage_stats(record)
    if "uncovered_frame_ranges" in record:
        supplied = record["uncovered_frame_ranges"]
        if (
            type(supplied) is not list
            or any(
                type(r) is not list or len(r) != 2 or any(type(v) is not int for v in r)
                for r in supplied
            )
            or supplied != coverage["uncovered_frame_ranges"]
        ):
            raise ValueError("uncovered_frame_ranges disagrees with events")
    if "notice" in record and type(record["notice"]) is not str:
        raise ValueError("notice must be a string")
    return "\n".join(output)


def coverage_stats(record):
    """Compute explicit annotation coverage; caller must first validate the record."""
    missing, cursor, covered = [], 0, 0
    for event in record["events"]:
        start, end = event["start_frame"], event["end_frame"]
        if cursor < start:
            missing.append([cursor, start])
        covered += end - start
        cursor = end
    if cursor < record["duration_frames"]:
        missing.append([cursor, record["duration_frames"]])
    return {"covered_frames": covered, "uncovered_frame_ranges": missing}


def load_events(raw):
    """Parse bounded strict UTF-8 JSON and validate before creating any output."""
    if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > 2 * 1024 * 1024:
        raise ValueError("caption events exceed 2 MiB")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    def reject_constant(value):
        raise ValueError("nonfinite JSON number: " + value)

    record = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=reject_constant)
    render_draft(record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path, help="JSON with fps, duration_frames and events")
    parser.add_argument(
        "--output", type=Path, help="create a new SRT file; existing files are not replaced"
    )
    args = parser.parse_args()
    try:
        with args.events.open("rb") as handle:
            record = load_events(handle.read(2 * 1024 * 1024 + 1))
        draft = render_draft(record)
        if args.output is not None:
            with args.output.open("x", encoding="utf-8", newline="\n") as output:
                output.write(draft)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        print(f"subtitle draft error: {exc}", file=sys.stderr)
        return 2
    print(
        "Draft only: timing and text come from supplied events; "
        "video and coverage are not verified.",
        file=sys.stderr,
    )
    if args.output is None:
        sys.stdout.write(draft)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
