"""Tier C GUI e2e: real MainView + RenderController + real RenderWorker (offscreen).

The first true discover -> render -> verify test through the actual GUI surface: a
real RenderController drives a real MainView and a real RenderWorker (started as a
QThread) over the real RenderRunner + fake-manim shim, with the event loop pumped via
QTest.qWait (no pytest-qt dependency). Needs PyQt5; skips on the Qt-free CI job.
Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest tests.e2e.test_gui_e2e
"""

import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    _HAS_QT = True
except ImportError:
    _HAS_QT = False

if _HAS_QT:
    from app.config.loader import load_config
    from app.environment.models import CheckStatus, EnvironmentCheck, EnvironmentReport
    from app.render.worker import RenderWorker
    from app.scenes.discovery import discover_scene_result
    from app.ui.controller import RenderController
    from app.ui.main_view import MainView
    from tests.support import fixtures, output_assertions

    _app = QApplication.instance() or QApplication([])

FAKE_MANIM = [sys.executable, os.path.join(os.path.dirname(__file__), "fake_manim.py")]


@unittest.skipUnless(_HAS_QT, "PyQt5 not installed (Qt-free job)")
class GuiE2ETests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()  # real packaged config (index 0 -> -ql fast preview)
        self.created = []

    def _tmp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def _make(self, manim_probe=None, doctor=None, submit=None):
        view = MainView(
            [r.label for r in self.config.resolutions],
            [e.label for e in self.config.engines],
            default_resolution_index=self.config.default_resolution_index,
        )
        self.addCleanup(view.deleteLater)

        def factory(jobs, resolution_flag, engine_flag, output_dir):
            worker = RenderWorker(
                jobs, resolution_flag, engine_flag, output_dir, base_command=FAKE_MANIM
            )
            self.created.append(worker)
            return worker

        controller = RenderController(
            view,
            self.config,
            worker_factory=factory,
            discover_fn=discover_scene_result,
            submit=submit or (lambda task: task()),  # default: synchronous + deterministic
            manim_probe=manim_probe or (lambda: True),
            doctor=doctor,
            settings=None,
        )
        return view, controller

    def _pump_until(self, predicate, timeout_ms=15000):
        waited = 0
        while not predicate() and waited < timeout_ms:
            QTest.qWait(25)
            waited += 25
        self.assertTrue(predicate(), "timed out waiting for the GUI to reach the expected state")

    def test_discovery_populates_scene_list(self):
        view, controller = self._make()
        root = self._tmp()
        fixtures.write_scene(root, "a.py", "Alpha")
        fixtures.write_scene(root, "b.py", "Beta")
        view.set_input_folder(root)
        controller.on_input_chosen(root)
        self.assertEqual(view.scene_list.count(), 2)
        self.assertIn("scene", view.status_label.text().lower())

    def test_mixed_discovery_issues_keep_valid_scene_but_block_start(self):
        root = fixtures.mixed_tree(self._tmp())
        output = os.path.join(self._tmp(), "not-created")
        view, controller = self._make()
        view.set_input_folder(root)
        controller.on_input_chosen(root)
        self.assertEqual(view.scene_list.count(), 1)
        self.assertEqual(view.scene_list.item(0).text(), "GoodScene")
        self.assertFalse(controller.discovery_pending)
        self.assertEqual(view.status_label.property("level"), "error")
        status = view.status_label.text()
        self.assertIn("broken.py", status)
        self.assertIn("fix or remove", status.lower())
        log = view.log_text()
        self.assertIn("broken.py", log)
        self.assertIn("SyntaxError", log)
        view.set_output_folder(output)
        view.select_all_scenes()
        view.primary_button_clicked.emit()
        self.assertEqual(controller.state, "idle")
        self.assertEqual(self.created, [])
        self.assertFalse(os.path.exists(output))
        self.assertEqual(view.status_label.property("level"), "error")
        self.assertIn("broken.py", view.status_label.text())

    def test_colliding_batch_is_blocked_before_worker_or_output(self):
        probe_calls = []

        def probe():
            probe_calls.append(True)
            return True

        view, controller = self._make(manim_probe=probe)
        root = fixtures.duplicate_name_tree(self._tmp())
        output = os.path.join(self._tmp(), "not-created")
        view.set_input_folder(root)
        controller.on_input_chosen(root)
        self.assertEqual(view.scene_list.count(), 2)
        view.select_all_scenes()
        self.assertEqual(len(view.scene_list.selectedItems()), 2)
        view.set_output_folder(output)
        view.append_log("existing diagnostic")
        log_before = view.log_text()
        probe_calls.clear()
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE": "success"}):
            view.primary_button_clicked.emit()
            # Safe RED run only: drain a real worker if production failed to block.
            if controller.state != "idle":
                self._pump_until(lambda: controller.state == "idle")
        self.assertEqual(controller.state, "idle")
        self.assertEqual(self.created, [])
        self.assertEqual(probe_calls, [])
        self.assertFalse(os.path.exists(output))
        self.assertEqual(view.log_text(), log_before)
        self.assertEqual(view.status_label.property("level"), "error")
        status = view.status_label.text()
        self.assertIn(os.path.join("a", "scene.py"), status)
        self.assertIn(os.path.join("b", "scene.py"), status)
        self.assertNotIn(root, status)
        self.assertIn("rename", status.lower())
        self.assertIn("separately", status.lower())

    def test_start_render_to_success_drives_state_machine(self):
        view, controller = self._make()
        root = self._tmp()
        fixtures.write_scene(root, "demo.py", "DemoScene")
        out = self._tmp()
        view.set_input_folder(root)
        controller.on_input_chosen(root)
        view.select_all_scenes()
        view.set_output_folder(out)
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE": "success"}):
            view.primary_button_clicked.emit()  # click Start
            self.assertEqual(controller.state, "running")
            self._pump_until(lambda: controller.state == "idle")
        self.assertEqual(view.primary_button.text(), "Start Render")
        self.assertTrue(view.input_edit.isEnabled())
        self.assertIn("succeeded", view.status_label.text())
        expected = output_assertions.media_output_path(out, "demo.py", "DemoScene", "480p15")
        output_assertions.assert_nonempty_video(self, expected)

    def test_cancel_mid_batch_reports_cancelled(self):
        view, controller = self._make()
        root = self._tmp()
        fixtures.write_scene(root, "a.py", "FastScene")
        fixtures.write_scene(root, "b.py", "HangScene")
        out = self._tmp()
        view.set_input_folder(root)
        controller.on_input_chosen(root)
        view.select_all_scenes()
        view.set_output_folder(out)
        modes = "FastScene=success,HangScene=hang"
        with mock.patch.dict(os.environ, {"FAKE_MANIM_MODE_MAP": modes}):
            view.primary_button_clicked.emit()
            self._pump_until(lambda: view.progress_bar.value() >= 1)  # first job done
            view.primary_button_clicked.emit()  # click Cancel while the 2nd hangs
            self._pump_until(lambda: controller.state == "idle")
        self.assertTrue(view.status_label.text().startswith("Cancelled:"))
        self.assertTrue(view.input_edit.isEnabled())

    def test_manim_missing_blocks_start(self):
        view, controller = self._make(manim_probe=lambda: False)
        root = self._tmp()
        fixtures.write_scene(root, "demo.py", "DemoScene")
        view.set_input_folder(root)
        controller.on_input_chosen(root)
        view.select_all_scenes()
        view.set_output_folder(self._tmp())
        view.primary_button_clicked.emit()
        self.assertEqual(controller.state, "idle")
        self.assertEqual(self.created, [])  # no worker created
        self.assertIn("manim", view.status_label.text().lower())

    def test_check_environment_surfaces_failing_report(self):
        report = EnvironmentReport([EnvironmentCheck("manim", CheckStatus.FAILED, "missing")])
        view, controller = self._make(doctor=lambda: report)
        shown = []
        view.show_environment_report = lambda r: shown.append(r)
        view.check_env_requested.emit()
        self.assertEqual(shown, [report])
        self.assertEqual(view.status_label.property("level"), "error")

    def test_environment_check_keeps_event_loop_alive(self):
        # Release B1: with the doctor genuinely blocked on a worker thread, the Qt
        # event loop must keep servicing timers (i.e. the GUI stays responsive),
        # and completion must restore the button through the queued signal.
        started = threading.Event()
        release = threading.Event()
        threads = []
        passing_report = EnvironmentReport(
            [EnvironmentCheck("manim", CheckStatus.OK, "version 0.20.1")]
        )
        self.addCleanup(release.set)  # a failed assert must not strand the daemon task

        def submit(task):
            thread = threading.Thread(target=task, daemon=True)
            threads.append(thread)
            thread.start()

        def doctor():
            started.set()
            release.wait(5)
            return passing_report

        view, controller = self._make(doctor=doctor, submit=submit)
        shown = []
        view.show_environment_report = shown.append
        timer_fired = []
        view.check_env_requested.emit()
        self._pump_until(started.is_set)
        QTimer.singleShot(0, lambda: timer_fired.append(True))
        self._pump_until(lambda: bool(timer_fired))  # loop alive while doctor blocks
        self.assertFalse(view.check_env_button.isEnabled())
        release.set()
        self._pump_until(view.check_env_button.isEnabled)
        self.assertEqual(shown, [passing_report])
        for thread in threads:
            thread.join(1)


if __name__ == "__main__":
    unittest.main()
