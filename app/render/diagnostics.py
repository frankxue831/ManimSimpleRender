"""Private, best-effort phase timing. Never a completion or recovery protocol.

Callers supply only fixed labels, correlation IDs and numeric metadata. Do not
pass paths, argv, environment, authorization text, exception messages or raw
subprocess output. This helper does not redact arbitrary caller-supplied text.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def exception_type(exc):
    """Classify errors without serializing custom names or exception messages."""
    for kind in (
        FileNotFoundError,
        FileExistsError,
        PermissionError,
        TimeoutError,
        BrokenPipeError,
        OSError,
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        KeyboardInterrupt,
        SystemExit,
    ):
        if isinstance(exc, kind):
            return kind.__name__
    return "Exception" if isinstance(exc, Exception) else "BaseException"


class DiagnosticTimeline:
    """Append and flush one JSON object per observed event, with a monotonic clock.

    Creation is exclusive and mode 0600 on POSIX. Failures disable this sink and
    never replace an operation's result or exception. No fsync, crash durability,
    liveness, process cleanup or semantic acceptance guarantee is implied.
    Instances are owned by one thread; nested phases are supported.
    """

    def __init__(self, path, *, context=None, warn=True):
        self.path = Path(path)
        self.context = dict(context or {})
        self.failed = False
        self.failure_type = None
        self.current_phase = None
        self.last_phase = None
        self._started = time.monotonic()
        self._sequence = 0
        self._file = None
        self._warn = warn
        self._closed = False
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                self._file = os.fdopen(descriptor, "w", encoding="utf-8")
            except BaseException:
                os.close(descriptor)
                raise
        except (OSError, ValueError) as exc:
            self._disable(exc)

    def _disable(self, exc):
        first = not self.failed
        self.failed = True
        self.failure_type = exception_type(exc)
        if first and self._warn:
            try:
                print("Warning: diagnostic timeline unavailable or incomplete.", file=sys.stderr)
            except (OSError, ValueError):
                pass

    def event(self, event, **fields):
        if self.failed or self._closed:
            return
        self._sequence += 1
        # Reserved envelope keys always describe this writer's observed event.
        value = dict(self.context, **fields)
        value.update(
            schema_version=1,
            sequence=self._sequence,
            event=event,
            timestamp=utc_timestamp(),
            elapsed_seconds=round(max(0, time.monotonic() - self._started), 6),
        )
        try:
            self._file.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
            self._file.flush()
        except (OSError, ValueError, TypeError) as exc:
            self._disable(exc)

    @contextmanager
    def phase(self, name):
        previous = self.current_phase
        self.current_phase = self.last_phase = name
        started = time.monotonic()
        self.event("phase_started", phase=name)
        try:
            yield self
        except BaseException as exc:
            self.event(
                "phase_failed",
                phase=name,
                duration_seconds=round(max(0, time.monotonic() - started), 6),
                exception_type=exception_type(exc),
            )
            raise
        else:
            self.event(
                "phase_finished",
                phase=name,
                duration_seconds=round(max(0, time.monotonic() - started), 6),
            )
        finally:
            self.current_phase = previous

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._file is not None:
            try:
                self._file.close()
            except (OSError, ValueError) as exc:
                self._disable(exc)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
