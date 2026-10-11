"""Tests for app.render.runner (Qt-free subprocess execution). Batch 4.

Exercised with python3 stubs, NEVER real manim. Covers the legacy fixes:
merged stdout+stderr (no deadlock, render.py:79-97), prompt cancellation of a
SILENT child (render.py:81-85), and FileNotFoundError -> MANIM_NOT_FOUND.
Run: python3 -m unittest tests.test_runner
"""

import os
import queue
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from app.render import process_session as process_session_mod
from app.render import runner as runner_mod
from app.render.aggregation import JobOutcome
from app.render.jobs import RenderJob
from app.render.process_session import _EOF, _creation_flags, _drain, _pump
from app.render.runner import RenderRunner

PY = sys.executable
JOB = RenderJob("input/x.py", "X")


def _windows_process_is_active(pid):
    """Probe a Windows PID without sending the destructive ``os.kill(pid, 0)``."""
    import ctypes
    from ctypes import wintypes

    process_synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_synchronize, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == error_invalid_parameter:
            return False
        raise OSError(error, f"could not open Windows process {pid}: {ctypes.FormatError(error)}")
    try:
        status = kernel32.WaitForSingleObject(handle, 0)
        if status == wait_timeout:
            return True
        if status == wait_object_0:
            return False
        raise OSError(f"unexpected Windows process wait status: {status}")
    finally:
        if not kernel32.CloseHandle(handle):
            error = ctypes.get_last_error()
            raise OSError(
                error, f"could not close Windows process handle: {ctypes.FormatError(error)}"
            )


