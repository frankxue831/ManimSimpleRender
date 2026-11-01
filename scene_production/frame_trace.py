"""Strict local pre-encoding RGBA evidence with observed native section bounds."""

from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

from . import pairs

MAX_FRAMES = 144000
MAX_BYTES = 16 * 1024 * 1024
ID = re.compile(r"[a-z][a-z0-9_-]*")
SHA = re.compile(r"[0-9a-f]{64}")
FIELDS = {
    "version",
    "format",
    "source_sha256",
    "frame_rate",
    "geometry",
    "frame_count",
    "frames",
    "sections",
}


def _integer(value):
    return type(value) is int


def _rate(value):
    if not isinstance(value, (str, Fraction)):
        raise ValueError("trace rate must be a rational string")
    try:
        rate = Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError) as e:
        raise ValueError("invalid trace frame rate") from e
    if not 0 < rate <= 240:
        raise ValueError("invalid trace frame rate")
    return rate


class FrameTrace:
    def __init__(self, frame_rate, source_sha256):
        if not isinstance(source_sha256, str) or SHA.fullmatch(source_sha256) is None:
            raise ValueError("invalid source identity")
        rate = _rate(frame_rate)
        self.rate = str(rate)
        self.source_sha256 = source_sha256
        self.frames = []
        self.sections = []
        self.geometry = None

    @property
    def count(self):
        return len(self.frames)

    def section(self, name):
        if not isinstance(name, str) or ID.fullmatch(name) is None or len(name) > 100:
            raise ValueError("invalid native section ID")
        if any(s["id"] == name for s in self.sections):
            raise ValueError("duplicate native section ID")
        if len(self.sections) >= 64:
            raise ValueError("at most 64 native sections")
        if self.sections:
            if self.sections[-1]["start_frame"] == self.count:
                raise ValueError("native section contains no frames")
            self.sections[-1]["end_frame"] = self.count
        self.sections.append({"id": name, "start_frame": self.count, "end_frame": None})

    def add_frame(self, rgba, geometry, num_frames=1):
        if not self.sections:
            raise ValueError("call next_section before writing the first frame")
        if not _integer(num_frames) or num_frames < 1 or self.count + num_frames > MAX_FRAMES:
            raise ValueError("invalid or excessive repeated frame count")
        shape = list(geometry)
        if (
            len(shape) != 3
            or any(not _integer(v) or not 1 <= v <= 8192 for v in shape[:2])
            or shape[2] != 4
        ):
            raise ValueError("trace requires native RGBA geometry")
        if self.geometry is not None and shape != self.geometry:
            raise ValueError("raw frame geometry changed")
        if not isinstance(rgba, bytes) or len(rgba) != shape[0] * shape[1] * 4:
            raise ValueError("raw RGBA byte length disagrees with geometry")
        self.geometry = shape
        self.frames.extend([hashlib.sha256(rgba).hexdigest()] * num_frames)

    def finish(self):
        if not self.sections or self.count == 0 or self.sections[-1]["start_frame"] == self.count:
            raise ValueError("empty native section trace")
        self.sections[-1]["end_frame"] = self.count
        return {
            "version": 1,
            "format": "rgba8",
            "source_sha256": self.source_sha256,
            "frame_rate": self.rate,
            "geometry": self.geometry,
            "frame_count": self.count,
            "frames": list(self.frames),
            "sections": [dict(s) for s in self.sections],
        }


def validate_trace(data, source_sha256, stream):
    if (
        not isinstance(data, dict)
        or set(data) != FIELDS
        or type(data["version"]) is not int
        or data["version"] != 1
        or data["format"] != "rgba8"
    ):
        raise ValueError("unsupported frame trace schema")
    if data["source_sha256"] != source_sha256:
        raise ValueError("trace source identity mismatch")
    count = data["frame_count"]
    if (
        not _integer(count)
        or not 1 <= count <= MAX_FRAMES
        or not isinstance(data["frames"], list)
        or len(data["frames"]) != count
    ):
        raise ValueError("trace frame count mismatch")
    if any(not isinstance(v, str) or SHA.fullmatch(v) is None for v in data["frames"]):
        raise ValueError("invalid raw frame hash")
    shape = data["geometry"]
    if not isinstance(shape, list) or len(shape) != 3 or any(not _integer(v) for v in shape):
        raise ValueError("invalid trace geometry")
    if shape != [stream["height"], stream["width"], 4]:
        raise ValueError("trace geometry mismatch")
    rate = _rate(data["frame_rate"])
    if not 0 < rate <= 240 or rate != Fraction(
        stream.get("r_frame_rate", stream["avg_frame_rate"])
    ):
        raise ValueError("trace frame rate mismatch")
    if str(count) != str(stream.get("nb_frames")):
        raise ValueError("trace does not match recorded video frame count")
    sections = data["sections"]
    seen = set()
    previous = 0
    if not isinstance(sections, list) or not 1 <= len(sections) <= 64:
        raise ValueError("invalid section list")
    for s in sections:
        if not isinstance(s, dict) or set(s) != {"id", "start_frame", "end_frame"}:
            raise ValueError("invalid native section schema")
        name = s["id"]
        start = s["start_frame"]
        end = s["end_frame"]
        if (
            not isinstance(name, str)
            or ID.fullmatch(name) is None
            or len(name) > 100
            or name in seen
        ):
            raise ValueError("invalid or duplicate native section ID")
        if (
            not _integer(start)
            or not _integer(end)
            or start != previous
            or not start < end <= count
        ):
            raise ValueError("native section boundaries are not contiguous")
        seen.add(name)
        previous = end
    if previous != count:
        raise ValueError("native sections do not cover all frames")
    return data


def read_trace(path, source_sha256, stream):
    with Path(path).open("rb") as f:
        raw = f.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("frame trace exceeds 16 MiB")
    return validate_trace(json.loads(raw, object_pairs_hook=pairs), source_sha256, stream)
