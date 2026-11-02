"""Explicit frozen teaching pauses on integer video-frame deadlines.

This does not retime animations or run a wall clock. Dynamic updates are paused
on purpose. Normal full renders only: skipped/cached animation time is not an
emitted-frame counter. The traced renderer provides an authoritative counter.
"""

import math
import re
from fractions import Fraction


def _rate(scene):
    rate = scene.camera.frame_rate
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not math.isfinite(rate)
        or rate != int(rate)
        or not 1 <= rate <= 240
    ):
        raise ValueError("frame clock requires integer frame rate from 1 to 240")
    if scene.renderer.skip_animations:
        raise ValueError("frame clock requires a full non-skipped render")
    return int(rate)


def frame_count(scene):
    rate = _rate(scene)
    observed = getattr(scene.renderer.file_writer, "_scene_production_frame_count", None)
    return observed if observed is not None else int(round(scene.renderer.time * rate))


def mark_section(scene, name):
    if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_-]*", name) is None:
        raise ValueError("section ID must be a stable ASCII identifier")
    count = frame_count(scene)
    scene.next_section(name)
    return count


def hold_until_frame(scene, end_frame):
    rate = _rate(scene)
    if type(end_frame) is not int or end_frame < 0:
        raise ValueError("frame deadline must be a nonnegative integer")
    remaining = end_frame - frame_count(scene)
    if remaining < 0:
        raise ValueError("frame deadline overrun; do not silently shorten a section")
    if remaining:
        # Manim's static wait uses int(duration / dt). Move one ULP upward so
        # an exact integer frame request cannot truncate to n-1 through rounding.
        scene.wait(math.nextafter(remaining / rate, math.inf), frozen_frame=True)
    if frame_count(scene) != end_frame:
        raise RuntimeError("emitted frame deadline mismatch")


def frame_coordinate(seconds, rate):
    """Exact decimal coordinate plus nearest frame and an 8-ULP roundoff bound.

    The bound handles JSON float representation only, never image differences.
    Callers choose the nearest integer only when error <= bound.
    """
    coordinate = Fraction(str(seconds)) * Fraction(rate)
    nearest = round(coordinate)
    error = abs(coordinate - nearest)
    limit = Fraction.from_float(math.ulp(float(coordinate))) * 8
    return coordinate, nearest, error, limit
