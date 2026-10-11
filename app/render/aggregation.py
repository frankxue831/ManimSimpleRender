"""Render-outcome bookkeeping (no Qt).

Replaces the legacy unconditional "All rendering tasks completed!" (render.py:53-54),
which ignored per-job failures, with an accurate tally and terminal message.
"""

import enum
from dataclasses import dataclass


class JobOutcome(enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MANIM_NOT_FOUND = "manim_not_found"


@dataclass
class RenderSummary:
    """Accumulates per-job outcomes and renders an accurate terminal message."""

    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    manim_not_found: int = 0

    _FIELD_BY_OUTCOME = {
        JobOutcome.SUCCESS: "succeeded",
        JobOutcome.FAILED: "failed",
        JobOutcome.CANCELLED: "cancelled",
        JobOutcome.MANIM_NOT_FOUND: "manim_not_found",
    }

    def add(self, outcome):
        field_name = self._FIELD_BY_OUTCOME.get(outcome)
        if field_name is None:
            raise ValueError(f"Unknown outcome: {outcome!r}")
        setattr(self, field_name, getattr(self, field_name) + 1)

    @property
    def total(self):
        return self.succeeded + self.failed + self.cancelled + self.manim_not_found

    def terminal_message(self):
        if self.total == 0:
            return "No render jobs were scheduled."
        # Lead with the actionable cause when manim could not start, instead of
        # burying it behind two zero counts (BL-27).
        if self.manim_not_found == self.total:
            return f"manim not found: {self.manim_not_found} job(s) could not start"
        parts = []
        if self.manim_not_found:
            parts.append(f"{self.manim_not_found} could not start (manim not found)")
        parts.extend([f"{self.succeeded} succeeded", f"{self.failed} failed"])
        if self.cancelled:
            parts.append(f"{self.cancelled} not completed")
            return "Cancelled: " + ", ".join(parts)
        return ", ".join(parts)
