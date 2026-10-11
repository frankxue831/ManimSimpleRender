"""RenderController: mediator between MainView and the services.

Owns the render worker lifecycle and a small state machine
(idle -> running -> [cancelling] -> idle; validation runs inline while idle).
Replaces the legacy
orchestration scattered across ui.py:187-235, 257-269, 283-304 -- including the
private ``thread._is_running`` reach-in (ui.py:286), now an explicit RenderSummary
signal. Resolution/engine flags come from the typed config by selected index.
Worker and discovery are injectable for headless testing.
"""

import os
import threading

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5 import sip

from app.environment.doctor import check_environment
from app.render.command_builder import build_command, display_command
from app.render.jobs import build_jobs, media_output_collisions
from app.render.runner import DEFAULT_SUPERVISION_CLEANUP_TIMEOUT
from app.render.worker import RenderWorker
from app.scenes.discovery import discover_scene_result
from app.validation import manim_available, validate_render_request

# The normal close path remains asynchronous, so it can afford the render
# supervisor's full cleanup budget without freezing the GUI thread.
_SHUTDOWN_WAIT_MS = int(DEFAULT_SUPERVISION_CLEANUP_TIMEOUT * 1000)
_FORCED_SHUTDOWN_WAIT_MS = 2000


class RenderController(QObject):
    # Carries a finished discovery result (a tuple) from the worker thread back to
    # the GUI thread; a queued connection marshals it across threads safely.
    _discovery_done = pyqtSignal(object)
    # Same pattern for the environment doctor (Release B1): the background task
    # emits (report, error) and only the GUI thread touches the view.
    _environment_done = pyqtSignal(object)

    def __init__(
        self,
        view,
        config,
        *,
        worker_factory=None,
        discover_fn=None,
        settings=None,
        manim_probe=None,
        doctor=None,
        submit=None,
        parent=None,
    ):
        super().__init__(parent)
        self._view = view
        self._config = config
        self._worker_factory = worker_factory or _default_worker_factory
        self._discover_fn = discover_fn or discover_scene_result
        self._manim_available = manim_probe or manim_available
        self._doctor = doctor or check_environment
        self._submit = submit or _default_task_submit
        self._settings = settings
        self._worker = None
        self._pending_summary = None
        self._state = "idle"
        self._discovery_seq = 0  # bumped per request; only the latest result is applied
        self._discovery_pending = False
        self._discovery_issues = ()  # (relative_path, detail) per skipped file
        self._discovery_unreadable = ()  # (relative_path, error) per unlistable dir
        self._discovery_scan_error = None  # set when the folder scan itself failed
        self._environment_pending = False  # one doctor run at a time (Release B1)
        self._closing = False
        self._close_pending = False
        self._shutdown_started = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)
        self._forced_shutdown_timer = QTimer(self)
        self._forced_shutdown_timer.setSingleShot(True)
        self._forced_shutdown_timer.timeout.connect(self._on_forced_shutdown_timeout)
        self._discovery_done.connect(self._on_discovery_finished)
        self._environment_done.connect(self._on_environment_finished)

        view.browse_input_requested.connect(self._on_browse_input)
        view.browse_output_requested.connect(self._on_browse_output)
        view.input_path_edited.connect(self._on_input_path_edited)
        view.primary_button_clicked.connect(self.on_primary_clicked)
        view.check_env_requested.connect(self._on_check_environment)
        view.engine_changed.connect(self._on_engine_changed)
        view.select_all_requested.connect(self._on_select_all)
        view.copy_command_requested.connect(self._on_copy_command)
        view.rerun_failed_requested.connect(self._on_rerun_failed)
        view.open_output_requested.connect(self._on_open_output)
        view.save_log_requested.connect(self._on_save_log)
        view.close_requested.connect(self.on_close_requested)

        self._restore_settings()
        self._show_startup_guidance()

    @property
    def state(self):
        return self._state

    @property
    def discovery_pending(self):
        return self._discovery_pending

    # --- primary action / state machine ---
    def on_primary_clicked(self):
        if self._state in ("running", "cancelling"):
            self._cancel()
        else:
            self.start_render()

    def start_render(self):
        if self._state != "idle":
            return  # self-enforcing for all callers (re-entrancy guard, BL-23)
        blocked = self._discovery_block_reason()
        if blocked:
            self._view.set_status(blocked, level="error")
            return
        if self._environment_pending:
            # An in-flight doctor would race the status line and confuse a user who
            # asked "is my environment ok?" and then started rendering anyway.
            self._view.set_status(
                "Environment check is still running; please wait...", level="error"
            )
            return
        input_folder = self._view.input_folder()
        output_folder = self._view.output_folder()
        selected = self._view.selected_scenes()

        result = validate_render_request(input_folder, output_folder, selected)
        if not result.ok:
            for error in result.errors:
                self._view.append_log(error)
            self._view.set_status(result.errors[0], level="error")
            self._state = "idle"
            return

        jobs = build_jobs(selected)
        if not jobs:
            self._view.set_status("No renderable scenes selected.", level="error")
            self._state = "idle"
            return

        collisions = media_output_collisions(jobs)
        if collisions:
            self._view.set_status(
                _media_output_collision_message(collisions, input_folder),
                level="error",
            )
            self._state = "idle"
            return

        # Resolve the output folder to an absolute path so a relative entry lands
        # under the project (not the launch CWD, which is the bundle for a Finder
        # launch) and the field shows the user where files go (BL-06).
        output_folder = os.path.abspath(output_folder)
        self._view.set_output_folder(output_folder)

        # Guard the combo indices before any side effects: QComboBox.currentIndex()
        # returns -1 when empty, and config[-1] would silently pick the LAST option.
        resolution_index = self._view.resolution_index()
        engine_index = self._view.engine_index()
        if not (0 <= resolution_index < len(self._config.resolutions)) or not (
            0 <= engine_index < len(self._config.engines)
        ):
            self._view.set_status("Please select a resolution and an engine.", level="error")
            self._state = "idle"
            return

        if not self._manim_available():
            self._view.set_status(
                "manim is not installed in this Python environment. Install it "
                "(e.g. 'pip install manim') or run from the environment where "
                "manim is available, then try again.",
                level="error",
            )
            self._state = "idle"
            return

        try:
            os.makedirs(output_folder, exist_ok=True)  # create-if-missing (validated non-empty)
        except OSError as exc:
            self._view.set_status(f"Cannot create output folder: {exc}", level="error")
            self._state = "idle"
            return

        resolution_flag = self._config.resolutions[resolution_index].flag
        engine_flag = self._config.engines[engine_index].renderer_flag

        self._save_settings()
        self._view.clear_log()
        self._view.reset_progress()
        self._view.clear_scene_results()
        self._view.set_controls_enabled(False)
        self._view.set_rerun_enabled(False)  # can't rerun mid-render; re-evaluated on finish
        self._view.set_button_mode("cancel")
        self._state = "running"
        self._pending_summary = None

        try:
            worker = self._worker_factory(jobs, resolution_flag, engine_flag, output_folder)
            # Parent the worker so Qt owns its lifetime: clearing the Python ref in
            # _on_thread_finished cannot destroy a still-running QThread ("QThread:
            # Destroyed while thread is still running"). finished (emitted AFTER
            # run() returns) drives both lifecycle completion and deleteLater.
            worker.setParent(self)
            worker.status.connect(self._view.set_status)
            worker.log.connect(self._view.append_log)
            worker.progress.connect(self._view.set_progress)
            worker.job_result.connect(self._on_job_result)
            worker.finished_summary.connect(self._on_summary)
            worker.finished.connect(self._on_thread_finished)
            worker.finished.connect(worker.deleteLater)
            self._worker = worker
            worker.start()
        except Exception as exc:  # factory/wiring/start failed -> don't stick disabled
            self._restore_idle_after_start_failure()
            self._view.set_status(f"Could not start render: {exc}", level="error")

    def _cancel(self):
        if self._worker is not None:
            self._state = "cancelling"
            self._view.set_status("Cancellation requested...")
            self._view.set_progress_busy()  # BL-24: the terminate wait reads as working
            self._view.set_button_mode("cancelling")
            self._worker.request_cancel()

    def _on_job_result(self, job, outcome, return_code):
        self._view.mark_scene_result(job.file_path, job.scene_class, outcome, return_code)

    def _on_summary(self, summary):
        self._pending_summary = summary
        # Style terminal failures red; a user-initiated cancel is not an error.
        level = "error" if (summary.failed or summary.manim_not_found) else ""
        self._view.set_status(summary.terminal_message(), level=level)
        # The primary button is intentionally excluded from set_controls_enabled()
        # while it serves as Cancel. Once accounting is final there is nothing left
        # to cancel, but it must not become Start until QThread.finished arrives.
        self._view.set_button_mode("finishing")

    def _on_thread_finished(self):
        self._shutdown_timer.stop()
        self._forced_shutdown_timer.stop()
        summary = self._pending_summary
        self._pending_summary = None
        self._worker = None
        self._shutdown_started = False
        self._state = "idle"
        self._view.reset_progress()  # finalize the bar (BL-22) + restore determinate (BL-24)
        self._view.set_controls_enabled(True)
        self._view.set_rerun_enabled(bool(self._view.rerunnable_scene_keys()))
        self._view.set_button_mode("start")
        if summary is None:
            self._view.set_status("Render worker stopped without a summary.", level="error")
        else:
            level = "error" if (summary.failed or summary.manim_not_found) else ""
            self._view.set_status(summary.terminal_message(), level=level)
        if self._close_pending:
            self._close_pending = False
            QTimer.singleShot(0, self._view.close)

    def _restore_idle_after_start_failure(self):
        """Restore controls when worker construction, wiring, or start fails."""
        self._worker = None
        self._pending_summary = None
        self._state = "idle"
        self._view.reset_progress()
        self._view.set_controls_enabled(True)
        self._view.set_rerun_enabled(bool(self._view.rerunnable_scene_keys()))
        self._view.set_button_mode("start")

    def on_close_requested(self, event=None):
        """Veto a normal close until the render QThread has finished."""
        self._save_settings()
        self._closing = True
        if self._worker is None:
            return
        if event is not None:
            event.ignore()
        self._close_pending = True
        self._begin_shutdown()

    def _begin_shutdown(self):
        """Request cooperative shutdown once and supervise it asynchronously."""
        if self._worker is None or self._shutdown_started:
            return
        self._shutdown_started = True
        if self._state != "cancelling":
            self._cancel()
        self._shutdown_timer.start(_SHUTDOWN_WAIT_MS)

    def _on_shutdown_timeout(self):
        """Escalate after the cooperative shutdown budget without dropping ownership."""
        if self._worker is None:
            return
        self._worker.terminate()
        self._view.set_status("Render worker is taking longer than expected; forcing it to stop...")
        self._forced_shutdown_timer.start(_FORCED_SHUTDOWN_WAIT_MS)

    def _on_forced_shutdown_timeout(self):
        """Keep a live worker owned and leave the window available for recovery."""
        if self._worker is None:
            return
        self._close_pending = False
        self._shutdown_started = False
        self._closing = False
        self._view.set_status(
            "Could not stop the render worker. The window will remain open; "
            "please wait for it to finish.",
            level="error",
        )

    def on_about_to_quit(self):
        """Synchronously retain the worker through the final non-window quit barrier."""
        self._save_settings()
        self._closing = True
        worker = self._worker
        if worker is None:
            return
        self._begin_shutdown()
        stopped = worker.wait(_SHUTDOWN_WAIT_MS)
        if not stopped:
            worker.terminate()
            worker.wait(_FORCED_SHUTDOWN_WAIT_MS)
        # QApplication is now exiting, so this last QThread wait is the required
        # safety barrier before Qt can destroy the controller and its child worker.
        worker.wait()
        self._shutdown_timer.stop()
        self._forced_shutdown_timer.stop()
        if self._worker is worker:
            self._worker = None

    # --- browse / discovery ---
    def _on_browse_input(self):
        path = self._view.prompt_input_folder()
        if path:
            self._view.set_input_folder(path)
            self.on_input_chosen(path)

    def _on_input_path_edited(self):
        # The field is editable: a typed path must re-discover (same as Browse) so
        # the scene list/selection never desyncs from the input folder.
        self.on_input_chosen(self._view.input_folder())

    def on_input_chosen(self, path):
        # Every call invalidates any older pending discovery task first, then
        # resolves to an absolute path so discovered file paths survive a launch
        # CWD that isn't the selected folder (Finder/frozen), mirroring the
        # output-folder fix (BL-06). Empty stays empty (abspath("") is the CWD).
        self._discovery_seq += 1
        request_id = self._discovery_seq
        self._discovery_issues = ()
        self._discovery_unreadable = ()
        self._discovery_scan_error = None
        self._discovery_pending = False
        if path:
            path = os.path.abspath(path)
            self._view.set_input_folder(path)
        # Distinguish a missing path, a file, and an empty/no-scenes folder instead of
        # collapsing all of them to "No Python files" (BL-10). Short-circuit before
        # submitting discovery so the four cases get honest, distinct messages.
        if path and not os.path.isdir(path):
            if not os.path.exists(path):
                self._view.set_status(f"Folder not found: {path}", level="error")
            else:
                self._view.set_status(
                    "That is a file, not a folder. Choose the folder that contains your "
                    "scene files.",
                    level="error",
                )
            self._view.set_scene_items([])  # clear any stale list
            self._view.set_rerun_enabled(False)
            return
        # Discover off the GUI thread so a large folder can't freeze the UI. Each
        # request gets an id; only the latest result is applied (stale-safe).
        self._discovery_pending = True
        self._view.set_status("Discovering scenes...")
        discover = self._discover_fn

        def task():
            try:
                snapshot = discover(path)
                result = (request_id, snapshot, None)
            except Exception as exc:  # never let a background failure kill the thread
                result = (request_id, None, exc)
            try:
                self._discovery_done.emit(result)
            except RuntimeError:
                # The controller may have been deleted while discovery was running.
                if not sip.isdeleted(self):
                    raise

        try:
            self._submit(task)
        except Exception as exc:
            # An exception escaping this slot would abort the process (qFatal), and a
            # stuck "pending" would block every later render. Report it in-band.
            self._discovery_pending = False
            self._view.set_status(f"Could not start scene discovery: {exc}", level="error")

    def _on_discovery_finished(self, result):
        request_id, snapshot, error = result
        if self._closing or request_id != self._discovery_seq:
            return  # stale (superseded by a newer request) or shutting down
        self._discovery_pending = False
        if error is not None:
            self._discovery_scan_error = f"{type(error).__name__}: {error}"
            self._view.set_scene_items([])
            self._view.set_rerun_enabled(False)
            self._view.set_status(
                _folder_scan_error_message(self._discovery_scan_error), level="error"
            )
            return
        scenes = list(snapshot.scenes)
        count = snapshot.python_file_count
        skipped = snapshot.skipped_files
        unreadable = snapshot.unreadable_dirs
        self._discovery_issues = skipped
        self._discovery_unreadable = unreadable
        self._view.set_scene_items(scenes)
        # Rebuilt rows carry no result decoration, so a stale Rerun button must be
        # re-evaluated (and disabled) here, not left enabled-but-inert (BL-13).
        self._view.set_rerun_enabled(bool(self._view.rerunnable_scene_keys()))
        if unreadable:
            self._view.append_log(
                "Could not read folder(s) during scene discovery:\n"
                + "\n".join(f"- {path}: {detail}" for path, detail in unreadable)
            )
        if skipped:
            self._view.append_log(
                "Skipped Python file(s) during scene discovery:\n"
                + "\n".join(f"- {path}: {detail}" for path, detail in skipped)
            )
        if skipped or unreadable:
            self._view.set_status(
                _discovery_issue_message(skipped, unreadable, len(scenes)), level="error"
            )
            return
        if scenes:
            self._view.set_status(f"{len(scenes)} scene(s) found.")
        elif count == 0:
            self._view.set_status("No Python files found in the selected folder.")
        else:
            self._view.set_status(
                "Found Python file(s) but no renderable scenes "
                "(need a Scene subclass that defines construct())."
            )

    def _discovery_block_reason(self):
        """Why a render must not start yet, or None when discovery is trustworthy."""
        if self._discovery_pending:
            return "Discovering scenes; please wait..."
        if self._discovery_scan_error is not None:
            return _folder_scan_error_message(self._discovery_scan_error)
        if self._discovery_issues or self._discovery_unreadable:
            return _discovery_issue_message(self._discovery_issues, self._discovery_unreadable)
        return None

    def _on_browse_output(self):
        path = self._view.prompt_output_folder()
        if path:
            self._view.set_output_folder(path)

    def _on_check_environment(self):
        # Release B1: the doctor shells out to subprocess probes (BL-07) that may
        # take tens of seconds, so it must never run on the GUI thread. Mirror the
        # discovery pattern: submit a task, marshal the result back via a signal.
        # The state guard keeps programmatic emissions safe mid-render.
        if self._closing or self._state != "idle" or self._environment_pending:
            return
        self._environment_pending = True
        self._view.set_environment_check_pending(True)
        self._view.set_status("Checking environment...")
        doctor = self._doctor  # capture locally; keep the background closure small

        def task():
            try:
                payload = (doctor(), None)
            except Exception as exc:  # marshalled home, not raised on a bare thread (BL-19)
                payload = (None, exc)
            try:
                self._environment_done.emit(payload)
            except RuntimeError:
                # The controller may have been deleted while the doctor was running.
                if not sip.isdeleted(self):
                    raise

        try:
            self._submit(task)
        except Exception as exc:
            self._environment_pending = False
            self._view.set_environment_check_pending(False)
            self._view.set_status(f"Environment check could not run: {exc}", level="error")

    def _on_environment_finished(self, payload):
        self._environment_pending = False
        if self._closing:
            return  # late completion during shutdown must not touch the view
        report, error = payload
        self._view.set_environment_check_pending(False)
        if error is not None:
            self._view.set_status(f"Environment check could not run: {error}", level="error")
            return
        self._view.show_environment_report(report)
        if report.ok:
            self._view.set_status("Environment check passed.")
        else:
            self._view.set_status("Environment check failed - see details.", level="error")

    def _on_engine_changed(self, index):
        # Warn when a non-default (cairo) engine is picked: OpenGL is experimental
        # and fails for some scenes/platforms (e.g. set_z_index missing on macOS).
        if 0 <= index < len(self._config.engines) and self._config.engines[index].renderer_flag:
            self._view.set_status(
                "OpenGL is experimental; if a render fails, switch to the Default engine."
            )

    def _on_select_all(self):
        self._view.select_all_scenes()

    def _on_copy_command(self):
        # Copy the exact manim command(s) for the selected scenes so the user can
        # run/debug them by hand (uses the current resolution/engine/output).
        selected = self._view.selected_scenes()
        if not selected:
            self._view.set_status("Select a scene to copy its command.", level="error")
            return
        resolution_index = self._view.resolution_index()
        engine_index = self._view.engine_index()
        if not (0 <= resolution_index < len(self._config.resolutions)) or not (
            0 <= engine_index < len(self._config.engines)
        ):
            self._view.set_status("Please select a resolution and an engine.", level="error")
            return
        resolution_flag = self._config.resolutions[resolution_index].flag
        engine_flag = self._config.engines[engine_index].renderer_flag
        output = os.path.abspath(self._view.output_folder() or "output")  # paste-safe (BL-06)
        commands = [
            display_command(build_command(job, resolution_flag, output, engine_flag))
            for job in build_jobs(selected)
        ]
        self._view.copy_to_clipboard("\n".join(commands))
        self._view.set_status(f"Copied {len(commands)} command(s) to the clipboard.")

    def _on_rerun_failed(self):
        blocked = self._discovery_block_reason()
        if blocked:
            self._view.set_status(blocked, level="error")
            return
        # Re-render the failed scenes and resume the cancelled (never-run) ones (BL-14).
        keys = self._view.rerunnable_scene_keys()
        if not keys:
            self._view.set_status("No failed or cancelled scenes to rerun.")
            return
        self._view.select_only(set(keys))
        self.start_render()

    def _on_save_log(self):
        # Let the user save/attach the render log for a bug report (BL-20).
        text = self._view.log_text()
        if not text.strip():
            self._view.set_status("Nothing to save yet - the log is empty.")
            return
        path = self._view.prompt_save_log()
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            self._view.set_status(f"Log saved to {path}.")
        except OSError as exc:
            self._view.set_status(f"Could not save log: {exc}", level="error")

    def _on_open_output(self):
        # Give feedback instead of a silent no-op on the always-enabled button (BL-18).
        output_folder = self._view.output_folder()
        if not output_folder:
            self._view.set_status("Choose an output folder first.")
            return
        if not os.path.isdir(output_folder):
            self._view.set_status(
                "Output folder doesn't exist yet - it will be created when you render."
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(output_folder)))

    def _show_startup_guidance(self):
        # Cold start (or any launch with no input folder yet): guide the user instead
        # of a bare "Idle", and surface a missing manim up front (BL-17). Restoring a
        # folder runs discovery, which sets its own status, so only guide when empty.
        # Use the fast manim probe, NOT the full doctor -- the doctor now shells out
        # (BL-07) and must not block launch; the deep check stays on the button.
        if self._view.input_folder():
            return
        try:
            ready = self._manim_available()
        except Exception:
            ready = True  # never block guidance on a probe error
        if not ready:
            self._view.set_status(
                "manim was not found - click Check Environment for details.",
                level="error",
            )
        else:
            self._view.set_status(
                "Choose an input folder containing your Manim .py scenes to begin."
            )

    # --- settings persistence (QSettings; decision 3) ---
    def _restore_settings(self):
        if self._settings is None:
            return
        input_folder = self._settings.value("input_folder", "")
        output_folder = self._settings.value("output_folder", "")
        if input_folder:
            self._view.set_input_folder(input_folder)
            self.on_input_chosen(
                input_folder
            )  # re-run discovery so the scene list is populated on restart
        if output_folder:
            self._view.set_output_folder(output_folder)
        self._restore_choice(
            "resolution_flag",
            "resolution_index",
            self._config.resolutions,
            lambda option: option.flag,
            self._view.set_resolution_index,
        )
        self._restore_choice(
            "engine_flag",
            "engine_index",
            self._config.engines,
            lambda option: option.renderer_flag,
            self._view.set_engine_index,
        )

    def _restore_choice(self, flag_key, legacy_index_key, options, flag_of, setter):
        # Restore by stable flag identifier so a reordered/edited config can't restore
        # the wrong option the way a positional index did (BL-08).
        saved = self._settings.value(flag_key, None)
        if saved is not None:
            for index, option in enumerate(options):
                if flag_of(option) == saved:
                    setter(index)
                    return
            return  # saved flag no longer present -> keep the constructor default
        # Back-compat: migrate a pre-BL-08 positional index once (setter bounds-checks).
        legacy = self._settings.value(legacy_index_key, None)
        if legacy is not None:
            try:
                setter(int(legacy))
            except (TypeError, ValueError):
                pass

    def _save_settings(self):
        if self._settings is None:
            return
        self._settings.setValue("input_folder", self._view.input_folder())
        self._settings.setValue("output_folder", self._view.output_folder())
        res_index = self._view.resolution_index()
        if 0 <= res_index < len(self._config.resolutions):
            self._settings.setValue("resolution_flag", self._config.resolutions[res_index].flag)
        eng_index = self._view.engine_index()
        if 0 <= eng_index < len(self._config.engines):
            self._settings.setValue("engine_flag", self._config.engines[eng_index].renderer_flag)


