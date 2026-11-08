"""Tests for app.render.worker.RenderWorker.run().

The controller suite injects a FakeWorker whose start() is a no-op, so run() --
which owns 1-based progress, the per-job "$ command" log line, the cancel-before-
next-job break, and the RenderSummary tally -- had zero coverage. Here run() is
called directly (synchronously, no QThread start) with an injected fake runner,
so no real subprocess or thread is involved.
"""

import glob
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from app.render.aggregation import JobOutcome  # noqa: E402
from app.render.jobs import RenderJob  # noqa: E402
from app.render.runner import JobResult  # noqa: E402
from app.render.worker import RenderWorker  # noqa: E402

_app = QApplication.instance() or QApplication([])


class FakeRunner:
    """Records run_job calls and returns configurable outcomes; can trip cancel."""

    def __init__(self, outcomes=None, cancel_after=None):
        self.outcomes = list(outcomes) if outcomes else []
        self.cancel_after = cancel_after
        self.calls = []

    def run_job(self, job, command, cancel_event, on_log):
        self.calls.append((job, command))
        on_log("line for " + job.scene_class)
        index = len(self.calls) - 1
        outcome = self.outcomes[index] if index < len(self.outcomes) else JobOutcome.SUCCESS
        if self.cancel_after is not None and len(self.calls) >= self.cancel_after:
            cancel_event.set()
        code = 0 if outcome == JobOutcome.SUCCESS else 1
        return JobResult(job=job, return_code=code, outcome=outcome)


class BaseExceptionRunner:
    def run_job(self, job, command, cancel_event, on_log):
        raise KeyboardInterrupt("unexpected runner boundary failure")


class InvalidOutcomeRunner:
    def run_job(self, job, command, cancel_event, on_log):
        return JobResult(job=job, return_code=None, outcome="invalid")


def _worker(jobs, runner):
    return RenderWorker(jobs, "-ql", "", "/tmp/out", runner=runner)


