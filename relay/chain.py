"""Build a chain of prompts and watch it run.

Two lists: the templates you already have on the left, the chain you are
building on the right. A step can be edited after it is added, and that is not
a convenience - the templates are skeletons with `<angle brackets>` in them,
and a queue that sends them unfilled sends nonsense. The box underneath is
where the chain actually gets written.

Underneath is the shelf: name a chain, keep it, and take it down again next
week. What is worth keeping is not the templates - those are already a click
away - but the blanks you filled in and the order you settled on, which is the
part that took the thinking. `chains.py` holds them.

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
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from . import agent
from . import chains as chains_mod
from .autopilot import (Autopilot, COUNTING, DONE, HOLDING, SENDING,
                        STARTING, STOPPED, WAITING)
from .look_picker import BG, LINE, MUTED, PANEL, STYLESHEET, TEXT
from .target import window_title
from .window import EDGE, FramelessWindow, TitleBar

_window = None

# Only a window title and a process name, so this is cheap enough to do while
# you are typing in the box above it.
TARGET_REFRESH_MS = 1000

# Three rows of the shelf, and three is the number this was argued down to. It
# is something you take one off, not something you read down, and the two lists
# above it are where this window's height belongs - a fourth list at full size
# would take the space from the ones doing the work. Three rows only fit in
# this many pixels because the shelf's own rows are tighter than theirs: at the
# 5px of padding those carry, a row is 42px and this band showed one entry and
# a sliver of the next, which reads as a list that is broken rather than short.
SHELF_HEIGHT = 118

PHRASES = {
    HOLDING: "waiting for it to finish",
    WAITING: "it has stopped to ask you something",
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
        self.kept = []          # what is on the shelf, in the order it shows

        self.pilot = Autopilot(
            send=send,
            on_progress=lambda phase, i, total, left: self.progress.emit(
                phase, i, total, left),
        )
        self.progress.connect(self._show_progress)

        self._build()
        self.setStyleSheet(STYLESHEET + EXTRA)
        self._fill_library()
        self._fill_shelf()
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

        self.shelf = QListWidget()
        self.shelf.setObjectName("shelf")
        # The same gesture the templates list uses, so the shelf reads as the
        # other place a step comes from rather than as a new kind of thing.
        self.shelf.itemDoubleClicked.connect(lambda _item: self._load())
        self.shelf.setSelectionMode(QAbstractItemView.SingleSelection)
        self.shelf.setFixedHeight(SHELF_HEIGHT)
        self.shelf.currentRowChanged.connect(lambda _row: self._show_buttons())

        self.name = QLineEdit()
        self.name.setPlaceholderText("Name this chain")
        self.name.setFixedWidth(200)
        self.name.returnPressed.connect(self._save)
        self.name.textChanged.connect(lambda _text: self._show_buttons())

        self.save_btn = self._button("Keep", self._save)
        self.load_btn = self._button("Load", self._load)

        keep_buttons = QHBoxLayout()
        keep_buttons.setSpacing(6)
        keep_buttons.addWidget(self.save_btn)
        keep_buttons.addWidget(self.load_btn)

        keep = QVBoxLayout()
        keep.setSpacing(6)
        keep.addWidget(self.name)
        keep.addLayout(keep_buttons)
        keep.addStretch(1)

        shelf_row = QHBoxLayout()
        shelf_row.setSpacing(10)
        shelf_row.addWidget(self.shelf, 1)
        shelf_row.addLayout(keep)

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
        body.addWidget(self._caption("KEPT CHAINS"))
        body.addLayout(shelf_row)
        body.addWidget(self.target)
        body.addLayout(footer)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(self.windowTitle(), self.showMinimized,
                                 self.close))
        outer.addLayout(body, 1)
        # Taller by roughly what the shelf costs, so the two lists that do the
        # building keep about the height they had. Not the whole of it: this
        # is already the tallest window Relay has, and the last twenty pixels
        # are cheaper taken off a templates list showing four and a half of
        # ten than added to a window that has to fit on a laptop.
        self.resize(720, 700)

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
        # Keeping stays offered while a chain runs. The steps are fixed by
        # then, so writing them out takes nothing away - and "I should have
        # kept that one" is a thought people have while watching it work.
        # Loading does not: it would rewrite the list under the running queue.
        self.save_btn.setEnabled(bool(self.steps)
                                 and bool(self.name.text().strip()))
        self.load_btn.setEnabled(not running and self.shelf.currentRow() >= 0)

    # --- the shelf --------------------------------------------------------

    def _fill_shelf(self, select=None):
        """Redraw the shelf from the file, so it says what is actually kept.

        `select` is the name to leave selected - the one just saved, which is
        the one you are about to look at to check it landed.
        """
        self.kept = chains_mod.load()
        self.shelf.blockSignals(True)
        self.shelf.clear()
        for chain in self.kept:
            count = len(chain["steps"])
            self.shelf.addItem(QListWidgetItem(
                f"{chain['name']}   ({count} step{'' if count == 1 else 's'})"))
        self.shelf.blockSignals(False)

        wanted = (select or "").strip().casefold()
        for i, chain in enumerate(self.kept):
            if chain["name"].casefold() == wanted:
                self.shelf.setCurrentRow(i)
                break
        else:
            if self.kept:
                self.shelf.setCurrentRow(0)
        # Not left to the selection signal: an empty shelf changes no row, and
        # Load would keep whatever state it had from the shelf before.
        self._show_buttons()

    def _save(self):
        """Keep this chain under the name in the box. True when it was written.

        Refusing is better than saving something that cannot come back: a
        chain with no name cannot be asked for again, and one with no steps is
        not a chain.
        """
        name = self.name.text().strip()
        if not self.steps:
            QMessageBox.warning(self, "Nothing to keep",
                                "Add a step before keeping the chain.")
            return False
        if not name:
            QMessageBox.warning(
                self, "It needs a name",
                "Type a name for this chain, so you can ask for it again.")
            self.name.setFocus()
            return False

        if chains_mod.steps_for(name) is not None:
            # Replacing is almost always what re-saving means, and it is also
            # the one that loses work - so it is the one that asks.
            answer = QMessageBox.question(
                self, "Already kept",
                f"There is already a chain called {name!r}.\n"
                "Replace it with this one?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return False

        if not chains_mod.save_chain(name, self.steps):
            QMessageBox.critical(
                self, "Could not keep it",
                f"{chains_mod.CHAINS_PATH.name} could not be written.\n"
                "Check the file is not open elsewhere, and that it still "
                "reads as JSON.",
            )
            return False

        self._fill_shelf(select=name)
        self.status.setText(f"kept as {name!r}")
        return True

    def _load(self):
        """Put a kept chain into the list, ready to start. True when one came."""
        row = self.shelf.currentRow()
        if self.pilot.running or not (0 <= row < len(self.kept)):
            return False
        chain = self.kept[row]

        if self.steps:
            # The shelf is reached with a double-click, which is one slip away
            # from a chain you have spent ten minutes filling in.
            answer = QMessageBox.question(
                self, "Replace the chain?",
                f"Load {chain['name']!r} over the {len(self.steps)} step(s) "
                "already here?\nThey are gone unless you have kept them.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return False

        self.steps = list(chain["steps"])
        self.name.setText(chain["name"])
        self._show_steps()
        # At the top, not wherever the last selection happened to be: a chain
        # you have just loaded is one you read from its first step.
        self.chain.setCurrentRow(0)
        self.status.setText(f"loaded {chain['name']!r}")
        return True

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
/* A kept chain is a name and a count, on one short line - it does not need
   the breathing room a prompt does, and three rows of it only fit in the
   shelf's band at this padding. */
QListWidget#shelf::item {{ padding: 2px 6px; }}
QPlainTextEdit {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 8px;
    color: {TEXT};
}}
QLineEdit {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 7px 9px;
    color: {TEXT};
    selection-background-color: {LINE};
}}
QLineEdit:focus {{ border: 1px solid #3d4451; }}
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