def _wait_for_pid_release(pid, timeout=5.0):
    """Poll until ``pid`` is gone, returning False if it outlives ``timeout``.

    Never assert on a bare ``os.kill(pid, 0)`` right after ``run_job`` returns:
    signalling a group is asynchronous, so ``os.killpg`` returns before the kernel
    has torn the targets down. An orphaned descendant is worse -- the runner has no
    parent relationship to it, so after it dies it stays visible as a zombie until
    whichever init adopted it reaps it, and ``os.kill`` still succeeds for a zombie.
    macOS/launchd reaps fast enough to hide this; Linux does not.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


class RunJobTests(unittest.TestCase):
    def setUp(self):
        self.runner = RenderRunner()
        self.logs = []

    def _log(self, line):
        self.logs.append(line)

    def test_exit_zero_is_success(self):
        result = self.runner.run_job(
            JOB, [PY, "-c", "print('hello')"], cancel_event=None, on_log=self._log
        )
        self.assertEqual(result.outcome, JobOutcome.SUCCESS)
        self.assertEqual(result.return_code, 0)
        self.assertIn("hello", self.logs)

    def test_nonzero_exit_is_failed_with_real_code(self):
        result = self.runner.run_job(JOB, [PY, "-c", "import sys; sys.exit(3)"], None, self._log)
        self.assertEqual(result.outcome, JobOutcome.FAILED)
        self.assertEqual(result.return_code, 3)

    def test_merged_streams_no_deadlock(self):
        stub = (
            "import sys\n"
            "for i in range(500):\n"
            "    print('out%d' % i)\n"
            "    print('err%d' % i, file=sys.stderr)\n"
        )
        result = self.runner.run_job(JOB, [PY, "-c", stub], None, self._log)
        self.assertEqual(result.outcome, JobOutcome.SUCCESS)
        self.assertEqual(len(self.logs), 1000)  # 500 stdout + 500 stderr, merged, none lost

    def test_missing_executable_is_manim_not_found(self):
        result = self.runner.run_job(JOB, ["this-binary-does-not-exist-xyz123"], None, self._log)
        self.assertEqual(result.outcome, JobOutcome.MANIM_NOT_FOUND)

    def test_complete_child_environment_is_forwarded(self):
        env = dict(os.environ)
        env["RUNNER_SENTINEL"] = "child-only"
        result = self.runner.run_job(
            JOB,
            [PY, "-c", "import os; print(os.environ['RUNNER_SENTINEL'])"],
            cancel_event=None,
            on_log=self._log,
            env=env,
        )
        self.assertEqual(result.outcome, JobOutcome.SUCCESS)
        self.assertEqual(self.logs, ["child-only"])

    def test_spawn_oserror_becomes_failed_result_with_diagnostic(self):
        with mock.patch.object(
            process_session_mod.subprocess, "Popen", side_effect=PermissionError("denied")
        ):
            result = self.runner.run_job(JOB, [PY, "-c", "pass"], None, self._log)
        self.assertEqual(result.outcome, JobOutcome.FAILED)
        self.assertIsNone(result.return_code)
        self.assertIn("denied", result.diagnostic)

    def test_missing_executable_has_actionable_diagnostic(self):
        result = self.runner.run_job(JOB, ["this-binary-does-not-exist-xyz123"], None, self._log)
        self.assertEqual(result.outcome, JobOutcome.MANIM_NOT_FOUND)
        self.assertIn("not found", result.diagnostic.lower())

    def test_cancel_silent_child_is_prompt(self):
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        start = time.monotonic()
        result = self.runner.run_job(
            JOB, [PY, "-c", "import time; time.sleep(30)"], cancel_event=cancel, on_log=self._log
        )
        elapsed = time.monotonic() - start
        self.assertEqual(result.outcome, JobOutcome.CANCELLED, result.diagnostic)
        self.assertLess(elapsed, 10)  # cancelled long before the 30s sleep finishes

    def test_cancel_escalates_to_kill_when_sigterm_ignored(self):
        # Child ignores SIGTERM, so terminate() alone never stops it; only the
        # terminate->wait(timeout)->kill escalation ends the 30s sleep.
        runner = RenderRunner(terminate_timeout=0.3)
        cancel = threading.Event()
        stub = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(30)\n"
        )
        logs = []
        threading.Timer(0.4, cancel.set).start()  # cancel once the child is up
        start = time.monotonic()
        result = runner.run_job(JOB, [PY, "-c", stub], cancel_event=cancel, on_log=logs.append)
        elapsed = time.monotonic() - start
        self.assertEqual(result.outcome, JobOutcome.CANCELLED, result.diagnostic)
        self.assertIsNotNone(result.return_code)
        self.assertIn("ready", logs)
        self.assertLess(elapsed, 10)  # SIGKILL fallback fired; not waiting out the 30s sleep

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group semantics")
    def test_natural_leader_exit_cleans_descendant_holding_stdout(self):
        runner = RenderRunner(terminate_timeout=0.3, drain_timeout=0.3)
        stub = (
            "import subprocess, sys\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "print('GRANDCHILD %d' % g.pid, flush=True)\n"
        )
        started = time.monotonic()
        result = runner.run_job(JOB, [PY, "-c", stub], None, self._log)
        elapsed = time.monotonic() - started
        self.assertEqual(result.outcome, JobOutcome.SUCCESS, result.diagnostic)
        self.assertLess(elapsed, 5.0)
        grandchild_pid = int(self.logs[0].split()[1])
        self.assertTrue(
            _wait_for_pid_release(grandchild_pid),
            "descendant survived natural leader exit",
        )

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group semantics")
    def test_natural_leader_exit_force_kills_descendant_ignoring_sigterm(self):
        runner = RenderRunner(terminate_timeout=0.2, drain_timeout=0.2)
        grandchild = (
            "import os, signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print(os.getpid(), flush=True); "
            "time.sleep(30)"
        )
        stub = (
            "import subprocess, sys\n"
            f"g = subprocess.Popen([sys.executable, '-c', {grandchild!r}], "
            "stdout=subprocess.PIPE, text=True)\n"
            "print('GRANDCHILD ' + g.stdout.readline().strip(), flush=True)\n"
        )
        result = runner.run_job(JOB, [PY, "-c", stub], None, self._log)
        grandchild_pid = int(self.logs[0].split()[1])
        try:
            self.assertEqual(result.outcome, JobOutcome.SUCCESS, result.diagnostic)
            self.assertTrue(
                _wait_for_pid_release(grandchild_pid),
                "SIGTERM-ignoring descendant survived forced group cleanup",
            )
        finally:
            try:
                os.kill(grandchild_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @unittest.skipUnless(sys.platform == "win32", "real Windows Job Object semantics")
    def test_windows_natural_leader_exit_cleans_descendant_that_closed_stdout(self):
        runner = RenderRunner(terminate_timeout=0.5, drain_timeout=0.2)
        descendant = "import time; time.sleep(30)"
        stub = (
            "import subprocess, sys, time\n"
            "time.sleep(0.2)\n"
            f"g = subprocess.Popen([sys.executable, '-c', {descendant!r}], "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "print('GRANDCHILD %d' % g.pid, flush=True)\n"
        )
        result = runner.run_job(JOB, [PY, "-c", stub], None, self._log)
        grandchild_pid = int(self.logs[0].split()[1])
        try:
            self.assertEqual(result.outcome, JobOutcome.SUCCESS, result.diagnostic)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not _windows_process_is_active(grandchild_pid):
                    break
                time.sleep(0.05)
            else:
                self.fail("Windows Job Object descendant survived natural leader exit")
        finally:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(grandchild_pid)],
                capture_output=True,
                timeout=3.0,
            )

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group semantics")
    def test_eof_does_not_abandon_live_leader_before_cancel(self):
        runner = RenderRunner(terminate_timeout=0.2, drain_timeout=0.2)
        cancel = threading.Event()
        stub = (
            "import os, sys, time\n"
            "print('LEADER %d' % os.getpid(), flush=True)\n"
            "os.close(sys.stdout.fileno())\n"
            "os.close(sys.stderr.fileno())\n"
            "time.sleep(30)\n"
        )
        threading.Timer(0.5, cancel.set).start()
        result = runner.run_job(JOB, [PY, "-c", stub], cancel, self._log)
        leader_pid = int(self.logs[0].split()[1])
        try:
            self.assertEqual(result.outcome, JobOutcome.CANCELLED, result.diagnostic)
            self.assertIsNotNone(result.return_code)
            with self.assertRaises(ProcessLookupError):
                os.kill(leader_pid, 0)
        finally:
            try:
                os.kill(leader_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group semantics")
    def test_log_callback_failure_still_cleans_process_group(self):
        runner = RenderRunner(terminate_timeout=0.2, drain_timeout=0.2)
        seen = []

        def broken_log(line):
            seen.append(line)
            raise RuntimeError("log sink failed")

        stub = (
            "import os, time\n" "print('LEADER %d' % os.getpid(), flush=True)\n" "time.sleep(30)\n"
        )
        result = runner.run_job(JOB, [PY, "-c", stub], None, broken_log)
        leader_pid = int(seen[0].split()[1])
        try:
            self.assertEqual(result.outcome, JobOutcome.FAILED)
            self.assertIn("log sink failed", result.diagnostic)
            with self.assertRaises(ProcessLookupError):
                os.kill(leader_pid, 0)
        finally:
            try:
                os.kill(leader_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group semantics")
    def test_baseexception_log_callback_still_cleans_process_group(self):
        class FatalLogError(BaseException):
            pass

        runner = RenderRunner(terminate_timeout=0.2, drain_timeout=0.2)
        seen = []

        def broken_log(line):
            seen.append(line)
            raise FatalLogError("fatal log sink failure")

        stub = (
            "import os, time\n" "print('LEADER %d' % os.getpid(), flush=True)\n" "time.sleep(30)\n"
        )
        leader_pid = None
        try:
            with self.assertRaisesRegex(FatalLogError, "fatal log sink failure"):
                runner.run_job(JOB, [PY, "-c", stub], None, broken_log)
            leader_pid = int(seen[0].split()[1])
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    os.killpg(leader_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("process group survived BaseException from log callback")
        finally:
            if leader_pid is not None:
                try:
                    os.killpg(leader_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(leader_pid, 0)
                except ChildProcessError:
                    pass

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group semantics")
    def test_reader_start_failure_rolls_back_real_sleeping_child(self):
        runner = RenderRunner(terminate_timeout=0.2, drain_timeout=0.2)
        real_popen = subprocess.Popen
        spawned = []

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process

        try:
            with (
                mock.patch.object(process_session_mod.subprocess, "Popen", side_effect=spawn),
                mock.patch.object(
                    process_session_mod.threading.Thread,
                    "start",
                    side_effect=RuntimeError("reader thread could not start"),
                ),
            ):
                try:
                    result = runner.run_job(
                        JOB,
                        [PY, "-c", "import time; time.sleep(30)"],
                        None,
                        self._log,
                    )
                except RuntimeError as exc:
                    self.fail(f"post-spawn setup failure escaped run_job: {exc}")

            self.assertEqual(result.outcome, JobOutcome.FAILED)
            self.assertIn("reader thread could not start", result.diagnostic)
            self.assertEqual(len(spawned), 1)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    os.killpg(spawned[0].pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("sleeping process group survived reader startup failure")
        finally:
            if spawned:
                try:
                    os.killpg(spawned[0].pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    spawned[0].wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    pass

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group semantics")
    def test_post_spawn_filenotfound_is_failed_and_rolls_back_real_sleeping_child(self):
        runner = RenderRunner(terminate_timeout=0.2, drain_timeout=0.2)
        real_popen = subprocess.Popen
        spawned = []

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process

        try:
            with (
                mock.patch.object(process_session_mod.subprocess, "Popen", side_effect=spawn),
                mock.patch.object(
                    process_session_mod.threading.Thread,
                    "start",
                    side_effect=FileNotFoundError("reader setup dependency missing"),
                ),
            ):
                result = runner.run_job(
                    JOB,
                    [PY, "-c", "import time; time.sleep(30)"],
                    None,
                    self._log,
                )

            self.assertEqual(len(spawned), 1)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    os.killpg(spawned[0].pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("sleeping process group survived post-spawn setup failure")

            self.assertEqual(result.outcome, JobOutcome.FAILED, result.diagnostic)
            self.assertIn("post-spawn setup failed", result.diagnostic.lower())
            self.assertIn("reader setup dependency missing", result.diagnostic)
        finally:
            if spawned:
                try:
                    os.killpg(spawned[0].pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    spawned[0].wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    spawned[0].kill()
                    spawned[0].wait(timeout=3.0)

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group semantics")
    def test_session_constructor_baseexception_rolls_back_real_sleeping_child(self):
        class ConstructorFatal(BaseException):
            pass

        fatal = ConstructorFatal("session construction interrupted")

        class BrokenSession(process_session_mod._ProcessSession):
            def __init__(self, *args, **kwargs):
                raise fatal

        real_popen = subprocess.Popen
        spawned = []

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process

        try:
            with mock.patch.object(
                process_session_mod.subprocess,
                "Popen",
                side_effect=spawn,
            ):
                with self.assertRaises(ConstructorFatal) as raised:
                    BrokenSession.start(
                        [PY, "-c", "import time; time.sleep(30)"],
                        env=None,
                        terminate_timeout=0.2,
                        drain_timeout=0.2,
                    )

            self.assertIs(raised.exception, fatal)
            self.assertEqual(len(spawned), 1)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    os.killpg(spawned[0].pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("process group survived session constructor BaseException")
        finally:
            if spawned:
                try:
                    os.killpg(spawned[0].pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    spawned[0].wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    spawned[0].kill()
                    spawned[0].wait(timeout=3.0)

    @unittest.skipIf(sys.platform == "win32", "POSIX direct-leader rollback probe")
    def test_reader_start_failure_kills_leader_when_group_cleanup_errors(self):
        runner = RenderRunner(terminate_timeout=0.1, drain_timeout=0.1)
        real_popen = subprocess.Popen
        spawned = []

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process

        try:
            with (
                mock.patch.object(process_session_mod.subprocess, "Popen", side_effect=spawn),
                mock.patch.object(
                    process_session_mod.threading.Thread,
                    "start",
                    side_effect=RuntimeError("reader thread could not start"),
                ),
                mock.patch.object(
                    process_session_mod,
                    "_signal_group",
                    side_effect=PermissionError("group signal denied"),
                ),
            ):
                result = runner.run_job(
                    JOB,
                    [PY, "-c", "import time; time.sleep(30)"],
                    None,
                    self._log,
                )

            self.assertEqual(result.outcome, JobOutcome.FAILED)
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and spawned[0].poll() is None:
                time.sleep(0.02)
            self.assertIsNotNone(
                spawned[0].poll(),
                "post-Popen rollback must kill the directly owned leader",
            )
        finally:
            if spawned and spawned[0].poll() is None:
                spawned[0].kill()
                spawned[0].wait(timeout=3.0)

    @unittest.skipIf(sys.platform == "win32", "POSIX process-group semantics")
    def test_cancel_kills_grandchild_process_group(self):
        # BL-02: manim spawns ffmpeg; cancelling must stop the whole tree, not just
        # the direct child. A child that forks a long-lived grandchild stands in for
        # manim+ffmpeg. Without start_new_session + killpg the grandchild is
        # re-parented to init and keeps running -- and because it inherits the stdout
        # pipe, run_job itself blocks for the grandchild's full lifetime. So the
        # signal is promptness: a working cancel returns long before the 30s sleep.
        runner = RenderRunner(terminate_timeout=2.0)
        cancel = threading.Event()
        logs = []
        stub = (
            "import subprocess, sys, time\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "print('GRANDCHILD %d' % g.pid, flush=True)\n"
            "time.sleep(30)\n"
        )
        threading.Timer(0.5, cancel.set).start()  # cancel once the grandchild is up
        start = time.monotonic()
        result = runner.run_job(JOB, [PY, "-c", stub], cancel_event=cancel, on_log=logs.append)
        elapsed = time.monotonic() - start
        self.assertEqual(result.outcome, JobOutcome.CANCELLED)
        self.assertLess(elapsed, 10, "cancel did not stop the child tree promptly")
        grandchild_pid = next(
            int(line.split()[1]) for line in logs if line.startswith("GRANDCHILD")
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(grandchild_pid, 0)  # still alive (or a not-yet-reaped zombie)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("grandchild survived cancel -> process group was not killed")

    def test_pump_collapses_carriage_return_progress(self):
        # BL-28: a CR-updated progress bar must collapse to its final value, not one
        # log line per tick, and produce no leading empty line.
        import io

        stream = io.StringIO("Rendering 10%\rRendering 20%\nDone\n", newline="\n")
        out = queue.Queue()
        _pump(stream, out)
        got = []
        while True:
            item = out.get_nowait()
            if item is _EOF:
                break
            got.append(item)
        self.assertEqual(got, ["Rendering 20%", "Done"])

    def test_drain_flushes_queued_lines_and_skips_eof(self):
        # Post-cancel safety flush: deliver any lines still queued, skipping the
        # internal EOF sentinel the reader thread pushes at end-of-stream.
        pending = queue.Queue()
        for item in ("a", "b", _EOF, "c"):
            pending.put(item)
        got = []
        _drain(pending, got.append)
        self.assertEqual(got, ["a", "b", "c"])

    def test_runner_module_does_not_import_qt(self):
        # Order-independent: import ONLY the runner in a fresh interpreter and confirm
        # no Qt binding is pulled in. (Checking this process's sys.modules would be
        # polluted by other test modules that legitimately import PyQt5.)
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = (
            "import sys, app.render.runner; "
            "print('PyQt5' in sys.modules or 'PySide6' in sys.modules)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, cwd=repo_root
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False", proc.stderr)

    def test_wildcard_import_exposes_only_the_runner_public_surface(self):
        namespace = {}
        exec("from app.render.runner import *", namespace)
        self.assertEqual(
            set(namespace) - {"__builtins__"},
            {
                "DEFAULT_DRAIN_TIMEOUT",
                "DEFAULT_SUPERVISION_CLEANUP_TIMEOUT",
                "DEFAULT_TERMINATE_TIMEOUT",
                "JobResult",
                "RenderRunner",
            },
        )


class WindowsProcessSessionTests(unittest.TestCase):
    class _Process:
        pid = 4321
        stdout = None

        def __init__(self):
            self.wait_timeouts = []

        def poll(self):
            return None

        def wait(self, timeout):
            self.wait_timeouts.append(timeout)
            return -9

    def _session(self, timeout):
        process = self._Process()
        session = process_session_mod._ProcessSession(
            process=process,
            group_target=process.pid,
            lines=queue.Queue(),
            reader=threading.Thread(),
            terminate_timeout=timeout,
            drain_timeout=0.1,
        )
        return session, process

    def test_suspended_child_is_assigned_before_resume(self):
        events = []

        class Stdout:
            def reconfigure(self, **_kwargs):
                pass

        class Process:
            pid = 4321
            stdout = Stdout()

        class Job:
            def assign(self, process):
                self.process = process
                events.append("assign")

        class Reader:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                events.append("reader")

        def resume(pid):
            self.assertEqual(pid, Process.pid)
            events.append("resume")

        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(process_session_mod.subprocess, "Popen", return_value=Process()),
            mock.patch.object(process_session_mod, "_WindowsJob", return_value=Job()),
            mock.patch.object(process_session_mod, "_resume_suspended_process", side_effect=resume),
            mock.patch.object(process_session_mod.threading, "Thread", Reader),
        ):
            session = process_session_mod._ProcessSession.start(
                ["manim"],
                env=None,
                terminate_timeout=0.1,
                drain_timeout=0.1,
            )

        self.assertEqual(session.process.pid, Process.pid)
        self.assertEqual(events, ["assign", "resume", "reader"])

    def test_resume_failure_survives_thread_handle_close_failure(self):
        import ctypes

        kernel32 = mock.Mock()
        kernel32.CreateToolhelp32Snapshot.return_value = 100

        def first_thread(_snapshot, entry_pointer):
            entry_pointer._obj.th32ThreadID = 1234
            entry_pointer._obj.th32OwnerProcessID = 4321
            return True

        kernel32.Thread32First.side_effect = first_thread
        kernel32.OpenThread.return_value = 200
        kernel32.ResumeThread.return_value = 0xFFFFFFFF
        kernel32.CloseHandle.side_effect = [False, True]

        with (
            mock.patch.object(ctypes, "WinDLL", return_value=kernel32, create=True),
            mock.patch.object(ctypes, "get_last_error", side_effect=[5, 6], create=True),
            mock.patch.object(
                ctypes,
                "FormatError",
                side_effect=lambda error: f"Windows error {error}",
                create=True,
            ),
        ):
            with self.assertRaisesRegex(
                OSError, "could not resume render process thread"
            ) as caught:
                process_session_mod._resume_suspended_process(4321)

        self.assertTrue(
            any(
                "could not close render process thread handle" in note
                for note in getattr(caught.exception, "__notes__", ())
            ),
            "thread handle close failure must remain attached to the resume failure",
        )

    def test_enumeration_failure_survives_snapshot_close_failure(self):
        import ctypes

        kernel32 = mock.Mock()
        kernel32.CreateToolhelp32Snapshot.return_value = 100
        kernel32.Thread32First.return_value = False
        kernel32.CloseHandle.return_value = False

        with (
            mock.patch.object(ctypes, "WinDLL", return_value=kernel32, create=True),
            mock.patch.object(ctypes, "get_last_error", side_effect=[5, 6], create=True),
            mock.patch.object(
                ctypes,
                "FormatError",
                side_effect=lambda error: f"Windows error {error}",
                create=True,
            ),
        ):
            with self.assertRaisesRegex(
                OSError, "could not enumerate Windows process threads"
            ) as caught:
                process_session_mod._resume_suspended_process(4321)

        self.assertTrue(
            any(
                "could not close Windows thread snapshot" in note
                for note in getattr(caught.exception, "__notes__", ())
            ),
            "snapshot close failure must remain attached to the enumeration failure",
        )

    def test_job_configuration_failure_survives_job_close_failure(self):
        import ctypes

        kernel32 = mock.Mock()
        kernel32.CreateJobObjectW.return_value = 100
        kernel32.SetInformationJobObject.return_value = False
        kernel32.CloseHandle.return_value = False

        with (
            mock.patch.object(ctypes, "WinDLL", return_value=kernel32, create=True),
            mock.patch.object(ctypes, "get_last_error", side_effect=[5, 6], create=True),
            mock.patch.object(
                ctypes,
                "FormatError",
                side_effect=lambda error: f"Windows error {error}",
                create=True,
            ),
        ):
            with self.assertRaisesRegex(
                OSError, "could not configure Windows Job Object"
            ) as caught:
                process_session_mod._WindowsJob()

        self.assertEqual(caught.exception.errno, 5)
        self.assertTrue(
            any(
                "could not close Windows Job Object" in note
                for note in getattr(caught.exception, "__notes__", ())
            ),
            "Job Object close failure must remain attached to the configuration failure",
        )

    def test_existing_diagnostic_does_not_skip_windows_job_close(self):
        class Job:
            assigned = True

            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class Reader:
            ident = 1

            def join(self, timeout):
                pass

            def is_alive(self):
                return False

        session, _process = self._session(0.01)
        session.reader = Reader()
        job = Job()
        session.windows_job = job
        session.lines.put(process_session_mod._EOF)
        with (
            mock.patch.object(session, "_group_is_active", return_value=True),
            mock.patch.object(
                session,
                "_stop_group",
                return_value="process supervision failed: forced taskkill timed out",
            ),
        ):
            cancel = threading.Event()
            cancel.set()
            _return_code, _cancelled, diagnostic = session.finish(cancel, lambda _line: None)

        self.assertIn("forced taskkill timed out", diagnostic)
        self.assertTrue(job.closed, "diagnostics must not suppress Job Object closure")
        self.assertIsNone(session.windows_job)

    def test_windows_job_close_error_is_retained_with_existing_diagnostic(self):
        class Job:
            assigned = True

            def close(self):
                raise OSError("close denied")

        class Reader:
            ident = 1

            def join(self, timeout):
                pass

            def is_alive(self):
                return False

        session, _process = self._session(0.01)
        session.reader = Reader()
        session.windows_job = Job()
        cancel = threading.Event()
        cancel.set()
        with mock.patch.object(
            session,
            "_stop_group",
            return_value="process supervision failed: forced taskkill timed out",
        ):
            _return_code, _cancelled, diagnostic = session.finish(cancel, lambda _line: None)

        self.assertIn("forced taskkill timed out", diagnostic)
        self.assertIn("could not close Windows Job Object", diagnostic)

    def test_cleanup_baseexception_does_not_mask_original_baseexception(self):
        class OriginalFatal(BaseException):
            pass

        class CleanupFatal(BaseException):
            pass

        class Job:
            assigned = True

            def close(self):
                raise CleanupFatal("close interrupted")

        class Reader:
            ident = 1

            def join(self, timeout):
                pass

            def is_alive(self):
                return False

        session, process = self._session(0.01)
        session.reader = Reader()
        session.windows_job = Job()
        session.lines.put("line")

        def fail_log(_line):
            raise OriginalFatal("original failure")

        with (
            mock.patch.object(process, "poll", side_effect=[None, 0]),
            mock.patch.object(session, "_stop_group", return_value=""),
            mock.patch.object(session, "_reap_leader", return_value=(0, "")),
        ):
            with self.assertRaisesRegex(OriginalFatal, "original failure"):
                session.finish(None, fail_log)

    def test_graceful_taskkill_timeout_escalates_with_bounded_force(self):
        session, process = self._session(0.25)
        graceful_timeout = subprocess.TimeoutExpired("taskkill", 0.25)
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess,
                "run",
                side_effect=[graceful_timeout, subprocess.CompletedProcess([], 0)],
            ) as taskkill,
        ):
            diagnostic = session._stop_group()

        self.assertEqual(diagnostic, "")
        self.assertEqual(process.wait_timeouts, [0.25])
        self.assertEqual(
            taskkill.call_args_list,
            [
                mock.call(
                    ["taskkill", "/T", "/PID", "4321"],
                    capture_output=True,
                    timeout=0.25,
                ),
                mock.call(
                    ["taskkill", "/F", "/T", "/PID", "4321"],
                    capture_output=True,
                    timeout=0.25,
                ),
            ],
        )

    def test_forced_taskkill_timeout_is_supervision_failure(self):
        session, _process = self._session(0.01)
        forced_timeout = subprocess.TimeoutExpired("taskkill", 0.01)
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess,
                "run",
                side_effect=[subprocess.CompletedProcess([], 0), forced_timeout],
            ) as taskkill,
        ):
            diagnostic = session._stop_group()

        self.assertTrue(diagnostic.startswith("process supervision failed:"), diagnostic)
        self.assertIn("forced taskkill timed out", diagnostic)
        self.assertEqual(taskkill.call_args_list[1].kwargs["timeout"], 0.01)

    def test_forced_taskkill_timeout_recovers_with_windows_job(self):
        class Job:
            assigned = True

            def __init__(self):
                self.terminated = False

            def active_processes(self):
                return 0 if self.terminated else 1

            def terminate(self):
                self.terminated = True

        session, _process = self._session(0.01)
        job = Job()
        session.windows_job = job
        taskkill_timeout = subprocess.TimeoutExpired("taskkill", 0.01)
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess,
                "run",
                side_effect=[taskkill_timeout, taskkill_timeout],
            ),
        ):
            diagnostic = session._stop_group()

        self.assertTrue(job.terminated, "Job Object fallback must stop the owned tree")
        self.assertEqual(
            diagnostic,
            "",
            "a verified-empty owned Job Object makes taskkill failure non-fatal",
        )

    def test_taskkill_errors_are_nonfatal_after_owned_job_release(self):
        class Job:
            assigned = True

            def __init__(self):
                self.terminated = False

            def active_processes(self):
                return 0 if self.terminated else 1

            def terminate(self):
                self.terminated = True

        session, _process = self._session(0.01)
        session.windows_job = Job()
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 5, stderr=b"graceful unavailable"),
                    subprocess.CompletedProcess([], 7, stderr=b"forced raced with exit"),
                ],
            ),
        ):
            diagnostic = session._stop_group()

        self.assertEqual(diagnostic, "")

    def test_nonzero_graceful_taskkill_is_supervision_failure(self):
        session, _process = self._session(0.01)
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 5, stderr=b"access denied"),
                    subprocess.CompletedProcess([], 0),
                ],
            ) as taskkill,
        ):
            diagnostic = session._stop_group()

        self.assertTrue(diagnostic.startswith("process supervision failed:"), diagnostic)
        self.assertIn("taskkill exited with code 5", diagnostic)
        self.assertEqual(taskkill.call_count, 2)

    def test_nonzero_forced_taskkill_is_supervision_failure(self):
        session, _process = self._session(0.01)
        graceful_timeout = subprocess.TimeoutExpired("taskkill", 0.01)
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess,
                "run",
                side_effect=[
                    graceful_timeout,
                    subprocess.CompletedProcess([], 7, stdout=b"not found"),
                ],
            ) as taskkill,
        ):
            diagnostic = session._stop_group()

        self.assertTrue(diagnostic.startswith("process supervision failed:"), diagnostic)
        self.assertIn("taskkill exited with code 7", diagnostic)
        self.assertEqual(taskkill.call_count, 2)

    def test_graceful_nonzero_is_retained_when_forced_taskkill_times_out(self):
        session, _process = self._session(0.01)
        forced_timeout = subprocess.TimeoutExpired("taskkill", 0.01)
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 5, stderr=b"access denied"),
                    forced_timeout,
                ],
            ),
        ):
            diagnostic = session._stop_group()

        self.assertIn("taskkill exited with code 5", diagnostic)
        self.assertIn("forced taskkill timed out", diagnostic)

    def test_forced_nonzero_is_retained_when_leader_wait_times_out(self):
        session, process = self._session(0.01)
        graceful_timeout = subprocess.TimeoutExpired("taskkill", 0.01)
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess,
                "run",
                side_effect=[
                    graceful_timeout,
                    subprocess.CompletedProcess([], 7, stderr=b"access denied"),
                ],
            ),
            mock.patch.object(
                process,
                "wait",
                side_effect=subprocess.TimeoutExpired("render", 0.01),
            ),
        ):
            diagnostic = session._stop_group()

        self.assertIn("taskkill exited with code 7", diagnostic)
        self.assertIn("process did not stop after forced termination", diagnostic)

    def test_taskkill_diagnostics_survive_windows_job_query_failure(self):
        class Job:
            assigned = True

            def active_processes(self):
                raise OSError("query denied")

        session, _process = self._session(0.01)
        session.windows_job = Job()
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 5, stderr=b"graceful denied"),
                    subprocess.CompletedProcess([], 7, stderr=b"forced denied"),
                ],
            ),
        ):
            diagnostic = session._stop_group()

        self.assertIn("taskkill exited with code 5", diagnostic)
        self.assertIn("taskkill exited with code 7", diagnostic)
        self.assertIn("query denied", diagnostic)

    def test_default_cleanup_budget_covers_late_baseexception_retry(self):
        class FatalLogError(BaseException):
            pass

        class Process:
            pid = 4321
            stdout = None

            def poll(self):
                return None

            def wait(self, timeout):
                clock[0] += timeout
                raise subprocess.TimeoutExpired("render", timeout)

            def kill(self):
                pass

        class Reader:
            ident = 1

            def join(self, timeout):
                clock[0] += timeout

            def is_alive(self):
                return False

        class Lines:
            def get(self, timeout):
                clock[0] += timeout
                raise queue.Empty

            def get_nowait(self):
                return "late line"

        class Job:
            assigned = True

            def active_processes(self):
                return 1

            def terminate(self):
                pass

            def close(self):
                pass

        clock = [0.0]
        session = process_session_mod._ProcessSession(
            process=Process(),
            group_target=4321,
            lines=Lines(),
            reader=Reader(),
            terminate_timeout=runner_mod.DEFAULT_TERMINATE_TIMEOUT,
            drain_timeout=runner_mod.DEFAULT_DRAIN_TIMEOUT,
            windows_job=Job(),
        )
        cancel = threading.Event()
        cancel.set()

        def run_taskkill(args, *, capture_output, timeout):
            clock[0] += timeout
            return subprocess.CompletedProcess(args, 0)

        def advance_clock(duration):
            clock[0] += duration

        def fail_log(_line):
            raise FatalLogError()

        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(process_session_mod.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(process_session_mod.time, "sleep", side_effect=advance_clock),
            mock.patch.object(process_session_mod.subprocess, "run", side_effect=run_taskkill),
        ):
            with self.assertRaises(FatalLogError):
                session.finish(cancel, fail_log)

        expected_bound = (
            10 * runner_mod.DEFAULT_TERMINATE_TIMEOUT + 3 * runner_mod.DEFAULT_DRAIN_TIMEOUT
        )
        self.assertEqual(clock[0], expected_bound, "test must execute every bounded retry phase")
        self.assertGreaterEqual(
            runner_mod.DEFAULT_SUPERVISION_CLEANUP_TIMEOUT,
            clock[0],
            "exported cleanup budget must cover exceptional cleanup retries",
        )

    def test_posix_group_probe_retries_transient_permission_error(self):
        session, _process = self._session(0.1)
        with (
            mock.patch.object(process_session_mod.sys, "platform", "darwin"),
            mock.patch.object(
                process_session_mod.os,
                "killpg",
                side_effect=[PermissionError("group is disappearing"), ProcessLookupError()],
                create=True,
            ) as probe,
            mock.patch.object(process_session_mod.time, "sleep"),
        ):
            try:
                released = session._wait_for_group_release(time.monotonic() + 1.0)
            except PermissionError as exc:
                self.fail(f"transient process-group probe failure escaped: {exc}")

        self.assertTrue(released)
        self.assertEqual(probe.call_count, 2)


class CreationFlagsTests(unittest.TestCase):
    """Windows must not flash a console window for the manim child; POSIX is a no-op."""

    def test_create_no_window_on_win32(self):
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True
            ),
        ):
            self.assertTrue(_creation_flags() & 0x08000000, "CREATE_NO_WINDOW not set")

    def test_new_process_group_ored_in_on_win32(self):
        # BL-02: the child needs its own process group so cancel can taskkill /T the tree.
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(
                process_session_mod.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True
            ),
            mock.patch.object(
                process_session_mod.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True
            ),
            mock.patch.object(
                process_session_mod.subprocess, "CREATE_SUSPENDED", 0x00000004, create=True
            ),
        ):
            flags = _creation_flags()
        self.assertTrue(flags & 0x00000004, "CREATE_SUSPENDED not set")
        self.assertTrue(flags & 0x00000200, "CREATE_NEW_PROCESS_GROUP not set")
        self.assertTrue(flags & 0x08000000, "CREATE_NO_WINDOW not set")

    def test_create_suspended_uses_win32_value_when_subprocess_omits_symbol(self):
        with (
            mock.patch.object(process_session_mod.sys, "platform", "win32"),
            mock.patch.object(process_session_mod.subprocess, "CREATE_NO_WINDOW", 0, create=True),
            mock.patch.object(
                process_session_mod.subprocess, "CREATE_NEW_PROCESS_GROUP", 0, create=True
            ),
            mock.patch.object(process_session_mod.subprocess, "CREATE_SUSPENDED", 0, create=True),
        ):
            flags = _creation_flags()
        self.assertTrue(flags & 0x00000004, "CREATE_SUSPENDED not set")

    def test_zero_off_win32(self):
        with mock.patch.object(process_session_mod.sys, "platform", "linux"):
            self.assertEqual(_creation_flags(), 0)


if __name__ == "__main__":
    unittest.main()