class RenderWorkerRunTests(unittest.TestCase):
    def test_baseexception_is_fenced_and_every_job_is_recorded_once(self):
        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B"), RenderJob("c.py", "C")]
        worker = _worker(jobs, BaseExceptionRunner())
        results, summaries, logs = [], [], []
        worker.job_result.connect(lambda job, outcome, code: results.append((job, outcome, code)))
        worker.finished_summary.connect(summaries.append)
        worker.log.connect(logs.append)
        worker.run()
        self.assertEqual([row[0] for row in results], jobs)
        self.assertEqual(
            [row[1] for row in results],
            [JobOutcome.FAILED, JobOutcome.CANCELLED, JobOutcome.CANCELLED],
        )
        self.assertEqual(len(summaries), 1)
        self.assertEqual((summaries[0].failed, summaries[0].cancelled), (1, 2))
        self.assertIn("KeyboardInterrupt", "\n".join(logs))

    def test_invalid_outcome_is_replaced_with_terminal_results(self):
        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B")]
        worker = _worker(jobs, InvalidOutcomeRunner())
        results, summaries = [], []
        worker.job_result.connect(lambda job, outcome, code: results.append((job, outcome, code)))
        worker.finished_summary.connect(summaries.append)
        worker.run()
        self.assertEqual([row[0] for row in results], jobs)
        self.assertEqual([row[1] for row in results], [JobOutcome.FAILED, JobOutcome.CANCELLED])
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].total, 2)

    def test_unprintable_baseexception_is_fenced(self):
        class UnprintableError(BaseException):
            def __str__(self):
                raise RuntimeError("string conversion failed")

        class UnprintableRunner:
            def run_job(self, job, command, cancel_event, on_log):
                raise UnprintableError()

        worker = _worker([RenderJob("a.py", "A")], UnprintableRunner())
        results, summaries, logs = [], [], []
        worker.job_result.connect(lambda job, outcome, code: results.append(outcome))
        worker.finished_summary.connect(summaries.append)
        worker.log.connect(logs.append)
        worker.run()
        self.assertEqual(results, [JobOutcome.FAILED])
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].total, 1)
        self.assertIn("UnprintableError", "\n".join(logs))

    def test_bad_buffered_log_line_cannot_suppress_terminal_summary(self):
        class BadLogRunner:
            def run_job(self, job, command, cancel_event, on_log):
                on_log(None)
                return JobResult(job=job, return_code=0, outcome=JobOutcome.SUCCESS)

        class NoDiskLogWorker(RenderWorker):
            def _open_log_file(self):
                return None

        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B")]
        worker = NoDiskLogWorker(jobs, "-ql", "", "/tmp/out", runner=BadLogRunner())
        results, summaries, logs = [], [], []
        worker.job_result.connect(lambda job, outcome, code: results.append((job, outcome)))
        worker.finished_summary.connect(summaries.append)
        worker.log.connect(logs.append)
        worker.run()
        self.assertEqual([row[0] for row in results], jobs)
        self.assertEqual([row[1] for row in results], [JobOutcome.FAILED, JobOutcome.CANCELLED])
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].total, 2)
        diagnostic = "\n".join(logs)
        self.assertIn("TypeError", diagnostic)
        self.assertIn("expected str instance, NoneType found", diagnostic)

    def test_duplicate_valued_jobs_are_recorded_by_batch_position(self):
        duplicate = RenderJob("same.py", "Same")
        jobs = [duplicate, duplicate]
        worker = _worker(jobs, FakeRunner())
        worker.request_cancel()
        results = []
        worker.job_result.connect(lambda job, outcome, code: results.append((job, outcome)))
        worker.run()
        self.assertEqual(results, [(duplicate, JobOutcome.CANCELLED)] * 2)

    def test_progress_is_one_based_over_total(self):
        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B")]
        worker = _worker(jobs, FakeRunner())
        events = []
        worker.progress.connect(lambda c, t: events.append((c, t)))
        worker.run()
        self.assertEqual(events, [(1, 2), (2, 2)])

    def test_emits_command_log_line_per_job(self):
        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B")]
        worker = _worker(jobs, FakeRunner())
        logs = []
        worker.log.connect(logs.append)
        worker.run()
        cmd_lines = [line for batch in logs for line in batch.splitlines() if "] $ " in line]
        self.assertEqual(len(cmd_lines), 2)
        self.assertIn("A", cmd_lines[0])
        self.assertIn("-ql", cmd_lines[0])

    def test_summary_tallies_mixed_outcomes(self):
        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B"), RenderJob("c.py", "C")]
        runner = FakeRunner(
            outcomes=[JobOutcome.SUCCESS, JobOutcome.FAILED, JobOutcome.MANIM_NOT_FOUND]
        )
        worker = _worker(jobs, runner)
        summaries = []
        worker.finished_summary.connect(summaries.append)
        worker.run()
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual((summary.succeeded, summary.failed, summary.manim_not_found), (1, 1, 1))

    def test_cancel_before_next_job_stops_remaining(self):
        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B"), RenderJob("c.py", "C")]
        runner = FakeRunner(cancel_after=1)
        worker = _worker(jobs, runner)
        worker.run()
        self.assertEqual(len(runner.calls), 1)  # cancel tripped after job 1 -> 2 and 3 skipped

    def test_cancel_before_first_job_records_all_as_cancelled(self):
        # Cancel already set when run() starts: every job is skipped, but the
        # summary must reflect them as cancelled (not "No render jobs scheduled").
        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B")]
        runner = FakeRunner()
        worker = _worker(jobs, runner)
        worker.request_cancel()
        outcomes = []
        worker.job_result.connect(lambda job, outcome, code: outcomes.append(outcome))
        summaries = []
        worker.finished_summary.connect(summaries.append)
        worker.run()
        self.assertEqual(len(runner.calls), 0)  # nothing actually ran
        self.assertEqual(summaries[0].cancelled, 2)  # both recorded cancelled
        self.assertEqual(outcomes, [JobOutcome.CANCELLED, JobOutcome.CANCELLED])
        self.assertIn("Cancelled", summaries[0].terminal_message())

    def test_cancel_after_first_job_records_remaining_as_cancelled(self):
        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B"), RenderJob("c.py", "C")]
        runner = FakeRunner(cancel_after=1)  # trips cancel once job 1 completes
        worker = _worker(jobs, runner)
        summaries = []
        worker.finished_summary.connect(summaries.append)
        worker.run()
        summary = summaries[0]
        self.assertEqual(len(runner.calls), 1)  # only job 1 ran
        self.assertEqual(summary.succeeded, 1)
        self.assertEqual(summary.cancelled, 2)  # jobs 2 and 3 recorded cancelled
        self.assertIn("Cancelled", summary.terminal_message())

    def test_progress_is_emitted_after_each_job_completes(self):
        # The progress bar must reflect FINISHED jobs, not jobs about to start;
        # emitting before run_job marks a job done while it is still rendering.
        events = []

        class RecordingRunner:
            def run_job(self, job, command, cancel_event, on_log):
                events.append("run_job")
                return JobResult(job=job, return_code=0, outcome=JobOutcome.SUCCESS)

        worker = _worker([RenderJob("a.py", "A")], RecordingRunner())
        worker.progress.connect(lambda c, t: events.append(f"progress {c}/{t}"))
        worker.run()
        self.assertEqual(events, ["run_job", "progress 1/1"])  # progress AFTER the job


