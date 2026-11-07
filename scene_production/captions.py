"""Bind explicit author caption markers to one existing successful production run.

No rendering, OCR, source execution or storyboard edits occur here. Hashes detect
changed bytes, not dishonest producers. Sidecars depend on the retained run.
"""

from fractions import Fraction
import hashlib
import json
from pathlib import Path
import tempfile

from app.render.subtitle_draft import coverage_stats, load_events, render_draft
from . import _verify_file, digest, number, pairs, tool
from .reinspection import _origin

NOTICE = (
    "Explicit author annotations only; video identity, declared integer frame rates and "
    "decoded frame count are checked. Per-frame timestamps and on-screen text are not "
    "verified. Gaps are unannotated, not caption-free. "
    "No storyboard or source changes applied. Hash integrity is not authentication."
)
FIELDS = {
    "schema_version",
    "operation",
    "status",
    "run_dir",
    "origin_receipt",
    "source",
    "timeline",
    "video",
    "events",
    "srt",
    "probe",
    "events_origin",
    "fps",
    "duration_frames",
    "width",
    "height",
    "cues",
    "coverage",
    "notice",
}
LIMIT = 2 * 1024 * 1024


def _read(path, limit=LIMIT):
    with Path(path).open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("caption input exceeds byte limit")
    return raw


def _json(raw):
    def reject(value):
        raise ValueError("nonfinite JSON number: " + value)

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)


def _entry(path, sha=None):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": sha or digest(path)}


def _same(left, right):
    # Unlike Python == this distinguishes bool from int throughout nested data.
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _origin_entries(from_run):
    receipt, sha, old, source, video = _origin(from_run)
    if old["status"] != "success":
        raise ValueError("captions require an original successful render receipt")
    entries = {
        "origin_receipt": _entry(receipt, sha),
        "source": _entry(source, old["source_sha256"]),
        "timeline": _entry(receipt.parent / "timeline.json", old["timeline_sha256"]),
        "video": _entry(video, old["video_sha256"]),
    }
    return entries, old


