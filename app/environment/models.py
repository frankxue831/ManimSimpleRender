"""Typed results for the environment preflight (no Qt, no I/O)."""

import enum
from dataclasses import dataclass, field


class CheckStatus(enum.Enum):
    OK = "ok"
    WARNING = "warning"  # not required for every render (e.g. LaTeX); does not fail the report
    FAILED = "failed"  # a render cannot work until this is fixed


@dataclass
class EnvironmentCheck:
    """One named preflight result: a status plus a human-readable detail."""

    name: str
    status: CheckStatus
    detail: str


@dataclass
class EnvironmentReport:
    """Aggregate of the individual checks."""

    checks: list = field(default_factory=list)  # list[EnvironmentCheck]

    @property
    def ok(self):
        """True if nothing FAILED (WARNING is tolerated)."""
        return not any(c.status is CheckStatus.FAILED for c in self.checks)

    def as_text(self):
        """Plain-text report, one line per check, for a dialog/log."""
        symbol = {CheckStatus.OK: "OK", CheckStatus.WARNING: "WARN", CheckStatus.FAILED: "FAIL"}
        return "\n".join(f"[{symbol[c.status]}] {c.name}: {c.detail}" for c in self.checks)

    def issue_text(self):
        """Only the FAILED/WARNING lines (the actionable ones), for always-visible
        display so fix-it guidance is not buried behind "Show Details" (BL-09).
        Empty when every check is OK."""
        symbol = {CheckStatus.WARNING: "WARN", CheckStatus.FAILED: "FAIL"}
        return "\n".join(
            f"[{symbol[c.status]}] {c.name}: {c.detail}"
            for c in self.checks
            if c.status is not CheckStatus.OK
        )
