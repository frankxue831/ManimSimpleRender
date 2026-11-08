"""Qt worker thread that drives the (Qt-free) RenderRunner over a job list.

Thin PyQt5 adapter: iterates an explicit list of RenderJob (no cross-product),
streams status/log/progress as signals, aggregates outcomes, and emits an
accurate final summary. This is the ONLY render-side module that imports Qt.

Replaces legacy render.RenderThread (render.py:5-56): the private ``_is_running``
bool becomes a public ``request_cancel()`` backed by a threading.Event, and the
unconditional success message becomes a RenderSummary terminal message.
"""

import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from PyQt5.QtCore import QThread, Qt, pyqtSignal

from app.render.aggregation import JobOutcome, RenderSummary
from app.render.command_builder import build_command, display_command
from app.render.implementation import write_implementation_manifest
from app.render.runner import RenderRunner

# Coalesce this many streamed lines into one cross-thread log signal so a verbose
# render can't post one queued QMetaCallEvent + repaint per line (BL-29).
_LOG_FLUSH_LINES = 40
_LOG_FLUSH_SECONDS = 0.2


class RenderWorker(QThread):
    status = pyqtSignal(str)
    log = pyqtSignal(str)  # carries a batch of newline-joined lines (BL-29)
    log_file_ready = pyqtSignal(str)
    logging_failed = pyqtSignal(str)
    diagnostics_failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)  # (completed, total)
    job_result = pyqtSignal(object, object, object)  # (RenderJob, JobOutcome, return_code)
    finished_summary = pyqtSignal(object)  # RenderSummary; controller wires finished -> deleteLater

    def __init__(
        self,
        jobs,
        resolution_flag,
        engine_flag,
        output_dir,
        runner=None,
        base_command=None,
        parent=None,
    ):
        super().__init__(parent)
        self._jobs = list(jobs)
        self._resolution_flag = resolution_flag
        self._engine_flag = engine_flag
        self._output_dir = output_dir
        self._runner = runner or RenderRunner()
        # Override the manim argv prefix (defaults to manim_base_command); lets e2e
        # tests drive a fake-manim shim through the real worker + runner path.
        self._base_command = base_command
        self._cancel = threading.Event()
        self._flush_stop = None
        self._flush_thread = None
        # Forced QThread termination bypasses run()'s Python finally. Stop the
        # helper before finished's queued GUI teardown can delete this object.
        self.finished.connect(self._stop_log_flusher, type=Qt.DirectConnection)

    def _stop_log_flusher(self):
        if self._flush_stop is not None:
            self._flush_stop.set()
        thread = self._flush_thread
        if thread is not None and thread.ident is not None:
            thread.join()
        self._flush_thread = None

    def request_cancel(self):
        """Cooperatively cancel an in-flight render (thread-safe). Public API.

        Cancellation flows through a single channel -- the threading.Event the
        runner polls. (The Qt interruption flag is intentionally not used: run()
        never reads isInterruptionRequested(), so setting it would be dead.)
        """
        self._cancel.set()

    def run(self):
        summary = RenderSummary()
        total = len(self._jobs)
        recorded = set()
        active_index = None
        log_file = None
        buffer = []
        lock = threading.RLock()
        stop_flush = threading.Event()
        self._flush_stop = stop_flush
        disk_failed = False
        batch_id = secrets.token_hex(8)
        batch_start = time.monotonic()
        job_starts = {}

        def timestamp():
            return (
                datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            )

        def flush():
            with lock:
                if buffer:
                    self.log.emit("\n".join(buffer))
                    buffer.clear()

        def periodic_flush():
            # QThread.run has no Qt event loop. A bounded, idle helper flushes a
            # sparse subprocess without waiting for its next line or completion.
            while not stop_flush.wait(_LOG_FLUSH_SECONDS):
                # A force-stopped worker may leave its RLock permanently held.
                # Never block helper shutdown on acquiring that abandoned lock.
                if lock.acquire(timeout=_LOG_FLUSH_SECONDS):
                    try:
                        if not stop_flush.is_set():
                            flush()
                    finally:
                        lock.release()

        def logging_failure(operation, error=None):
            nonlocal disk_failed
            with lock:
                if disk_failed:
                    return
                disk_failed = True
                detail = f" ({type(error).__name__})" if error is not None else ""
                warning = (
                    f"Disk logging failed during {operation}{detail}; "
                    "the render log may be incomplete. Rendering continues."
                )
                buffer.append(f"[{timestamp()}] WARNING: {warning}")
                self.logging_failed.emit(warning)
                flush()

        def emit_line(line):
            # Disk copy is uncapped and gets every line (BL-20); the UI signal is
            # coalesced to bound the cross-thread event/repaint rate (BL-29).
            if not isinstance(line, str):
                raise TypeError(f"expected str instance, {type(line).__name__} found")
            stamp = timestamp()
            with lock:
                for part in line.split("\n"):
                    text = f"[{stamp}] {part}"
                    if log_file is not None and not disk_failed:
                        try:
                            log_file.write(text + "\n")
                        except OSError as exc:
                            logging_failure("write", exc)
                    buffer.append(text)
                    if len(buffer) >= _LOG_FLUSH_LINES:
                        flush()

        def event(name, **fields):
            emit_line(
                json.dumps(
                    {
                        "event": name,
                        "batch_id": batch_id,
                        "elapsed_ms": round((time.monotonic() - batch_start) * 1000, 3),
                        **fields,
                    },
                    ensure_ascii=True,
                )
            )

        def record(index, outcome, return_code):
            if index in recorded:
                return
            recorded.add(index)
            job = self._jobs[index]
            try:
                summary.add(outcome)
            except BaseException:
                recorded.remove(index)
                raise
            started = job_starts.get(index)
            event(
                "job_finished",
                job_index=index + 1,
                scene=job.scene_class,
                outcome=outcome.value,
                return_code=return_code,
                started=started is not None,
                duration_ms=(
                    round((time.monotonic() - started) * 1000, 3) if started is not None else None
                ),
            )
            self.job_result.emit(job, outcome, return_code)

        try:
            self.status.emit("Rendering process started...")
            log_file = self._open_log_file()
            if log_file is None:
                logging_failure("open")
            else:
                self.log_file_ready.emit(os.path.abspath(log_file.name))
                manifest_path = Path(log_file.name).with_suffix(".implementation.json")
                if write_implementation_manifest(
                    manifest_path, operation="gui_render", run_id=batch_id
                ):
                    emit_line(f"Implementation manifest: {manifest_path}")
                else:
                    warning = (
                        "Implementation manifest unavailable; runtime identity is unverified. "
                        "Rendering continues."
                    )
                    emit_line("WARNING: " + warning)
                    self.diagnostics_failed.emit(warning)
            event("batch_started", jobs=total)
            self._flush_thread = threading.Thread(
                target=periodic_flush, daemon=True, name=f"msr-log-flush-{batch_id}"
            )
            self._flush_thread.start()
            for index, job in enumerate(self._jobs):
                active_index = index
                if self._cancel.is_set():
                    # Record every remaining (unstarted) job as cancelled so the
                    # terminal summary reflects them instead of silently dropping them.
                    for skipped_index in range(index, total):
                        record(skipped_index, JobOutcome.CANCELLED, None)
                    break
                completed = index + 1
                job_starts[index] = time.monotonic()
                event("job_started", job_index=completed, scene=job.scene_class)
                command = build_command(
                    job,
                    self._resolution_flag,
                    self._output_dir,
                    self._engine_flag,
                    base_command=self._base_command,
                )
                emit_line("$ " + display_command(command))
                flush()
                self.status.emit(
                    f"Rendering {completed}/{total}: {job.scene_class} "
                    f"from {os.path.basename(job.file_path)}..."
                )
                result = self._runner.run_job(job, command, self._cancel, emit_line)
                if result.diagnostic:
                    emit_line(result.diagnostic)
                record(index, result.outcome, result.return_code)
                flush()  # make this job's output visible before the next starts
                self.progress.emit(completed, total)  # after the job actually finished
            flush()
        except BaseException as exc:
            if active_index is not None:
                record(active_index, JobOutcome.FAILED, None)
            for unrecorded_index in range(total):
                record(unrecorded_index, JobOutcome.CANCELLED, None)
            exception_type = type(exc).__name__
            try:
                exception_message = str(exc)
            except BaseException as formatting_error:
                exception_message = f"<message unavailable: {type(formatting_error).__name__}>"
            try:
                diagnostic = (
                    f"Unexpected render worker failure ({exception_type}): {exception_message}"
                )
                emit_line(diagnostic)
            except BaseException:
                pass
        finally:
            self._stop_log_flusher()
            try:
                event(
                    "batch_finished",
                    succeeded=summary.succeeded,
                    failed=summary.failed,
                    cancelled=summary.cancelled,
                    manim_not_found=summary.manim_not_found,
                    cancellation_requested=self._cancel.is_set(),
                )
                flush()
            except BaseException:
                buffer.clear()
            if log_file is not None:
                try:
                    log_file.close()
                except BaseException as exc:
                    logging_failure("close", exc)
            try:
                self.status.emit(summary.terminal_message())
            except BaseException:
                pass
            self.finished_summary.emit(summary)

    def _open_log_file(self):
        """Open a per-render, uncapped log file under the output dir (BL-20).

        Returns None if creation fails; run() reports that to the user without
        failing the render. Private permissions protect raw commands/output."""
        try:
            os.makedirs(self._output_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            return open(
                os.path.join(self._output_dir, f"render-{stamp}-{secrets.token_hex(4)}.log"),
                "x",
                encoding="utf-8",
                errors="replace",
                buffering=1,
                opener=lambda path, flags: os.open(path, flags, 0o600),
            )
        except OSError:
            return None
