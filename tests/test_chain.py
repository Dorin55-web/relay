"""Drive the chain window without a human: click its buttons, watch it run.

The queue's own rules are held down by test_autopilot. This is about the
window in front of them - that the list you build is the list that gets sent,
that editing a step edits the right one, and that a chain aimed at a window
Relay cannot read is refused with a reason rather than starting and stalling.

The shelf under it gets the same treatment. A chain that can be kept and taken
down again is only worth having if what comes back is what went in, in the
order it went in, and if nothing about keeping one can cost you the ones
already on the shelf.
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

import context  # noqa: E402,F401
STATE = context.isolate_state()

from PySide6.QtWidgets import QApplication

from relay import chains as chains_mod
from relay import prompts as prompts_mod

# isolate_state knows about prompts.json and the orb's saved position.
# chains.json is newer than it, and left alone this suite would keep its own
# test chains on the shelf of whoever ran it.
chains_mod.CHAINS_PATH = STATE / "chains.json"

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


class Target:
    """A window that behaves like a real one: picks the work up, then finishes.

    A fake that stays idle after being sent a prompt makes the queue wait out
    START_SECONDS looking for a start that never comes - which is correct of
    it, and useless as a stand-in for an application.
    """

    def __init__(self):
        self.state = agent.IDLE

    def read(self, _hwnd):
        return self.state

    def send(self, text, _hwnd):
        sent.append(text)
        self.state = agent.BUSY
        threading.Timer(0.15, lambda: setattr(self, "state", agent.IDLE)).start()
        return True


def stub(win, states=None):
    """Everything below the window: this suite is about the widgets."""
    win.pilot.read_state = (states or (lambda _h: agent.BUSY))
    win.pilot.is_window = lambda _h: True
    win.pilot.focus = lambda _h: True
    win.pilot.poll_seconds = 0.02
    win.pilot.countdown_seconds = 1
    win.pilot.countdown_tick = 0.02
    return win


def window(target=HWND, states=None):
    """A chain window pointed at a fake target, with a scripted state reader."""
    return stub(ChainWindow(prompts_mod.load, lambda: target, send), states)


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

print("\n--- closing it while a chain runs does not stop the chain ---")
# It used to. A three-step chain sent one step and stopped, because the window
# was sitting in front of the application being driven and got closed.
sent.clear()
target = Target()
# Through the real entry point this time, and actually on screen: the window
# has to be visible for hiding it to mean anything, and open_chain is what
# has to hand the running one back rather than building a second.
win = chain_mod.open_chain(prompts_mod.load, lambda: HWND, target.send)
stub(win, target.read)
app.processEvents()
check("on screen to begin with", win.isVisible())
for _ in range(2):
    win.library.setCurrentRow(0)
    win._add()
win.chain.setCurrentRow(0)
win.text.setPlainText("one")
win.chain.setCurrentRow(1)
win.text.setPlainText("two")
win._start_or_stop()
check("started with two steps", win.pilot.running)

win.close()
app.processEvents()
check("the window went away", not win.isVisible())
check("but the chain did not", win.pilot.running)
check("and it is still the same window", chain_mod.running_window() is win)

settle(win, 4.0)
check("both steps went out", sent == ["one", "two"], str(sent))
check("and it came back to report", win.isVisible())

print("\n--- closing it when nothing is running really closes ---")
win.close()
app.processEvents()
check("gone", not win.isVisible())
check("and forgotten", chain_mod.running_window() is None)

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

print("\n--- keeping a chain ---")
# Everything below here talks to dialogs. A modal runs its own event loop and
# never returns without a click, so a suite that raises one stops dead.
dialogs = context.silence_dialogs(chain_mod)
win = window()
check("the shelf starts empty", win.shelf.count() == 0, str(win.shelf.count()))
check("nothing to keep, so Keep is off", not win.save_btn.isEnabled())
check("nothing to load, so Load is off", not win.load_btn.isEnabled())

win.library.setCurrentRow(0)
win._add()
win.chain.setCurrentRow(0)
win.text.setPlainText("first step")
win.library.setCurrentRow(1)
win._add()
win.chain.setCurrentRow(1)
win.text.setPlainText("second step")
check("still off without a name", not win.save_btn.isEnabled())
check("and pressing it anyway refuses", win._save() is False)
check("saying what is missing rather than nothing",
      any("name" in title.lower() for _kind, title, _text in dialogs),
      str(dialogs))

win.name.setText("morning triage")
check("with a name it is offered", win.save_btn.isEnabled())
check("and it lands", win._save() is True)
check("the shelf shows it", win.shelf.count() == 1, str(win.shelf.count()))
check("by name and by length",
      "morning triage" in win.shelf.item(0).text()
      and "2 steps" in win.shelf.item(0).text(), win.shelf.item(0).text())
check("the file holds the steps in the order they were built",
      chains_mod.steps_for("morning triage") == ["first step", "second step"],
      str(chains_mod.steps_for("morning triage")))
check("and the name is on the module-level list",
      chains_mod.names() == ["morning triage"], str(chains_mod.names()))
win.close()


print("\n--- and taking it down again in a window that never saw it ---")
win = window()
check("the shelf survived the window", win.shelf.count() == 1,
      str(win.shelf.count()))
check("with a row already picked", win.shelf.currentRow() == 0)
check("so Load is offered", win.load_btn.isEnabled())
check("it loads", win._load() is True)
check("the steps came back in order",
      win.steps == ["first step", "second step"], str(win.steps))
check("the name came back with them",
      win.name.text() == "morning triage", win.name.text())
check("the list shows both", win.chain.count() == 2, str(win.chain.count()))
check("and it reads from the first step",
      win.chain.currentRow() == 0 and win.text.toPlainText() == "first step",
      f"row {win.chain.currentRow()}, {win.text.toPlainText()!r}")
check("start is offered", win.start_btn.isEnabled())


print("\n--- loading over a chain you are in the middle of asks first ---")
# The shelf answers a double-click, which is one slip away from ten minutes of
# filling in blanks.
refused = context.silence_dialogs(chain_mod, answer_yes=False)
win.steps = ["something I was in the middle of"]
win._show_steps()
check("it did not load", win._load() is False)
check("because it asked", any("Replace" in title for _k, title, _t in refused),
      str(refused))
check("and the chain is untouched",
      win.steps == ["something I was in the middle of"], str(win.steps))

dialogs = context.silence_dialogs(chain_mod)
check("saying yes loads it over the top", win._load() is True)
check("with the kept steps", win.steps == ["first step", "second step"],
      str(win.steps))
win.close()


print("\n--- a name that is already taken ---")
# Two chains under one name is a name that cannot be asked for - from the
# phone it would be a command with two answers - so saving over one replaces
# it, and replaces it where it stands rather than at the end of the shelf.
win = window()
win.library.setCurrentRow(0)
win._add()
win.chain.setCurrentRow(0)
win.text.setPlainText("only step")
win.name.setText("release checks")
check("a second chain is kept", win._save() is True)
check("the shelf holds both", win.shelf.count() == 2, str(win.shelf.count()))

win.text.setPlainText("only step, rewritten")
win.name.setText("Release Checks")      # the same chain to the person typing
dialogs = context.silence_dialogs(chain_mod)
check("saving over it lands", win._save() is True)
check("having asked first",
      any(kind == "question" for kind, _t, _x in dialogs), str(dialogs))
check("there is still only one of it", win.shelf.count() == 2,
      str(win.shelf.count()))
check("holding the new steps",
      chains_mod.steps_for("release checks") == ["only step, rewritten"],
      str(chains_mod.steps_for("release checks")))
check("and it did not travel to the end of the shelf",
      chains_mod.names() == ["morning triage", "Release Checks"],
      str(chains_mod.names()))

refused = context.silence_dialogs(chain_mod, answer_yes=False)
win.text.setPlainText("no, keep the old one")
check("saying no writes nothing", win._save() is False)
check("and the kept chain is the one that was there",
      chains_mod.steps_for("Release Checks") == ["only step, rewritten"],
      str(chains_mod.steps_for("Release Checks")))
win.close()


print("\n--- a chains.json that will not parse ---")
# The fallback is an empty shelf, and the danger is the save that comes after
# it: writing that empty list back would turn one stray comma into every chain
# gone.
good = chains_mod.CHAINS_PATH.read_text(encoding="utf-8")
broken = '{"chains": [{"name": "morning triage",]}'
chains_mod.CHAINS_PATH.write_text(broken, encoding="utf-8")
check("load falls back rather than raising", chains_mod.load() == [],
      str(chains_mod.load()))
check("and so does the name list", chains_mod.names() == [],
      str(chains_mod.names()))
check("and asking for one by name",
      chains_mod.steps_for("morning triage") is None)

dialogs = context.silence_dialogs(chain_mod)
win = window()
check("the window comes up with an empty shelf", win.shelf.count() == 0,
      str(win.shelf.count()))
win.library.setCurrentRow(0)
win._add()
win.name.setText("morning triage")
check("keeping is refused rather than writing over the file",
      win._save() is False)
check("and you are told why",
      any(kind == "critical" for kind, _t, _x in dialogs), str(dialogs))
check("the file is exactly as it was",
      chains_mod.CHAINS_PATH.read_text(encoding="utf-8") == broken)
win.close()

chains_mod.CHAINS_PATH.write_text(good, encoding="utf-8")
check("and with the file readable again, so is the shelf",
      chains_mod.names() == ["morning triage", "Release Checks"],
      str(chains_mod.names()))


print("\n--- the shelf on its own, without a window in front of it ---")
shelf_dir = Path(tempfile.mkdtemp(prefix="relay-chains-"))
path = shelf_dir / "chains.json"
check("an empty list is refused", chains_mod.save([], path) is False)
check("and nothing was written", not path.exists())
check("a chain with no steps is refused",
      chains_mod.save_chain("empty", [], path) is False)
check("a chain with no name is refused",
      chains_mod.save_chain("   ", ["a step"], path) is False)
check("one with both lands", chains_mod.save_chain("a", ["one"], path) is True)
check("with no temp file left behind",
      not any(p.suffix == ".tmp" for p in shelf_dir.iterdir()),
      str([p.name for p in shelf_dir.iterdir()]))
check("a save that cannot land fails cleanly",
      chains_mod.save_chain("b", ["two"], shelf_dir / "nowhere" / "chains.json")
      is False)
check("and the good file is untouched",
      chains_mod.steps_for("a", path) == ["one"],
      str(chains_mod.steps_for("a", path)))
# ensure_ascii=False, read back as utf-8: a chain named in Romanian that comes
# back as ș escapes is one you cannot find on the shelf.
chains_mod.save_chain("ședință de dimineață", ["un pas"], path)
check("a Romanian name comes back as one",
      chains_mod.names(path)[-1] == "ședință de dimineață",
      str(chains_mod.names(path)))
check("and is found without its capitals",
      chains_mod.steps_for("ȘEDINȚĂ DE DIMINEAȚĂ", path) == ["un pas"],
      str(chains_mod.steps_for("ȘEDINȚĂ DE DIMINEAȚĂ", path)))


print("\n--- and it fits without crowding what was already there ---")
# This window is small on purpose. A shelf that squeezed the two lists doing
# the building, or that showed one entry and a sliver of the next, would leave
# a worse window than one with no shelf in it at all.
win = window()
win.show()
app.processEvents()
row = win.shelf.sizeHintForRow(0)
check("the shelf shows three chains without scrolling",
      row > 0 and win.shelf.height() // row >= 3,
      f"{win.shelf.height()}px in rows of {row}px")
check("the templates list still shows the five it always did",
      win.library.height() // win.library.sizeHintForRow(0) >= 5,
      f"{win.library.height()}px in rows of {win.library.sizeHintForRow(0)}px")
for name, widget in [("name box", win.name), ("Keep button", win.save_btn),
                     ("Load button", win.load_btn)]:
    check(f"the {name} fits what it holds",
          widget.width() >= widget.sizeHint().width(),
          f"{widget.width()}px wide, needs {widget.sizeHint().width()}px")
check("nothing sits on the line that names the target",
      win.shelf.geometry().bottom() < win.target.geometry().top(),
      f"shelf ends at {win.shelf.geometry().bottom()}, "
      f"target starts at {win.target.geometry().top()}")
check("and the whole window still opens short enough for a laptop screen",
      win.height() <= 720, f"{win.height()}px tall")
win.close()


print("\n--- a kept chain sends the same steps in the same order ---")
# The whole point of the shelf: what comes back has to be what went in, all
# the way through the queue and out into the window being driven.
sent.clear()
win = window()
for row in range(3):
    win.library.setCurrentRow(row)
    win._add()
for row, text in enumerate(["one", "two", "three"]):
    win.chain.setCurrentRow(row)
    win.text.setPlainText(text)
win.name.setText("three in a row")
check("kept", win._save() is True)
win.close()

# A different window, the way it would be next week, and a fake target that
# picks the work up and finishes it rather than sitting idle.
target = Target()
win = stub(ChainWindow(prompts_mod.load, lambda: HWND, target.send),
           target.read)
check("the kept chain is on its shelf",
      any("three in a row" in win.shelf.item(i).text()
          for i in range(win.shelf.count())),
      str([win.shelf.item(i).text() for i in range(win.shelf.count())]))
win.shelf.setCurrentRow(win.shelf.count() - 1)
check("it loads", win._load() is True)
check("with all three steps", win.steps == ["one", "two", "three"],
      str(win.steps))
win._start_or_stop()
check("it started", win.pilot.running)
settle(win, 8.0)
check("and all three went out, in the order they were kept",
      sent == ["one", "two", "three"], str(sent))
win.close()


print("\n--- the keystroke route the hook uses ---")
# __main__ reaches the running window through this, and only through this.
check("no window, no module state", chain_mod.running_window() is None)

sys.exit(report.finish())
