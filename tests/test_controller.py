"""Tests for app.ui.controller.RenderController. Batch 7.

Uses a real (offscreen) MainView but an INJECTED fake worker + fake discovery, so
the state machine, flag resolution, job building, view wiring, and QSettings
persistence are verified headlessly without launching a real render.
Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest tests.test_controller
"""

import os
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402
from PyQt5 import sip  # noqa: E402

from app.config.models import AppConfig, EngineOption, ResolutionOption  # noqa: E402
from app.environment.models import CheckStatus, EnvironmentCheck, EnvironmentReport  # noqa: E402
from app.render.aggregation import JobOutcome, RenderSummary  # noqa: E402
from app.scenes.discovery import DiscoveredScene, DiscoveryResult  # noqa: E402
from app.ui.controller import RenderController  # noqa: E402
from app.ui.main_view import MainView  # noqa: E402

_app = QApplication.instance() or QApplication([])

CONFIG = AppConfig(
    schema_version=2,
    resolutions=[ResolutionOption("Low (-ql)", "-ql"), ResolutionOption("4K (-qk)", "-qk")],
    engines=[EngineOption("Default", ""), EngineOption("OpenGL", "--renderer=opengl")],
)
KNOWN = [
    DiscoveredScene("input/proxy_pattern_scene.py", "ProxyScenario1Scene", "ProxyScenarioBase"),
    DiscoveredScene("input/simple.py", "ImprovedMindMapAnimation07", "MovingCameraScene"),
]


class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, fn):
        self._slots.append(fn)

    def emit(self, *args):
        for fn in list(self._slots):
            fn(*args)


class FakeWorker:
    def __init__(self, jobs, resolution_flag, engine_flag, output_dir):
        self.jobs = jobs
        self.resolution_flag = resolution_flag
        self.engine_flag = engine_flag
        self.output_dir = output_dir
        self.status = _FakeSignal()
        self.log = _FakeSignal()
        self.progress = _FakeSignal()
        self.job_result = _FakeSignal()
        self.finished_summary = _FakeSignal()
        self.finished = _FakeSignal()  # QThread built-in: emitted AFTER run() returns
        self.started = False
        self.cancel_count = 0
        self.parent_obj = None
        self.deleted = False
        self.wait_calls = []
        self.wait_returns = [True]  # simulate wait() succeeding within the budget
        self.terminate_count = 0

    def setParent(self, parent):
        self.parent_obj = parent

    def deleteLater(self):
        self.deleted = True

    def start(self):
        self.started = True

    def request_cancel(self):
        self.cancel_count += 1

    def wait(self, *args):
        self.wait_calls.append(args)
        if len(self.wait_returns) > 1:
            return self.wait_returns.pop(0)
        return self.wait_returns[0]

    def terminate(self):
        self.terminate_count += 1


class FakeSettings:
    # value() stringifies stored values to mirror the real INI/registry backend,
    # which returns strings even for ints (BL-31) -- so restore code can't lean on
    # a Python type that won't survive a round-trip on a real machine.
    def __init__(self):
        self.store = {}

    def setValue(self, key, value):
        self.store[key] = value

    def value(self, key, default=None):
        if key not in self.store:
            return default
        return str(self.store[key])


def _scene_in(root, subdir, filename, scene_class):
    """A DiscoveredScene under root/subdir/filename, with the matching relative path."""
    relative = os.path.join(subdir, filename)
    return DiscoveredScene(
        os.path.join(root, relative), scene_class, "Scene", relative_path=relative
    )


