"""Passive PyQt5 main window: widgets, setters, and intent signals only.

Holds NO render/thread logic and never reaches into the worker (the controller
does that). Decomposes the legacy god-class MainWindow (ui.py:15-304). Scene-list
items carry their DiscoveredScene identity in Qt.UserRole (Cond 2) so duplicate
class names across files stay independently selectable.
"""

import os
from collections import Counter
from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QKeySequence, QTextCursor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QShortcut,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.ui import sections, theme

_SCENE_ROLE = Qt.UserRole
_STATUS_ROLE = Qt.UserRole + 1  # per-scene JobOutcome after a render
_LOG_MAX_BLOCKS = 5000  # FIFO cap so a long, verbose render can't grow the log document unbounded
_STATUS_COLORS = {
    "success": "#28a745",
    "failed": "#dc3545",
    "manim_not_found": "#dc3545",
    "cancelled": "#ffc107",
}
_FAILED_STATUS_VALUES = {"failed", "manim_not_found"}  # outcomes eligible for "Rerun Failed"
# Also resumable: a cancelled (never-run) scene can be re-rendered in one click (BL-14).
_RERUNNABLE_STATUS_VALUES = {"failed", "manim_not_found", "cancelled"}


class MainView(QMainWindow):
    browse_input_requested = pyqtSignal()
    browse_output_requested = pyqtSignal()
    input_path_edited = pyqtSignal()
    primary_button_clicked = pyqtSignal()
    check_env_requested = pyqtSignal()
    engine_changed = pyqtSignal(int)
    select_all_requested = pyqtSignal()
    copy_command_requested = pyqtSignal()
    rerun_failed_requested = pyqtSignal()
    open_output_requested = pyqtSignal()
    save_log_requested = pyqtSignal()
    close_requested = pyqtSignal(object)

    def __init__(self, resolution_labels, engine_labels, default_resolution_index=0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manim Simple Render")
        self.setGeometry(100, 100, 700, 600)
        self.setStyleSheet(theme.load_stylesheet())
        self._build_ui(resolution_labels, engine_labels, default_resolution_index)

    def _build_ui(self, resolution_labels, engine_labels, default_resolution_index):
        central = QWidget(self)
        root = QVBoxLayout(central)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Folder containing your Manim .py scenes")
        self.input_edit.editingFinished.connect(self.input_path_edited.emit)
        self.input_browse = QPushButton("&Browse")
        self.input_browse.clicked.connect(self.browse_input_requested.emit)
        root.addWidget(sections.labeled_row("&Input folder:", self.input_edit, self.input_browse))

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Folder where rendered videos are written")
        self.output_browse = QPushButton("Bro&wse")
        self.output_browse.clicked.connect(self.browse_output_requested.emit)
        self.open_output_button = QPushButton("&Open")
        self.open_output_button.clicked.connect(self.open_output_requested.emit)
        root.addWidget(
            sections.labeled_row(
                "&Output folder:", self.output_edit, self.output_browse, self.open_output_button
            )
        )

        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(resolution_labels)
        if resolution_labels:
            index = default_resolution_index
            if not (0 <= index < len(resolution_labels)):
                index = 0
            self.resolution_combo.setCurrentIndex(index)  # data-driven default (BL-17)
        root.addWidget(sections.labeled_row("&Resolution:", self.resolution_combo))

        self.engine_combo = QComboBox()
        self.engine_combo.addItems(engine_labels)
        # connect AFTER addItems so the initial population (-1 -> 0) is not propagated
        self.engine_combo.currentIndexChanged.connect(self.engine_changed.emit)
        root.addWidget(sections.labeled_row("En&gine:", self.engine_combo))

        self.select_all_button = QPushButton("Select &All")
        self.select_all_button.clicked.connect(self.select_all_requested.emit)
        self.copy_command_button = QPushButton("&Copy Command")
        self.copy_command_button.clicked.connect(self.copy_command_requested.emit)
        root.addWidget(
            sections.labeled_row("Sc&enes:", self.select_all_button, self.copy_command_button)
        )
        self.scene_list = QListWidget()
        self.scene_list.setSelectionMode(QAbstractItemView.MultiSelection)
        root.addWidget(self.scene_list)

        self.check_env_button = QPushButton("Check &Environment")
        self.check_env_button.clicked.connect(self.check_env_requested.emit)
        root.addWidget(self.check_env_button)

        self.primary_button = QPushButton("Start Render")
        self.primary_button.clicked.connect(self.primary_button_clicked.emit)
        root.addWidget(self.primary_button)

        self.rerun_button = QPushButton("&Rerun Failed / Resume")
        self.rerun_button.setEnabled(False)  # enabled only after a render leaves failures
        self.rerun_button.clicked.connect(self.rerun_failed_requested.emit)
        root.addWidget(self.rerun_button)

        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)  # long error text / paths must wrap, not clip
        root.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        root.addWidget(self.progress_bar)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFixedHeight(150)
        self.log_area.document().setMaximumBlockCount(_LOG_MAX_BLOCKS)
        root.addWidget(self.log_area)

        self.save_log_button = QPushButton("Sa&ve Log")
        self.save_log_button.clicked.connect(self.save_log_requested.emit)
        root.addWidget(self.save_log_button)

        # Keyboard affordances for power/AT users (BL-21): render and cancel without
        # the mouse. Esc only fires while a render is actually running.
        self._render_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self._render_shortcut.activated.connect(self.primary_button_clicked.emit)
        self._cancel_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._cancel_shortcut.activated.connect(self._on_escape)

        self.setCentralWidget(central)
        self._input_controls = (
            self.input_edit,
            self.input_browse,
            self.output_edit,
            self.output_browse,
            self.resolution_combo,
            self.engine_combo,
            self.scene_list,
            self.select_all_button,
            self.copy_command_button,
            self.check_env_button,  # no doctor mid-render; results would interleave (B1)
        )  # open_output_button stays enabled during a render (peek at partial output)

    def _on_escape(self):
        if self.primary_button.isEnabled() and self.primary_button.text() == "Cancel Render":
            self.primary_button_clicked.emit()

    # --- setters used by the controller ---
    def set_status(self, text, level=""):
        """Set the status text. ``level="error"`` applies the error style (red,
        via QLabel[level="error"]); the default level clears any prior styling."""
        self.status_label.setText(text)
        theme.set_dynamic_property(self.status_label, "level", level)

    def set_progress(self, completed, total):
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(completed)

    def reset_progress(self):
        self.progress_bar.setRange(0, 1)  # determinate again (undoes set_progress_busy)
        self.progress_bar.setValue(0)

    def set_progress_busy(self):
        """Show an indeterminate "busy" animation (range 0,0) while cancelling, so the
        up-to-5s terminate wait reads as working rather than hung (BL-24)."""
        self.progress_bar.setRange(0, 0)

    def append_log(self, message):
        # Auto-scroll only when the user is already at the bottom, so scrolling up to
        # read earlier output during a verbose render is not yanked back (BL-12).
        scrollbar = self.log_area.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_area.append(f"[{timestamp}] {message}")
        if at_bottom:
            self.log_area.moveCursor(QTextCursor.End)
            scrollbar.setValue(scrollbar.maximum())

    def clear_log(self):
        self.log_area.clear()

    def set_controls_enabled(self, enabled):
        for widget in self._input_controls:
            widget.setEnabled(enabled)  # primary_button stays enabled (becomes Cancel)

    def set_environment_check_pending(self, pending):
        """Presentation only (Release B1): reflect an in-flight environment check.
        The controller owns the pending state; this setter starts no work."""
        self.check_env_button.setText(
            "Checking Environment..." if pending else "Check &Environment"
        )
        self.check_env_button.setEnabled(not pending)

    def set_button_mode(self, mode):
        if mode == "cancel":
            self.primary_button.setText("Cancel Render")
            self.primary_button.setEnabled(True)
            theme.set_button_state(self.primary_button, "cancel")
        elif mode == "cancelling":
            # Cancel is in flight; disable so it can't be clicked again (BL-24).
            self.primary_button.setText("Cancelling...")
            self.primary_button.setEnabled(False)
            theme.set_button_state(self.primary_button, "cancel")
        elif mode == "finishing":
            # Job accounting is final but the worker thread has not stopped yet:
            # there is nothing left to cancel and nothing may be started.
            self.primary_button.setText("Finishing...")
            self.primary_button.setEnabled(False)
            theme.set_button_state(self.primary_button, "cancel")
        else:
            self.primary_button.setText("Start Render")
            self.primary_button.setEnabled(True)
            theme.set_button_state(self.primary_button, "")

    # --- accessors used by the controller ---
    def input_folder(self):
        return self.input_edit.text()

    def output_folder(self):
        return self.output_edit.text()

    def set_input_folder(self, path):
        self.input_edit.setText(path)

    def set_output_folder(self, path):
        self.output_edit.setText(path)

    def resolution_index(self):
        return self.resolution_combo.currentIndex()

    def engine_index(self):
        return self.engine_combo.currentIndex()

    def log_text(self):
        return self.log_area.toPlainText()

    def set_scene_items(self, discovered_scenes):
        """Populate the scene list; each item carries its DiscoveredScene (Cond 2).

        Preserves the current selection across a re-discovery: any row whose
        (file_path, scene_class) identity was selected before is re-selected, so
        re-browsing the same folder (or restoring on restart) does not silently
        drop the user's selection. Scenes that vanished simply are not re-selected.
        """
        previously_selected = {(s.file_path, s.scene_class) for s in self.selected_scenes()}
        self.scene_list.clear()
        for scene in discovered_scenes:
            item = QListWidgetItem(scene.scene_class)
            item.setData(_SCENE_ROLE, scene)
            self.scene_list.addItem(item)
            if (scene.file_path, scene.scene_class) in previously_selected:
                item.setSelected(True)
        self._disambiguate_duplicate_labels()

    def selected_scenes(self):
        """Return the DiscoveredScene objects for the currently selected rows."""
        return [
            self.scene_list.item(row).data(_SCENE_ROLE)
            for row in range(self.scene_list.count())
            if self.scene_list.item(row).isSelected()
        ]

    def select_all_scenes(self):
        self.scene_list.selectAll()

    def copy_to_clipboard(self, text):
        """Put ``text`` on the system clipboard (Qt owned by the view)."""
        QApplication.clipboard().setText(text)

    def set_rerun_enabled(self, enabled):
        self.rerun_button.setEnabled(enabled)

    def failed_scene_keys(self):
        """(file_path, scene_class) of rows whose last outcome was a failure."""
        keys = []
        for row in range(self.scene_list.count()):
            item = self.scene_list.item(row)
            outcome = item.data(_STATUS_ROLE)
            if outcome is not None and getattr(outcome, "value", None) in _FAILED_STATUS_VALUES:
                scene = item.data(_SCENE_ROLE)
                keys.append((scene.file_path, scene.scene_class))
        return keys

    def rerunnable_scene_keys(self):
        """(file_path, scene_class) of rows whose last outcome failed OR was cancelled,
        so one click re-renders the failures and resumes the un-run scenes (BL-14)."""
        keys = []
        for row in range(self.scene_list.count()):
            item = self.scene_list.item(row)
            outcome = item.data(_STATUS_ROLE)
            if outcome is not None and getattr(outcome, "value", None) in _RERUNNABLE_STATUS_VALUES:
                scene = item.data(_SCENE_ROLE)
                keys.append((scene.file_path, scene.scene_class))
        return keys

    def select_only(self, keys):
        """Select exactly the rows whose (file_path, scene_class) is in ``keys``."""
        for row in range(self.scene_list.count()):
            item = self.scene_list.item(row)
            scene = item.data(_SCENE_ROLE)
            item.setSelected((scene.file_path, scene.scene_class) in keys)

    def mark_scene_result(self, file_path, scene_class, outcome, return_code=None):
        """Decorate the matching scene row with its render outcome (color + tooltip).

        For a failed row the tooltip carries the exit code so a multi-failure batch is
        attributable without scrolling the merged log (BL-30)."""
        item = self._scene_item(file_path, scene_class)
        if item is None:
            return
        item.setData(_STATUS_ROLE, outcome)
        key = getattr(outcome, "value", str(outcome))
        tooltip = key
        if key in _FAILED_STATUS_VALUES and isinstance(return_code, int) and return_code != 0:
            tooltip = f"{key} (exit {return_code})"
        item.setToolTip(tooltip)
        color = _STATUS_COLORS.get(key)
        if color:
            item.setForeground(QBrush(QColor(color)))

    def scene_status(self, file_path, scene_class):
        """Return the stored JobOutcome for a scene row, or None if unmarked."""
        item = self._scene_item(file_path, scene_class)
        return item.data(_STATUS_ROLE) if item is not None else None

    def clear_scene_results(self):
        """Reset per-scene result decoration (called at the start of each render)."""
        for row in range(self.scene_list.count()):
            item = self.scene_list.item(row)
            item.setData(_STATUS_ROLE, None)
            item.setToolTip("")
            item.setForeground(QBrush())

    def _scene_item(self, file_path, scene_class):
        for row in range(self.scene_list.count()):
            item = self.scene_list.item(row)
            scene = item.data(_SCENE_ROLE)
            if scene.file_path == file_path and scene.scene_class == scene_class:
                return item
        return None

    def _disambiguate_duplicate_labels(self):
        counts = Counter(
            self.scene_list.item(row).data(_SCENE_ROLE).scene_class
            for row in range(self.scene_list.count())
        )
        for row in range(self.scene_list.count()):
            item = self.scene_list.item(row)
            scene = item.data(_SCENE_ROLE)
            location = scene.relative_path or os.path.basename(scene.file_path)
            in_subdir = os.sep in (scene.relative_path or "")
            # show the location for duplicates AND for any scene found in a subdir
            if counts[scene.scene_class] > 1 or in_subdir:
                item.setText(f"{scene.scene_class}  ({location})")

    def set_resolution_index(self, index):
        if 0 <= index < self.resolution_combo.count():
            self.resolution_combo.setCurrentIndex(index)

    def set_engine_index(self, index):
        if 0 <= index < self.engine_combo.count():
            # Programmatic restore must not fire engine_changed (which would emit the
            # OpenGL experimental advisory unsolicited on startup); block during the
            # set, leave genuine user clicks emitting normally (BL-26).
            blocked = self.engine_combo.blockSignals(True)
            self.engine_combo.setCurrentIndex(index)
            self.engine_combo.blockSignals(blocked)

    def show_environment_report(self, report):
        """Show the environment preflight result in a modal dialog."""
        self._build_environment_box(report).exec_()

    def _build_environment_box(self, report):
        """Build the environment-report dialog (no exec_, so it is headless-testable)."""
        box = QMessageBox(self)
        box.setWindowTitle("Environment Check")
        box.setIcon(QMessageBox.Information if report.ok else QMessageBox.Warning)
        box.setText(
            "Your environment looks ready to render."
            if report.ok
            else "Some environment checks failed - see details."
        )
        # Show the actionable FAILED/WARNING lines up front instead of hiding them
        # behind the collapsed "Show Details..." disclosure (BL-09).
        issues = report.issue_text()
        if issues:
            box.setInformativeText(issues)
        box.setDetailedText(report.as_text())
        return box

    def prompt_input_folder(self):
        return QFileDialog.getExistingDirectory(self, "Select input folder")

    def prompt_output_folder(self):
        return QFileDialog.getExistingDirectory(self, "Select output folder")

    def prompt_save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save log", "render.log", "Log files (*.log)")
        return path

    def closeEvent(self, event):
        self.close_requested.emit(event)
        if event.isAccepted():
            super().closeEvent(event)
