"""Deterministic cleanup observations; no real children or signals."""

import unittest
from unittest.mock import Mock, patch

from app.render.cleanup import RenderCleanupState as State, cleanup_state
from app.render.process_session import _ProcessSession


class CleanupEvidenceTests(unittest.TestCase):
    def session(self):
        session = _ProcessSession(Mock(), 123, None, Mock(), 0.01, 0.01)
        session.reader.is_alive.return_value = False
        return session

    def test_default_and_invalid_evidence_unknown(self):
        for value in (None, {}, True, 0, "success", Mock()):
            self.assertEqual(cleanup_state(value), State.UNKNOWN)

    def test_positive_posix_absence(self):
        session = self.session()
        with (
            patch("app.render.process_session.sys.platform", "darwin"),
            patch("os.killpg", create=True, side_effect=ProcessLookupError),
        ):
            session._observe_cleanup(0)
        self.assertEqual(session.render_cleanup_state, State.COMPLETE)

    def test_uncertain_group_or_reader_or_reap(self):
        for code, alive, probe in (
            (None, False, ProcessLookupError),
            (0, True, ProcessLookupError),
            (0, False, PermissionError),
            (0, False, None),
        ):
            session = self.session()
            session.reader.is_alive.return_value = alive
            with (
                patch("app.render.process_session.sys.platform", "darwin"),
                patch("os.killpg", create=True, side_effect=probe),
            ):
                session._observe_cleanup(code)
            self.assertEqual(session.render_cleanup_state, State.UNKNOWN)

    def test_windows_needs_owned_empty_job(self):
        for job, expected in ((None, State.UNKNOWN), (Mock(assigned=True), State.COMPLETE)):
            session = self.session()
            session.windows_job = job
            if job is not None:
                job.active_processes.return_value = 0
            with patch("app.render.process_session.sys.platform", "win32"):
                session._observe_cleanup(0)
            self.assertEqual(session.render_cleanup_state, expected)

    def test_acquisition_boundary_and_custom_start(self):
        from app.render.runner import RenderRunner

        for failure in (FileNotFoundError("missing"), OSError("acquire")):
            with patch("subprocess.Popen", side_effect=failure):
                result = RenderRunner().run_job(None, [], None, lambda _: None)
            self.assertEqual(result.render_cleanup_state, State.NOT_STARTED)
        with patch("app.render.runner._ProcessSession.start", side_effect=RuntimeError("custom")):
            result = RenderRunner().run_job(None, [], None, lambda _: None)
        self.assertEqual(result.render_cleanup_state, State.UNKNOWN)

    def test_finish_preserves_original_when_cleanup_throws(self):
        session = self.session()
        original = RuntimeError("original")
        with (
            patch.object(session, "_finish", side_effect=original),
            patch.object(session, "_cleanup_after_error", side_effect=ValueError("cleanup")),
        ):
            with self.assertRaises(RuntimeError) as caught:
                session.finish(None, lambda _: None)
        self.assertIs(caught.exception, original)
        self.assertEqual(original.render_cleanup_state, State.UNKNOWN)

    def test_finish_exception_keeps_independent_observations(self):
        for state in (State.COMPLETE, State.UNKNOWN):
            session = self.session()

            def cleanup():
                session.render_cleanup_state = state

            with (
                patch.object(session, "_finish", side_effect=ValueError("same")),
                patch.object(session, "_cleanup_after_error", side_effect=cleanup),
            ):
                with self.assertRaises(ValueError) as caught:
                    session.finish(None, lambda _: None)
            self.assertEqual(caught.exception.render_cleanup_state, state)

    def test_windows_query_failure_and_close_after_positive_snapshot(self):
        session = self.session()
        session.windows_job = Mock(assigned=True)
        with patch("app.render.process_session.sys.platform", "win32"):
            session.windows_job.active_processes.side_effect = OSError("query")
            session._observe_cleanup(0)
            self.assertEqual(session.render_cleanup_state, State.UNKNOWN)
            session.windows_job.active_processes.side_effect = None
            session.windows_job.active_processes.return_value = 0
            session._observe_cleanup(0)
            session.windows_job.close.side_effect = OSError("close")
            self.assertTrue(session._close_windows_job())
            self.assertEqual(session.render_cleanup_state, State.COMPLETE)

    def test_post_spawn_reader_start_failure_and_constructor_failure(self):
        from app.render.process_session import _PostSpawnSetupError

        process = Mock(pid=123)
        process.wait.return_value = 0
        process.poll.return_value = 0
        reader = Mock(ident=None)
        reader.is_alive.return_value = False
        reader.start.side_effect = RuntimeError("reader start")
        with (
            patch("subprocess.Popen", return_value=process),
            patch("app.render.process_session.sys.platform", "darwin"),
            patch("os.killpg", create=True, side_effect=ProcessLookupError),
            patch("app.render.process_session.threading.Thread", return_value=reader),
            patch("subprocess.run", side_effect=AssertionError("no subprocess")),
        ):
            with self.assertRaises(_PostSpawnSetupError) as caught:
                _ProcessSession.start([], env=None, terminate_timeout=0.01, drain_timeout=0.01)
            self.assertEqual(caught.exception.render_cleanup_state, State.UNKNOWN)

            reader.start.side_effect = KeyboardInterrupt("paused native thread bootstrap")
            with self.assertRaises(KeyboardInterrupt) as caught:
                _ProcessSession.start([], env=None, terminate_timeout=0.01, drain_timeout=0.01)
            self.assertEqual(caught.exception.render_cleanup_state, State.UNKNOWN)

            class BrokenSession(_ProcessSession):
                def __init__(self, *args, **kwargs):
                    raise RuntimeError("constructor")

            with self.assertRaises(_PostSpawnSetupError) as caught:
                BrokenSession.start([], env=None, terminate_timeout=0.01, drain_timeout=0.01)
            self.assertEqual(caught.exception.render_cleanup_state, State.COMPLETE)

    def test_windows_setup_fault_matrix_under_process_guards(self):
        from app.render import process_session as module
        from app.render.process_session import _PostSpawnSetupError

        for stage in ("assignment", "resume"):
            for error in (RuntimeError(stage), KeyboardInterrupt(stage)):
                process = Mock(pid=123)
                process.poll.return_value = process.wait.return_value = 0
                job = Mock(assigned=False)
                job.active_processes.return_value = 0

                def assign(_):
                    if stage == "assignment":
                        raise error
                    job.assigned = True

                job.assign.side_effect = assign
                with (
                    patch.object(module.sys, "platform", "win32"),
                    patch("subprocess.Popen", return_value=process),
                    patch("subprocess.run", side_effect=AssertionError("real command prohibited")),
                    patch("os.kill", side_effect=AssertionError("real signal prohibited")),
                    patch(
                        "os.killpg",
                        create=True,
                        side_effect=AssertionError("real group signal prohibited"),
                    ),
                    patch.object(module, "_WindowsJob", return_value=job),
                    patch.object(module, "_resume_suspended_process", side_effect=error),
                ):
                    expected = (
                        _PostSpawnSetupError if isinstance(error, Exception) else KeyboardInterrupt
                    )
                    with self.assertRaises(expected) as caught:
                        _ProcessSession.start(
                            [], env=None, terminate_timeout=0.01, drain_timeout=0.01
                        )
                self.assertEqual(
                    caught.exception.render_cleanup_state,
                    State.UNKNOWN if stage == "assignment" else State.COMPLETE,
                )

    def test_setup_cleanup_exception_does_not_replace_original(self):
        from app.render import process_session as module
        from app.render.process_session import _PostSpawnSetupError

        process = Mock(pid=123)
        original = ValueError("setup-original")
        with (
            patch("subprocess.Popen", return_value=process),
            patch.object(module.sys, "platform", "win32"),
            patch.object(module, "_WindowsJob", side_effect=original),
            patch.object(
                _ProcessSession, "_cleanup_after_error", side_effect=RuntimeError("cleanup")
            ),
            patch("subprocess.run", side_effect=AssertionError("real command prohibited")),
            patch("os.kill", side_effect=AssertionError("real signal prohibited")),
            patch(
                "os.killpg", create=True, side_effect=AssertionError("real group signal prohibited")
            ),
        ):
            with self.assertRaises(_PostSpawnSetupError) as caught:
                _ProcessSession.start([], env=None, terminate_timeout=0.01, drain_timeout=0.01)
        self.assertIs(caught.exception.__cause__, original)
        self.assertEqual(caught.exception.render_cleanup_state, State.UNKNOWN)

    def test_existing_failed_force_cleanup_is_unknown_not_diagnostic_classification(self):
        import subprocess
        from app.render import process_session as module
        from app.render.runner import RenderRunner
        from app.render.aggregation import JobOutcome
        from tests.test_runner import WindowsProcessSessionTests, JOB

        session, process = WindowsProcessSessionTests()._session(0.01)
        with (
            patch.object(module.sys, "platform", "win32"),
            patch("subprocess.Popen", side_effect=AssertionError("real process prohibited")),
            patch("os.kill", side_effect=AssertionError("real signal prohibited")),
            patch(
                "os.killpg", create=True, side_effect=AssertionError("real group signal prohibited")
            ),
            patch(
                "subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess([], 5, stderr=b"graceful denied"),
                    subprocess.CompletedProcess([], 7, stderr=b"forced denied"),
                ],
            ),
            patch.object(process, "wait", side_effect=subprocess.TimeoutExpired("render", 0.01)),
        ):
            diagnostic = session._stop_group()
        self.assertIn("process did not stop", diagnostic)
        for state in (State.UNKNOWN, State.COMPLETE):
            session.render_cleanup_state = state
            with (
                patch("app.render.runner._ProcessSession.start", return_value=session),
                patch.object(session, "finish", return_value=(None, False, diagnostic)),
                patch("subprocess.Popen", side_effect=AssertionError("real process prohibited")),
                patch("subprocess.run", side_effect=AssertionError("real command prohibited")),
                patch("os.kill", side_effect=AssertionError("real signal prohibited")),
                patch(
                    "os.killpg",
                    create=True,
                    side_effect=AssertionError("real group signal prohibited"),
                ),
            ):
                result = RenderRunner().run_job(JOB, ["never-executed"], None, lambda _: None)
            self.assertEqual(result.outcome, JobOutcome.FAILED)
            self.assertEqual(result.render_cleanup_state, state)

    def test_ident_publication_during_interrupted_bootstrap_stays_unknown(self):
        import threading
        from app.render.runner import RenderRunner

        # This Thread is never started: publish only the identity step that
        # CPython performs before its started event, after cleanup's join guard.
        reader = threading.Thread(target=lambda: None)
        original_alive = reader.is_alive
        process = Mock(pid=123)
        process.poll.return_value = process.wait.return_value = 0
        original = KeyboardInterrupt("interrupted reader start")

        def publish_identity():
            reader._ident = 777
            return original_alive()

        def absent_group(pgid, signum):
            self.assertEqual((pgid, signum), (123, 0))
            raise ProcessLookupError

        with (
            patch("subprocess.Popen", return_value=process),
            patch("subprocess.run", side_effect=AssertionError("no real command")),
            patch("os.kill", side_effect=AssertionError("no real signal")),
            patch("os.killpg", create=True, side_effect=absent_group),
            patch("app.render.process_session.sys.platform", "darwin"),
            patch("app.render.process_session.threading.Thread", return_value=reader),
            patch.object(reader, "start", side_effect=original),
            patch.object(reader, "is_alive", side_effect=publish_identity),
            patch.object(reader, "join") as join,
            patch.object(_ProcessSession, "_stop_group", return_value=""),
        ):
            with self.assertRaises(KeyboardInterrupt) as caught:
                RenderRunner().run_job(None, [], None, lambda _: None)
        join.assert_not_called()
        process.stdout.close.assert_not_called()
        self.assertIs(caught.exception, original)
        self.assertEqual(original.render_cleanup_state, State.UNKNOWN)
        self.assertFalse(reader._started.is_set())
        self.assertFalse(original_alive())

    def test_incomplete_start_stays_unknown_even_with_finished_identity(self):
        session = self.session()
        session.reader.ident = 777
        session.reader_start_attempted = True
        session.reader_start_completed = False
        with patch.object(session, "_group_is_active", return_value=False):
            session._observe_cleanup(0)
        self.assertEqual(session.render_cleanup_state, State.UNKNOWN)
        session.reader_start_completed = True
        with patch.object(session, "_group_is_active", return_value=False):
            session._observe_cleanup(0)
        self.assertEqual(session.render_cleanup_state, State.COMPLETE)