class ControllerTests(unittest.TestCase):
    def _counting_probe(self):
        """A manim probe that reports success and records every call."""
        calls = []

        def probe():
            calls.append(True)
            return True

        return probe, calls

    def _assert_rerun_blocked_before_selection(self, view, controller, created, status_substring):
        """The rerun guard must reject before it reads or rewrites the selection."""
        before = {(scene.file_path, scene.scene_class) for scene in view.selected_scenes()}
        self.assertEqual(before, {(KNOWN[1].file_path, KNOWN[1].scene_class)})
        self.assertEqual(
            view.rerunnable_scene_keys(),
            [(KNOWN[0].file_path, KNOWN[0].scene_class)],
        )
        with (
            mock.patch.object(
                view, "rerunnable_scene_keys", wraps=view.rerunnable_scene_keys
            ) as spy_keys,
            mock.patch.object(view, "select_only", wraps=view.select_only) as spy_select,
        ):
            view.rerun_failed_requested.emit()
        spy_keys.assert_not_called()
        spy_select.assert_not_called()
        after = {(scene.file_path, scene.scene_class) for scene in view.selected_scenes()}
        self.assertEqual(before, after)
        self.assertEqual(created, [])
        self.assertEqual(controller.state, "idle")
        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn(status_substring, view.status_label.text().lower())

    def _make(
        self,
        settings=None,
        manim_probe=None,
        discover_fn=None,
        resolution_labels=None,
        engine_labels=None,
        doctor=None,
        submit=None,
        worker_factory=None,
        config=None,
    ):
        cfg = config if config is not None else CONFIG
        res = (
            resolution_labels
            if resolution_labels is not None
            else [r.label for r in cfg.resolutions]
        )
        eng = engine_labels if engine_labels is not None else [e.label for e in cfg.engines]
        view = MainView(res, eng)
        self.addCleanup(view.deleteLater)
        created = []

        def factory(jobs, resolution_flag, engine_flag, output_dir):
            worker = FakeWorker(jobs, resolution_flag, engine_flag, output_dir)
            created.append(worker)
            return worker

        controller = RenderController(
            view,
            cfg,
            worker_factory=worker_factory or factory,
            discover_fn=discover_fn or (lambda path: DiscoveryResult(tuple(KNOWN), len(KNOWN), ())),
            settings=settings,
            manim_probe=manim_probe or (lambda: True),  # hermetic: don't depend on real PATH
            doctor=doctor,
            # default: run background tasks synchronously so existing tests stay deterministic
            submit=submit or (lambda task: task()),
        )
        return view, controller, created

    def _ready_view(self, view, controller):
        self._tmp_in = tempfile.TemporaryDirectory()
        self._tmp_out = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_in.cleanup)
        self.addCleanup(self._tmp_out.cleanup)
        view.set_input_folder(self._tmp_in.name)
        view.set_output_folder(self._tmp_out.name)
        controller.on_input_chosen(self._tmp_in.name)
        view.select_all_scenes()

    def _finish_worker(self, worker, summary):
        worker.finished_summary.emit(summary)
        worker.finished.emit()

    def test_input_chosen_populates_scene_list(self):
        view, controller, _ = self._make()
        controller.on_input_chosen("input")
        self.assertEqual(view.scene_list.count(), 2)

    def test_discovery_runs_in_background_and_marks_pending(self):
        captured = []
        view, controller, _ = self._make(submit=lambda task: captured.append(task))
        controller.on_input_chosen("input")
        self.assertTrue(controller.discovery_pending)
        self.assertIn("discover", view.status_label.text().lower())
        self.assertEqual(view.scene_list.count(), 0)  # not applied until the task runs
        captured[0]()  # run the background task -> emits result -> applied on this thread
        self.assertFalse(controller.discovery_pending)
        self.assertEqual(view.scene_list.count(), len(KNOWN))

    def test_pending_discovery_blocks_render(self):
        captured = []
        view, controller, created = self._make(submit=lambda task: captured.append(task))
        controller.on_input_chosen("input")  # discovery pending (task not run)
        controller.on_primary_clicked()  # try to render while pending
        self.assertEqual(controller.state, "idle")
        self.assertEqual(len(created), 0)  # blocked, no worker
        self.assertIn("discover", view.status_label.text().lower())

    def test_stale_discovery_result_is_ignored(self):
        captured = []
        seq = {"n": 0}

        def discover(path):
            seq["n"] += 1
            scene = DiscoveredScene(f"{path}/x.py", f"Scene{seq['n']}", "Scene")
            return DiscoveryResult((scene,), 1, ())

        view, controller, _ = self._make(
            submit=lambda task: captured.append(task), discover_fn=discover
        )
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            controller.on_input_chosen(a)  # request 1 (real dir, passes BL-10 guard)
            controller.on_input_chosen(b)  # request 2 (supersedes 1)
            captured[0]()  # deliver request 1 LATE -> stale -> ignored
            self.assertEqual(view.scene_list.count(), 0)
            captured[1]()  # deliver request 2 -> current -> applied
            self.assertEqual(view.scene_list.count(), 1)
            self.assertEqual(view.scene_list.item(0).data(Qt.UserRole).scene_class, "Scene2")

    def test_empty_discovery_message_distinguishes_no_py_from_no_scenes(self):
        def discover(p):
            count = sum(1 for name in os.listdir(p) if name.endswith(".py"))
            return DiscoveryResult((), count, ())

        view, controller, _ = self._make(discover_fn=discover)
        with tempfile.TemporaryDirectory() as folder:
            controller.on_input_chosen(folder)  # empty folder
            self.assertIn("No Python files", view.status_label.text())
            open(os.path.join(folder, "scene.py"), "w").close()
            controller.on_input_chosen(folder)  # has a .py file, but no renderable scene
            self.assertIn("no renderable scenes", view.status_label.text().lower())

    def test_discovery_surfaces_skipped_parse_errors_alongside_valid_scenes(self):
        issues = (
            ("broken.py", "SyntaxError line 3"),
            ("locked.py", "OSError"),
            ("encoded.py", "UnicodeDecodeError"),
            ("nested/bad.py", "ValueError"),
        )
        snapshot = DiscoveryResult((KNOWN[0],), 5, issues)
        view, controller, _ = self._make(discover_fn=lambda _path: snapshot)
        with tempfile.TemporaryDirectory() as root:
            controller.on_input_chosen(root)
        self.assertFalse(controller.discovery_pending)
        self.assertEqual(view.scene_list.count(), 1)
        self.assertEqual(view.scene_list.item(0).data(Qt.UserRole), KNOWN[0])
        self.assertEqual(view.status_label.property("level"), "error")
        status = view.status_label.text()
        self.assertIn("1 scene(s) found", status)
        self.assertIn("broken.py", status)
        self.assertIn("1 more", status)
        self.assertIn("fix or remove", status.lower())
        self.assertIn("rescan", status.lower())
        log = view.log_text()
        for path, detail in issues:
            self.assertIn(path, log)
            self.assertIn(detail, log)

        # Issues alone (no valid scenes) must still surface as discovery error,
        # not collapse into the empty-folder "no renderable scenes" copy.
        empty_snapshot = DiscoveryResult((), 4, issues)
        view2, controller2, _ = self._make(discover_fn=lambda _path: empty_snapshot)
        with tempfile.TemporaryDirectory() as root2:
            controller2.on_input_chosen(root2)
        self.assertEqual(view2.scene_list.count(), 0)
        self.assertEqual(view2.status_label.property("level"), "error")
        self.assertIn("broken.py", view2.status_label.text())
        self.assertNotIn("no renderable scenes", view2.status_label.text().lower())

    def test_unreadable_folder_keeps_sibling_scenes_but_blocks_render(self):
        """An unlistable folder hides only its own subtree: the scenes found elsewhere
        stay selectable, but the partial scan still blocks a render."""
        settings = FakeSettings()
        probe, probe_calls = self._counting_probe()
        snapshot = DiscoveryResult(tuple(KNOWN), 3, (), (("locked", "PermissionError"),))
        with tempfile.TemporaryDirectory() as root:
            view, controller, created = self._make(
                settings=settings,
                manim_probe=probe,
                discover_fn=lambda _path: snapshot,
            )
            output = os.path.join(root, "not-created")
            view.set_input_folder(root)
            view.set_output_folder(output)
            controller.on_input_chosen(root)

            self.assertEqual(view.scene_list.count(), len(KNOWN))
            self.assertEqual(view.status_label.property("level"), "error")
            self.assertIn("locked", view.status_label.text())
            self.assertIn("locked", view.log_text())

            view.select_all_scenes()
            probe_calls.clear()
            with mock.patch("app.ui.controller.validate_render_request") as validate:
                controller.on_primary_clicked()
            validate.assert_not_called()
            self.assertEqual(controller.state, "idle")
            self.assertEqual(created, [])
            self.assertEqual(probe_calls, [])
            self.assertFalse(os.path.exists(output))
            self.assertEqual(settings.store, {})
            status = view.status_label.text()
            self.assertIn("could not read", status.lower())
            self.assertIn("locked", status)
            self.assertIn("rescan", status.lower())
            self.assertNotIn("skipped", status.lower())

    def test_discovery_issues_block_render_before_side_effects(self):
        settings = FakeSettings()
        probe, probe_calls = self._counting_probe()

        snapshot = DiscoveryResult((KNOWN[0],), 2, (("broken.py", "SyntaxError line 3"),))
        view, controller, created = self._make(
            settings=settings,
            manim_probe=probe,
            discover_fn=lambda _path: snapshot,
        )
        with tempfile.TemporaryDirectory() as root:
            output = os.path.join(root, "not-created")
            view.set_input_folder(root)
            view.set_output_folder(output)
            controller.on_input_chosen(root)
            view.select_all_scenes()
            view.mark_scene_result(KNOWN[0].file_path, KNOWN[0].scene_class, JobOutcome.FAILED, 1)
            view.append_log("existing diagnostic")
            selected_before = {
                (scene.file_path, scene.scene_class) for scene in view.selected_scenes()
            }
            status_before = view.scene_status(KNOWN[0].file_path, KNOWN[0].scene_class)
            button_before = view.primary_button.text()
            input_enabled_before = view.input_edit.isEnabled()
            output_before = view.output_folder()
            probe_calls.clear()
            with mock.patch("app.ui.controller.validate_render_request") as validate:
                controller.on_primary_clicked()
            validate.assert_not_called()
            self.assertEqual(controller.state, "idle")
            self.assertEqual(created, [])
            self.assertEqual(probe_calls, [])
            self.assertFalse(os.path.exists(output))
            self.assertEqual(settings.store, {})
            self.assertIn("existing diagnostic", view.log_text())
            self.assertEqual(view.status_label.property("level"), "error")
            self.assertIn("broken.py", view.status_label.text())
            selected_after = {
                (scene.file_path, scene.scene_class) for scene in view.selected_scenes()
            }
            self.assertEqual(selected_before, selected_after)
            self.assertEqual(
                view.scene_status(KNOWN[0].file_path, KNOWN[0].scene_class),
                status_before,
            )
            self.assertEqual(view.primary_button.text(), button_before)
            self.assertEqual(view.input_edit.isEnabled(), input_enabled_before)
            self.assertEqual(view.output_folder(), output_before)

    def test_discovery_issues_block_rerun_before_selection(self):
        snapshot = DiscoveryResult(tuple(KNOWN), 3, (("broken.py", "SyntaxError line 3"),))
        with tempfile.TemporaryDirectory() as root:
            view, controller, created = self._make(discover_fn=lambda _path: snapshot)
            view.set_output_folder(root)
            controller.on_input_chosen(root)
            view.mark_scene_result(KNOWN[0].file_path, KNOWN[0].scene_class, JobOutcome.FAILED, 1)
            view.select_only({(KNOWN[1].file_path, KNOWN[1].scene_class)})
            self._assert_rerun_blocked_before_selection(view, controller, created, "broken.py")

    def test_pending_discovery_blocks_rerun_before_selection(self):
        captured = []
        view, controller, created = self._make(submit=lambda task: captured.append(task))
        with tempfile.TemporaryDirectory() as root:
            view.set_output_folder(root)
            controller.on_input_chosen(root)
            captured.pop(0)()
            view.mark_scene_result(KNOWN[0].file_path, KNOWN[0].scene_class, JobOutcome.FAILED, 1)
            view.select_only({(KNOWN[1].file_path, KNOWN[1].scene_class)})
            controller.on_input_chosen(root)
            self.assertTrue(controller.discovery_pending)
            self._assert_rerun_blocked_before_selection(view, controller, created, "discover")
            self.assertTrue(controller.discovery_pending)

    def test_clean_rediscovery_clears_discovery_issues(self):
        issue_snapshot = DiscoveryResult((KNOWN[0],), 2, (("broken.py", "SyntaxError line 3"),))
        clean_snapshot = DiscoveryResult((KNOWN[0],), 1, ())
        responses = [issue_snapshot, clean_snapshot]

        def discover(_path):
            return responses.pop(0)

        probe, probe_calls = self._counting_probe()

        with tempfile.TemporaryDirectory() as root:
            view, controller, created = self._make(discover_fn=discover, manim_probe=probe)
            view.set_input_folder(root)
            view.set_output_folder(root)
            controller.on_input_chosen(root)
            view.select_all_scenes()
            probe_calls.clear()
            controller.on_primary_clicked()
            self.assertEqual(controller.state, "idle")
            self.assertEqual(len(created), 0)
            self.assertEqual(probe_calls, [])
            self.assertIn("broken.py", view.status_label.text())

            controller.on_input_chosen(root)
            self.assertNotEqual(view.status_label.property("level"), "error")
            self.assertNotIn("broken.py", view.status_label.text())
            view.select_all_scenes()
            controller.on_primary_clicked()
            self.assertEqual(probe_calls, [True])
            self.assertEqual(len(created), 1)
            self.assertEqual(controller.state, "running")

    def test_stale_discovery_result_cannot_restore_diagnostics(self):
        captured = []
        with tempfile.TemporaryDirectory() as base:
            old = os.path.join(base, "old")
            new = os.path.join(base, "new")
            output = os.path.join(base, "output")
            os.makedirs(old)
            os.makedirs(new)
            os.makedirs(output)
            snapshots = {
                os.path.abspath(old): DiscoveryResult(
                    (KNOWN[0],), 2, (("broken.py", "SyntaxError line 3"),)
                ),
                os.path.abspath(new): DiscoveryResult((KNOWN[1],), 1, ()),
            }

            def discover(path):
                return snapshots[path]

            view, controller, created = self._make(
                submit=lambda task: captured.append(task), discover_fn=discover
            )
            controller.on_input_chosen(old)
            controller.on_input_chosen(new)
            captured[1]()
            self.assertFalse(controller.discovery_pending)
            self.assertEqual(view.scene_list.count(), 1)
            self.assertEqual(view.scene_list.item(0).data(Qt.UserRole), KNOWN[1])
            status_text = view.status_label.text()
            log_text = view.log_text()
            rerun_enabled = view.rerun_button.isEnabled()

            captured[0]()
            self.assertEqual(view.scene_list.count(), 1)
            self.assertEqual(view.scene_list.item(0).data(Qt.UserRole), KNOWN[1])
            self.assertEqual(view.status_label.text(), status_text)
            self.assertEqual(view.log_text(), log_text)
            self.assertEqual(view.rerun_button.isEnabled(), rerun_enabled)
            self.assertNotIn("broken.py", view.status_label.text())

            view.set_output_folder(output)
            view.select_all_scenes()
            controller.on_primary_clicked()
            self.assertEqual(len(created), 1)
            self.assertEqual(controller.state, "running")

    def test_invalid_path_supersedes_pending_discovery(self):
        for kind in ("missing", "file"):
            with self.subTest(kind=kind):
                captured = []
                view, controller, _ = self._make(submit=lambda task: captured.append(task))
                with tempfile.TemporaryDirectory() as base:
                    valid = os.path.join(base, "valid")
                    os.makedirs(valid)
                    if kind == "missing":
                        target = os.path.join(base, "absent")
                        marker = "not found"
                    else:
                        target = os.path.join(base, "scene.py")
                        open(target, "w").close()
                        marker = "not a folder"

                    view.set_scene_items(KNOWN)
                    view.mark_scene_result(
                        KNOWN[0].file_path,
                        KNOWN[0].scene_class,
                        JobOutcome.FAILED,
                        1,
                    )
                    view.set_rerun_enabled(True)
                    view.append_log("sentinel")

                    controller.on_input_chosen(valid)
                    self.assertTrue(controller.discovery_pending)
                    self.assertEqual(len(captured), 1)

                    controller.on_input_chosen(target)
                    self.assertEqual(len(captured), 1)
                    self.assertFalse(controller.discovery_pending)
                    self.assertEqual(view.scene_list.count(), 0)
                    self.assertFalse(view.rerun_button.isEnabled())
                    self.assertIn(marker, view.status_label.text().lower())

                    status_text = view.status_label.text()
                    log_text = view.log_text()
                    captured[0]()
                    self.assertFalse(controller.discovery_pending)
                    self.assertEqual(view.scene_list.count(), 0)
                    self.assertFalse(view.rerun_button.isEnabled())
                    self.assertEqual(view.status_label.text(), status_text)
                    self.assertEqual(view.log_text(), log_text)

    def test_discovery_exception_clears_rows_and_blocks_render(self):
        settings = FakeSettings()
        probe, probe_calls = self._counting_probe()

        def discover(_path):
            raise OSError("denied")

        with tempfile.TemporaryDirectory() as root:
            view, controller, created = self._make(
                settings=settings,
                manim_probe=probe,
                discover_fn=discover,
            )
            output = os.path.join(root, "not-created")
            view.set_input_folder(root)
            view.set_output_folder(output)
            view.set_scene_items(KNOWN)
            view.mark_scene_result(KNOWN[0].file_path, KNOWN[0].scene_class, JobOutcome.FAILED, 1)
            view.set_rerun_enabled(True)
            view.append_log("existing diagnostic")

            controller.on_input_chosen(root)
            self.assertFalse(controller.discovery_pending)
            self.assertEqual(view.scene_list.count(), 0)
            self.assertFalse(view.rerun_button.isEnabled())
            self.assertEqual(view.status_label.property("level"), "error")
            self.assertIn("denied", view.status_label.text())

            view.set_scene_items((KNOWN[0],))
            view.select_all_scenes()
            selected_before = {
                (scene.file_path, scene.scene_class) for scene in view.selected_scenes()
            }
            probe_calls.clear()
            with mock.patch("app.ui.controller.validate_render_request") as validate:
                controller.on_primary_clicked()
            validate.assert_not_called()
            self.assertEqual(controller.state, "idle")
            self.assertEqual(created, [])
            self.assertEqual(probe_calls, [])
            self.assertFalse(os.path.exists(output))
            self.assertEqual(settings.store, {})
            self.assertIn("existing diagnostic", view.log_text())
            selected_after = {
                (scene.file_path, scene.scene_class) for scene in view.selected_scenes()
            }
            self.assertEqual(selected_before, selected_after)
            self.assertEqual(view.status_label.property("level"), "error")
            status = view.status_label.text()
            self.assertIn("could not scan", status.lower())
            self.assertIn("OSError: denied", status)
            self.assertIn("check folder access", status.lower())
            self.assertIn("rescan", status.lower())
            self.assertNotIn("skipped", status.lower())

    def test_start_render_blocks_colliding_outputs_before_side_effects(self):
        settings = FakeSettings()
        probe, probe_calls = self._counting_probe()

        with tempfile.TemporaryDirectory() as root:
            scenes = (
                _scene_in(root, "a", "scene.py", "Demo"),
                _scene_in(root, "b", "scene.py", "Demo"),
            )
            snapshot = DiscoveryResult(scenes, 2, ())
            view, controller, created = self._make(
                settings=settings,
                manim_probe=probe,
                discover_fn=lambda _path: snapshot,
                resolution_labels=[],
                engine_labels=[],
            )
            output = os.path.join(root, "not-created")
            view.set_input_folder(root)
            view.set_output_folder(output)
            controller.on_input_chosen(root)
            view.select_all_scenes()
            view.mark_scene_result(scenes[0].file_path, scenes[0].scene_class, JobOutcome.FAILED, 7)
            view.set_progress(1, 2)
            view.set_rerun_enabled(True)
            view.append_log("existing diagnostic")
            probe_calls.clear()
            log_before = view.log_text()
            status_before = view.scene_status(scenes[0].file_path, scenes[0].scene_class)
            progress_value_before = view.progress_bar.value()
            progress_maximum_before = view.progress_bar.maximum()
            rerun_enabled_before = view.rerun_button.isEnabled()
            button_text_before = view.primary_button.text()
            button_enabled_before = view.primary_button.isEnabled()
            input_enabled_before = view.input_edit.isEnabled()
            output_before = view.output_folder()
            selected_before = list(view.selected_scenes())
            with mock.patch.object(
                view, "set_output_folder", wraps=view.set_output_folder
            ) as set_output:
                controller.start_render()
            set_output.assert_not_called()
            self.assertEqual(controller.state, "idle")
            self.assertEqual(created, [])
            self.assertEqual(probe_calls, [])
            self.assertFalse(os.path.exists(output))
            self.assertEqual(settings.store, {})
            self.assertEqual(view.log_text(), log_before)
            self.assertEqual(
                view.scene_status(scenes[0].file_path, scenes[0].scene_class),
                status_before,
            )
            self.assertEqual(view.progress_bar.value(), progress_value_before)
            self.assertEqual(view.progress_bar.maximum(), progress_maximum_before)
            self.assertEqual(view.rerun_button.isEnabled(), rerun_enabled_before)
            self.assertEqual(view.primary_button.text(), button_text_before)
            self.assertEqual(view.primary_button.isEnabled(), button_enabled_before)
            self.assertEqual(view.input_edit.isEnabled(), input_enabled_before)
            self.assertEqual(view.output_folder(), output_before)
            self.assertEqual(list(view.selected_scenes()), selected_before)
            self.assertEqual(view.status_label.property("level"), "error")
            status = view.status_label.text()
            self.assertIn(os.path.join("a", "scene.py"), status)
            self.assertIn(os.path.join("b", "scene.py"), status)
            self.assertNotIn(root, status)
            self.assertIn("rename", status.lower())
            self.assertIn("separately", status.lower())

    def test_start_render_collision_message_lists_every_group(self):
        with tempfile.TemporaryDirectory() as root:
            scenes = (
                _scene_in(root, "a", "scene.py", "Demo"),
                _scene_in(root, "b", "scene.py", "Demo"),
                _scene_in(root, "c", "clip.py", "Other"),
                _scene_in(root, "d", "clip.py", "Other"),
            )
            view, controller, created = self._make(
                discover_fn=lambda _path: DiscoveryResult(scenes, 4, ())
            )
            output = os.path.join(root, "not-created")
            view.set_input_folder(root)
            view.set_output_folder(output)
            controller.on_input_chosen(root)
            view.select_all_scenes()
            controller.start_render()
            self.assertEqual(controller.state, "idle")
            self.assertEqual(created, [])
            self.assertEqual(view.status_label.property("level"), "error")
            status = view.status_label.text()
            for scene in scenes:
                self.assertIn(scene.relative_path, status)
            self.assertNotIn(root, status)
            self.assertIn("rename", status.lower())
            self.assertIn("separately", status.lower())

    def test_start_render_happy_path_sets_running_and_flags(self):
        probe, probe_calls = self._counting_probe()

        view, controller, created = self._make(manim_probe=probe)
        self._ready_view(view, controller)
        probe_calls.clear()
        controller.start_render()
        self.assertEqual(controller.state, "running")
        self.assertEqual(len(created), 1)
        self.assertEqual(probe_calls, [True])
        self.assertEqual(created[0].resolution_flag, "-ql")  # data-driven default -> fast preview
        self.assertEqual(created[0].engine_flag, "")  # default engine
        self.assertEqual(len(created[0].jobs), 2)  # 2 scenes -> 2 jobs (no cross-product)
        self.assertTrue(created[0].started)
        self.assertEqual(view.primary_button.text(), "Cancel Render")
        self.assertFalse(view.input_edit.isEnabled())

    def test_finish_resets_to_idle_with_summary(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        summary = RenderSummary()
        summary.add(JobOutcome.SUCCESS)
        summary.add(JobOutcome.SUCCESS)
        self._finish_worker(created[0], summary)
        self.assertEqual(controller.state, "idle")
        self.assertEqual(view.primary_button.text(), "Start Render")
        self.assertTrue(view.input_edit.isEnabled())
        self.assertIn("succeeded", view.status_label.text())

    def test_worker_parented_and_autodeleted_on_finish(self):
        # Regression: run() emits finished_summary before built-in finished; lifecycle
        # completion must retain Qt ownership until finished can schedule deletion.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        worker = created[0]
        self.assertIs(worker.parent_obj, controller)  # Qt owns it; clearing the py ref is safe
        self.assertFalse(worker.deleted)  # not deleted while the thread may be running
        self._finish_worker(worker, RenderSummary())
        self.assertTrue(worker.deleted)  # deleteLater scheduled safe cleanup

    def test_summary_does_not_enable_new_render_before_thread_finished(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        worker = created[0]
        summary = RenderSummary()
        summary.add(JobOutcome.SUCCESS)
        worker.finished_summary.emit(summary)
        self.assertEqual(controller.state, "running")
        self.assertFalse(view.primary_button.isEnabled())
        worker.finished.emit()
        self.assertEqual(controller.state, "idle")
        self.assertTrue(view.primary_button.isEnabled())

    def test_progress_signal_updates_progress_bar(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        created[0].progress.emit(1, 2)  # worker reports 1 of 2 done
        self.assertEqual(view.progress_bar.maximum(), 2)
        self.assertEqual(view.progress_bar.value(), 1)

    def test_job_result_decorates_scene_row(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        scene = KNOWN[0]  # DiscoveredScene duck-types as a job (file_path + scene_class)
        created[0].job_result.emit(scene, JobOutcome.SUCCESS, 0)
        self.assertEqual(view.scene_status(scene.file_path, scene.scene_class), JobOutcome.SUCCESS)

    def test_failed_job_tooltip_includes_exit_code(self):
        # BL-30: a failed row must be attributable (exit code), not a bare "failed".
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        scene = KNOWN[0]
        created[0].job_result.emit(scene, JobOutcome.FAILED, 1)
        item = view._scene_item(scene.file_path, scene.scene_class)
        self.assertIn("exit 1", item.toolTip())

    def test_save_log_writes_file(self):
        # BL-20: the user can save the render log for a bug report.
        view, controller, _ = self._make()
        view.append_log("traceback line")
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "saved.log")
            view.prompt_save_log = lambda: target
            view.save_log_requested.emit()
            with open(target, encoding="utf-8") as f:
                self.assertIn("traceback line", f.read())

    def test_save_log_empty_explains(self):
        view, controller, _ = self._make()
        view.clear_log()
        called = []
        view.prompt_save_log = lambda: called.append(True) or ""
        view.save_log_requested.emit()
        self.assertEqual(called, [])  # didn't even prompt
        self.assertIn("empty", view.status_label.text().lower())

    def test_close_teardown_is_idempotent(self):
        # Repeated direct shutdown requests must not cancel twice or block the UI.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        worker = created[0]
        controller.on_close_requested()
        controller.on_close_requested()  # second fire must no-op
        self.assertEqual(worker.cancel_count, 1)
        self.assertEqual(worker.wait_calls, [])

    def test_close_event_waits_for_worker_finished_without_blocking(self):
        # A normal window close must leave a live QThread owned by the controller
        # and defer the actual close until QThread.finished arrives.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        worker = created[0]
        view.show()
        QApplication.processEvents()

        self.assertTrue(view.isVisible())
        view.close()
        self.assertTrue(view.isVisible(), "a live worker must veto the close event")
        self.assertIs(controller._worker, worker)
        self.assertEqual(worker.cancel_count, 1)
        self.assertEqual(worker.wait_calls, [])

        worker.finished.emit()
        QApplication.processEvents()
        self.assertFalse(view.isVisible(), "the deferred close must finish after QThread.finished")

    def test_repeated_close_event_requests_cancel_once(self):
        # Repeated close attempts during an in-flight shutdown must not reissue
        # cancellation or reset the supervisor timer.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        worker = created[0]
        view.show()
        QApplication.processEvents()

        view.close()
        self.assertTrue(view.isVisible())
        view.close()
        self.assertTrue(view.isVisible())
        self.assertEqual(worker.cancel_count, 1)
        self.assertEqual(worker.wait_calls, [])

        worker.finished.emit()
        QApplication.processEvents()
        self.assertFalse(view.isVisible())

    def test_forced_shutdown_timeout_retains_live_worker(self):
        # A failed forced shutdown must keep the live QThread owned and the
        # window open, while giving the user an actionable error rather than
        # allowing Qt to destroy a running thread.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        worker = created[0]
        view.show()
        QApplication.processEvents()

        view.close()
        controller._shutdown_timer.timeout.emit()
        self.assertEqual(worker.terminate_count, 1)
        self.assertIs(controller._worker, worker)
        controller._forced_shutdown_timer.timeout.emit()

        self.assertIs(controller._worker, worker)
        self.assertTrue(view.isVisible())
        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn("could not stop", view.status_label.text().lower())

    def test_about_to_quit_waits_until_worker_stops_before_releasing_ownership(self):
        # Non-window quit paths cannot defer through closeEvent. They must retain
        # ownership through cooperative and forced waits, then perform the final
        # unbounded QThread safety wait before allowing teardown to continue.
        from app.ui import controller as ctrl

        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        worker = created[0]
        worker.wait_returns = [False, False, True]

        controller.on_about_to_quit()

        self.assertEqual(worker.cancel_count, 1)
        self.assertEqual(worker.terminate_count, 1)
        self.assertEqual(
            worker.wait_calls,
            [(ctrl._SHUTDOWN_WAIT_MS,), (ctrl._FORCED_SHUTDOWN_WAIT_MS,), ()],
        )
        self.assertIsNone(controller._worker)

    def test_cancel_requests_worker_cancel(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        controller.on_primary_clicked()  # second click while running -> cancel
        self.assertEqual(controller.state, "cancelling")
        self.assertEqual(created[0].cancel_count, 1)

    def test_validation_failure_blocks_render(self):
        view, controller, created = self._make()
        controller.on_input_chosen("input")
        view.select_all_scenes()  # folders left empty -> validation fails
        controller.on_primary_clicked()
        self.assertEqual(controller.state, "idle")
        self.assertEqual(len(created), 0)

    def test_missing_manim_blocks_render(self):
        view, controller, created = self._make(manim_probe=lambda: False)
        self._ready_view(view, controller)  # valid request, but manim is "absent"
        controller.on_primary_clicked()
        self.assertEqual(controller.state, "idle")
        self.assertEqual(len(created), 0)  # no worker started
        self.assertIn("manim", view.status_label.text().lower())

    def test_start_render_creates_missing_output_folder(self):
        view, controller, created = self._make()
        with tempfile.TemporaryDirectory() as base:
            inp = os.path.join(base, "in")
            os.makedirs(inp)
            out = os.path.join(base, "new_output")  # does not exist yet
            view.set_input_folder(inp)
            view.set_output_folder(out)
            controller.on_input_chosen(inp)
            view.select_all_scenes()
            controller.on_primary_clicked()
            self.assertTrue(os.path.isdir(out))  # created at render time
            self.assertEqual(controller.state, "running")
            self.assertEqual(len(created), 1)

    def test_start_render_blocks_when_output_cannot_be_created(self):
        view, controller, created = self._make()
        with tempfile.TemporaryDirectory() as base:
            inp = os.path.join(base, "in")
            os.makedirs(inp)
            blocker = os.path.join(base, "afile")
            open(blocker, "w").close()
            out = os.path.join(blocker, "sub")  # parent is a file -> makedirs fails
            view.set_input_folder(inp)
            view.set_output_folder(out)
            controller.on_input_chosen(inp)
            view.select_all_scenes()
            controller.on_primary_clicked()
            self.assertEqual(controller.state, "idle")
            self.assertEqual(len(created), 0)
            self.assertIn("create", view.status_label.text().lower())

    def test_validation_failure_sets_error_level(self):
        view, controller, _ = self._make()
        controller.on_input_chosen("input")
        view.select_all_scenes()  # folders left empty -> validation fails
        controller.on_primary_clicked()
        self.assertEqual(view.status_label.property("level"), "error")

    def test_finish_with_failures_sets_error_level(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        summary = RenderSummary()
        summary.add(JobOutcome.SUCCESS)
        summary.add(JobOutcome.FAILED)
        self._finish_worker(created[0], summary)
        self.assertEqual(view.status_label.property("level"), "error")

    def test_finish_all_success_clears_error_level(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        summary = RenderSummary()
        summary.add(JobOutcome.SUCCESS)
        summary.add(JobOutcome.SUCCESS)
        self._finish_worker(created[0], summary)
        self.assertIn(view.status_label.property("level"), ("", None))

    def test_rerun_failed_reselects_failed_scenes_and_restarts(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)  # both scenes selected
        controller.on_primary_clicked()
        created[0].job_result.emit(KNOWN[0], JobOutcome.FAILED, 1)
        created[0].job_result.emit(KNOWN[1], JobOutcome.SUCCESS, 0)
        summary = RenderSummary()
        summary.add(JobOutcome.FAILED)
        summary.add(JobOutcome.SUCCESS)
        self._finish_worker(created[0], summary)
        self.assertTrue(view.rerun_button.isEnabled())  # a failure -> rerun offered
        view.rerun_failed_requested.emit()
        self.assertEqual(len(created), 2)  # a new render started
        self.assertEqual(len(created[1].jobs), 1)  # only the failed scene
        self.assertEqual(created[1].jobs[0].scene_class, KNOWN[0].scene_class)

    def test_rerun_disabled_after_all_success(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        summary = RenderSummary()
        summary.add(JobOutcome.SUCCESS)
        summary.add(JobOutcome.SUCCESS)
        self._finish_worker(created[0], summary)
        self.assertFalse(view.rerun_button.isEnabled())

    def test_copy_command_copies_manim_command_for_selection(self):
        from PyQt5.QtWidgets import QApplication

        view, controller, _ = self._make()
        controller.on_input_chosen("input")  # 2 known scenes
        view.select_all_scenes()
        view.set_output_folder("/out")
        view.copy_command_requested.emit()
        text = QApplication.clipboard().text()
        self.assertIn("-m", text)
        self.assertIn("manim", text)
        self.assertIn("--media_dir", text)
        self.assertIn("/out", text)
        self.assertIn(KNOWN[0].scene_class, text)
        self.assertEqual(len(text.splitlines()), 2)  # one command line per selected scene

    def test_copy_command_with_no_selection_is_safe(self):
        from PyQt5.QtWidgets import QApplication

        view, controller, _ = self._make()
        controller.on_input_chosen("input")  # populated but nothing selected
        QApplication.clipboard().setText("SENTINEL")
        view.copy_command_requested.emit()
        self.assertEqual(QApplication.clipboard().text(), "SENTINEL")  # unchanged
        self.assertIn("select", view.status_label.text().lower())

    def test_editing_input_path_rediscovers_to_avoid_stale_selection(self):
        # Browsing re-discovers; typing a new path must too, or selected scenes
        # could carry a previous folder's files while the field shows another.
        calls = []

        def discover(path):
            calls.append(path)
            return DiscoveryResult(tuple(KNOWN), len(KNOWN), ())

        view, controller, _ = self._make(discover_fn=discover)
        calls.clear()  # ignore any discovery from construction
        with tempfile.TemporaryDirectory() as folder:  # real dir passes the BL-10 guard
            view.set_input_folder(folder)
            view.input_path_edited.emit()
            self.assertIn(folder, calls)
            self.assertEqual(view.scene_list.count(), len(KNOWN))  # list reflects the new path

    def test_check_environment_runs_doctor_and_shows_report(self):
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.OK, "version 0.18.1")])
        view, controller, _ = self._make(doctor=lambda: report)
        shown = []
        view.show_environment_report = lambda r: shown.append(r)  # spy; avoid real dialog
        view.check_env_requested.emit()
        self.assertEqual(shown, [report])
        self.assertIn("environment", view.status_label.text().lower())
        self.assertIn(view.status_label.property("level"), ("", None))  # ok -> not error

    def test_pending_environment_result_is_ignored_while_closing(self):
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.OK, "version 0.18.1")])
        submitted = []
        view, controller, _ = self._make(
            doctor=lambda: report,
            submit=lambda task: submitted.append(task),
        )
        shown = []
        view.show_environment_report = lambda result: shown.append(result)

        view.check_env_requested.emit()
        controller.on_close_requested()
        status_before_result = view.status_label.text()

        submitted[0]()
        QApplication.processEvents()

        self.assertEqual(shown, [])
        self.assertEqual(view.status_label.text(), status_before_result)

    def test_pending_environment_task_does_not_emit_after_controller_is_deleted(self):
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.OK, "version 0.18.1")])
        submitted = []
        view, controller, _ = self._make(
            doctor=lambda: report,
            submit=lambda task: submitted.append(task),
        )

        view.check_env_requested.emit()
        controller.on_close_requested()
        controller.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(sip.isdeleted(controller))

        submitted[0]()

    def test_pending_environment_task_does_not_emit_after_controller_deleted_before_close(self):
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.OK, "version 0.18.1")])
        submitted = []
        view, controller, _ = self._make(
            doctor=lambda: report,
            submit=lambda task: submitted.append(task),
        )

        view.check_env_requested.emit()
        controller.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(sip.isdeleted(controller))

        submitted[0]()

    def test_discovery_submit_error_recovers_instead_of_escaping_the_slot(self):
        # An exception escaping a Qt slot is fatal under PyQt5 (qFatal/abort), so a
        # failed submit must be reported in-band -- and must not leave discovery
        # "pending" forever, which would block every later render.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        submit_calls = []

        def submit(task):
            submit_calls.append(task)
            if len(submit_calls) == 1:
                raise RuntimeError("task queue unavailable")
            task()

        view, controller, _ = self._make(submit=submit)

        view.set_input_folder(tmp.name)
        view.input_path_edited.emit()  # the real slot path, via the view's signal

        self.assertFalse(controller.discovery_pending)
        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn("could not start", view.status_label.text().lower())

        view.input_path_edited.emit()  # a new scan can start after the failure

        self.assertEqual(len(submit_calls), 2)
        self.assertFalse(controller.discovery_pending)
        self.assertIn("scene(s) found", view.status_label.text())

    def test_pending_discovery_task_does_not_emit_after_controller_is_deleted(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        submitted = []
        view, controller, _ = self._make(submit=lambda task: submitted.append(task))

        controller.on_input_chosen(tmp.name)
        controller.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(sip.isdeleted(controller))

        submitted[0]()  # a late scan must not raise on its bare daemon thread

    def test_environment_result_from_real_thread_reaches_gui_thread(self):
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.OK, "version 0.18.1")])
        submitted_threads = []
        doctor_threads = []
        callback_threads = []

        def doctor():
            doctor_threads.append(threading.get_ident())
            return report

        def submit(task):
            thread = threading.Thread(target=task)
            submitted_threads.append(thread)
            thread.start()

        view, controller, _ = self._make(doctor=doctor, submit=submit)
        shown = []

        def show_report(result):
            shown.append(result)
            callback_threads.append(threading.get_ident())

        view.show_environment_report = show_report
        gui_thread = threading.get_ident()

        view.check_env_requested.emit()
        self.assertEqual(len(submitted_threads), 1)
        submitted_threads[0].join(timeout=1)
        self.assertFalse(submitted_threads[0].is_alive())
        self.assertEqual(len(doctor_threads), 1)
        self.assertNotEqual(doctor_threads[0], gui_thread)
        self.assertEqual(shown, [])

        QApplication.processEvents()

        self.assertEqual(shown, [report])
        self.assertEqual(callback_threads, [gui_thread])

    def test_environment_submit_error_allows_retry(self):
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.OK, "version 0.18.1")])
        submit_calls = []

        def submit(task):
            submit_calls.append(task)
            if len(submit_calls) == 1:
                raise RuntimeError("task queue unavailable")
            task()

        view, controller, _ = self._make(doctor=lambda: report, submit=submit)
        shown = []
        view.show_environment_report = lambda result: shown.append(result)

        view.check_env_requested.emit()

        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn("could not run", view.status_label.text().lower())
        self.assertEqual(shown, [])
        self.assertTrue(view.check_env_button.isEnabled())
        self.assertEqual(view.check_env_button.text(), "Check &Environment")

        view.check_env_requested.emit()

        self.assertEqual(len(submit_calls), 2)
        self.assertEqual(shown, [report])
        self.assertEqual(view.status_label.text(), "Environment check passed.")
        self.assertTrue(view.check_env_button.isEnabled())

    def test_check_environment_failure_sets_error_status(self):
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.FAILED, "missing")])
        view, controller, _ = self._make(doctor=lambda: report)
        view.show_environment_report = lambda r: None
        view.check_env_requested.emit()
        self.assertEqual(view.status_label.property("level"), "error")

    def test_check_environment_runner_error_is_guarded(self):
        # BL-19: a doctor that raises must not leave a dead button / unhandled exception.
        def boom():
            raise RuntimeError("probe blew up")

        view, controller, _ = self._make(doctor=boom)
        view.check_env_requested.emit()
        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn("could not run", view.status_label.text().lower())

    def test_environment_check_is_submitted_without_blocking(self):
        # Release B1: the doctor must run through the injected submitter, never
        # inline on the GUI thread; pending presentation applies immediately.
        captured = []
        doctor_calls = []
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.OK, "version 0.20.1")])

        def doctor():
            doctor_calls.append(1)
            return report

        view, controller, _ = self._make(submit=lambda task: captured.append(task), doctor=doctor)
        shown = []
        view.show_environment_report = lambda r: shown.append(r)
        view.check_env_requested.emit()
        self.assertEqual(doctor_calls, [])  # not run inline
        self.assertEqual(len(captured), 1)
        self.assertFalse(view.check_env_button.isEnabled())
        self.assertEqual(view.check_env_button.text(), "Checking Environment...")
        self.assertIn("checking environment", view.status_label.text().lower())
        self.assertEqual(shown, [])
        captured[0]()  # run the background task -> queued completion applies here
        self.assertEqual(doctor_calls, [1])
        self.assertEqual(shown, [report])
        self.assertTrue(view.check_env_button.isEnabled())
        self.assertEqual(view.check_env_button.text(), "Check &Environment")
        self.assertIn("passed", view.status_label.text().lower())

    def test_environment_check_pending_blocks_duplicate_and_render(self):
        captured = []
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.OK, "ok")])
        view, controller, created = self._make(
            submit=lambda task: captured.append(task), doctor=lambda: report
        )
        view.show_environment_report = lambda r: None
        view.check_env_requested.emit()
        view.check_env_requested.emit()  # duplicate while pending
        self.assertEqual(len(captured), 1)
        view.primary_button_clicked.emit()  # Start while the check is pending
        self.assertEqual(created, [])
        self.assertEqual(controller.state, "idle")
        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn("environment check", view.status_label.text().lower())
        captured[0]()
        self.assertTrue(view.check_env_button.isEnabled())

    def test_environment_check_failed_report_restores_button_with_error(self):
        captured = []
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.FAILED, "missing")])
        view, controller, _ = self._make(
            submit=lambda task: captured.append(task), doctor=lambda: report
        )
        shown = []
        view.show_environment_report = lambda r: shown.append(r)
        view.check_env_requested.emit()
        captured[0]()
        self.assertEqual(shown, [report])
        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn("failed", view.status_label.text().lower())
        self.assertTrue(view.check_env_button.isEnabled())
        self.assertEqual(view.check_env_button.text(), "Check &Environment")

    def test_environment_check_exception_restores_button(self):
        captured = []

        def doctor():
            raise RuntimeError("boom")

        view, controller, _ = self._make(submit=lambda task: captured.append(task), doctor=doctor)
        shown = []
        view.show_environment_report = lambda r: shown.append(r)
        view.check_env_requested.emit()
        captured[0]()
        self.assertEqual(shown, [])
        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn("could not run", view.status_label.text().lower())
        self.assertTrue(view.check_env_button.isEnabled())
        self.assertEqual(view.check_env_button.text(), "Check &Environment")
        view.check_env_requested.emit()  # a new check can start after the failure
        self.assertEqual(len(captured), 2)

    def test_environment_box_surfaces_issues_in_visible_text(self):
        # BL-09: the actionable lines must be in always-visible text, not only DetailedText.
        view, controller, _ = self._make()
        report = EnvironmentReport(
            [EnvironmentCheck("manim", CheckStatus.FAILED, "not installed (pip install manim)")]
        )
        box = view._build_environment_box(report)
        self.addCleanup(box.deleteLater)
        self.assertIn("manim", box.informativeText())
        self.assertIn("manim", box.detailedText())

    def test_selecting_nondefault_engine_shows_experimental_advisory(self):
        view, controller, _ = self._make()
        view.engine_changed.emit(1)  # index 1 = OpenGL (renderer_flag non-empty)
        self.assertIn("experimental", view.status_label.text().lower())

    def test_selecting_default_engine_leaves_status_untouched(self):
        view, controller, _ = self._make()
        view.set_status("ready")
        view.engine_changed.emit(0)  # Default engine (renderer_flag "")
        self.assertEqual(view.status_label.text(), "ready")

    def test_select_all_intent_selects_all_scenes(self):
        view, controller, _ = self._make()
        controller.on_input_chosen("input")  # populates 2 scenes
        view.select_all_requested.emit()
        self.assertEqual(len(view.selected_scenes()), 2)

    def test_open_output_intent_opens_existing_output_folder(self):
        view, controller, _ = self._make()
        with tempfile.TemporaryDirectory() as real:
            view.set_output_folder(real)
            with mock.patch("app.ui.controller.QDesktopServices.openUrl") as opened:
                view.open_output_requested.emit()
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(opened.call_args[0][0].toLocalFile(), real)

    def test_open_output_empty_explains_instead_of_silent_noop(self):
        # BL-18: a dead control that does nothing -> give feedback.
        view, controller, _ = self._make()
        view.set_output_folder("")
        with mock.patch("app.ui.controller.QDesktopServices.openUrl") as opened:
            view.open_output_requested.emit()
        self.assertEqual(opened.call_count, 0)
        self.assertIn("choose an output folder", view.status_label.text().lower())

    def test_open_output_nonexistent_explains_will_be_created(self):
        # BL-18: a not-yet-created folder must explain, not openUrl silently failing.
        view, controller, _ = self._make()
        view.set_output_folder("/no/such/output/dir")
        with mock.patch("app.ui.controller.QDesktopServices.openUrl") as opened:
            view.open_output_requested.emit()
        self.assertEqual(opened.call_count, 0)
        self.assertIn("render", view.status_label.text().lower())

    def test_input_path_that_is_a_file_reports_not_a_folder(self):
        # BL-10: a file (not a dir) must not collapse to "No Python files".
        captured = []
        view, controller, _ = self._make(submit=lambda task: captured.append(task))
        with tempfile.NamedTemporaryFile(suffix=".py") as f:
            controller.on_input_chosen(f.name)
            self.assertIn("not a folder", view.status_label.text().lower())
        self.assertEqual(captured, [])  # discovery not even submitted

    def test_input_path_nonexistent_reports_folder_not_found(self):
        # BL-10: a missing/deleted path must say so, not "No Python files".
        captured = []
        view, controller, _ = self._make(submit=lambda task: captured.append(task))
        controller.on_input_chosen("/definitely/not/here/xyz")
        self.assertIn("not found", view.status_label.text().lower())
        self.assertEqual(captured, [])

    def test_rebrowse_disables_stale_rerun_button(self):
        # BL-13: after a render leaves Rerun enabled, re-browsing rebuilds rows and
        # must disable the now-inert button.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        created[0].job_result.emit(KNOWN[0], JobOutcome.FAILED, 1)
        summary = RenderSummary()
        summary.add(JobOutcome.FAILED)
        summary.add(JobOutcome.SUCCESS)
        self._finish_worker(created[0], summary)
        self.assertTrue(view.rerun_button.isEnabled())
        controller.on_input_chosen("input")  # re-browse rebuilds the list
        self.assertFalse(view.rerun_button.isEnabled())

    def test_cancelled_scenes_are_rerunnable_to_resume(self):
        # BL-14: cancelling a batch must leave the un-run scenes resumable in one click.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        created[0].job_result.emit(KNOWN[0], JobOutcome.SUCCESS, 0)
        created[0].job_result.emit(KNOWN[1], JobOutcome.CANCELLED, None)
        summary = RenderSummary()
        summary.add(JobOutcome.SUCCESS)
        summary.add(JobOutcome.CANCELLED)
        self._finish_worker(created[0], summary)
        self.assertTrue(view.rerun_button.isEnabled())  # resume offered
        view.rerun_failed_requested.emit()
        self.assertEqual(len(created), 2)
        self.assertEqual(len(created[1].jobs), 1)
        self.assertEqual(created[1].jobs[0].scene_class, KNOWN[1].scene_class)  # the cancelled one

    def test_cold_start_shows_input_folder_hint(self):
        # BL-17: a true cold start must guide the user, not show a bare "Idle".
        view, controller, _ = self._make(settings=FakeSettings(), manim_probe=lambda: True)
        self.assertIn("choose an input folder", view.status_label.text().lower())

    def test_default_resolution_is_fast_preview_not_last(self):
        # BL-17: default to the fast preview (index 0), not the heaviest 4K (last).
        view, controller, _ = self._make()
        self.assertEqual(view.resolution_index(), 0)

    def test_cold_start_with_missing_manim_sets_readiness_warning(self):
        # BL-17: on cold start, a missing manim should point at Check Environment.
        view, controller, _ = self._make(settings=FakeSettings(), manim_probe=lambda: False)
        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn("check environment", view.status_label.text().lower())

    def test_empty_resolution_engine_combo_blocks_render(self):
        # Defensive: an empty combo -> currentIndex() == -1, and config[-1] would
        # SILENTLY pick the last option. The guard must block and stay idle.
        view, controller, created = self._make(resolution_labels=[], engine_labels=[])
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        self.assertEqual(controller.state, "idle")
        self.assertEqual(len(created), 0)  # no worker started
        self.assertIn("resolution", view.status_label.text().lower())

    def test_relative_output_folder_normalized_to_absolute(self):
        # BL-06: a relative output path must resolve to an absolute one at the
        # controller boundary, so files land predictably and the field reflects it.
        view, controller, created = self._make()
        with tempfile.TemporaryDirectory() as base:
            inp = os.path.join(base, "in")
            os.makedirs(inp)
            view.set_input_folder(inp)
            controller.on_input_chosen(inp)
            view.select_all_scenes()
            cwd = os.getcwd()
            os.chdir(base)
            try:
                view.set_output_folder("relout")  # relative
                expected = os.path.abspath("relout")  # under base (cwd-resolved)
                controller.on_primary_clicked()
            finally:
                os.chdir(cwd)
            self.assertEqual(len(created), 1)
            self.assertTrue(os.path.isabs(created[0].output_dir))
            self.assertEqual(created[0].output_dir, expected)
            self.assertEqual(os.path.basename(created[0].output_dir), "relout")
            self.assertTrue(os.path.isabs(view.output_folder()))  # reflected back to the user

    def test_relative_input_folder_absolutized_before_discovery(self):
        # Review finding (BL-06 sibling): BL-06 absolutized the OUTPUT folder but left
        # the input folder relative, so a typed/restored relative input path yields
        # relative discovered file paths that the render subprocess resolves against the
        # wrong CWD (Finder/frozen launch). Input must be absolutized too.
        captured = []
        view, controller, _ = self._make(
            discover_fn=lambda p: captured.append(p) or DiscoveryResult((), 0, ())
        )
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "scenes"))
            cwd = os.getcwd()
            os.chdir(base)
            try:
                controller.on_input_chosen("scenes")  # relative path
            finally:
                os.chdir(cwd)
        self.assertTrue(captured)
        self.assertTrue(os.path.isabs(captured[-1]), captured)  # discovery got an absolute path
        self.assertEqual(os.path.basename(captured[-1]), "scenes")
        self.assertTrue(os.path.isabs(view.input_folder()))  # field reflects the absolute path

    def test_cancel_finalizes_progress_bar(self):
        # BL-22: after a mid-batch cancel the bar must not stay stranded below 100%.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        created[0].progress.emit(1, 3)
        self.assertEqual(view.progress_bar.value(), 1)
        controller.on_primary_clicked()  # cancel
        summary = RenderSummary()
        summary.add(JobOutcome.SUCCESS)
        summary.add(JobOutcome.CANCELLED)
        summary.add(JobOutcome.CANCELLED)
        self._finish_worker(created[0], summary)
        self.assertEqual(view.progress_bar.value(), 0)  # finalized, not frozen at 1/3
        self.assertEqual(view.progress_bar.maximum(), 1)

    def test_cancelling_shows_busy_state_and_disables_button(self):
        # BL-24: while cancelling, show a busy indicator and a disabled "Cancelling..."
        # button so the up-to-5s wait reads as working, not hung.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        controller.on_primary_clicked()  # cancel
        self.assertEqual(controller.state, "cancelling")
        self.assertEqual(view.progress_bar.minimum(), 0)
        self.assertEqual(view.progress_bar.maximum(), 0)  # 0,0 == indeterminate busy
        self.assertFalse(view.primary_button.isEnabled())
        self.assertEqual(view.primary_button.text(), "Cancelling...")
        self._finish_worker(created[0], RenderSummary())
        self.assertTrue(view.primary_button.isEnabled())
        self.assertEqual(view.primary_button.text(), "Start Render")
        self.assertGreaterEqual(view.progress_bar.maximum(), 1)  # determinate again

    def test_worker_factory_failure_restores_idle(self):
        # BL-23: if the factory/start raises, the UI must not stick disabled on Cancel.
        def boom(*args):
            raise RuntimeError("factory exploded")

        view, controller, _ = self._make(worker_factory=boom)
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        self.assertEqual(controller.state, "idle")
        self.assertTrue(view.input_edit.isEnabled())
        self.assertEqual(view.primary_button.text(), "Start Render")
        self.assertIn("could not start", view.status_label.text().lower())

    def test_start_render_is_noop_when_not_idle(self):
        # BL-23: re-entrancy guard -- a direct start_render() while running is a no-op,
        # not a second live worker overwriting the first.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        controller.start_render()
        self.assertEqual(len(created), 1)

    def test_finished_without_summary_falls_back_to_idle(self):
        # BL-23: run() can throw before emitting finished_summary; QThread.finished
        # still fires, so an idle fallback must run.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        created[0].finished.emit()  # finished WITHOUT a preceding finished_summary
        self.assertEqual(controller.state, "idle")
        self.assertTrue(view.input_edit.isEnabled())
        self.assertEqual(view.primary_button.text(), "Start Render")
        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn("without a summary", view.status_label.text().lower())

    def test_shutdown_timer_uses_full_supervision_cleanup_budget(self):
        # Normal close is asynchronous, so its cooperative timer can cover the
        # renderer's full supervision budget without freezing the interface.
        from app.render import runner as runner_mod
        from app.ui import controller as ctrl

        self.assertEqual(
            ctrl._SHUTDOWN_WAIT_MS,
            int(runner_mod.DEFAULT_SUPERVISION_CLEANUP_TIMEOUT * 1000),
        )

    def test_close_terminates_worker_when_wait_times_out(self):
        # BL-24: a wait() timeout must terminate the QThread, never leave it running.
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        controller.on_close_requested()
        self.assertEqual(created[0].wait_calls, [])
        controller._shutdown_timer.timeout.emit()
        self.assertEqual(created[0].terminate_count, 1)

    def test_close_starts_forced_timer_after_cooperative_timeout(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        worker = created[0]
        controller.on_close_requested()
        controller._shutdown_timer.timeout.emit()
        self.assertEqual(worker.terminate_count, 1)
        self.assertTrue(controller._forced_shutdown_timer.isActive())
        self.assertEqual(worker.wait_calls, [])

    def test_forced_timeout_reports_error_without_releasing_live_worker(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        worker = created[0]
        controller.on_close_requested()
        controller._shutdown_timer.timeout.emit()
        controller._forced_shutdown_timer.timeout.emit()
        self.assertEqual(worker.terminate_count, 1)
        self.assertIs(controller._worker, worker)
        self.assertEqual(view.status_label.property("level"), "error")

    def test_repeated_shutdown_request_stays_idempotent_after_forced_timeout(self):
        view, controller, created = self._make()
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        worker = created[0]

        controller.on_close_requested()
        controller._shutdown_timer.timeout.emit()
        controller._forced_shutdown_timer.timeout.emit()

        self.assertIs(controller._worker, worker)
        self.assertEqual(worker.cancel_count, 1)
        self.assertEqual(worker.wait_calls, [])
        self.assertEqual(worker.terminate_count, 1)
        self.assertFalse(controller._shutdown_started)
        self.assertFalse(controller._close_pending)

        controller.on_close_requested()
        controller.on_close_requested()  # duplicate retry must not cancel or escalate again

        self.assertIs(controller._worker, worker)
        self.assertEqual(worker.cancel_count, 1)
        self.assertEqual(worker.wait_calls, [])
        self.assertEqual(worker.terminate_count, 1)
        self.assertTrue(controller._shutdown_started)
        self.assertTrue(controller._close_pending)

    def test_settings_persisted_on_start(self):
        # BL-08: persist by stable flag identifier, not positional index.
        settings = FakeSettings()
        view, controller, created = self._make(settings=settings)
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        self.assertEqual(settings.store.get("input_folder"), self._tmp_in.name)
        self.assertEqual(settings.store.get("resolution_flag"), "-ql")  # data-driven default
        self.assertEqual(settings.store.get("engine_flag"), "")  # default engine

    def test_settings_persisted_on_clean_quit_without_render(self):
        # BL-25: a user who sets fields and quits WITHOUT clicking Start is remembered.
        settings = FakeSettings()
        view, controller, _ = self._make(settings=settings)
        view.set_input_folder("/some/in")
        view.set_output_folder("/some/out")
        controller.on_close_requested()
        self.assertEqual(settings.store.get("input_folder"), "/some/in")
        self.assertEqual(settings.store.get("output_folder"), "/some/out")
        self.assertIn("resolution_flag", settings.store)

    def test_restore_selects_saved_flag_after_config_reorder(self):
        # BL-08: the saved option must survive a config reorder; an index would point
        # at a DIFFERENT option (Low silently becoming 4K).
        reordered = AppConfig(
            schema_version=2,
            resolutions=[ResolutionOption("4K (-qk)", "-qk"), ResolutionOption("Low (-ql)", "-ql")],
            engines=CONFIG.engines,
        )
        settings = FakeSettings()
        settings.store["resolution_flag"] = "-ql"  # user had picked Low
        view, controller, _ = self._make(settings=settings, config=reordered)
        self.assertEqual(reordered.resolutions[view.resolution_index()].flag, "-ql")

    def test_restore_unknown_flag_falls_back_to_default(self):
        # BL-31: a saved flag no longer in the config must not raise or mis-select.
        settings = FakeSettings()
        settings.store["resolution_flag"] = "-qZZ"  # removed since last session
        view, controller, _ = self._make(settings=settings)
        self.assertTrue(0 <= view.resolution_index() < len(CONFIG.resolutions))

    def test_legacy_index_setting_is_migrated(self):
        # BL-08 back-compat: a pre-upgrade integer index still restores once.
        settings = FakeSettings()
        settings.store["resolution_index"] = 0  # old key, stringified by FakeSettings
        view, controller, _ = self._make(settings=settings)
        self.assertEqual(view.resolution_index(), 0)

    def test_restore_engine_does_not_emit_experimental_warning(self):
        # BL-26: restoring OpenGL must not fire the experimental advisory as a side effect.
        settings = FakeSettings()
        settings.store["engine_flag"] = "--renderer=opengl"
        view, controller, _ = self._make(settings=settings)
        self.assertEqual(CONFIG.engines[view.engine_index()].renderer_flag, "--renderer=opengl")
        self.assertNotIn("experimental", view.status_label.text().lower())

    def test_restore_deleted_input_folder_is_graceful(self):
        # BL-31: a remembered-but-deleted folder must restore without raising.
        settings = FakeSettings()
        settings.store["input_folder"] = "/definitely/not/here/anymore"
        view, controller, _ = self._make(
            settings=settings, discover_fn=lambda p: DiscoveryResult((), 0, ())
        )
        self.assertEqual(view.scene_list.count(), 0)  # no crash; just empty

    def test_settings_persisted_on_start_legacy_keys_absent(self):
        # The old positional keys are no longer written.
        settings = FakeSettings()
        view, controller, _ = self._make(settings=settings)
        self._ready_view(view, controller)
        controller.on_primary_clicked()
        self.assertNotIn("resolution_index", settings.store)
        self.assertNotIn("engine_index", settings.store)

    def test_restore_settings_triggers_discovery(self):
        # Regression: _restore_settings set the input folder but never ran discovery,
        # so the scene list was empty after a restart until the user re-clicked Browse.
        settings = FakeSettings()
        settings.store["input_folder"] = "input"  # discover_fn is faked to return KNOWN
        view, controller, _ = self._make(settings=settings)
        self.assertEqual(view.scene_list.count(), len(KNOWN))  # discovery ran during restore


if __name__ == "__main__":
    unittest.main()
