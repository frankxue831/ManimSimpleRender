"""Load the external Qt stylesheet and toggle cancel-state button styling.

Replaces the legacy inline QSS (ui.py:44-104) and the whole-stylesheet re-apply
on every Start/Cancel toggle (ui.py:216-217, 292-293).
"""

import os
import sys

_QSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "theme.qss")


def load_stylesheet(path=_QSS_PATH):
    """Return the QSS text from app/ui/theme.qss (path resolved via __file__, not cwd).

    Degrades to an empty stylesheet (unstyled but fully functional) if the file is
    missing or unreadable, rather than letting MainView construction raise an
    uncaught exception after QApplication already exists. Warns on stderr so a
    broken packaged build (theme.qss not shipped as package data) stays diagnosable.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        print(f"warning: could not load theme stylesheet ({path}): {exc}", file=sys.stderr)
        return ""


def set_button_state(button, state):
    """Set the dynamic ``state`` property and re-polish only this button."""
    set_dynamic_property(button, "state", state)


def set_dynamic_property(widget, name, value):
    """Set a dynamic QSS property and re-polish so a property-based selector
    (e.g. ``QPushButton[state="cancel"]`` or ``QLabel[level="error"]``) restyles
    immediately. An empty/None value resets the property to the default rule."""
    widget.setProperty(name, value or "")
    widget.style().unpolish(widget)
    widget.style().polish(widget)
