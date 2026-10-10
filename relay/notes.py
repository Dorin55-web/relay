"""Notes & Ideas library for Relay's desktop bubble.

Stores ideas, prompt sketches, architecture thoughts, and snippets in notes.json
next to config so they persist across restarts.

Accessible directly from the desktop orb's right-click menu. Any note can be
viewed, searched, edited, copied, or pasted straight into the active target
window where the cursor is typing.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from .prompt_editor import BG, LINE, MUTED, PANEL, TEXT
from .window import EDGE, FramelessWindow, TitleBar

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NOTES_PATH = PROJECT_ROOT / "notes.json"

DEFAULT_NOTES = [
    {
        "id": "welcome-idea",
        "title": "Welcome to Notes & Ideas",
        "text": "Keep your project ideas, prompt blueprints, and reminders here.\n\n"
                "• Click '+ New Idea' to jot down a new thought.\n"
                "• Search filters through titles, tags, and content.\n"
                "• Click 'Paste to Window' or double-click a note to insert it directly into your active text box!",
        "tag": "Idea",
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M"),
    }
]

MAX_TARGET_CHARS = 24
TARGET_POLL_MS = 750
DEBOUNCE_SAVE_MS = 600
ROW_HEIGHT = 44

TAG_CHOICES = ["Idea", "Prompt", "Task", "Code", "Architecture", "Note"]

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QWidget#shell {{
    background: {BG};
    border: 1px solid {EDGE};
    border-radius: 8px;
}}
QLabel#title {{ color: {MUTED}; font-size: 12px; }}
QLabel#field {{ color: {MUTED}; font-size: 11px; letter-spacing: 1px; font-weight: 600; }}
QLabel#status {{ color: {MUTED}; font-size: 12px; }}
QLabel#hint {{ color: {MUTED}; font-size: 11px; }}

QListWidget {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 10px;
    border-radius: 5px;
    margin-bottom: 3px;
}}
QListWidget::item:selected {{
    background: {LINE};
    color: {TEXT};
}}
QListWidget::item:hover {{
    background: #1c212b;
}}

QLineEdit, QPlainTextEdit, QComboBox {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 7px 10px;
    selection-background-color: {LINE};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border: 1px solid #3d4451;
}}
QComboBox {{
    padding-right: 20px;
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QPushButton {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background: {LINE}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#tiny {{ padding: 4px 0; font-size: 15px; }}

QPushButton#chrome {{
    background: transparent;
    border: none;
    border-radius: 5px;
    color: {MUTED};
    font-size: 13px;
    padding: 0;
}}
QPushButton#chrome:hover {{ background: {LINE}; color: {TEXT}; }}

QPushButton#paste {{
    background: #192231;
    color: #38bdf8;
    border: 1px solid #283548;
    font-weight: 600;
}}
QPushButton#paste:hover {{
    background: #223046;
    border: 1px solid #3d4f6a;
}}
"""


# --- Data persistence -----------------------------------------------------

def _generate_id():
    return f"note-{int(time.time() * 1000)}"


def ensure_file():
    """Ensure notes.json exists and has valid initial structure."""
    if not NOTES_PATH.exists():
        save(DEFAULT_NOTES)


def load():
    """Load list of notes from notes.json."""
    if not NOTES_PATH.exists():
        return list(DEFAULT_NOTES)
    try:
        data = json.loads(NOTES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return _clean_notes(data)
        if isinstance(data, dict) and "notes" in data and isinstance(data["notes"], list):
            return _clean_notes(data["notes"])
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[notes] could not read {NOTES_PATH.name}: {exc}")
    return list(DEFAULT_NOTES)


def _clean_notes(items):
    out = []
    now_str = time.strftime("%Y-%m-%d %H:%M")
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "")
        tag = str(item.get("tag") or "Idea").strip() or "Idea"
        nid = str(item.get("id") or _generate_id())
        created_at = str(item.get("created_at") or now_str)
        updated_at = str(item.get("updated_at") or created_at)
        out.append({
            "id": nid,
            "title": title or "Untitled note",
            "text": text,
            "tag": tag,
            "created_at": created_at,
            "updated_at": updated_at,
        })
    return out


def save(notes):
    """Save notes to notes.json atomically."""
    clean = _clean_notes(notes)
    try:
        temp = NOTES_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(NOTES_PATH)
        return True
    except OSError as exc:
        print(f"[notes] could not save {NOTES_PATH.name}: {exc}")
        return False


