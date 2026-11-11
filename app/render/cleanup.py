"""Bounded owned-session cleanup evidence, independent of operation outcome.

COMPLETE does not certify detached descendants or arbitrary scene side effects.
"""

from enum import Enum

from app.render.aggregation import JobOutcome


class RenderCleanupState(str, Enum):
    NOT_STARTED = "not_started"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


def cleanup_state(value):
    """Accept only explicit enum/string evidence; legacy omissions fail closed."""
    if not isinstance(value, (str, RenderCleanupState)):
        return RenderCleanupState.UNKNOWN
    try:
        return RenderCleanupState(value)
    except ValueError:
        return RenderCleanupState.UNKNOWN


def result_cleanup_state(result):
    """Validate cleanup evidence against the returned render result contract.

    No-start is incompatible with success or any observed render return code.
    Failed setup rollback may instead be COMPLETE with no exposed return code.
    Do not infer complete cleanup from an outcome or code.
    """
    state = cleanup_state(getattr(result, "render_cleanup_state", None))
    if state == RenderCleanupState.NOT_STARTED and (
        getattr(result, "return_code", object()) is not None
        or getattr(result, "outcome", None) not in (JobOutcome.FAILED, JobOutcome.MANIM_NOT_FOUND)
    ):
        return RenderCleanupState.UNKNOWN
    return state


def set_cleanup_state(error, state):
    error.render_cleanup_state = cleanup_state(state)
    return error
