"""Bounded, Qt-free supervision for one render process session."""

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

_POLL_INTERVAL = 0.1
_EOF = object()


class _PostSpawnSetupError(Exception):
    """Wrap an ordinary acquisition failure after ``Popen`` succeeds."""


def _creation_flags():
    """Return the isolated-process creation flags supported by this platform."""
    if sys.platform == "win32":
        suspended = 0x00000004
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return suspended | no_window | new_group
    return 0


def _resume_suspended_process(pid):
    """Find and resume the initial thread of a suspended Windows process."""
    import ctypes
    from ctypes import wintypes

    th32cs_snapthread = 0x00000004
    thread_suspend_resume = 0x0002
    error_no_more_files = 18
    error_not_found = 1168
    resume_failed = 0xFFFFFFFF

    class _ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    )
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    )
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    def raise_windows_error(action, error=None):
        if error is None:
            error = ctypes.get_last_error()
        message = ctypes.FormatError(error).strip()
        raise OSError(error, f"{action}: {message}")

    def close_handle(handle, description):
        primary_error = sys.exception()
        if kernel32.CloseHandle(handle):
            return
        try:
            raise_windows_error(f"could not close {description}")
        except OSError as close_error:
            if primary_error is None:
                raise
            primary_error.add_note(f"cleanup also failed: {close_error}")

    snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapthread, 0)
    invalid_handle = wintypes.HANDLE(-1).value
    if snapshot == invalid_handle:
        raise_windows_error("could not snapshot Windows process threads")

    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Thread32First(snapshot, ctypes.byref(entry)):
            raise_windows_error("could not enumerate Windows process threads")

        while True:
            if entry.th32OwnerProcessID == pid:
                thread = kernel32.OpenThread(
                    thread_suspend_resume,
                    False,
                    entry.th32ThreadID,
                )
                if not thread:
                    raise_windows_error("could not open suspended render process thread")
                try:
                    if kernel32.ResumeThread(thread) == resume_failed:
                        raise_windows_error("could not resume render process thread")
                finally:
                    close_handle(thread, "render process thread handle")
                return

            if not kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != error_no_more_files:
                    raise_windows_error("could not enumerate Windows process threads", error)
                raise_windows_error(
                    f"could not find a thread for render process {pid}",
                    error_not_found,
                )
    finally:
        close_handle(snapshot, "Windows thread snapshot")


def _pump(stream, lines):
    try:
        for chunk in iter(stream.readline, ""):
            text = chunk.rstrip("\r\n")
            if "\r" in text:
                text = text.rsplit("\r", 1)[-1]
            if text:
                lines.put(text)
    finally:
        lines.put(_EOF)


def _drain(lines, on_log):
    while True:
        try:
            item = lines.get_nowait()
        except queue.Empty:
            return
        if item is not _EOF:
            on_log(item)