def _probe_values(probe, record):
    try:
        streams = probe["streams"]
        if type(streams) is not list or len(streams) != 1:
            raise ValueError("expected one selected video stream")
        stream = streams[0]
        if stream["codec_type"] != "video":
            raise ValueError("expected a video stream")
        for key in ("avg_frame_rate", "r_frame_rate"):
            if type(stream[key]) is not str or Fraction(stream[key]) != record["fps"]:
                raise ValueError("video fps disagrees with integer caption fps")
        count = stream["nb_read_frames"]
        if type(count) is not str or not count.isascii() or not count.isdecimal():
            raise ValueError("missing decoded video frame count")
        if int(count) != record["duration_frames"]:
            raise ValueError("video frame count disagrees with duration_frames")
        if "nb_frames" in stream and stream["nb_frames"] != "N/A":
            if str(stream["nb_frames"]) != count:
                raise ValueError("reported and decoded frame counts disagree")
        width, height = stream["width"], stream["height"]
        if any(type(value) is not int or value <= 0 for value in (width, height)):
            raise ValueError("invalid video geometry")
    except (KeyError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError("missing or invalid video probe fields") from exc
    return {"width": width, "height": height}


def _cues(record):
    return [dict(event, event_pointer=f"/events/{i}") for i, event in enumerate(record["events"])]


def _verify(data, root):
    if (
        type(data) is not dict
        or set(data) != FIELDS
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["operation"] != "captions"
        or data["status"] != "success"
        or data["run_dir"] != str(root)
        or data["notice"] != NOTICE
    ):
        raise ValueError("unsupported caption manifest schema")
    for key in ("origin_receipt", "source", "timeline", "video", "events", "srt", "probe"):
        entry = data[key]
        if type(entry) is not dict or set(entry) != {"path", "sha256"}:
            raise ValueError("invalid artifact identity: " + key)
    entries, old = _origin_entries(data["origin_receipt"]["path"])
    for key, expected in entries.items():
        if not _same(data[key], expected):
            raise ValueError("origin identity mismatch: " + key)
    if root.is_relative_to(Path(entries["origin_receipt"]["path"]).parent):
        raise ValueError("caption output must be outside the original run")
    for key, name in (("events", "events.json"), ("srt", "draft.srt"), ("probe", "probe.json")):
        entry = data[key]
        path = _verify_file(entry["path"], root, entry["sha256"])
        if path != root / name:
            raise ValueError("unexpected sidecar artifact path: " + key)
    if type(data["events_origin"]) is not str or not Path(data["events_origin"]).is_absolute():
        raise ValueError("events_origin must be an absolute provenance path")
    record = load_events(_read(data["events"]["path"]))
    if not _same(
        [data["fps"], data["duration_frames"]], [record["fps"], record["duration_frames"]]
    ):
        raise ValueError("manifest timing disagrees with events")
    if not _same(data["cues"], _cues(record)) or not _same(
        data["coverage"], coverage_stats(record)
    ):
        raise ValueError("manifest cues or coverage disagree with events")
    if _read(data["srt"]["path"]) != render_draft(record).encode("utf-8"):
        raise ValueError("SRT disagrees with retained events")
    probe = _json(_read(data["probe"]["path"]))
    geometry = _probe_values(probe, record)
    if not _same([data["width"], data["height"]], [geometry["width"], geometry["height"]]):
        raise ValueError("manifest geometry disagrees with probe")
    if "frame_trace" in old:
        from .frame_trace import read_trace

        read_trace(old["frame_trace"]["path"], old["source_sha256"], probe["streams"][0])
    return data


def verify_sidecar(manifest_path):
    """Recheck all retained bytes and derived fields without executing media tools.

    Verification reuses the recorded probe only for the identical video hash.
    It does not authenticate a producer or prove semantic screen content.
    """
    path = Path(manifest_path).resolve(strict=True)
    if path.name != "manifest.json":
        raise ValueError("use canonical caption sidecar/manifest.json")
    data = _verify(_json(_read(path, 8 * LIMIT)), path.parent)
    return dict(data, manifest_path=str(path))


def bind(from_run, events, output_dir, tool_timeout=30):
    """Create a fresh captions-* sidecar from a successful public run and events."""
    if not 0 < number(tool_timeout) <= 3600:
        raise ValueError("tool_timeout must be in (0, 3600]")
    event_path = Path(events).resolve(strict=True)
    raw = _read(event_path)
    record = load_events(raw)
    entries, old = _origin_entries(from_run)
    root = Path(output_dir).resolve()
    origin_root = Path(entries["origin_receipt"]["path"]).parent
    if root.is_relative_to(origin_root):
        raise ValueError("caption output must be outside the original run")
    probe_raw = tool(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_streams",
            "-of",
            "json",
            entries["video"]["path"],
        ],
        tool_timeout,
    )
    if len(probe_raw) > LIMIT:
        raise ValueError("probe exceeds byte limit")
    geometry = _probe_values(_json(probe_raw), record)
    # Detect source, receipt, timeline or video replacement during probing.
    checked, _ = _origin_entries(from_run)
    if not _same(entries, checked):
        raise ValueError("origin identity changed during caption binding")
    root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="captions-", dir=root))
    for name, content in (
        ("events.json", raw),
        ("draft.srt", render_draft(record).encode("utf-8")),
        ("probe.json", probe_raw),
    ):
        with (run / name).open("xb") as handle:
            handle.write(content)
    data = {
        "schema_version": 1,
        "operation": "captions",
        "status": "success",
        "run_dir": str(run),
        **entries,
        "events": _entry(run / "events.json", hashlib.sha256(raw).hexdigest()),
        "srt": _entry(run / "draft.srt"),
        "probe": _entry(run / "probe.json"),
        "events_origin": str(event_path),
        "fps": record["fps"],
        "duration_frames": record["duration_frames"],
        **geometry,
        "cues": _cues(record),
        "coverage": coverage_stats(record),
        "notice": NOTICE,
    }
    _verify(data, run)
    path = run / "manifest.json"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return dict(data, manifest_path=str(path))


def verify_text_revision(before_manifest, after_manifest):
    """Verify exactly one annotation text edit on the same immutable video bytes.

    This is not visual acceptance of different encodings, and applies no edits.
    """
    before, after = verify_sidecar(before_manifest), verify_sidecar(after_manifest)
    for field in ("video", "source", "timeline", "origin_receipt", "fps", "duration_frames"):
        if not _same(before[field], after[field]):
            raise ValueError("text-only revision requires the same origin and video identity")
    if len(before["cues"]) != len(after["cues"]):
        raise ValueError("text-only revision must preserve the event structure")
    changed = []
    for old, new in zip(before["cues"], after["cues"]):
        if not _same(
            {k: v for k, v in old.items() if k != "text"},
            {k: v for k, v in new.items() if k != "text"},
        ):
            raise ValueError("text-only revision must preserve event timing and structure")
        if old["text"] != new["text"]:
            changed.append(old["event_pointer"] + "/text")
    if len(changed) != 1:
        raise ValueError("text-only revision requires exactly one text change")
    return {
        "status": "success",
        "changed_event_pointer": changed[0],
        "video_sha256": before["video"]["sha256"],
        "notice": "Annotation text comparison only; no video pixels were changed or accepted.",
    }
