"""Pure diagram layout: named anchors -> manim coordinates, path bow control
points, and a geometry sanity guard. Manim-free so it runs at load time inside
the storyboard loader (and is unit-tested without manim).
"""

FRAME_X = 7.0  # safe half-width  (manim frame is ~7.11 x 4.0)
FRAME_Y = 4.0  # safe half-height
MIN_SEP = 0.9  # minimum center-to-center distance for node/mark anchors

ANCHORS = {
    "center": (0.0, 0.0),
    "left": (-5.0, 0.0),
    "right": (5.0, 0.0),
    "top": (0.0, 3.0),
    "bottom": (0.0, -3.0),
    "upper-left": (-5.0, 2.6),
    "upper-right": (5.0, 2.6),
    "lower-left": (-5.0, -2.6),
    "lower-right": (5.0, -2.6),
    "upper-center": (0.0, 2.6),
    "lower-center": (0.0, -2.6),
    "center-left": (-3.0, 0.0),
    "center-right": (3.0, 0.0),
    "far-left": (-6.2, 0.0),
    "far-right": (6.2, 0.0),
}


class LayoutError(Exception):
    """Raised on an unknown anchor or a failing geometry check."""


def resolve_anchor(name):
    """Return the ``(x, y)`` manim coordinate for a fixed anchor name."""
    try:
        return ANCHORS[name]
    except KeyError:
        raise LayoutError(f"unknown anchor {name!r} (known: {sorted(ANCHORS)})")


def bow_control_points(start, end, bow):
    """Two cubic-bezier control points between ``start`` and ``end``.

    ``bow`` is ``up`` / ``down`` (arch the curve off the midline) or ``none``
    (controls on the straight line).
    """
    sx, sy = start
    ex, ey = end
    one_third = (sx + (ex - sx) / 3.0, sy + (ey - sy) / 3.0)
    two_third = (sx + 2.0 * (ex - sx) / 3.0, sy + 2.0 * (ey - sy) / 3.0)
    if bow == "none":
        return one_third, two_third
    dip = 1.5 if bow == "up" else -1.5
    return (one_third[0], one_third[1] + dip), (two_third[0], two_third[1] + dip)


def check_layout(points):
    """Raise :class:`LayoutError` if any node/mark anchor is off-frame or two
    centers are closer than :data:`MIN_SEP`.

    ``points`` is a list of ``(label, (x, y))``. Deliberately narrow: positions
    and center distances only -- text width, label/arrow crossings, and caption
    collisions are not computable here and are the key-frame review's job.
    """
    for label, (x, y) in points:
        if abs(x) > FRAME_X or abs(y) > FRAME_Y:
            raise LayoutError(
                f"{label} at ({x:.2f}, {y:.2f}) falls outside the safe frame "
                f"(|x|<={FRAME_X}, |y|<={FRAME_Y})"
            )
    for i in range(len(points)):
        la, (ax, ay) = points[i]
        for j in range(i + 1, len(points)):
            lb, (bx, by) = points[j]
            if ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 < MIN_SEP:
                raise LayoutError(f"{la} and {lb} are closer than the {MIN_SEP}-unit minimum")