def _signal_group(group_target, force, timeout):
    """Signal the captured process group/tree without consulting its leader."""
    if sys.platform == "win32":
        args = ["taskkill", "/T", "/PID", str(group_target)]
        if force:
            args.insert(1, "/F")
        completed = subprocess.run(args, capture_output=True, timeout=timeout)
        if completed.returncode:
            detail = completed.stderr or completed.stdout or b""
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            detail = str(detail).strip()
            suffix = f": {detail}" if detail else ""
            return f"taskkill exited with code {completed.returncode}{suffix}"
        return ""

    # A captured numeric PGID is not an owned handle: it can theoretically be
    # reused only after the original group empties, and portable zombie-safe
    # membership enumeration is unavailable. Reader EOF/join and structured
    # diagnostics therefore bound the observable cleanup contract.
    try:
        os.killpg(group_target, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pass
    return ""


def _rollback_spawned_process(process, group_target, terminate_timeout):
    """Best-effort bounded cleanup before a session instance exists."""
    try:
        _signal_group(group_target, force=False, timeout=terminate_timeout)
    except BaseException:
        pass
    try:
        process.wait(timeout=terminate_timeout)
    except BaseException:
        pass
    try:
        _signal_group(group_target, force=True, timeout=terminate_timeout)
    except BaseException:
        pass
    try:
        if process.poll() is None:
            process.kill()
    except BaseException:
        pass
    try:
        process.wait(timeout=terminate_timeout)
    except BaseException:
        pass
    try:
        if process.stdout is not None:
            process.stdout.close()
    except BaseException:
        pass


class _WindowsJob:
    """Small ctypes owner for one Windows process tree."""

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class _BasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._accounting_type = _BasicAccountingInformation
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_api()
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        self.assigned = False
        if not self._handle:
            self._raise_last_error("could not create Windows Job Object")
        try:
            limits = _ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self._kernel32.SetInformationJobObject(
                self._handle,
                self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                self._raise_last_error("could not configure Windows Job Object")
        except BaseException:
            primary_error = sys.exception()
            try:
                self.close()
            except BaseException as cleanup_error:
                primary_error.add_note(f"cleanup also failed: {cleanup_error}")
            raise

    def _configure_api(self):
        ctypes = self._ctypes
        wintypes = self._wintypes
        self._kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
        )
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPDWORD,
        )
        self._kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        self._kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        self._kernel32.TerminateJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def _raise_last_error(self, action):
        error = self._ctypes.get_last_error()
        message = self._ctypes.FormatError(error).strip()
        raise OSError(error, f"{action}: {message}")

    def assign(self, process):
        process_handle = self._wintypes.HANDLE(int(process._handle))
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            self._raise_last_error("could not assign render process to Windows Job Object")
        self.assigned = True

    def active_processes(self):
        if not self.assigned:
            return 0
        info = self._accounting_type()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            self._JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            self._ctypes.byref(info),
            self._ctypes.sizeof(info),
            None,
        ):
            self._raise_last_error("could not query Windows Job Object")
        return info.ActiveProcesses

    def terminate(self):
        if self.assigned and not self._kernel32.TerminateJobObject(self._handle, 1):
            self._raise_last_error("could not terminate Windows Job Object")

    def close(self):
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        if not self._kernel32.CloseHandle(handle):
            self._raise_last_error("could not close Windows Job Object")


