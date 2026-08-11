"""Drive the chain window without a human: click its buttons, watch it run.

The queue's own rules are held down by test_autopilot. This is about the
window in front of them - that the list you build is the list that gets sent,
that editing a step edits the right one, and that a chain aimed at a window
Relay cannot read is refused with a reason rather than starting and stalling.
"""
import sys
import time

import context  # noqa: E402,F401
context.isolate_state()

from PySide6.QtWidgets import QApplication

from relay import prompts as prompts_mod

prompts_mod.ensure_file()
app = QApplication.instance() or QApplication(sys.argv)

from relay import agent                       # noqa: E402
import relay.chain as chain_mod               # noqa: E402
from relay.chain import ChainWindow           # noqa: E402

report = context.Report()
check = report.check

HWND = 4242
sent = []


def send(text, _hwnd):
    sent.append(text)
    return True


def window(target=HWND, states=None):
    """A chain window pointed at a fake target, with a scripted state reader."""
    win = ChainWindow(prompts_mod.load, lambda: target, send)
    # Everything below the window is stubbed: this suite is about the widgets.
    win.pilot.read_state = (states or (lambda _h: agent.BUSY))
    win.pilot.is_window = lambda _h: True
    win.pilot.focus = lambda _h: True
    win.pilot.poll_seconds = 0.02
    win.pilot.countdown_seconds = 1
    win.pilot.countdown_tick = 0.02
    return win


def settle(win, seconds=1.5):
    """Let the queue's thread run while Qt keeps delivering its signals."""
    deadline = time.monotonic() + seconds
    while win.pilot.running and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


agent.profile_for = lambda hwnd, profiles=None: {"name": "fake"} if hwnd else None

print("\n--- building a chain ---")
win = window()
check("library filled from prompts.json", win.library.count() == 10)
check("chain starts empty", win.steps == [])
check("start is refused with nothing in it", not win.start_btn.isEnabled())

win.library.setCurrentRow(0)
win._add()
win.library.setCurrentRow(3)
win._add()
check("two steps", len(win.steps) == 2)
check("in the order added",
      win.steps == [prompts_mod.load()[0]["text"], prompts_mod.load()[3]["text"]])
check("start is offered now", win.start_btn.isEnabled())
check("the list shows both", win.chain.count() == 2)

print("\n--- filling in the blanks ---")
# The templates arrive with <angle brackets> in them. Sending one unfilled is
# the most likely way for a chain to be useless, so the box has to write back
# to the step that is selected and no other.
untouched = win.steps[1]
check("the template arrived with its blanks", "<problem>" in win.steps[0])
win.chain.setCurrentRow(0)
win.text.setPlainText("Find the cause of the freeze.")
check("edit reached the model", win.steps[0] == "Find the cause of the freeze.")
check("the other step is untouched", win.steps[1] == untouched)
check("the list caption followed", "Find the cause" in win.chain.item(0).text())

win.chain.setCurrentRow(1)
check("selecting shows that step's text", win.text.toPlainText() == win.steps[1])

print("\n--- the small buttons can show what they say ---")
# They came up blank once. A fixed 34-pixel width against the sheet's 16
# pixels of padding a side left two pixels for the arrow, and a button with
# no glyph in it looks like a button that does nothing.
for name, button in [("up", win.up_btn), ("down", win.down_btn),
                     ("remove", win.del_btn)]:
    wanted = button.sizeHint().width()
    check(f"the {name} button fits its glyph",
          button.minimumWidth() >= wanted,
          f"{button.minimumWidth()}px wide, needs {wanted}px")

print("\n--- reordering and removing ---")
first, second = win.steps
win.chain.setCurrentRow(0)
win._move(1)
check("move down swaps", win.steps == [second, first])
check("selection follows", win.chain.currentRow() == 1)
win._move(-1)
check("move up puts it back", win.steps == [first, second])

win.chain.setCurrentRow(0)
check("up is disabled at the top", not win.up_btn.isEnabled())
win.chain.setCurrentRow(1)
check("down is disabled at the bottom", not win.down_btn.isEnabled())

win._remove()
check("remove takes one out", win.steps == [first])
win._remove()
check("and the last one too", win.steps == [])
check("nothing left to start", not win.start_btn.isEnabled())
win.close()

print("\n--- it sends what is in the list ---")
sent.clear()
states = iter([agent.IDLE] * 4 + [agent.BUSY] * 2 + [agent.IDLE] * 40)
win = window(states=lambda _h: next(states, agent.IDLE))
win.library.setCurrentRow(0)
win._add()
win.chain.setCurrentRow(0)
win.text.setPlainText("the only step")
win._start_or_stop()
check("it started", win.pilot.running)
settle(win, 3.0)
check("sent exactly what was in the box", sent == ["the only step"], str(sent))

print("\n--- stop puts the window back ---")
# The window goes on top while a chain runs, because it holds the only Stop
# button. It must not stay there afterwards.
check("not left on top", not bool(win.windowFlags() & 0x00040000))
check("the buttons came back", win.start_btn.text() == "Start")
check("the step is editable again", not win.text.isReadOnly())
win.close()

print("\n--- a target Relay cannot read ---")
sent.clear()
was = agent.profile_for
agent.profile_for = lambda hwnd, profiles=None: None
win = window()
win.library.setCurrentRow(0)
win._add()
check("says so before you press anything",
      "does not know" in win.target.text(), win.target.text())
win._start_or_stop()
check("refused to start", not win.pilot.running)
check("and sent nothing", sent == [], str(sent))
check("with a reason on screen", "could not start" in win.status.text(),
      win.status.text())
agent.profile_for = was
win.close()

print("\n--- nowhere to send ---")
win = window(target=None)
check("says there is no target", "Nowhere to send" in win.target.text(),
      win.target.text())
win.close()

print("\n--- the keystroke route the hook uses ---")
# __main__ reaches the running window through this, and only through this.
check("no window, no module state", chain_mod.running_window() is None)

sys.exit(report.finish())
