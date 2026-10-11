"""Shared codegen constants and helpers, imported by manim_codegen and
diagram_codegen. Pure string-building; no manim import.
"""

INDENT = "        "  # construct() body indentation (8 spaces)
LABEL_FONT = "Arial"
TEXT_COLOR = "#E6E6E6"
MUTED_COLOR = "#9AA0A6"
SIMPLIFIED_COLOR = "#E2B341"  # amber: on-screen text is a teaching simplification

# Diagram palette: storyboard color name -> hex. Theme roles are resolved by the
# caller (they need the Theme); everything here is fixed.
PALETTE = {
    "blue": "#4C9AFF",
    "red": "#F2553D",
    "green": "#3FB950",
    "yellow": "#F2C94C",
    "purple": "#B26BE0",
    "teal": "#2DD4BF",
    "gold": "#F2C94C",
    "white": "#FFFFFF",
    "grey": "#9AA0A6",
}


def cite_basename(cite):
    """`a/b/c.rs:10-12` -> `c.rs:10-12` for a compact on-screen cite label."""
    cite = cite or ""
    path, sep, lineref = cite.rpartition(":")
    basename = (path if sep else cite).rsplit("/", 1)[-1]
    return f"{basename}:{lineref}" if sep and lineref else (basename or cite)


def _s(text):
    """A safe Python string literal for an authored value (repr handles escaping)."""
    return repr("" if text is None else str(text))


def badge_lines(var, cite_label, badge_text, badge_color, font_size=14):
    """Emit a provenance row VGroup named ``var``: cite + amber 'simplified' +
    a colored verified badge. Shared by the code callout and the diagram beat."""
    return [
        f"{INDENT}{var}_cite = Text({_s(cite_label)}, font={_s(LABEL_FONT)}, "
        f"font_size={font_size}, color={_s(MUTED_COLOR)}, disable_ligatures=True)",
        f"{INDENT}{var}_kind = Text('simplified', font={_s(LABEL_FONT)}, "
        f"font_size={font_size}, color={_s(SIMPLIFIED_COLOR)}, disable_ligatures=True)",
        f"{INDENT}{var}_badge = Text({_s(badge_text)}, font={_s(LABEL_FONT)}, "
        f"font_size={font_size}, color={_s(badge_color)}, disable_ligatures=True)",
        f"{INDENT}{var} = VGroup({var}_cite, {var}_kind, {var}_badge).arrange(RIGHT, buff=0.3)",
    ]
