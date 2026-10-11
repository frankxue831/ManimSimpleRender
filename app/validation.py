"""Pure pre-render validation (no widgets, no Qt).

Extracts the legacy validate_folders + scene-count gate (ui.py:205-210, 237-255)
into a toolkit-independent function that returns data; the controller maps the
result to status/log. Errors accumulate so the user sees every problem at once.
"""

import importlib.util
import os
import shutil
import sys
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    ok: bool
    errors: list = field(default_factory=list)


def validate_render_request(input_folder, output_folder, selected_scenes):
    """Validate a render request. Returns a :class:`ValidationResult`."""
    errors = []

    input_given = bool(input_folder and input_folder.strip())
    output_given = bool(output_folder and output_folder.strip())
    if not input_given or not output_given:
        errors.append("Please select both an input and an output folder.")
    elif not os.path.isdir(input_folder):
        errors.append(f"Input folder does not exist: {input_folder}")
        # Output folder existence is NOT required: the controller creates it
        # (os.makedirs) at render time, surfacing a distinct error if it can't.

    if not selected_scenes:
        errors.append("Please select at least one scene.")

    return ValidationResult(ok=not errors, errors=errors)


def manim_available(find_spec=importlib.util.find_spec, frozen=None, which=shutil.which):
    """Return True if the render's manim invocation will resolve.

    Mirrors :func:`app.render.command_builder.manim_base_command`:
    - Normal: probe the current interpreter (``find_spec``), since the render runs
      ``[sys.executable, '-m', 'manim', ...]``.
    - Frozen app (``sys.frozen``): the render uses an EXTERNAL manim, so probe
      PATH (``which``) instead of the bundled interpreter's importable set.

    Reported up front so a missing manim is surfaced before a worker/subprocess is
    spawned (the runner still maps a late FileNotFoundError to MANIM_NOT_FOUND as a
    backstop). ``find_spec``/``frozen``/``which`` are injectable for testing; a
    raised ImportError/ValueError is treated as "not available".
    """
    if frozen is None:
        frozen = getattr(sys, "frozen", False)
    if frozen:
        return which("manim") is not None
    try:
        return find_spec("manim") is not None
    except (ImportError, ValueError):
        return False
