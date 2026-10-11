"""Canonical manim quality ladder shared by the GUI config and the explainer.

ONE source of truth for which quality flags exist and which pixel height each
selects (manim 0.20.1, the pyproject pin). GUI labels stay in config (the one
place that consumes them); the explainer derives a flag from a storyboard
resolution height. Pure stdlib -- this module is part of the Qt-free render
core. See docs/adr/0001-render-core-boundary.md.
"""

# Ordered low -> high: (flag, pixel_height) pairs matching manim 0.20.1.
QUALITY_LADDER = (
    ("-ql", 480),
    ("-qm", 720),
    ("-qh", 1080),
    ("-qp", 1440),
    ("-qk", 2160),
)

_FLAG_BY_HEIGHT = {height: flag for flag, height in QUALITY_LADDER}


def quality_flag_for_height(height, fallback="-qm"):
    """Return the manim quality flag for an integer pixel height.

    Unknown heights return ``fallback`` so callers degrade to a sane render
    instead of erroring (the explainer treats resolution as advisory).
    """
    return _FLAG_BY_HEIGHT.get(height, fallback)


def known_quality_flags():
    """Return the set of valid manim quality flags."""
    return {flag for flag, _ in QUALITY_LADDER}
