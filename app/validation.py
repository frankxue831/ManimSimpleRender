"""Pure pre-render validation (no widgets, no Qt).

Extracts the legacy validate_folders + scene-count gate (ui.py:205-210, 237-255)
into a toolkit-independent function that returns data; the controller maps the
result to status/log. Errors accumulate so the user sees every problem at once.
"""

import os
from dataclasses import dataclass, field

# Re-exported: the probe lives in the render core beside manim_base_command, the
# argv it mirrors, so the explainer can run the same check (explainer/ may import
# only app.render.*). The controller keeps importing it from here.
from app.render.command_builder import manim_available  # noqa: F401


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
