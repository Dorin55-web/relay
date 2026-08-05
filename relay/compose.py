"""Type Romanian, get English, send it where you were writing.

The dictation path never touches the keyboard, which leaves nothing for the
times you would rather type - a term you know Whisper mangles, something you
are copying off a page, or a room where talking is not on.

Translation runs on a worker thread. It costs 50-250ms a sentence, which is
imperceptible in a menu but a visible stutter if it lands between keystrokes on
the thread that draws them.
"""

import threading

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from .prompt_editor import BG, LINE, MUTED, PANEL, TEXT
from .window import FramelessWindow, TitleBar

# Long enough that a normal typing rhythm does not trigger a translation on
# every keystroke, short enough that pausing to think produces one.
DEBOUNCE_MS = 450

# How often the Send button re-reads where it would actually paste. The target
# changes when you click into a different text box, which can happen while this
# window is open.
TARGET_POLL_MS = 700

# A window title is not a name. A browser's is the whole page title, which on a
# search results page is the query and every parameter with it - long enough to
# stretch the button across the window if it is used raw.
MAX_TARGET_CHARS = 22


def short_target(name):
    """A window title cut down to something that fits on a button."""
    name = " ".join((name or "").split())
    if not name:
        return ""
    if len(name) <= MAX_TARGET_CHARS:
        return name
    return name[: MAX_TARGET_CHARS - 1].rstrip() + "…"

STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QWidget#shell {{
    background: {BG};
    border: 1px solid {LINE};
    border-radius: 8px;
}}
QLabel#field {{ color: {MUTED}; font-size: 11px; letter-spacing: 1px; }}
QLabel#status {{ color: {MUTED}; font-size: 12px; }}
QLabel#title {{ color: {MUTED}; font-size: 12px; }}
QPlainTextEdit {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 10px 12px;
    selection-background-color: {LINE};
}}
QPlainTextEdit:focus {{ border: 1px solid #3d4451; }}
QPlainTextEdit#output {{ background: #10131a; }}
QPushButton {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 8px 16px;
}}
QPushButton:hover {{ background: {LINE}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#chrome {{
    background: transparent; border: none; border-radius: 5px;
    color: {MUTED}; font-size: 13px; padding: 0;
}}
QPushButton#chrome:hover {{ background: {LINE}; color: {TEXT}; }}
"""

_window = None


class _Bridge(QObject):
    """Carries a worker thread's result back to the GUI thread."""

    done = Signal(str, str)      # english, error


class Compose(FramelessWindow):
    border_colour = LINE

    def __init__(self, translator, on_paste, target_getter=None):
        super().__init__("Relay - Write")
        self.translator = translator
        self.on_paste = on_paste
        # A callable, not a string: the target moves when you click into a
        # different text box, and that can happen while this window is open.
        # A label captured at construction would name the wrong window.
        self.target_getter = target_getter
        self._target_shown = None

        self._pending = 0            # newest request id, so stale ones are dropped
        self._english = ""

        self.setMinimumSize(620, 460)
        self.resize(820, 620)

        self.bridge = _Bridge()
        self.bridge.done.connect(self._show_result)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._translate_now)

        self._target_timer = QTimer(self)
        self._target_timer.timeout.connect(self._refresh_target)
        self._target_timer.start(TARGET_POLL_MS)

        self._build()
        self.setStyleSheet(STYLESHEET)
        self._refresh_target()
        self.source.setFocus()

    # --- layout -----------------------------------------------------------

    def _build(self):
        self.source = QPlainTextEdit()
        self.source.setPlaceholderText(
            "Scrie aici in romana...\n\nCtrl+Enter trimite textul englezesc "
            "unde scriai ultima data."
        )
        self.source.setFont(QFont("Segoe UI", 12))
        self.source.textChanged.connect(self._on_typed)

        self.output = QPlainTextEdit()
        self.output.setObjectName("output")
        self.output.setFont(QFont("Segoe UI", 12))
        self.output.setPlaceholderText("The English lands here, and is yours to edit.")

        self.status = QLabel("")
        self.status.setObjectName("status")

        self.copy_btn = QPushButton("Copy")
        self.copy_btn.clicked.connect(self._copy)
        self.paste_btn = QPushButton("Send")
        # Belt and braces on top of truncating the title: whatever ends up in
        # the label, the button cannot shove the rest of the row off screen.
        self.paste_btn.setMaximumWidth(260)
        self.paste_btn.clicked.connect(self._send)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        for button in (self.copy_btn, self.paste_btn):
            button.setEnabled(False)

        footer = QHBoxLayout()
        footer.addWidget(self.status)
        footer.addStretch(1)
        footer.addWidget(close_btn)
        footer.addWidget(self.copy_btn)
        footer.addWidget(self.paste_btn)

        body = QVBoxLayout()
        body.setContentsMargins(18, 6, 18, 18)
        body.setSpacing(8)
        body.addWidget(self._caption("ROMANIAN"))
        body.addWidget(self.source, 1)
        body.addSpacing(8)
        body.addWidget(self._caption("ENGLISH"))
        body.addWidget(self.output, 1)
        body.addSpacing(6)
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

    def _refresh_target(self):
        """Keep the Send button naming the window it would really paste into."""
        name = ""
        if self.target_getter is not None:
            try:
                name = self.target_getter() or ""
            except Exception:
                name = ""
        if name == self._target_shown:
            return
        self._target_shown = name
        short = short_target(name)
        self.paste_btn.setText(f"Send to {short}" if short else "Send")
        # The full title is worth having somewhere, just not on the button.
        self.paste_btn.setToolTip(
            f"Paste into: {name}" if name
            else "Paste into whatever has focus when you send"
        )

    # --- translating ------------------------------------------------------

    def _on_typed(self):
        if not self.source.toPlainText().strip():
            self._debounce.stop()
            self.output.setPlainText("")
            self.status.setText("")
            self._set_ready(False)
            return
        self.status.setText("typing...")
        self._debounce.start()

    def _translate_now(self):
        text = self.source.toPlainText()
        if not text.strip():
            return
        self._pending += 1
        request = self._pending
        self.status.setText(
            "translating..." if self.translator.ready else "loading the translator..."
        )
        threading.Thread(
            target=self._work, args=(request, text), daemon=True
        ).start()

    def _work(self, request, text):
        try:
            english = self.translator.translate(text)
            error = ""
        except Exception as exc:
            english, error = "", str(exc)
        # Drop anything that finished after a newer request went out, or the
        # box would flicker back to an older translation.
        if request == self._pending:
            self.bridge.done.emit(english, error)

    def _show_result(self, english, error):
        if error:
            self.status.setText(error[:90])
            self._set_ready(False)
            return
        self._english = english
        self.output.setPlainText(english)
        where = self.translator.device or "?"
        self.status.setText(f"translated on {where}")
        self._set_ready(bool(english.strip()))

    def _set_ready(self, ready):
        self.copy_btn.setEnabled(ready)
        self.paste_btn.setEnabled(ready)

    # --- output -----------------------------------------------------------

    def _current_english(self):
        """What is in the box, not what came back: it is editable on purpose."""
        return self.output.toPlainText().strip()

    def _copy(self):
        import pyperclip

        text = self._current_english()
        if not text:
            return
        try:
            pyperclip.copy(text)
            self.status.setText("copied")
        except Exception as exc:
            self.status.setText(f"could not copy: {exc}")

    def _send(self):
        text = self._current_english()
        if not text or self.on_paste is None:
            return
        self.close()          # get out of the way before focus moves back
        self.on_paste(text)

    def keyPressEvent(self, event):
        # The shortcut the placeholder promises: translate whatever is there
        # right now and send it, without waiting out the debounce. Escape and
        # everything else is the base window's business.
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() & Qt.ControlModifier):
            if self._current_english():
                self._send()
            else:
                self._debounce.stop()
                self._translate_now()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        global _window
        _window = None
        self._debounce.stop()
        self._target_timer.stop()
        super().closeEvent(event)


def open_compose(translator, on_paste, target_getter=None):
    """Show the window, raising the one already open rather than a second."""
    global _window
    if _window is not None and _window.isVisible():
        _window.raise_()
        _window.activateWindow()
        return _window
    _window = Compose(translator, on_paste, target_getter)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
