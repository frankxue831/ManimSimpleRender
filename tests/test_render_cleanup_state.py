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
        # Backend and ownership evidence must be explicit: an unowned Windows
        # tree cannot become complete merely because its reader has stopped.
        for platform, owned, expected in (
            ("darwin", False, State.COMPLETE),
            ("win32", True, State.COMPLETE),
            ("win32", False, State.UNKNOWN),
        ):
            with self.subTest(platform=platform, owned=owned):
                session = self.session()
                session.reader.ident = 777
                session.reader_start_attempted = True
                session.reader_start_completed = False
                if owned:
                    session.windows_job = Mock(assigned=True)
                    session.windows_job.active_processes.return_value = 0
                with (
                    patch("app.render.process_session.sys.platform", platform),
                    patch("subprocess.Popen", side_effect=AssertionError("no real process")),
                    patch("subprocess.run", side_effect=AssertionError("no real command")),
                    patch("os.kill", side_effect=AssertionError("no real signal")),
                    patch("os.killpg", create=True, side_effect=ProcessLookupError),
                ):
                    session._observe_cleanup(0)
                    self.assertEqual(session.render_cleanup_state, State.UNKNOWN)
                    session.reader_start_completed = True
                    session._observe_cleanup(0)
                    self.assertEqual(session.render_cleanup_state, expected)


class ForcedCleanupWindowTests(unittest.TestCase):
    """The leader wait and post-kill observation share one bounded window."""

    def run_case(self, platform, release_at, probe_error=None, reader_alive=False, reap_delay=0.05):
        import subprocess
        from app.render import process_session as module

        clock = [0.0]
        reaped = [False]
        process = Mock(pid=123, stdout=None)
        process.poll.side_effect = lambda: -9 if reaped[0] else None
        reader = Mock()
        reader.is_alive.return_value = reader_alive
        session = _ProcessSession(process, 123, None, reader, 0.25, 0.01)
        job = Mock(assigned=True)
        if platform == "win32":
            session.windows_job = job

        def wait(timeout):
            clock[0] += min(reap_delay, timeout)
            if reap_delay > timeout:
                raise subprocess.TimeoutExpired("render", timeout)
            reaped[0] = True
            return -9

        process.wait.side_effect = wait

        def active():
            if clock[0] >= release_at:
                return False
            if probe_error is not None and clock[0] > 0:
                raise probe_error("probe uncertain")
            return True

        def killpg(_target, _signal):
            self.assertEqual(_signal, 0, "only a guarded read-only probe is allowed")
            if not active():
                raise ProcessLookupError()

        job.active_processes.side_effect = lambda: int(active())

        def advance(duration):
            clock[0] += duration

        with (
            patch.object(module.sys, "platform", platform),
            patch.object(module.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(module.time, "sleep", side_effect=advance),
            patch.object(module, "_signal_group", side_effect=["graceful unavailable", ""]),
            patch("os.killpg", create=True, side_effect=killpg),
            patch("subprocess.Popen", side_effect=AssertionError("no process")),
            patch("subprocess.run", side_effect=AssertionError("no command")),
            patch("os.kill", side_effect=AssertionError("no signal")),
        ):
            diagnostic = session._stop_group()
            session._observe_cleanup(-9 if reaped[0] else None)
        self.assertLessEqual(clock[0], 0.25)
        return session, diagnostic, clock[0]

    def test_post_force_transient_membership_or_permission_releases(self):
        for platform, error in (("darwin", None), ("darwin", PermissionError), ("win32", None)):
            with self.subTest(platform=platform, error=error):
                session, diagnostic, elapsed = self.run_case(
                    platform, release_at=0.15, probe_error=error
                )
                self.assertEqual(session.render_cleanup_state, State.COMPLETE)
                self.assertGreaterEqual(elapsed, 0.15)
                self.assertNotIn("remained active", diagnostic)
                if platform == "win32":
                    self.assertEqual(diagnostic, "")

    def test_persistent_membership_permission_and_probe_error_stay_unknown(self):
        for platform, error in (
            ("darwin", None),
            ("darwin", PermissionError),
            ("darwin", OSError),
            ("win32", None),
            ("win32", OSError),
        ):
            with self.subTest(platform=platform, error=error):
                session, diagnostic, elapsed = self.run_case(platform, float("inf"), error)
                self.assertEqual(session.render_cleanup_state, State.UNKNOWN)
                self.assertTrue(diagnostic.startswith("process supervision failed:"))
                if error is not OSError:
                    self.assertEqual(elapsed, 0.25)
                else:
                    self.assertIn("probe uncertain", diagnostic)

    def test_leader_and_reader_evidence_still_required(self):
        for platform in ("darwin", "win32"):
            with self.subTest(platform=platform):
                session, _, _ = self.run_case(platform, 0.15, reader_alive=True)
                self.assertEqual(session.render_cleanup_state, State.UNKNOWN)
                session, diagnostic, elapsed = self.run_case(platform, 0.15, reap_delay=1.0)
                self.assertEqual(session.render_cleanup_state, State.UNKNOWN)
                self.assertIn("process did not stop", diagnostic)
                self.assertEqual(elapsed, 0.25)

    def test_slow_reap_does_not_restart_post_force_window(self):
        for platform in ("darwin", "win32"):
            with self.subTest(platform=platform):
                session, diagnostic, elapsed = self.run_case(platform, 0.3, reap_delay=0.2)
                self.assertEqual(session.render_cleanup_state, State.UNKNOWN)
                self.assertIn("remained active", diagnostic)
                self.assertEqual(elapsed, 0.25)
