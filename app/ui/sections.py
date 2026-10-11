"""Pure layout factory helpers for MainView (no business logic, no state)."""

from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget


def labeled_row(label_text, *widgets):
    """Return a QWidget: a label followed by the given widgets laid out in a row.

    If ``label_text`` carries an ``&`` mnemonic, the label is made a buddy of the
    first widget, which both creates the Alt+<key> shortcut and the accessible-name
    relationship a screen reader announces (BL-21)."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(label_text)
    if widgets and "&" in label_text:
        label.setBuddy(widgets[0])
    layout.addWidget(label)
    for widget in widgets:
        layout.addWidget(widget)
    return container
