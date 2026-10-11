"""Thin wrapper over the manim 0.20.1 ``Code`` API (isolates the version-fragile bits).

The ``Code`` constructor was rewritten in manim 0.19; 0.20.1 uses ``code_string=``,
``formatter_style=``, ``add_line_numbers=``, and exposes per-line mobjects as
``code.code_lines[i]`` (a ``Paragraph`` of ``VGroup`` lines). Keeping that here
means a future manim bump touches ONE file, and the generated scene + codegen
never reference the raw Code internals directly. Verified by spike on 0.20.1.
"""

from manim import Code


def build_code(code_string, language="text", formatter_style="monokai", add_line_numbers=False):
    """Construct a 0.20.1 ``Code`` mobject with a window background."""
    return Code(
        code_string=code_string,
        language=language,
        formatter_style=formatter_style,
        add_line_numbers=add_line_numbers,
        background="window",
    )


def code_line(code, one_based_index):
    """Return the highlightable mobject (a ``VGroup``) for a 1-based source line."""
    return code.code_lines[one_based_index - 1]


def code_line_count(code):
    return len(code.code_lines)