def _media_output_collision_message(collisions, input_folder):
    groups = []
    for index, group in enumerate(collisions, start=1):
        members = ", ".join(
            f"{os.path.relpath(job.file_path, input_folder)}::{job.scene_class}" for job in group
        )
        groups.append(f"{index}) {members}")
    detail = "; ".join(groups)
    return (
        f"Selected scenes would overwrite the same media output: {detail}. "
        "Rename a source file or scene class, or render the conflicting scenes separately."
    )


def _folder_scan_error_message(detail):
    return (
        f"Could not scan the folder: {detail}. "
        "Check folder access, then rescan before rendering."
    )


def _preview_entries(entries, limit=3):
    preview = [f"{path} ({detail})" for path, detail in entries[:limit]]
    extra = len(entries) - len(preview)
    listed = ", ".join(preview)
    if extra > 0:
        listed = f"{listed}, and {extra} more"
    return listed


def _discovery_issue_message(skipped, unreadable=(), found_count=0):
    """Report what discovery could not read, so partial results are never mistaken
    for a complete scan."""
    parts = []
    if unreadable:
        parts.append(f"could not read {len(unreadable)} folder(s): {_preview_entries(unreadable)}")
    if skipped:
        parts.append(f"skipped {len(skipped)} Python file(s): {_preview_entries(skipped)}")
    prefix = f"{found_count} scene(s) found; " if found_count else ""
    remedy = "Fix or remove them, then rescan before rendering."
    return f"{prefix}{'; '.join(parts)}. {remedy}"


def _default_worker_factory(jobs, resolution_flag, engine_flag, output_dir):
    return RenderWorker(jobs, resolution_flag, engine_flag, output_dir)


def _default_task_submit(task):
    """Run a background task on a short-lived daemon thread (no executor lifecycle
    to manage; daemon so it never blocks interpreter exit)."""
    threading.Thread(target=task, daemon=True).start()