def add_note(title="New idea", text="", tag="Idea"):
    """Append a new note and save."""
    notes = load()
    now_str = time.strftime("%Y-%m-%d %H:%M")
    new_note = {
        "id": _generate_id(),
        "title": title.strip() or "New idea",
        "text": text,
        "tag": tag.strip() or "Idea",
        "created_at": now_str,
        "updated_at": now_str,
    }
    notes.insert(0, new_note)
    save(notes)
    return new_note


def filter_notes(query, notes=None):
    """Filter notes by case-insensitive text match in title, tag, or content."""
    source = notes if notes is not None else load()
    q = (query or "").strip().lower()
    if not q:
        return list(source)
    return [
        n for n in source
        if q in n.get("title", "").lower()
        or q in n.get("tag", "").lower()
        or q in n.get("text", "").lower()
    ]


def _short_target(name):
    name = " ".join((name or "").split())
    if not name:
        return ""
    if len(name) <= MAX_TARGET_CHARS:
        return name
    return name[: MAX_TARGET_CHARS - 1].rstrip() + "…"


# --- UI Window ------------------------------------------------------------

_window = None


class NotesWindow(FramelessWindow):
    border_colour = EDGE

    def __init__(self, on_paste=None, target_getter=None):
        super().__init__("Relay - Notes & Ideas")
        self.setMinimumSize(800, 520)
        self.resize(940, 580)

        self.on_paste = on_paste
        self.target_getter = target_getter

        self.notes = load()
        self.active_id = None
        self._loading = False
        self._target_shown = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(DEBOUNCE_SAVE_MS)
        self._debounce_timer.timeout.connect(self._auto_save)

        self._target_timer = QTimer(self)
        self._target_timer.setInterval(TARGET_POLL_MS)
        self._target_timer.timeout.connect(self._update_target_label)

        self._build()
        self.setStyleSheet(STYLESHEET)
        self._refresh_list()

        if self.notes:
            self._select_by_id(self.notes[0]["id"])

        self._target_timer.start()

    def _build(self):
        # --- Left column: Search + Notes list + Controls ---
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter ideas & notes... 🔍")
        self.search_input.textChanged.connect(self._on_search_changed)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentRowChanged.connect(self._on_row_changed)
        self.list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list.setFixedWidth(290)
        self.list.setUniformItemSizes(True)

        self.add_btn = self._button("+", "Add new idea", self._add_new)
        self.del_btn = self._button("−", "Delete this note", self._delete_active)
        self.up_btn = self._button("↑", "Move up", lambda: self._move(-1))
        self.down_btn = self._button("↓", "Move down", lambda: self._move(1))

        tools = QHBoxLayout()
        tools.setSpacing(6)
        for b in (self.add_btn, self.del_btn, self.up_btn, self.down_btn):
            tools.addWidget(b)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.search_input)
        left.addWidget(self.list, 1)
        left.addLayout(tools)

        # --- Right column: Editor ---
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Title of your idea...")
        self.title_input.textChanged.connect(self._on_field_edited)

        self.tag_combo = QComboBox()
        self.tag_combo.setEditable(True)
        self.tag_combo.addItems(TAG_CHOICES)
        self.tag_combo.currentTextChanged.connect(self._on_field_edited)
        self.tag_combo.setFixedWidth(140)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        header_row.addWidget(self.title_input, 1)
        header_row.addWidget(self.tag_combo)

        self.text_editor = QPlainTextEdit()
        self.text_editor.setPlaceholderText(
            "Jot down your idea, prompt draft, architecture thoughts, or code snippet...\n\n"
            "Everything saves automatically. Click 'Paste to Window' to send it where your cursor is."
        )
        self.text_editor.setFont(QFont("Consolas", 11))
        self.text_editor.textChanged.connect(self._on_field_edited)

        self.status_label = QLabel(f"{len(self.notes)} notes")
        self.status_label.setObjectName("status")

        self.paste_btn = QPushButton("Paste to Window")
        self.paste_btn.setObjectName("paste")
        self.paste_btn.setToolTip("Insert this note directly into the window you were typing in")
        self.paste_btn.clicked.connect(self._paste_active)

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setToolTip("Copy this note text to clipboard")
        self.copy_btn.clicked.connect(self._copy_active)

        self.save_btn = QPushButton("Save")
        self.save_btn.setStyleSheet(
            f"QPushButton {{ background: {TEXT}; color: {BG}; border: none;"
            f" border-radius: 6px; padding: 7px 18px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: #ffffff; }}"
        )
        self.save_btn.clicked.connect(self._explicit_save)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addWidget(self.status_label)
        footer.addStretch(1)
        footer.addWidget(self.paste_btn)
        footer.addWidget(self.copy_btn)
        footer.addWidget(self.save_btn)
        footer.addWidget(close_btn)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self._caption("TITLE & CATEGORY"))
        right.addLayout(header_row)
        right.addSpacing(4)
        right.addWidget(self._caption("NOTE CONTENT"))
        right.addWidget(self.text_editor, 1)
        right.addLayout(footer)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        columns.addLayout(left)
        columns.addLayout(right, 1)

        body = QVBoxLayout()
        body.setContentsMargins(18, 4, 18, 18)
        body.setSpacing(12)
        body.addLayout(columns, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(self.windowTitle(), self.showMinimized, self.close))
        outer.addLayout(body, 1)

    def _caption(self, text):
        label = QLabel(text)
        label.setObjectName("field")
        return label

    def _button(self, glyph, tip, slot):
        b = QPushButton(glyph)
        b.setObjectName("tiny")
        b.setToolTip(tip)
        b.setFixedWidth(64)
        b.clicked.connect(slot)
        return b

    # --- List population and selection ---

    def _refresh_list(self):
        query = self.search_input.text()
        filtered = filter_notes(query, self.notes)
        self._loading = True
        self.list.clear()

        for item_data in filtered:
            title = item_data.get("title") or "Untitled"
            tag = item_data.get("tag") or "Idea"
            date = item_data.get("updated_at") or item_data.get("created_at") or ""
            date_short = date.split()[-1] if " " in date else date

            display_text = f"[{tag}]  {title}"
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, item_data["id"])
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            item.setToolTip(f"{title}\n{date}\n\n{item_data.get('text', '')[:200]}")
            self.list.addItem(item)

        self._loading = False

        # Restore selection
        if self.active_id:
            self._select_by_id(self.active_id)
        elif self.list.count() > 0:
            self.list.setCurrentRow(0)
        else:
            self._clear_editor()

    def _select_by_id(self, note_id):
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) == note_id:
                self.list.setCurrentRow(row)
                return
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def _on_row_changed(self, row):
        if self._loading or row < 0:
            return
        item = self.list.item(row)
        if not item:
            return
        note_id = item.data(Qt.UserRole)
        note = self._get_note(note_id)
        if not note:
            return

        self.active_id = note_id
        self._loading = True
        self.title_input.setText(note.get("title", ""))
        self.tag_combo.setCurrentText(note.get("tag", "Idea"))
        self.text_editor.setPlainText(note.get("text", ""))
        self._loading = False
        self.status_label.setText(f"Last updated: {note.get('updated_at', 'never')}")

    def _get_note(self, note_id):
        for n in self.notes:
            if n.get("id") == note_id:
                return n
        return None

    def _clear_editor(self):
        self._loading = True
        self.active_id = None
        self.title_input.clear()
        self.tag_combo.setCurrentText("Idea")
        self.text_editor.clear()
        self._loading = False
        self.status_label.setText("No notes")

    # --- Editing and Auto-save ---

    def _on_field_edited(self):
        if self._loading or not self.active_id:
            return
        note = self._get_note(self.active_id)
        if not note:
            return

        note["title"] = self.title_input.text().strip() or "Untitled"
        note["tag"] = self.tag_combo.currentText().strip() or "Idea"
        note["text"] = self.text_editor.toPlainText()
        note["updated_at"] = time.strftime("%Y-%m-%d %H:%M")

        self.status_label.setText("Unsaved changes...")
        self._debounce_timer.start()

    def _auto_save(self):
        if save(self.notes):
            # Update list item label without resetting selection
            current_item = self.list.currentItem()
            if current_item and self.active_id:
                note = self._get_note(self.active_id)
                if note:
                    current_item.setText(f"[{note['tag']}]  {note['title']}")
            self.status_label.setText("Saved ✓")

    def _explicit_save(self):
        self._debounce_timer.stop()
        if self.active_id:
            note = self._get_note(self.active_id)
            if note:
                note["title"] = self.title_input.text().strip() or "Untitled"
                note["tag"] = self.tag_combo.currentText().strip() or "Idea"
                note["text"] = self.text_editor.toPlainText()
                note["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
        if save(self.notes):
            self._refresh_list()
            self.status_label.setText(f"Saved to {NOTES_PATH.name} ✓")

    # --- CRUD operations ---

    def _add_new(self):
        now_str = time.strftime("%Y-%m-%d %H:%M")
        new_note = {
            "id": _generate_id(),
            "title": "New idea",
            "text": "",
            "tag": "Idea",
            "created_at": now_str,
            "updated_at": now_str,
        }
        self.notes.insert(0, new_note)
        save(self.notes)
        self.active_id = new_note["id"]
        self.search_input.clear()
        self._refresh_list()
        self.title_input.setFocus()
        self.title_input.selectAll()

    def _delete_active(self):
        if not self.active_id:
            return
        note = self._get_note(self.active_id)
        title = note.get("title", "this note") if note else "this note"
        reply = QMessageBox.question(
            self,
            "Delete note",
            f"Are you sure you want to delete '{title}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.notes = [n for n in self.notes if n.get("id") != self.active_id]
            save(self.notes)
            self.active_id = None
            self._refresh_list()

    def _move(self, direction):
        if not self.active_id:
            return
        idx = next((i for i, n in enumerate(self.notes) if n.get("id") == self.active_id), -1)
        if idx < 0:
            return
        target_idx = idx + direction
        if 0 <= target_idx < len(self.notes):
            self.notes[idx], self.notes[target_idx] = self.notes[target_idx], self.notes[idx]
            save(self.notes)
            self._refresh_list()

    def _on_search_changed(self, _text):
        self._refresh_list()

    # --- Target Window & Paste / Copy ---

    def _update_target_label(self):
        if self.target_getter is None:
            self.paste_btn.setEnabled(self.on_paste is not None)
            return
        raw = self.target_getter()
        target = _short_target(raw)
        if target == self._target_shown:
            return
        self._target_shown = target
        if target:
            self.paste_btn.setText(f"Paste to {target}")
            self.paste_btn.setEnabled(True)
        else:
            self.paste_btn.setText("Paste to Window")
            self.paste_btn.setEnabled(False)

    def _paste_active(self):
        if not self.active_id or self.on_paste is None:
            return
        note = self._get_note(self.active_id)
        if not note:
            return
        content = note.get("text", "").strip() or note.get("title", "").strip()
        if not content:
            QMessageBox.information(self, "Empty note", "This note has no text to paste.")
            return

        self.on_paste(content)
        self.close()

    def _copy_active(self):
        if not self.active_id:
            return
        note = self._get_note(self.active_id)
        if not note:
            return
        content = note.get("text", "") or note.get("title", "")
        QApplication.clipboard().setText(content)
        self.status_label.setText("Copied to clipboard ✓")

    def _on_item_double_clicked(self, _item):
        """Double clicking a note pastes it straight to target if on_paste is configured."""
        if self.on_paste is not None:
            self._paste_active()

    def closeEvent(self, event):
        global _window
        self._debounce_timer.stop()
        self._target_timer.stop()
        _window = None
        super().closeEvent(event)


# --- Factory & Warmup -----------------------------------------------------

def _warm_up(window):
    window.setAttribute(Qt.WA_ShowWithoutActivating, True)
    window.move(-4000, -4000)
    try:
        window.show()
        QApplication.processEvents()
        window.hide()
    finally:
        window.setAttribute(Qt.WA_ShowWithoutActivating, False)
        _centre(window)


def _centre(window):
    screen = QApplication.primaryScreen()
    if screen is None:
        return
    frame = window.frameGeometry()
    frame.moveCenter(screen.availableGeometry().center())
    window.move(frame.topLeft())


def prebuild(on_paste=None, target_getter=None):
    """Build the notes window ahead of time so opening it is instantaneous."""
    global _window
    if _window is not None:
        return _window
    _window = NotesWindow(on_paste, target_getter)
    _warm_up(_window)
    return _window


def open_notes(on_paste=None, target_getter=None):
    """Show the notes window, raising the existing instance if already open."""
    global _window
    if _window is None:
        _window = NotesWindow(on_paste, target_getter)
    elif not _window.isVisible():
        _window.on_paste = on_paste
        _window.target_getter = target_getter
        _window.notes = load()
        _window._refresh_list()
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
