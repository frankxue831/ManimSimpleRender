"""The planner's input record: exactly the source the model is allowed to see.

Ingest is deliberately not agentic -- the model plans from these ranges and
nothing else, so the input to a run is reproducible and its token cost is
bounded. Everything here is plain data; reading and validation live in
ingest.py.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FocusTarget:
    """One cited-able span of a source file, already read off disk."""

    path: str  # repo-relative, spelled the way a cite spells it
    start: int  # 1-based inclusive
    end: int  # 1-based inclusive
    text: str  # exactly lines start..end, no trailing newline
    language: str  # syntax-highlight hint; "" when unknown
    kind: str  # "code" or "prose" -- selects the evidence extractor


@dataclass(frozen=True)
class FactSheet:
    """Everything one planner run may draw on."""

    repo_root: str
    commit: str  # full HEAD sha, stamped into meta.source.commit
    dirty: bool
    topic: str
    targets: tuple  # tuple[FocusTarget, ...]
    beat_budget: int
