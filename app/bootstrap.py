"""Application entry point.

Fixes the legacy bootstrap (main.py:6-11) where config was loaded from a
CWD-relative path BEFORE QApplication existed (so a failure crashed with no GUI).
Here QApplication is created first, the packaged config is loaded with a
fall-back to built-in defaults if it is corrupt/missing (BL-33), and the
view/controller are wired with QSettings-backed persistence and an aboutToQuit
teardown.
"""

import sys

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication

from app.config.loader import load_config_or_default
from app.ui.controller import RenderController
from app.ui.main_view import MainView


def build_app(config, settings=None):
    """Construct the view + controller from a typed config (no event loop). Testable."""
    view = MainView(
        [resolution.label for resolution in config.resolutions],
        [engine.label for engine in config.engines],
        default_resolution_index=config.default_resolution_index,
    )
    controller = RenderController(view, config, settings=settings)
    return view, controller


def main(argv=None):
    app = QApplication(sys.argv if argv is None else argv)
    # A corrupt/missing *packaged* config degrades to built-in defaults instead of
    # exiting (BL-33); a future --config entry point would surface its errors hard.
    config = load_config_or_default()
    settings = QSettings("ManimSimpleRender", "ManimSimpleRender")
    view, controller = build_app(config, settings=settings)
    app.aboutToQuit.connect(controller.on_about_to_quit)
    view.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
