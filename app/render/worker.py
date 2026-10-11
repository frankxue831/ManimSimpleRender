"""Qt worker thread that drives the (Qt-free) RenderRunner over a job list.

Thin PyQt5 adapter: iterates an explicit list of RenderJob (no cross-product),
streams status/log/progress as signals, aggregates outcomes, and emits an
accurate final summary. This is the ONLY render-side module that imports Qt.

Replaces legacy render.RenderThread (render.py:5-56): the private ``_is_running``
bool becomes a public ``request_cancel()`` backed by a threading.Event, and the
unconditional success message becomes a RenderSummary terminal message.
"""

import os
import threading
from datetime import datetime

from PyQt5.QtCore import QThread, pyqtSignal

from app.render.aggregation import JobOutcome, RenderSummary
from app.render.command_builder import build_command, display_command
from app.render.runner import RenderRunner

# Coalesce this many streamed lines into one cross-thread log signal so a verbose
# render can't post one queued QMetaCallEvent + repaint per line (BL-29).
_LOG_FLUSH_LINES = 40


class RenderWorker(QThread):
    status = pyqtSignal(str)
    log = pyqtSignal(str)  # carries a batch of newline-joined lines (BL-29)
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

        def flush():
            if buffer:
                self.log.emit("\n".join(buffer))
                buffer.clear()

        def emit_line(line):
            # Disk copy is uncapped and gets every line (BL-20); the UI signal is
            # coalesced to bound the cross-thread event/repaint rate (BL-29).
            if log_file is not None:
                try:
                    log_file.write(line + "\n")
                except OSError:
                    pass
            buffer.append(line)
            if len(buffer) >= _LOG_FLUSH_LINES:
                flush()

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
            self.job_result.emit(job, outcome, return_code)

        try:
            self.status.emit("Rendering process started...")
            log_file = self._open_log_file()
            for index, job in enumerate(self._jobs):
                active_index = index
                if self._cancel.is_set():
                    # Record every remaining (unstarted) job as cancelled so the
                    # terminal summary reflects them instead of silently dropping them.
                    for skipped_index in range(index, total):
                        record(skipped_index, JobOutcome.CANCELLED, None)
                    break
                completed = index + 1
                command = build_command(
                    job,
                    self._resolution_flag,
                    self._output_dir,
                    self._engine_flag,
                    base_command=self._base_command,
                )
                emit_line("$ " + display_command(command))
                self.status.emit(
                    f"Rendering {completed}/{total}: {job.scene_class} "
                    f"from {os.path.basename(job.file_path)}..."
                )
                result = self._runner.run_job(job, command, self._cancel, emit_line)
                if result.diagnostic:
                    emit_line(result.diagnostic)
                flush()  # make this job's output visible before the next starts
                record(index, result.outcome, result.return_code)
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
                buffer.clear()
                emit_line(diagnostic)
            except BaseException:
                pass
        finally:
            try:
                flush()
            except BaseException:
                buffer.clear()
            if log_file is not None:
                try:
                    log_file.close()
                except BaseException:
                    pass
            try:
                self.status.emit(summary.terminal_message())
            except BaseException:
                pass
            self.finished_summary.emit(summary)

    def _open_log_file(self):
        """Open a per-render, uncapped log file under the output dir (BL-20).

        Returns None (logging to disk is skipped) if the file can't be created, so a
        permissions problem never fails the render itself."""
        try:
            os.makedirs(self._output_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            return open(
                os.path.join(self._output_dir, f"render-{stamp}.log"), "w", encoding="utf-8"
            )
        except OSError:
            return None
