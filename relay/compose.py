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

from .prompt_editor import (BG, LINE, MUTED, PANEL, RESIZE_MARGIN, TEXT,
                            TitleBar, _colorref)

# Long enough that a normal typing rhythm does not trigger a translation on
# every keystroke, short enough that pausing to think produces one.
DEBOUNCE_MS = 450

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


class Compose(QWidget):
    def __init__(self, translator, on_paste, target_name=None):
        super().__init__()
        self.translator = translator
        self.on_paste = on_paste
        self.target_name = target_name

        self._pending = 0            # newest request id, so stale ones are dropped
        self._english = ""

        self.setWindowTitle("Relay - Write")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("shell")
        self.setMouseTracking(True)
        self.setMinimumSize(620, 460)
        self.resize(820, 620)
        self._rounded = False

        self.bridge = _Bridge()
        self.bridge.done.connect(self._show_result)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._translate_now)

        self._build()
        self.setStyleSheet(STYLESHEET)
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
        self.paste_btn = QPushButton(
            f"Send to {self.target_name}" if self.target_name else "Send"
        )
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

    # --- frameless chrome -------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._rounded:
            self._rounded = True
            self._round_corners()

    def _round_corners(self):
        import ctypes
        import sys

        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi
            preference = ctypes.c_int(2)          # DWMWCP_ROUND
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference),
                                      ctypes.sizeof(preference))
            border = ctypes.c_uint(_colorref(LINE))
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border),
                                      ctypes.sizeof(border))
        except Exception as exc:
            print(f"[compose] could not round the window corners: {exc}")

    def _edges_at(self, pos):
        edges = Qt.Edges()
        if pos.x() <= RESIZE_MARGIN:
            edges |= Qt.LeftEdge
        elif pos.x() >= self.width() - RESIZE_MARGIN:
            edges |= Qt.RightEdge
        if pos.y() <= RESIZE_MARGIN:
            edges |= Qt.TopEdge
        elif pos.y() >= self.height() - RESIZE_MARGIN:
            edges |= Qt.BottomEdge
        return edges

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edges = self._edges_at(event.position())
            handle = self.windowHandle()
            if edges and handle is not None:
                handle.startSystemResize(edges)
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        # Ctrl+Enter is the whole point of the window: translate whatever is
        # there right now and send it, without waiting out the debounce.
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
        super().closeEvent(event)


def open_compose(translator, on_paste, target_name=None):
    """Show the window, raising the one already open rather than a second."""
    global _window
    if _window is not None and _window.isVisible():
        _window.raise_()
        _window.activateWindow()
        return _window
    _window = Compose(translator, on_paste, target_name)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