@dataclass
class _ProcessSession:
    process: object
    group_target: int
    lines: queue.Queue
    reader: threading.Thread
    terminate_timeout: float
    drain_timeout: float
    windows_job: object = None

    @classmethod
    def start(cls, command, *, env, terminate_timeout, drain_timeout):
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
            start_new_session=(sys.platform != "win32"),
            env=env,
        )
        session = None
        try:
            session = cls(
                process,
                process.pid,
                lines=None,
                reader=None,
                terminate_timeout=terminate_timeout,
                drain_timeout=drain_timeout,
            )
            if sys.platform == "win32":
                session.windows_job = _WindowsJob()
                session.windows_job.assign(process)
                _resume_suspended_process(process.pid)
            try:
                process.stdout.reconfigure(newline="\n")
            except (AttributeError, ValueError):
                pass
            session.lines = queue.Queue()
            session.reader = threading.Thread(
                target=_pump,
                args=(process.stdout, session.lines),
                daemon=True,
            )
            session.reader.start()
        except BaseException as exc:
            if session is None:
                _rollback_spawned_process(process, process.pid, terminate_timeout)
            else:
                session._cleanup_after_error()
            if isinstance(exc, Exception):
                raise _PostSpawnSetupError(exc) from exc
            raise
        return session

    def finish(self, cancel_event, on_log):
        try:
            return self._finish(cancel_event, on_log)
        except BaseException:
            self._cleanup_after_error()
            raise

    def _finish(self, cancel_event, on_log):
        cancelled = False
        eof = False
        while self.process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if eof:
                time.sleep(_POLL_INTERVAL)
                continue
            try:
                item = self.lines.get(timeout=_POLL_INTERVAL)
            except queue.Empty:
                continue
            if item is _EOF:
                eof = True
                continue
            on_log(item)

        diagnostic = ""
        if cancelled:
            diagnostic = self._stop_group()
        else:
            if not eof:
                eof = self._drain_until(time.monotonic() + self.drain_timeout, on_log)
            if self._group_is_active():
                diagnostic = self._stop_group()

        return_code, reap_error = self._reap_leader()
        diagnostic = diagnostic or reap_error
        if not eof:
            eof = self._drain_until(time.monotonic() + self.drain_timeout, on_log)
        self.reader.join(timeout=self.drain_timeout)
        if self.reader.is_alive():
            diagnostic = diagnostic or "process supervision failed: stdout reader did not stop"
        else:
            _drain(self.lines, on_log)
            if self.process.stdout is not None:
                self.process.stdout.close()
        close_error = self._close_windows_job()
        if close_error:
            diagnostic = f"{diagnostic}; {close_error}" if diagnostic else close_error
        return return_code, cancelled, diagnostic

    def _drain_until(self, deadline, on_log):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                item = self.lines.get(timeout=min(_POLL_INTERVAL, remaining))
            except queue.Empty:
                continue
            if item is _EOF:
                return True
            on_log(item)

    def _stop_group(self):
        diagnostics = []
        # A no-window child can reject graceful taskkill, and forced taskkill
        # can race a concurrently exiting process. Keep those diagnostics only
        # until the owned Job Object proves that the complete tree is gone.
        taskkill_diagnostics = []
        owned_job_released = False
        try:
            if self._windows_job_owns_tree() and self.process.poll() is not None:
                try:
                    self.windows_job.terminate()
                except Exception as exc:
                    diagnostics.append(f"could not terminate Windows Job Object: {exc}")
                else:
                    deadline = time.monotonic() + self.terminate_timeout
                    if self._wait_for_group_release(deadline):
                        return ""
                    diagnostics.append("Windows Job Object did not become empty")

            try:
                graceful_error = _signal_group(
                    self.group_target,
                    force=False,
                    timeout=self.terminate_timeout,
                )
            except subprocess.TimeoutExpired:
                pass
            else:
                if graceful_error:
                    taskkill_diagnostics.append(graceful_error)
                elif self._wait_for_group_release(time.monotonic() + self.terminate_timeout):
                    return ""
            try:
                force_error = _signal_group(
                    self.group_target,
                    force=True,
                    timeout=self.terminate_timeout,
                )
            except subprocess.TimeoutExpired:
                taskkill_diagnostics.append("forced taskkill timed out")
            else:
                if force_error:
                    taskkill_diagnostics.append(force_error)
            if self._windows_job_owns_tree() and self._group_is_active():
                try:
                    self.windows_job.terminate()
                except Exception as exc:
                    diagnostics.append(f"could not terminate Windows Job Object: {exc}")
            try:
                self.process.wait(timeout=self.terminate_timeout)
            except subprocess.TimeoutExpired:
                diagnostics.append("process did not stop after forced termination")
        except Exception as exc:
            diagnostics.append(f"could not stop process group: {exc}")
        else:
            try:
                if self._windows_job_owns_tree():
                    if self._group_is_active():
                        diagnostics.append(
                            "Windows Job Object remained active after forced termination"
                        )
                    else:
                        owned_job_released = True
            except Exception as exc:
                diagnostics.append(f"could not verify Windows Job Object state: {exc}")
        if not owned_job_released:
            diagnostics.extend(taskkill_diagnostics)
        if diagnostics:
            return "process supervision failed: " + "; ".join(diagnostics)
        return ""

    def _wait_for_group_release(self, deadline):
        while True:
            self.process.poll()
            if not self._group_is_active():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(_POLL_INTERVAL, remaining))

    def _group_is_active(self):
        if sys.platform == "win32":
            if self._windows_job_owns_tree():
                return self.windows_job.active_processes() > 0
            reader_alive = self.reader is not None and self.reader.is_alive()
            return self.process.poll() is None or reader_alive
        try:
            os.killpg(self.group_target, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # A just-terminated group can briefly be visible but unsignalable on
            # macOS. Keep treating it as active so the bounded wait retries; a
            # persistent permission failure still reaches forced cleanup.
            return True
        return True

    def _windows_job_owns_tree(self):
        return (
            sys.platform == "win32" and self.windows_job is not None and self.windows_job.assigned
        )

    def _reap_leader(self):
        try:
            return self.process.wait(timeout=self.terminate_timeout), ""
        except subprocess.TimeoutExpired:
            return None, "process supervision failed: process leader did not exit"
        except Exception as exc:
            return None, f"process supervision failed: could not reap process leader: {exc}"

    def _cleanup_after_error(self):
        try:
            self._stop_group()
            if self.process.poll() is None:
                self.process.kill()
            self._reap_leader()
            if self.reader is not None and self.reader.ident is not None:
                self.reader.join(timeout=self.drain_timeout)
            reader_alive = self.reader is not None and self.reader.is_alive()
            if not reader_alive and self.process.stdout is not None:
                self.process.stdout.close()
        except BaseException:
            pass
        finally:
            try:
                self._close_windows_job()
            except BaseException:
                pass

    def _close_windows_job(self):
        if self.windows_job is None:
            return ""
        job = self.windows_job
        self.windows_job = None
        try:
            job.close()
        except Exception as exc:
            return f"process supervision failed: could not close Windows Job Object: {exc}"
        return ""