class WorkerLogAndResultTests(unittest.TestCase):
    def test_forced_thread_stop_does_not_leave_log_helper_alive(self):
        # QThread.terminate can bypass Python finally. Keep the actual forced
        # stop in a child interpreter, including the abandoned-lock case, so it
        # cannot poison this test runner's Python/Qt thread state.
        script = textwrap.dedent("""
            import json
            import sys
            import tempfile
            import threading
            import time

            from PyQt5 import sip
            from PyQt5.QtCore import QCoreApplication, Qt
            from app.render.jobs import RenderJob
            from app.render.worker import RenderWorker

            app = QCoreApplication([])
            reached = threading.Event()
            block = threading.Event()
            mode = sys.argv[1]
            seen = []

            class BlockingLog:
                def __init__(self, handle):
                    self.handle = handle
                    self.name = handle.name

                def write(self, text):
                    if "blocked write" in text:
                        reached.set()
                        block.wait(60)
                    return self.handle.write(text)

                def close(self):
                    self.handle.close()

            class Worker(RenderWorker):
                def _open_log_file(self):
                    handle = super()._open_log_file()
                    return BlockingLog(handle) if mode == "write" else handle

            class Runner:
                def run_job(self, job, command, cancel, on_log):
                    on_log("blocked write" if mode == "write" else "pending output")
                    reached.set()
                    block.wait(60)

            with tempfile.TemporaryDirectory() as output:
                worker = Worker([RenderJob("a.py", "A")], "-ql", "", output, runner=Runner())
                worker.log.connect(seen.append, type=Qt.DirectConnection)
                worker.start()
                assert reached.wait(3), "render did not reach forced-stop boundary"
                time.sleep(0.3)  # Let the helper contend with the blocked write.
                helpers = [t for t in threading.enumerate() if t.name.startswith("msr-log-flush-")]
                assert len(helpers) == 1, "expected a live flush helper before termination"
                worker.terminate()
                assert worker.wait(2000), "forced stop or helper cleanup did not finish"
                assert not helpers[0].is_alive(), "flush helper survived QThread.finished"
                count = len(seen)
                sip.delete(worker)
                time.sleep(0.3)
                assert len(seen) == count, "helper emitted after worker deletion"
                print(json.dumps({"mode": mode, "helper_stopped": True}))
            """)
        for mode in ("runner", "write"):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [sys.executable, "-c", script, mode],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout), {"mode": mode, "helper_stopped": True})

    def test_lifecycle_identifies_attempted_and_skipped_jobs(self):
        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B")]
        with tempfile.TemporaryDirectory() as out:
            worker = RenderWorker(jobs, "-ql", "", out, runner=FakeRunner(cancel_after=1))
            paths = []
            worker.log_file_ready.connect(paths.append)
            worker.run()
            self.assertEqual(len(paths), 1)
            with open(paths[0], encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            manifest = json.loads(Path(paths[0]).with_suffix(".implementation.json").read_text())
            if os.name == "posix":
                self.assertEqual(os.stat(paths[0]).st_mode & 0o777, 0o600)
        for line in lines:
            self.assertRegex(line, r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\] ")
        events = [json.loads(line.split("] ", 1)[1]) for line in lines if "] {" in line]
        self.assertEqual(len({event["batch_id"] for event in events}), 1)
        self.assertEqual(manifest["run_id"], events[0]["batch_id"])
        self.assertEqual(manifest["operation"], "gui_render")
        self.assertEqual(events[0]["event"], "batch_started")
        self.assertEqual(events[-1]["event"], "batch_finished")
        results = [event for event in events if event["event"] == "job_finished"]
        self.assertEqual([event["job_index"] for event in results], [1, 2])
        self.assertEqual([event["outcome"] for event in results], ["success", "cancelled"])
        self.assertGreaterEqual(results[0]["duration_ms"], 0)
        self.assertIsNone(results[1]["duration_ms"])
        self.assertFalse(results[1]["started"])

    def test_sparse_output_is_visible_and_persisted_before_job_completes(self):
        visible = threading.Event()

        class SparseRunner:
            def run_job(inner, job, command, cancel_event, on_log):
                on_log("sparse output <Scene>")
                self.assertTrue(visible.wait(2), "sparse log was held until render completion")
                with open(paths[0], encoding="utf-8") as handle:
                    self.assertIn("sparse output <Scene>", handle.read())
                return JobResult(job=job, return_code=0, outcome=JobOutcome.SUCCESS)

        with tempfile.TemporaryDirectory() as out:
            worker = RenderWorker([RenderJob("a.py", "A")], "-ql", "", out, runner=SparseRunner())
            paths = []
            worker.log_file_ready.connect(paths.append)
            worker.log.connect(
                lambda text: visible.set() if "sparse output" in text else None,
                type=Qt.DirectConnection,
            )
            results = []
            worker.job_result.connect(lambda job, outcome, code: results.append(outcome))
            worker.run()
            self.assertEqual(results, [JobOutcome.SUCCESS])

    def test_log_io_failures_are_visible_without_failing_render(self):
        for operation in ("open", "write", "close"):
            with self.subTest(operation=operation):
                worker = _worker([RenderJob("a.py", "A")], FakeRunner())
                handle = mock.Mock(name="log handle")
                handle.name = "/tmp/injected-render.log"
                if operation != "open":
                    getattr(handle, operation).side_effect = OSError("private detail")
                warnings, results, logs = [], [], []
                worker.logging_failed.connect(warnings.append)
                worker.job_result.connect(lambda job, outcome, code: results.append(outcome))
                worker.log.connect(logs.append)
                with (
                    mock.patch.object(
                        worker,
                        "_open_log_file",
                        return_value=None if operation == "open" else handle,
                    ),
                    mock.patch(
                        "app.render.worker.write_implementation_manifest", return_value=True
                    ),
                ):
                    worker.run()
                self.assertEqual(results, [JobOutcome.SUCCESS])
                self.assertEqual(len(warnings), 1)
                self.assertIn(operation, warnings[0])
                self.assertIn(warnings[0], "\n".join(logs))
                self.assertNotIn("private detail", "\n".join(logs))

    def test_identity_persistence_failure_is_visible_without_failing_render(self):
        with tempfile.TemporaryDirectory() as out:
            worker = RenderWorker([RenderJob("a.py", "A")], "-ql", "", out, runner=FakeRunner())
            diagnostics, logging, results, paths = [], [], [], []
            worker.diagnostics_failed.connect(diagnostics.append)
            worker.logging_failed.connect(logging.append)
            worker.job_result.connect(lambda job, outcome, code: results.append(outcome))
            worker.log_file_ready.connect(paths.append)
            with mock.patch("app.render.worker.write_implementation_manifest", return_value=False):
                worker.run()
            self.assertEqual(results, [JobOutcome.SUCCESS])
            self.assertEqual(logging, [])
            self.assertEqual(len(diagnostics), 1)
            self.assertIn("runtime identity is unverified", diagnostics[0])
            self.assertIn(diagnostics[0], Path(paths[0]).read_text())

    def test_job_result_diagnostic_is_logged(self):
        class DiagnosticRunner:
            def run_job(self, job, command, cancel_event, on_log):
                return JobResult(
                    job=job,
                    return_code=None,
                    outcome=JobOutcome.FAILED,
                    diagnostic="process supervision failed: broken session",
                )

        worker = _worker([RenderJob("a.py", "A")], DiagnosticRunner())
        logs = []
        worker.log.connect(logs.append)
        worker.run()
        self.assertIn("process supervision failed: broken session", "\n".join(logs))

    def test_job_result_carries_return_code(self):
        # BL-30: the exit code must reach the UI so a failed row is attributable.
        runner = FakeRunner(outcomes=[JobOutcome.FAILED])
        worker = _worker([RenderJob("a.py", "A")], runner)
        results = []
        worker.job_result.connect(lambda job, outcome, code: results.append((outcome, code)))
        worker.run()
        self.assertEqual(results, [(JobOutcome.FAILED, 1)])

    def test_writes_uncapped_log_file_to_output_dir(self):
        # BL-20: every render persists its full merged output to disk for bug reports.
        jobs = [RenderJob("a.py", "A"), RenderJob("b.py", "B")]
        with tempfile.TemporaryDirectory() as out:
            RenderWorker(jobs, "-ql", "", out, runner=FakeRunner()).run()
            written = glob.glob(os.path.join(out, "render-*.log"))
            self.assertEqual(len(written), 1)
            with open(written[0], encoding="utf-8") as f:
                content = f.read()
        self.assertIn("line for A", content)
        self.assertIn("line for B", content)
        self.assertIn("$ ", content)  # the command echo is persisted too

    def test_log_signals_are_coalesced(self):
        # BL-29: many streamed lines must not become one cross-thread signal each.
        class ChattyRunner:
            def run_job(self, job, command, cancel_event, on_log):
                for i in range(100):
                    on_log(f"line {i}")
                return JobResult(job=job, return_code=0, outcome=JobOutcome.SUCCESS)

        worker = _worker([RenderJob("a.py", "A")], ChattyRunner())
        signals = []
        worker.log.connect(signals.append)
        worker.run()
        delivered = sum(s.count("\n") + 1 for s in signals)
        self.assertGreater(delivered, 100)  # all lines delivered (100 + the "$ cmd")
        self.assertLess(len(signals), 20)  # but far fewer signals than lines


if __name__ == "__main__":
    unittest.main()
