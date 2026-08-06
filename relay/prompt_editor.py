"""A window for rewriting the right-click templates.

The alternative was opening prompts.json in whatever handles .json, which asks
you to keep a file format intact while editing prose - one missing comma and the
menu silently falls back to the defaults. Here the structure is the form, so
there is no syntax left to get wrong.

Runs on the same Qt event loop as the orb, and is created from the menu action,
which is already on the GUI thread. Unlike the orb this window does want focus -
you type into it - so none of the non-activating style applies. It never becomes
a paste target either: the tracker ignores windows belonging to our own process.
"""

from PySide6.QtCore import QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from . import prompts as prompts_mod
from .window import FramelessWindow, TitleBar

# The orb's palette, so the two read as one program.
BG = "#0e1015"
PANEL = "#15181f"
LINE = "#252a33"
TEXT = "#e8ebf0"
MUTED = "#8b93a1"

ROW_HEIGHT = 32          # ten of these fit without the list ever scrolling

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QWidget#shell {{
    background: {BG};
    border: 1px solid {LINE};
    border-radius: 8px;
}}
QLabel#title {{ color: {MUTED}; font-size: 12px; }}
QPushButton#chrome {{
    background: transparent;
    border: none;
    border-radius: 5px;
    color: {MUTED};
    font-size: 13px;
    padding: 0;
}}
QPushButton#chrome:hover {{ background: {LINE}; color: {TEXT}; }}
QLabel#hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#field {{ color: {MUTED}; font-size: 11px; letter-spacing: 1px; }}
QListWidget {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{ padding-left: 8px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {LINE}; color: {TEXT}; }}
QScrollBar:vertical {{
    background: transparent; width: 9px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {LINE}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #333a46; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QLineEdit, QPlainTextEdit {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 7px 9px;
    selection-background-color: {LINE};
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid #3d4451; }}
QPushButton {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background: {LINE}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#tiny {{ padding: 4px 0; font-size: 15px; }}
"""

_window = None


class PromptEditor(FramelessWindow):
    border_colour = LINE

    def __init__(self):
        super().__init__("Relay - Prompts")
        self.setMinimumSize(720, 460)
        self.resize(880, 520)

        self.entries = [dict(p) for p in prompts_mod.load()]
        self.index = -1
        self._loading = False       # guard: filling the fields is not an edit

        self._build()
        # After _build, not before: the #primary rule only matches once the
        # button carries its object name.
        self.setStyleSheet(STYLESHEET)
        self._refresh_list()
        if self.entries:
            self.list.setCurrentRow(0)

    # --- layout -----------------------------------------------------------

    def _build(self):
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentRowChanged.connect(self._select)
        self.list.setFixedWidth(260)
        # Padding alone left the rows tall enough that ten of them scrolled;
        # an explicit hint is the only way to pin the height deterministically.
        self.list.setUniformItemSizes(True)

        self.add_btn = self._tiny("+", "Add a prompt", self._add)
        self.del_btn = self._tiny("−", "Delete this prompt", self._delete)
        self.up_btn = self._tiny("↑", "Move up", lambda: self._move(-1))
        self.down_btn = self._tiny("↓", "Move down", lambda: self._move(1))

        tools = QHBoxLayout()
        tools.setSpacing(6)
        for button in (self.add_btn, self.del_btn, self.up_btn, self.down_btn):
            tools.addWidget(button)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.list)
        left.addLayout(tools)

        self.group = QLineEdit()
        self.group.setPlaceholderText("Diagnose, Review, Plan...")
        self.group.textChanged.connect(self._edited)

        self.label = QLineEdit()
        self.label.setPlaceholderText("What it does, in a few words")
        self.label.textChanged.connect(self._edited)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "I need you to review this in complete depth and pinpoint the exact "
            "cause of <problem>."
        )
        self.text.setFont(QFont("Consolas", 11))
        self.text.textChanged.connect(self._edited)

        hint = QLabel(
            "Anything in <angle brackets> stays a blank for you to fill in, "
            "which is why inserting a prompt never presses Enter."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(self._caption("GROUP"))
        right.addWidget(self.group)
        right.addSpacing(6)
        right.addWidget(self._caption("NAME"))
        right.addWidget(self.label)
        right.addSpacing(6)
        right.addWidget(self._caption("PROMPT"))
        right.addWidget(self.text, 1)
        right.addWidget(hint)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addLayout(left)
        columns.addLayout(right, 1)

        self.restore_btn = QPushButton("Restore defaults")
        self.restore_btn.clicked.connect(self._restore_defaults)
        cancel = QPushButton("Close")
        cancel.clicked.connect(self.close)
        save = QPushButton("Save")
        # Set on the widget rather than through the sheet's #primary rule: on
        # Windows the native button style wins over an ID selector and the fill
        # never appears.
        save.setStyleSheet(
            f"QPushButton {{ background: {TEXT}; color: {BG}; border: none;"
            f" border-radius: 6px; padding: 7px 18px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: #ffffff; }}"
        )
        save.clicked.connect(self._save)

        footer = QHBoxLayout()
        footer.addWidget(self.restore_btn)
        footer.addStretch(1)
        footer.addWidget(cancel)
        footer.addWidget(save)

        body = QVBoxLayout()
        body.setContentsMargins(18, 4, 18, 18)
        body.setSpacing(14)
        body.addLayout(columns, 1)
        body.addLayout(footer)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(self.windowTitle(), self.showMinimized, self.close))
        outer.addLayout(body, 1)

    def _caption(self, text):
        label = QLabel(text)
        label.setObjectName("field")
        return label

    def _tiny(self, glyph, tip, slot):
        button = QPushButton(glyph)
        button.setObjectName("tiny")
        button.setToolTip(tip)
        button.setFixedWidth(58)
        button.clicked.connect(slot)
        return button

    # --- list <-> fields --------------------------------------------------

    def _refresh_list(self):
        """Redraw the numbers; they are how the menu is navigated."""
        row = self.list.currentRow()
        self._loading = True
        self.list.clear()
        for i, entry in enumerate(self.entries, start=1):
            group = entry.get("section") or ""
            name = entry.get("label") or entry.get("text", "")[:40]
            item = QListWidgetItem(f"{i}.  {name}")
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            if group:
                item.setToolTip(group)
            self.list.addItem(item)
        self._loading = False
        if 0 <= row < len(self.entries):
            self.list.setCurrentRow(row)

        full = len(self.entries) >= prompts_mod.MAX_PROMPTS
        self.add_btn.setEnabled(not full)
        self.add_btn.setToolTip(
            f"The menu holds {prompts_mod.MAX_PROMPTS}" if full else "Add a prompt"
        )

    def _select(self, row):
        if self._loading:
            return
        self.index = row
        self._loading = True
        if 0 <= row < len(self.entries):
            entry = self.entries[row]
            self.group.setText(entry.get("section", ""))
            self.label.setText(entry.get("label", ""))
            self.text.setPlainText(entry.get("text", ""))
        else:
            self.group.clear()
            self.label.clear()
            self.text.clear()
        self._loading = False

        has = 0 <= row < len(self.entries)
        self.del_btn.setEnabled(has and len(self.entries) > 1)
        self.up_btn.setEnabled(has and row > 0)
        self.down_btn.setEnabled(has and row < len(self.entries) - 1)
        for widget in (self.group, self.label, self.text):
            widget.setEnabled(has)

    def _edited(self):
        """Write straight through to the model, so nothing needs committing."""
        if self._loading or not (0 <= self.index < len(self.entries)):
            return
        entry = self.entries[self.index]
        entry["section"] = self.group.text().strip()
        entry["label"] = self.label.text().strip()
        entry["text"] = self.text.toPlainText().strip()

        # Only the list caption depends on what was typed; rebuilding the whole
        # list on every keystroke would fight the cursor.
        item = self.list.item(self.index)
        if item is not None:
            name = entry["label"] or entry["text"][:40]
            item.setText(f"{self.index + 1}.  {name}")
            item.setToolTip(entry["section"])

    # --- list edits -------------------------------------------------------

    def _add(self):
        if len(self.entries) >= prompts_mod.MAX_PROMPTS:
            return
        self.entries.append({"section": "", "label": "New prompt", "text": ""})
        self._refresh_list()
        self.list.setCurrentRow(len(self.entries) - 1)
        self.label.setFocus()
        self.label.selectAll()

    def _delete(self):
        if not (0 <= self.index < len(self.entries)) or len(self.entries) <= 1:
            return
        del self.entries[self.index]
        row = min(self.index, len(self.entries) - 1)
        self._refresh_list()
        self.list.setCurrentRow(row)

    def _move(self, step):
        row = self.index
        target = row + step
        if not (0 <= row < len(self.entries)) or not (0 <= target < len(self.entries)):
            return
        self.entries[row], self.entries[target] = self.entries[target], self.entries[row]
        self._refresh_list()
        self.list.setCurrentRow(target)

    def _restore_defaults(self):
        answer = QMessageBox.question(
            self, "Restore defaults",
            "Replace all prompts with the built-in set?\n"
            "Anything you have written here is lost.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.entries = [dict(p) for p in prompts_mod.DEFAULTS]
        self._refresh_list()
        self.list.setCurrentRow(0)

    # --- saving -----------------------------------------------------------

    def _save(self):
        """True when the file was written. Refuses rather than silently dropping.

        An entry with no text would be stripped on load and come back as a gap
        in the numbering, so it is better to say so than to save something that
        quietly turns into nine prompts.
        """
        blank = [i for i, e in enumerate(self.entries, start=1) if not e.get("text")]
        if blank:
            numbers = ", ".join(str(i) for i in blank)
            QMessageBox.warning(
                self, "Nothing to paste",
                f"Prompt {numbers} has no text.\n"
                "Write something, or delete the entry.",
            )
            return False

        if prompts_mod.save(self.entries):
            self.close()
            return True

        QMessageBox.critical(
            self, "Could not save",
            f"{prompts_mod.PROMPTS_PATH.name} could not be written.\n"
            "Check the file is not open elsewhere or read-only.",
        )
        return False

    def closeEvent(self, event):
        global _window
        _window = None
        super().closeEvent(event)


def open_editor():
    """Show the editor, raising the one already open rather than a second copy."""
    global _window
    if _window is not None and _window.isVisible():
        _window.raise_()
        _window.activateWindow()
        return _window
    _window = PromptEditor()
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
