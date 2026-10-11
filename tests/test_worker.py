"""Tests for app.render.worker.RenderWorker.run().

The controller suite injects a FakeWorker whose start() is a no-op, so run() --
which owns 1-based progress, the per-job "$ command" log line, the cancel-before-
next-job break, and the RenderSummary tally -- had zero coverage. Here run() is
called directly (synchronously, no QThread start) with an injected fake runner,
so no real subprocess or thread is involved.
"""

import glob
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
        cmd_lines = [line for line in logs if line.startswith("$ ")]
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
