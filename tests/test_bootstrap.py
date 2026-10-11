"""Tests for app.bootstrap.build_app (entry-point wiring). Batch 8.

build_app constructs the view + controller from a typed config WITHOUT starting
the event loop, so it is verifiable headless. main() (QApplication + load + exec_)
is exercised by manual launch (out of scope for automated tests).
Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest tests.test_bootstrap
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

import app.bootstrap as bootstrap  # noqa: E402
from app.bootstrap import build_app  # noqa: E402
from app.config.loader import load_config  # noqa: E402

_app = QApplication.instance() or QApplication([])


class BuildAppTests(unittest.TestCase):
    def test_build_app_wires_view_and_controller_from_config(self):
        config = load_config()  # packaged config.v2.json
        view, controller = build_app(config)
        self.addCleanup(view.deleteLater)
        self.assertEqual(view.windowTitle(), "Manim Simple Render")
        self.assertEqual(view.resolution_combo.count(), len(config.resolutions))
        self.assertEqual(view.engine_combo.count(), len(config.engines))
        self.assertIs(controller._view, view)
        self.assertIs(controller._config, config)

    def test_main_wires_about_to_quit_to_the_final_safety_barrier(self):
        class FakeApp:
            def __init__(self):
                self.aboutToQuit = _FakeSignal()

            def exec_(self):
                return 0

        app = FakeApp()
        view = mock.Mock()
        controller = mock.Mock()
        barrier = mock.Mock()
        controller.on_about_to_quit = barrier
        with (
            mock.patch("app.bootstrap.QApplication", return_value=app),
            mock.patch("app.bootstrap.load_config_or_default", return_value=object()),
            mock.patch("app.bootstrap.build_app", return_value=(view, controller)),
        ):
            self.assertEqual(bootstrap.main(["test"]), 0)

        self.assertEqual(app.aboutToQuit._slots, [barrier])
        view.show.assert_called_once_with()


class _FakeSignal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)


if __name__ == "__main__":
    unittest.main()
