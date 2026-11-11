"""Guarded cleanup-state propagation across production, protocol and scheduling."""

from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.render.aggregation import JobOutcome
from app.render.cleanup import RenderCleanupState as State, set_cleanup_state
from app.render.runner import JobResult
from scene_production import agent
from scene_production.completion import read_completion
from scene_production.handoff.runner import verify_result, _handoff_result, reconcile_evidence
from tests import test_scene_production as fixtures
from tests.test_production_completion import capture, success


class ProductionCleanupStateTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    invoke = fixtures.PipelineTests.invoke
    receipt = fixtures.PipelineTests.receipt
    runner = fixtures.PipelineTests.runner
    fake_tool = staticmethod(fixtures.PipelineTests.fake_tool)

    def guarded(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for target in ("subprocess.Popen", "subprocess.run", "os.kill", "os.killpg"):
            stack.enter_context(
                patch(target, side_effect=AssertionError("real execution forbidden"), create=True)
            )
        stack.enter_context(
            patch("scene_production.write_implementation_manifest", return_value=True)
        )
        stack.enter_context(patch("scene_production.threading.Timer", return_value=Mock()))
        return stack

    def result_runner(self, state=State.UNKNOWN, outcome=JobOutcome.FAILED):
        def run(job, *args):
            return JobResult(
                job,
                0 if outcome == JobOutcome.SUCCESS else None,
                outcome,
                "same diagnostic",
                render_cleanup_state=state,
            )

        return Mock(run_job=Mock(side_effect=run))

    def test_unknown_receipt_exception_and_agent_hold_slot(self):
        self.guarded()
        runner = self.result_runner()
        with self.assertRaises(RuntimeError) as error:
            self.invoke(runner=runner)
        self.assertEqual(error.exception.render_cleanup_state, State.UNKNOWN)
        self.assertEqual(self.receipt()["render_cleanup_state"], "unknown")
        output = (self.root / "agent").resolve()
        stream = io.StringIO()
        with patch("scene_production.RenderRunner", return_value=runner), redirect_stdout(stream):
            code = agent.main(
                [
                    "render",
                    "--scene-file",
                    str(self.source),
                    "--scene",
                    "Demo",
                    "--timeline",
                    str(self.timeline),
                    "--output-dir",
                    str(output),
                ]
            )
        completion = read_completion(stream.getvalue(), code, expected_operation="render")
        self.assertEqual((code, completion.status), (3, "unknown"))
        terminal = completion.terminal
        self.assertIsNotNone(terminal)
        self.assertEqual(terminal["error"]["code"], "render_cleanup_unverified")
        self.assertEqual(terminal["render_cleanup_state"], "unknown")
        self.assertIs(terminal["process_cleanup_verified"], False)
        self.assertNotIn("artifacts", terminal)
        self.assertEqual(verify_result(output, stream.getvalue().encode(), code), "unknown")
        self.assertEqual(json.loads((output / "attempt.json").read_text()), terminal)
        self.assertEqual(terminal["diagnostics"]["failure_reason"], "render_failed")
        self.assert_slot("unknown", "render_slot_held")
        self.assert_slot("failed", None)

    def assert_slot(self, status, expected):
        # Real scheduling logic, storage/transactions replaced, no SQLite or process.
        from contextlib import nullcontext
        from scene_production.handoff.ledger import Ledger

        ledger = object.__new__(Ledger)
        ledger.clock = lambda: 10
        auth = dict(status="active", allowed_actions=["render"], allowed_resources=["scene"])
        old = dict(
            task_id="old",
            status="running",
            fence=1,
            authorization_id="auth",
            action="render",
            resource="scene",
            kind="render",
            depends_on=[],
        )
        pending = dict(old, task_id="next", status="pending", fence=0)
        dependent = dict(pending, task_id="review", kind="manual", depends_on=["old"])
        attempt = dict(
            attempt_id="attempt", task_id="old", status="running", fence=1, lease_until=100
        )
        rows = {
            ("auth", "auth"): auth,
            ("task", "old"): old,
            ("task", "next"): pending,
            ("task", "review"): dependent,
            ("attempt", "attempt"): attempt,
        }
        ledger._get = Mock(side_effect=lambda kind, key: rows[(kind, key)])
        ledger._all = Mock(side_effect=lambda kind: [v for (k, _), v in rows.items() if k == kind])
        ledger._put = Mock(
            side_effect=lambda kind, key, value: rows.__setitem__((kind, key), value)
        )
        ledger._transaction = Mock(side_effect=nullcontext)
        ledger._event = Mock()
        ledger._notify_ready = Mock()
        ledger.finish("attempt", 1, status, {"status": status})
        self.assertEqual(ledger._block(pending), expected)
        self.assertEqual(ledger._block(dependent), "dependency_blocked")

    def test_contradictory_no_start_results_hold_slot_and_safe_controls_release(self):
        self.guarded()
        cases = (
            (JobOutcome.FAILED, 1, State.NOT_STARTED, "unknown"),
            (JobOutcome.SUCCESS, None, State.NOT_STARTED, "unknown"),
            (JobOutcome.SUCCESS, 0, State.NOT_STARTED, "unknown"),
            (JobOutcome.CANCELLED, None, State.NOT_STARTED, "unknown"),
            (JobOutcome.FAILED, None, State.NOT_STARTED, "failed"),
            (JobOutcome.MANIM_NOT_FOUND, None, State.NOT_STARTED, "failed"),
            (JobOutcome.FAILED, None, State.COMPLETE, "failed"),
        )
        for index, (outcome, returncode, state, expected) in enumerate(cases):
            with self.subTest(outcome=outcome, returncode=returncode, state=state):
                output = (self.root / ("agent-%s" % index)).resolve()
                runner = Mock(
                    run_job=Mock(
                        side_effect=lambda job, *args: JobResult(
                            job, returncode, outcome, render_cleanup_state=state
                        )
                    )
                )
                stream = io.StringIO()
                with (
                    patch("scene_production.RenderRunner", return_value=runner),
                    patch("scene_production._inspect_video") as inspect,
                    redirect_stdout(stream),
                ):
                    code = agent.main(
                        [
                            "render",
                            "--scene-file",
                            str(self.source),
                            "--scene",
                            "Demo",
                            "--timeline",
                            str(self.timeline),
                            "--output-dir",
                            str(output),
                        ]
                    )
                inspect.assert_not_called()
                terminal = json.loads(stream.getvalue().splitlines()[-1])
                self.assertEqual(
                    (terminal["status"], code), (expected, 3 if expected == "unknown" else 1)
                )
                self.assertEqual(
                    terminal["render_cleanup_state"],
                    "unknown" if expected == "unknown" else state.value,
                )
                self.assertNotIn("artifacts", terminal)
                self.assertEqual(verify_result(output, stream.getvalue().encode(), code), expected)
                self.assert_slot(expected, "render_slot_held" if expected == "unknown" else None)

    def test_api_success_with_no_start_is_unknown_before_verification(self):
        self.guarded()
        output = (self.root / "agent").resolve()
        stream = io.StringIO()
        with (
            patch(
                "scene_production.render",
                return_value={"status": "success", "render_cleanup_state": "not_started"},
            ),
            patch.object(agent, "_verified_result") as verify,
            redirect_stdout(stream),
        ):
            code = agent.main(
                [
                    "render",
                    "--scene-file",
                    str(self.source),
                    "--scene",
                    "Demo",
                    "--timeline",
                    str(self.timeline),
                    "--output-dir",
                    str(output),
                ]
            )
        verify.assert_not_called()
        terminal = json.loads(stream.getvalue().splitlines()[-1])
        self.assertEqual(code, 3)
        self.assertEqual(terminal["status"], "unknown")
        self.assertEqual(terminal["render_cleanup_state"], "unknown")
        self.assertEqual(terminal["error"]["code"], "render_cleanup_unverified")
        self.assertEqual(verify_result(output, stream.getvalue().encode(), code), "unknown")

    def test_no_start_contradiction_is_latched_before_timer_join_error(self):
        self.guarded()
        timer = Mock()
        timer.join.side_effect = set_cleanup_state(RuntimeError("join failed"), State.COMPLETE)
        with patch("scene_production.threading.Timer", return_value=timer):
            with self.assertRaises(RuntimeError) as caught:
                self.invoke(runner=self.result_runner(State.NOT_STARTED, JobOutcome.SUCCESS))
        self.assertEqual(caught.exception.render_cleanup_state, State.UNKNOWN)

    def test_same_failure_diagnostic_can_have_complete_cleanup(self):
        self.guarded()
        with self.assertRaises(RuntimeError) as error:
            self.invoke(runner=self.result_runner(State.COMPLETE))
        self.assertEqual(error.exception.render_cleanup_state, State.COMPLETE)
        self.assertEqual(self.receipt()["render_cleanup_state"], "complete")

    def test_unknown_or_missing_success_never_reaches_artifact_work(self):
        self.guarded()
        for state in (State.UNKNOWN, None, {}, True, "invalid"):
            with self.subTest(state=state), patch("scene_production._inspect_video") as inspect:
                with self.assertRaises(RuntimeError) as error:
                    self.invoke(runner=self.result_runner(state, JobOutcome.SUCCESS))
                self.assertEqual(error.exception.render_cleanup_state, State.UNKNOWN)
                inspect.assert_not_called()
        result = SimpleNamespace(outcome=JobOutcome.SUCCESS, return_code=0, diagnostic="")
        with self.assertRaises(RuntimeError) as error:
            self.invoke(runner=Mock(run_job=Mock(return_value=result)))
        self.assertEqual(error.exception.render_cleanup_state, State.UNKNOWN)

    def test_preflight_error_not_started_without_output_directory(self):
        self.guarded()
        with self.assertRaises(ValueError) as error:
            self.invoke(quality="invalid")
        self.assertEqual(error.exception.render_cleanup_state, State.NOT_STARTED)
        self.assertFalse(self.output.exists())

    def test_receipt_failure_preserves_unknown(self):
        self.guarded()
        with patch("scene_production._write_receipt", side_effect=OSError("disk")):
            with self.assertRaises(OSError) as error:
                self.invoke(runner=self.result_runner())
        self.assertEqual(error.exception.render_cleanup_state, State.UNKNOWN)
        self.assertIsInstance(error.exception.__cause__, RuntimeError)

    def test_timer_join_failure_preserves_latched_evidence(self):
        self.guarded()
        for state in (State.COMPLETE, State.UNKNOWN):
            timer = Mock()
            timer.join.side_effect = RuntimeError("join failed")
            with patch("scene_production.threading.Timer", return_value=timer):
                with self.assertRaisesRegex(RuntimeError, "join failed") as error:
                    self.invoke(runner=self.result_runner(state))
            self.assertEqual(error.exception.render_cleanup_state, state)

    def test_runner_exception_carries_state_and_absence_fails_closed(self):
        self.guarded()
        for state in (None, State.COMPLETE, State.NOT_STARTED):
            error = RuntimeError("runner error")
            if state is not None:
                set_cleanup_state(error, state)
            with self.assertRaises(RuntimeError) as caught:
                self.invoke(runner=Mock(run_job=Mock(side_effect=error)))
            self.assertEqual(caught.exception.render_cleanup_state, state or State.UNKNOWN)

    def test_interruption_stays_unknown_even_after_positive_cleanup(self):
        self.guarded()
        error = set_cleanup_state(KeyboardInterrupt(), State.COMPLETE)
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.invoke(runner=Mock(run_job=Mock(side_effect=error)))
        self.assertEqual(caught.exception.render_cleanup_state, State.UNKNOWN)
        self.assertEqual(self.receipt()["render_cleanup_state"], "unknown")
        self.assertIs(self.receipt()["process_cleanup_verified"], False)

    def test_timeout_and_cancel_keep_reason_but_not_safe_cleanup(self):
        self.guarded()
        for state in (State.UNKNOWN, State.COMPLETE):
            for cause in ("timeout", "cancel"):

                def timer_factory(timeout, callback):
                    timer = Mock()
                    if cause == "timeout":
                        timer.start.side_effect = callback
                    return timer

                def run(job, command, event, *args):
                    if cause == "cancel":
                        event.set()
                    return JobResult(job, None, JobOutcome.CANCELLED, render_cleanup_state=state)

                with patch("scene_production.threading.Timer", side_effect=timer_factory):
                    with self.assertRaises(RuntimeError) as caught:
                        self.invoke(runner=Mock(run_job=Mock(side_effect=run)))
                self.assertEqual(caught.exception.render_cleanup_state, state)
                receipt = json.loads(Path(caught.exception.receipt_path).read_text())
                self.assertEqual(receipt["render_cleanup_state"], state.value)
                self.assertEqual(
                    receipt["failure_reason"],
                    "render_timeout" if cause == "timeout" else "cancelled",
                )

    def test_diagnostic_failure_does_not_erase_latched_state(self):
        self.guarded()
        for state in (State.UNKNOWN, State.COMPLETE):

            def run(job, *args):
                return JobResult(job, 0, JobOutcome.SUCCESS, object(), render_cleanup_state=state)

            with self.assertRaises(TypeError) as caught:
                self.invoke(runner=Mock(run_job=Mock(side_effect=run)))
            self.assertEqual(caught.exception.render_cleanup_state, state)

    def test_agent_persistence_failure_keeps_cleanup_unknown(self):
        self.guarded()
        stream = io.StringIO()
        output = (self.root / "agent").resolve()
        original = agent._atomic_write

        def persist(path, value):
            if json.loads(value.splitlines()[-1]).get("event") == "terminal":
                raise OSError("disk failure")
            original(path, value)

        with (
            patch("scene_production.RenderRunner", return_value=self.result_runner()),
            patch.object(agent, "_atomic_write", side_effect=persist),
            redirect_stdout(stream),
        ):
            code = agent.main(
                [
                    "render",
                    "--scene-file",
                    str(self.source),
                    "--scene",
                    "Demo",
                    "--timeline",
                    str(self.timeline),
                    "--output-dir",
                    str(output),
                ]
            )
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([e["event"] for e in events], ["started", "terminal"])
        terminal = events[-1]
        self.assertEqual(
            (code, terminal["status"], terminal["error"]["code"]),
            (3, "unknown", "persistence_failed"),
        )
        self.assertEqual(terminal["render_cleanup_state"], "unknown")
        self.assertIs(terminal["process_cleanup_verified"], False)
        self.assertNotIn("artifacts", terminal)


class CleanupProtocolTests(unittest.TestCase):
    def test_legacy_reader_vs_scheduler_and_no_success_metadata(self):
        import tempfile

        events = success()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            attempt = directory / "agent"
            attempt.mkdir()
            events[0]["attempt_dir"] = str(attempt)
            data = capture(events).encode()
            (attempt / "attempt.json").write_text(json.dumps(events[-1]))
            (attempt / "events.jsonl").write_bytes(data)
            (directory / "protocol.log").write_bytes(data)
            (directory / "process.json").write_text(
                json.dumps({"attempt_id": "attempt", "fence": 1})
            )
            (directory / "exit.json").write_text(
                json.dumps(
                    dict(exit_observed=True, interrupted=False, returncode=0, status="succeeded")
                )
            )
            self.assertEqual(
                read_completion(data, 0, expected_operation="render").status, "succeeded"
            )
            self.assertEqual(verify_result(attempt, data, 0), "unknown")
            result = _handoff_result(directory, "unknown")
            self.assertEqual(result["diagnostic_code"], "cleanup_evidence_missing")
            self.assertNotIn("artifacts", result)
            self.assertNotIn("verification_scope", result)
            self.assertEqual(reconcile_evidence(directory)["status"], "unknown")
            self.assertEqual(
                json.loads((directory / "exit.json").read_text())["status"], "succeeded"
            )

    def test_invalid_contradictory_state_and_false_safe_flag_rejected(self):
        for state, flag in (
            ("unknown", None),
            ({}, None),
            (True, None),
            ("bad", None),
            ("not_started", None),
            ("complete", False),
        ):
            events = deepcopy(success())
            events[-1]["render_cleanup_state"] = state
            if flag is not None:
                events[-1]["process_cleanup_verified"] = flag
            result = read_completion(capture(events), 0, expected_operation="render")
            self.assertEqual(result.status, "unknown")
            self.assertIsNone(result.terminal)
        events = success()
        terminal = events[-1]
        for key in ("artifacts", "verification_scope", "semantic_acceptance"):
            terminal.pop(key)
        terminal.update(
            status="failed",
            exit_code=1,
            error={"code": "operation_failed"},
            process_cleanup_verified=False,
        )
        self.assertIsNone(read_completion(capture(events), 1, expected_operation="render").terminal)

    def test_normal_handoff_unknown_does_not_signal_wrapper(self):
        import tempfile
        from scene_production.handoff.runner import run_task

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "source.py"
            source.write_text("# inert fixture")
            ledger = Mock(directory=root)
            ledger.get_task.return_value = dict(
                kind="render",
                action="render",
                payload=dict(scene_file=str(source), scene="Demo", timeline=str(source)),
            )
            ledger.claim_local_render.return_value = dict(attempt_id="attempt", fence=1)

            def acquire(args, **kwargs):
                attempt = Path(args[args.index("--output-dir") + 1])
                attempt.mkdir()
                events = success()
                events[0]["attempt_dir"] = str(attempt)
                terminal = events[-1]
                for key in ("artifacts", "verification_scope", "semantic_acceptance"):
                    terminal.pop(key)
                terminal.update(
                    status="unknown",
                    exit_code=3,
                    error={"code": "render_cleanup_unverified"},
                    render_cleanup_state="unknown",
                    process_cleanup_verified=False,
                )
                data = capture(events).encode()
                (attempt / "attempt.json").write_text(json.dumps(terminal))
                (attempt / "events.jsonl").write_bytes(data)
                return Mock(
                    pid=987654,
                    returncode=3,
                    poll=Mock(return_value=3),
                    stdout=io.BytesIO(data),
                    stderr=io.BytesIO(b""),
                )

            with (
                patch("subprocess.Popen", side_effect=acquire),
                patch("subprocess.run", side_effect=AssertionError("no real process")),
                patch("os.kill", side_effect=AssertionError("no signal")),
                patch("os.killpg", side_effect=AssertionError("no group signal"), create=True),
                patch("scene_production.handoff.runner._signal_group") as signal,
            ):
                result = run_task(ledger, "task", "worker", 1, poll_interval=0.001)
            signal.assert_not_called()
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["diagnostic_code"], "render_cleanup_unverified")
            self.assertTrue(result["exit_observed"])
            self.assertEqual(result["returncode"], 3)
            self.assertNotIn("artifacts", result)
            exit_evidence = json.loads((root / "outputs" / "attempt" / "exit.json").read_text())
            self.assertIs(exit_evidence["interrupted"], False)
            self.assertEqual(reconcile_evidence(root / "outputs" / "attempt")["status"], "unknown")
            ledger.finish.assert_called_once()
            self.assertEqual(ledger.finish.call_args.args[2], "unknown")
