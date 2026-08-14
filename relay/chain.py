"""Build a chain of prompts and watch it run.

Two lists: the templates you already have on the left, the chain you are
building on the right. A step can be edited after it is added, and that is not
a convenience - the templates are skeletons with `<angle brackets>` in them,
and a queue that sends them unfilled sends nonsense. The box underneath is
where the chain actually gets written.

While it runs the window stays on top. It is the only place the countdown is
visible and the only place with a Stop button, and the queue puts the window it
is driving in front every time it sends - so a chain window that could be
buried would be one you had to go looking for at the moment you wanted to stop
it.

Created from the orb's menu, so already on the GUI thread. The queue itself
polls on a thread of its own; progress comes back through a signal, because
touching widgets from that thread is how you get a crash that only happens on
someone else's machine.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from . import agent
from .autopilot import (Autopilot, COUNTING, DONE, HOLDING, SENDING, STARTING,
                        STOPPED)
from .look_picker import BG, LINE, MUTED, PANEL, STYLESHEET, TEXT
from .target import window_title
from .window import EDGE, FramelessWindow, TitleBar

_window = None

# Only a window title and a process name, so this is cheap enough to do while
# you are typing in the box above it.
TARGET_REFRESH_MS = 1000

PHRASES = {
    HOLDING: "waiting for it to finish",
    SENDING: "sending",
    STARTING: "sent - waiting for it to start",
    DONE: "chain finished",
    STOPPED: "stopped",
}


class ChainWindow(FramelessWindow):
    border_colour = EDGE

    # The queue runs on its own thread. Qt marshals a signal onto the thread
    # that owns the receiver, which is the only reason it is safe for that
    # thread to say anything at all.
    progress = Signal(str, int, int, object)

    def __init__(self, prompts_getter, target_getter, send):
        super().__init__("Relay - Chain")
        self.prompts_getter = prompts_getter
        self.target_getter = target_getter
        self.steps = []
        self.editing = -1

        self.pilot = Autopilot(
            send=send,
            on_progress=lambda phase, i, total, left: self.progress.emit(
                phase, i, total, left),
        )
        self.progress.connect(self._show_progress)

        self._build()
        self.setStyleSheet(STYLESHEET + EXTRA)
        self._fill_library()
        self._show_steps()
        self._show_target()

        # The target is whichever window you last clicked into, and you will
        # click into it after opening this - so a line read once at startup
        # would spend the whole time you were building the chain naming the
        # wrong application, or none.
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._show_target)
        self._watch.start(TARGET_REFRESH_MS)

    # --- layout -----------------------------------------------------------

    def _build(self):
        self.library = QListWidget()
        self.library.itemDoubleClicked.connect(lambda _item: self._add())
        self.library.setSelectionMode(QAbstractItemView.SingleSelection)

        self.chain = QListWidget()
        self.chain.currentRowChanged.connect(self._select_step)

        add = self._button("Add  →", self._add)
        self.up_btn = self._step_button("↑", "Move up", lambda: self._move(-1))
        self.down_btn = self._step_button("↓", "Move down", lambda: self._move(1))
        self.del_btn = self._step_button("✕", "Remove", self._remove)

        order = QVBoxLayout()
        order.setSpacing(6)
        order.addStretch(1)
        order.addWidget(self.up_btn)
        order.addWidget(self.down_btn)
        order.addWidget(self.del_btn)
        order.addStretch(1)

        lists = QHBoxLayout()
        lists.setSpacing(10)
        lists.addLayout(self._column("TEMPLATES", self.library, add), 1)
        lists.addLayout(order)
        lists.addLayout(self._column("THE CHAIN", self.chain), 1)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "Add a template, then fill in its <blanks> here.")
        self.text.setFixedHeight(96)
        self.text.textChanged.connect(self._text_changed)

        self.target = QLabel()
        self.target.setObjectName("hint")

        self.status = QLabel("")
        self.status.setObjectName("status")

        self.start_btn = QPushButton("Start")
        self.start_btn.setStyleSheet(
            f"QPushButton {{ background: {TEXT}; color: {BG}; border: none;"
            f" border-radius: 6px; padding: 7px 18px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: #ffffff; }}"
        )
        self.start_btn.clicked.connect(self._start_or_stop)
        close = self._button("Close", self.close)

        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(close)
        footer.addWidget(self.start_btn)

        body = QVBoxLayout()
        body.setContentsMargins(18, 6, 18, 18)
        body.setSpacing(12)
        body.addLayout(lists, 1)
        body.addWidget(self._caption("THIS STEP"))
        body.addWidget(self.text)
        body.addWidget(self.target)
        body.addLayout(footer)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(self.windowTitle(), self.showMinimized,
                                 self.close))
        outer.addLayout(body, 1)
        self.resize(720, 560)

    def _column(self, caption, widget, *extra):
        column = QVBoxLayout()
        column.setSpacing(6)
        column.addWidget(self._caption(caption))
        column.addWidget(widget, 1)
        for item in extra:
            column.addWidget(item)
        return column

    def _caption(self, text):
        label = QLabel(text)
        label.setObjectName("field")
        return label

    def _button(self, text, slot):
        button = QPushButton(text)
        button.clicked.connect(slot)
        return button

    def _step_button(self, glyph, tip, slot):
        """One of the three square buttons between the lists.

        Their own object name, because the ordinary button padding in the
        sheet is 16 pixels a side. Against a fixed 34-pixel width that leaves
        two pixels for the arrow, and the button comes up blank.
        """
        button = QPushButton(glyph)
        button.setObjectName("step")
        button.setToolTip(tip)
        button.setFixedSize(34, 30)
        button.clicked.connect(slot)
        return button

    # --- the chain --------------------------------------------------------

    def _fill_library(self):
        try:
            self.prompts = list(self.prompts_getter() or [])
        except Exception as exc:
            print(f"[chain] could not read the prompts: {exc}")
            self.prompts = []
        self.library.clear()
        for i, prompt in enumerate(self.prompts, start=1):
            self.library.addItem(QListWidgetItem(f"{i}.  {prompt['label']}"))
        if self.prompts:
            self.library.setCurrentRow(0)

    def _add(self):
        row = self.library.currentRow()
        if not (0 <= row < len(self.prompts)):
            return
        self.steps.append(self.prompts[row]["text"])
        self._show_steps()
        self.chain.setCurrentRow(len(self.steps) - 1)
        self.text.setFocus()

    def _remove(self):
        row = self.chain.currentRow()
        if 0 <= row < len(self.steps):
            self.steps.pop(row)
            self._show_steps()
            self.chain.setCurrentRow(min(row, len(self.steps) - 1))

    def _move(self, delta):
        row = self.chain.currentRow()
        target = row + delta
        if 0 <= row < len(self.steps) and 0 <= target < len(self.steps):
            self.steps[row], self.steps[target] = self.steps[target], self.steps[row]
            self._show_steps()
            self.chain.setCurrentRow(target)

    def _show_steps(self):
        row = self.chain.currentRow()
        self.chain.blockSignals(True)
        self.chain.clear()
        for i, step in enumerate(self.steps, start=1):
            # One line, because the list is about order, not content - the box
            # below is where a step is read.
            first = " ".join(step.split())
            self.chain.addItem(QListWidgetItem(f"{i}.  {first[:60]}"))
        self.chain.blockSignals(False)
        if self.steps:
            self.chain.setCurrentRow(min(max(row, 0), len(self.steps) - 1))
        else:
            self._select_step(-1)
        self._show_buttons()

    def _select_step(self, row):
        self.editing = row
        self.text.blockSignals(True)
        self.text.setPlainText(self.steps[row] if 0 <= row < len(self.steps) else "")
        self.text.blockSignals(False)
        self._show_buttons()

    def _text_changed(self):
        if 0 <= self.editing < len(self.steps):
            self.steps[self.editing] = self.text.toPlainText()
            item = self.chain.item(self.editing)
            if item is not None:
                first = " ".join(self.steps[self.editing].split())
                item.setText(f"{self.editing + 1}.  {first[:60]}")

    def _show_buttons(self):
        row = self.chain.currentRow()
        running = self.pilot.running
        self.up_btn.setEnabled(not running and row > 0)
        self.down_btn.setEnabled(not running and 0 <= row < len(self.steps) - 1)
        self.del_btn.setEnabled(not running and row >= 0)
        self.text.setReadOnly(running)
        self.start_btn.setText("Stop" if running else "Start")
        self.start_btn.setEnabled(running or bool(self.steps))

    # --- the target -------------------------------------------------------

    def _target(self):
        try:
            return self.target_getter()
        except Exception:
            return None

    def _show_target(self):
        """Say what will be driven, and whether it is even recognised.

        A chain aimed at a window with no profile can never advance, and the
        only thing worse than refusing to start is refusing without saying so.
        """
        hwnd = self._target()
        if not hwnd:
            self.target.setText("Nowhere to send: click into the window you "
                                "want to drive, then come back.")
            return
        title = window_title(hwnd)
        profile = agent.profile_for(hwnd)
        if profile is None:
            self.target.setText(f"Into {title!r} - which Relay does not know "
                                f"how to read. Add it to profiles.json first.")
        else:
            self.target.setText(f"Into {title!r}, read as {profile['name']}.")

    # --- running ----------------------------------------------------------

    def _start_or_stop(self):
        if self.pilot.running:
            self.pilot.stop("you stopped it")
            return
        self._show_target()
        hwnd = self._target()
        if not self.pilot.start(list(self.steps), hwnd):
            self.status.setText("could not start - see the line above")
            return
        # On top for as long as it runs. The queue brings the window it is
        # driving to the front on every send, so without this the countdown
        # and the Stop button end up behind the thing being driven.
        self._stay_on_top(True)
        self._show_buttons()

    def _stay_on_top(self, on):
        was = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if was:
            # Changing a window flag re-creates the native window, which hides
            # it. Nothing else puts it back.
            self.show()

    def _show_progress(self, phase, index, total, seconds_left):
        step = f"step {index + 1} of {total}"
        if phase == COUNTING:
            self.status.setText(f"{step}: sending in {seconds_left}s - "
                                f"press any key to stop")
        elif phase in (DONE, STOPPED):
            self.status.setText(PHRASES[phase] + (
                f" - {self.pilot.reason}" if self.pilot.reason else ""))
            self._stay_on_top(False)
            self._show_buttons()
            if not self.isVisible():
                # Closed to get it out of the way while the chain ran. Now
                # there is something to say, so it comes back - without
                # raising or activating, because the chain has just been
                # typing into another window and you are probably reading it.
                self.show()
        else:
            self.status.setText(f"{step}: {PHRASES.get(phase, phase)}")

    def user_typed(self):
        self.pilot.user_typed()

    # --- lifecycle --------------------------------------------------------

    def closeEvent(self, event):
        """Closing gets the window out of the way. It does not stop the chain.

        It used to. The reasoning was that this window holds the only Stop
        button, so letting it go would leave a queue running with no way to
        reach it - but that was wrong twice over. A keystroke stops a chain
        from anywhere, so it was never unreachable; and the reason to close
        this window mid-chain is that it is in front of the thing you are
        watching, which is not a reason to abandon the chain. Measured in the
        log: a three-step chain sent one step and stopped, because the window
        was in the way and got closed.

        So while a chain runs, closing hides. The window comes back by itself
        when the chain ends, and the menu opens this one rather than a second.
        """
        global _window
        if self.pilot.running:
            self.hide()
            event.ignore()
            return
        self._watch.stop()
        self.pilot.stop("the window was closed")
        _window = None
        super().closeEvent(event)


EXTRA = f"""
QListWidget {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 4px;
}}
QListWidget::item {{ padding: 5px 6px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {LINE}; color: {TEXT}; }}
QPlainTextEdit {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 8px;
    color: {TEXT};
}}
QPushButton#step {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 0;
    font-size: 15px;
    color: {TEXT};
}}
QPushButton#step:hover {{ background: {LINE}; }}
QPushButton#step:disabled {{ color: {LINE}; }}
QLabel#status {{ color: {MUTED}; font-size: 12px; }}
"""


def open_chain(prompts_getter, target_getter, send):
    """Show the chain window, or bring back the one that already exists.

    Not `isVisible()`. A window hidden because a chain is running is still the
    window that chain belongs to, and building a second one would leave the
    first sending prompts with nothing on screen attached to it.
    """
    global _window
    if _window is not None:
        _window.show()
        _window.raise_()
        _window.activateWindow()
        return _window
    _window = ChainWindow(prompts_getter, target_getter, send)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def running_window():
    """The open chain window, if there is one. For the keyboard listener."""
    return _window
