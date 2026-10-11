"""Qt-free subprocess execution for a single render job.

Fixes the legacy hazards (render.py:73-122):
- merges stderr into stdout (``stderr=STDOUT``) and drains via a reader thread,
  so a full pipe buffer can never deadlock the read-stdout-then-stderr pattern;
- polls ``cancel_event`` on a short interval (not only between stdout lines), so
  a child that is silent on the stream is still cancellable promptly;
- escalates terminate -> wait(timeout) -> kill;
- maps FileNotFoundError to a distinct MANIM_NOT_FOUND outcome.

Pure stdlib (subprocess, threading, queue) - NO Qt import, so it is unit-testable
headless and reusable outside the GUI.
"""

from dataclasses import dataclass

from app.render.aggregation import JobOutcome
from app.render.process_session import _PostSpawnSetupError, _ProcessSession

__all__ = [
    "DEFAULT_DRAIN_TIMEOUT",
    "DEFAULT_SUPERVISION_CLEANUP_TIMEOUT",
    "DEFAULT_TERMINATE_TIMEOUT",
    "JobResult",
    "RenderRunner",
]

# Default terminate->kill grace window (seconds). Exported so the GUI can derive
# its shutdown wait budget from it instead of a fragile magic constant (BL-24).
DEFAULT_TERMINATE_TIMEOUT = 5.0
DEFAULT_DRAIN_TIMEOUT = 1.0

# Worst-case cooperative cleanup includes one bounded emergency retry when a
# late BaseException interrupts output delivery after the normal stop/reap
# phases. Windows has the longest taskkill/release/force/wait sequence; the
# extra drain allowance keeps the bound conservative across every callback site.
DEFAULT_SUPERVISION_CLEANUP_TIMEOUT = 10 * DEFAULT_TERMINATE_TIMEOUT + 3 * DEFAULT_DRAIN_TIMEOUT


@dataclass
class JobResult:
    job: object  # RenderJob
    return_code: object  # int, or None when the binary was not found
    outcome: JobOutcome
    diagnostic: str = ""


class RenderRunner:
    def __init__(
        self,
        terminate_timeout=DEFAULT_TERMINATE_TIMEOUT,
        drain_timeout=DEFAULT_DRAIN_TIMEOUT,
    ):
        self._terminate_timeout = terminate_timeout
        self._drain_timeout = drain_timeout

    def run_job(self, job, command, cancel_event, on_log, env=None):
        """Run ``command`` for ``job``; stream lines to ``on_log``; honor ``cancel_event``."""
        try:
            session = _ProcessSession.start(
                command,
                env=env,
                terminate_timeout=self._terminate_timeout,
                drain_timeout=self._drain_timeout,
            )
        except FileNotFoundError as exc:
            return JobResult(
                job, None, JobOutcome.MANIM_NOT_FOUND, f"render executable not found: {exc}"
            )
        except _PostSpawnSetupError as exc:
            return JobResult(job, None, JobOutcome.FAILED, f"post-spawn setup failed: {exc}")
        except Exception as exc:
            return JobResult(job, None, JobOutcome.FAILED, f"could not start render: {exc}")

        try:
            return_code, cancelled, diagnostic = session.finish(cancel_event, on_log)
        except Exception as exc:
            return JobResult(job, None, JobOutcome.FAILED, f"process supervision failed: {exc}")

        if diagnostic:
            outcome = JobOutcome.FAILED
        elif cancelled:
            outcome = JobOutcome.CANCELLED
        elif return_code == 0:
            outcome = JobOutcome.SUCCESS
        else:
            outcome = JobOutcome.FAILED
        return JobResult(job, return_code, outcome, diagnostic)
