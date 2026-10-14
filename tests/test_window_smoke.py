"""Smoke test for the PyQt5 view layer (app.ui). Batch 6 — the ONLY Qt test.

Run headless: QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest tests.test_window_smoke
Verifies the preserved contracts: window title, externalized theme with a
property-driven cancel selector, the controller-facing setter/intent surface,
the [HH:MM:SS.mmm] log format, default-to-4K, and (Cond 2) that scene-list items
carry DiscoveredScene identity so duplicate class names stay distinct.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

from app.render.aggregation import JobOutcome  # noqa: E402
from app.scenes.discovery import DiscoveredScene  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.main_view import MainView  # noqa: E402

_app = QApplication.instance() or QApplication([])

RES_LABELS = ["Low Quality (-ql)", "Medium Quality (-qm)", "High Quality (-qh)", "4K Quality (-qk)"]
ENG_LABELS = ["Default", "OpenGL"]


class MainViewSmokeTests(unittest.TestCase):
    def setUp(self):
        self.view = MainView(RES_LABELS, ENG_LABELS)

    def tearDown(self):
        self.view.deleteLater()

    def test_window_title_preserved(self):
        self.assertEqual(self.view.windowTitle(), "Manim Simple Render")

    def test_environment_pending_toggles_only_check_button(self):
        self.view.set_environment_check_pending(True)
        self.assertFalse(self.view.check_env_button.isEnabled())
        self.assertEqual(self.view.check_env_button.text(), "Checking Environment...")
        self.assertTrue(self.view.input_edit.isEnabled())
        self.view.set_environment_check_pending(False)
        self.assertTrue(self.view.check_env_button.isEnabled())
        self.assertEqual(self.view.check_env_button.text(), "Check &Environment")

    def test_theme_qss_loads_with_cancel_state_selector(self):
        qss = theme.load_stylesheet()
        self.assertTrue(qss.strip())
        self.assertIn('[state="cancel"]', qss)  # property-driven, replaces #CancelButton churn

    def test_view_exposes_controller_surface(self):
        for attr in (
            "set_status",
            "append_log",
            "set_controls_enabled",
            "set_button_mode",
            "browse_input_requested",
            "browse_output_requested",
            "primary_button_clicked",
        ):
            self.assertTrue(hasattr(self.view, attr), f"missing {attr}")

    def test_append_log_has_millisecond_timestamp(self):
        self.view.append_log("hello world")
        self.assertRegex(self.view.log_text(), r"\[\d{2}:\d{2}:\d{2}\.\d{3}\] hello world")

    def test_log_area_is_capped_for_long_renders(self):
        # A long verbose render must not grow the log document without bound.
        max_blocks = self.view.log_area.document().maximumBlockCount()
        self.assertGreater(max_blocks, 0)  # a FIFO cap is configured (0 == unlimited)
        for i in range(max_blocks + 200):
            self.view.append_log(f"line {i}")
        self.assertLessEqual(self.view.log_area.document().blockCount(), max_blocks)

    def test_progress_bar_set_and_reset(self):
        self.view.set_progress(2, 5)
        self.assertEqual(self.view.progress_bar.maximum(), 5)
        self.assertEqual(self.view.progress_bar.value(), 2)
        self.view.reset_progress()
        self.assertEqual(self.view.progress_bar.value(), 0)

    def test_scene_results_marked_and_cleared(self):
        scenes = [DiscoveredScene("a.py", "A", "Scene"), DiscoveredScene("b.py", "B", "Scene")]
        self.view.set_scene_items(scenes)
        self.view.mark_scene_result("a.py", "A", JobOutcome.SUCCESS)
        self.assertEqual(self.view.scene_status("a.py", "A"), JobOutcome.SUCCESS)
        self.assertIsNone(self.view.scene_status("b.py", "B"))  # untouched
        self.view.clear_scene_results()
        self.assertIsNone(self.view.scene_status("a.py", "A"))

    def test_set_scene_items_preserves_selection_across_rediscovery(self):
        # Re-running discovery (re-browsing the same folder, or restoring on
        # restart) must not silently drop the user's current scene selection.
        scenes = [DiscoveredScene("a.py", "A", "Scene"), DiscoveredScene("b.py", "B", "Scene")]
        self.view.set_scene_items(scenes)
        self.view.scene_list.item(1).setSelected(True)  # select only "B"
        self.view.set_scene_items(scenes)  # re-discovery with the same scenes
        selected = self.view.selected_scenes()
        self.assertEqual([s.scene_class for s in selected], ["B"])

    def test_rediscovery_only_reselects_still_present_scenes(self):
        a = DiscoveredScene("a.py", "A", "Scene")
        b = DiscoveredScene("b.py", "B", "Scene")
        self.view.set_scene_items([a, b])
        self.view.select_all_scenes()  # select both
        self.view.set_scene_items([b])  # "A" no longer discovered
        selected = self.view.selected_scenes()
        self.assertEqual([s.scene_class for s in selected], ["B"])  # only the survivor stays

    def test_select_all_button_emits_intent(self):
        fired = []
        self.view.select_all_requested.connect(lambda: fired.append(True))
        self.view.select_all_button.click()
        self.assertEqual(fired, [True])

    def test_open_output_button_emits_intent(self):
        fired = []
        self.view.open_output_requested.connect(lambda: fired.append(True))
        self.view.open_output_button.click()
        self.assertEqual(fired, [True])

    def test_status_label_word_wraps(self):
        # Long error text (a manim traceback line, a long path) must wrap, not clip.
        self.assertTrue(self.view.status_label.wordWrap())

    def test_set_status_error_level_styles_and_clears(self):
        self.view.set_status("boom", level="error")
        self.assertEqual(self.view.status_label.property("level"), "error")
        self.view.set_status("ok")  # a subsequent default-level message clears it
        self.assertIn(self.view.status_label.property("level"), ("", None))

    def test_theme_qss_has_error_level_selector(self):
        qss = theme.load_stylesheet()
        self.assertIn('[level="error"]', qss)  # property-driven error styling

    def test_check_environment_button_emits_intent(self):
        fired = []
        self.view.check_env_requested.connect(lambda: fired.append(True))
        self.view.check_env_button.click()
        self.assertEqual(fired, [True])

    def test_rerun_button_starts_disabled_and_toggles(self):
        self.assertFalse(self.view.rerun_button.isEnabled())
        self.view.set_rerun_enabled(True)
        self.assertTrue(self.view.rerun_button.isEnabled())

    def test_rerun_button_emits_intent(self):
        self.view.set_rerun_enabled(True)
        fired = []
        self.view.rerun_failed_requested.connect(lambda: fired.append(True))
        self.view.rerun_button.click()
        self.assertEqual(fired, [True])

    def test_failed_scene_keys_and_select_only(self):
        scenes = [DiscoveredScene("a.py", "A", "Scene"), DiscoveredScene("b.py", "B", "Scene")]
        self.view.set_scene_items(scenes)
        self.view.mark_scene_result("a.py", "A", JobOutcome.FAILED)
        self.view.mark_scene_result("b.py", "B", JobOutcome.SUCCESS)
        self.assertEqual(self.view.failed_scene_keys(), [("a.py", "A")])
        self.view.select_only({("a.py", "A")})
        self.assertEqual([s.scene_class for s in self.view.selected_scenes()], ["A"])

    def test_copy_command_button_emits_intent(self):
        fired = []
        self.view.copy_command_requested.connect(lambda: fired.append(True))
        self.view.copy_command_button.click()
        self.assertEqual(fired, [True])

    def test_copy_to_clipboard_sets_clipboard(self):
        from PyQt5.QtWidgets import QApplication

        self.view.copy_to_clipboard("manim cmd here")
        self.assertEqual(QApplication.clipboard().text(), "manim cmd here")

    def test_input_path_edit_emits_intent(self):
        # Typing a path (not Browsing) + commit must signal so the controller can
        # re-discover -> the scene list never desyncs from the input field.
        fired = []
        self.view.input_path_edited.connect(lambda: fired.append(True))
        self.view.input_edit.editingFinished.emit()
        self.assertEqual(fired, [True])

    def test_engine_combo_change_emits_intent(self):
        fired = []
        self.view.engine_changed.connect(fired.append)
        self.view.engine_combo.setCurrentIndex(1)  # switch to OpenGL
        self.assertEqual(fired[-1], 1)

    def test_default_resolution_is_fast_preview(self):
        # BL-17: default to the first (fast) preset, not the heaviest last one.
        self.assertEqual(self.view.resolution_index(), 0)

    def test_log_autoscroll_only_when_pinned(self):
        # BL-12: appending must not yank a user who scrolled up back to the bottom.
        for i in range(300):
            self.view.append_log(f"line {i}")
        scrollbar = self.view.log_area.verticalScrollBar()
        if scrollbar.maximum() == 0:
            self.skipTest("log area not scrollable in this environment")
        scrollbar.setValue(0)  # scroll to the top
        self.view.append_log("appended while scrolled up")
        self.assertEqual(scrollbar.value(), 0)  # stayed put
        scrollbar.setValue(scrollbar.maximum())  # re-pin to the bottom
        pinned_max = scrollbar.maximum()
        self.view.append_log("appended while pinned")
        self.assertGreaterEqual(scrollbar.value(), pinned_max - 4)  # tracked down

    def test_static_buttons_expose_mnemonics(self):
        # BL-21: keyboard/AT users get Alt+<key> on the action buttons.
        for button in (
            self.view.select_all_button,
            self.view.copy_command_button,
            self.view.check_env_button,
            self.view.rerun_button,
            self.view.save_log_button,
            self.view.input_browse,
            self.view.output_browse,
            self.view.open_output_button,
        ):
            self.assertIn("&", button.text())

    def test_field_labels_have_buddies(self):
        # BL-21: the input/output/resolution/engine labels link to their control.
        from PyQt5.QtWidgets import QLabel

        with_buddy = [lbl for lbl in self.view.findChildren(QLabel) if lbl.buddy() is not None]
        self.assertGreaterEqual(len(with_buddy), 4)

    def test_render_and_cancel_keyboard_shortcuts_exist(self):
        # BL-21: Ctrl+Return renders; Esc cancels.
        from PyQt5.QtWidgets import QShortcut

        keys = {sc.key().toString() for sc in self.view.findChildren(QShortcut)}
        self.assertIn("Ctrl+Return", keys)
        self.assertIn("Esc", keys)

    def test_button_mode_toggles_state_property(self):
        self.view.set_button_mode("cancel")
        self.assertEqual(self.view.primary_button.property("state"), "cancel")
        self.assertEqual(self.view.primary_button.text(), "Cancel Render")
        self.view.set_button_mode("start")
        self.assertIn(self.view.primary_button.property("state"), ("", None))
        self.assertEqual(self.view.primary_button.text(), "Start Render")

    def test_subdir_scene_label_shows_relative_path(self):
        # A scene found in a subfolder should display its location so the user
        # knows where it came from (recursive discovery).
        rel = os.path.join("pkg", "a.py")
        self.view.set_scene_items([DiscoveredScene("/x/pkg/a.py", "A", "Scene", relative_path=rel)])
        self.assertIn(rel, self.view.scene_list.item(0).text())

    def test_scene_items_carry_discovered_identity(self):
        # Cond 2: duplicate class names must remain independently selectable.
        scenes = [
            DiscoveredScene(
                "examples/scenes/test_debugging_workflow.py",
                "TestDebuggingWorkflowDetailed",
                "Scene",
            ),
            DiscoveredScene(
                "examples/scenes/test_debugging_workflow_improved.py",
                "TestDebuggingWorkflowDetailed",
                "Scene",
            ),
        ]
        self.view.set_scene_items(scenes)
        self.view.select_all_scenes()
        selected = self.view.selected_scenes()
        self.assertEqual(len(selected), 2)
        self.assertTrue(all(isinstance(s, DiscoveredScene) for s in selected))
        self.assertEqual(
            {s.file_path for s in selected}, {scenes[0].file_path, scenes[1].file_path}
        )


if __name__ == "__main__":
    unittest.main()
