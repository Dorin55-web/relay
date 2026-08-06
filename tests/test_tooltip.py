"""The prompt behind a label appears only when you stop on it.

Qt shows action tooltips on a short fuse, and once one has been shown the next
comes up almost at once - so running an eye down ten templates trailed a
paragraph of prompt text after the pointer the whole way down. The orb counts
the rest itself now.

This drives the real signals a menu emits rather than a helper, so it would
catch the wiring being disconnected as well as the timing being wrong.
"""
import sys

import context  # noqa: E402,F401
context.isolate_state()

from PySide6.QtCore import QElapsedTimer, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QToolTip

app = QApplication.instance() or QApplication(sys.argv)

from relay.overlay import (TOOLTIP_REST_MS, Orb,  # noqa: E402
                           _has_more_to_say)

fails = []


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not condition:
        fails.append(name)


def spin(ms):
    """Run the event loop for real, so single-shot timers actually fire."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


PROMPTS = [
    {"section": "Testing", "label": "Find the bug",
     "text": "Read the failing test and explain what the code actually does."},
    {"section": "Testing", "label": "Write a test",
     "text": "Add a regression test that fails without the fix."},
    {"section": "Writing", "label": "Tighten this",
     "text": "Cut every sentence that is not carrying its weight."},
]

orb = Orb(on_toggle=lambda: None, on_quit=lambda: None, tooltip="F9",
          prompts_getter=lambda: PROMPTS, on_prompt=lambda text: None,
          on_edit_prompts=lambda: None, on_pick_look=lambda: None)
orb.move(-3000, -3000)
orb._build_menu("F9", PROMPTS)
items = [a for a in orb.menu.actions() if _has_more_to_say(a)]

print("\n--- the wiring ---")
check("Qt's own action tooltips are off",
      not orb.menu.toolTipsVisible())
check("the templates still carry their full text",
      all(any(p["text"] == a.toolTip() for a in items) for p in PROMPTS))
# QAction.toolTip() falls back to the label, so every action claims one -
# without a check for that, holding still on "Dictate (F9)" was rewarded with
# a box reading "Dictate (F9)".
plain = [a.text() for a in orb.menu.actions()
         if a.text() and not _has_more_to_say(a)]
check("items with nothing more to say are left alone",
      any("Dictate" in t for t in plain)
      and any(p["section"] == t for t in plain for p in PROMPTS),
      ", ".join(plain))
check("and the rest is long enough to be deliberate",
      TOOLTIP_REST_MS >= 3000, f"{TOOLTIP_REST_MS} ms")

print("\n--- passing over shows nothing ---")
orb.menu.hovered.emit(items[0])
spin(400)
check("nothing after four hundred milliseconds", not QToolTip.isVisible())

# Down the list the way an eye goes, a few hundred milliseconds each. The old
# behaviour showed a tooltip on the first and then one per item after it.
for action in items[:3]:
    orb.menu.hovered.emit(action)
    spin(300)
check("nothing after running down three of them", not QToolTip.isVisible())
check("and the timer is still counting, not stopped",
      orb._tip_timer.isActive())

print("\n--- moving restarts the count ---")
orb.menu.hovered.emit(items[0])
spin(TOOLTIP_REST_MS - 600)
orb.menu.hovered.emit(items[1])
spin(700)
check("a move just before it would have fired shows nothing",
      not QToolTip.isVisible())
check("and it is the item moved to that is now pending",
      orb._tip_action is items[1])

print("\n--- stopping on one shows it ---")
orb.menu.hovered.emit(items[1])
clock = QElapsedTimer()
clock.start()
shown = False
while clock.elapsed() < TOOLTIP_REST_MS + 1200:
    spin(50)
    if QToolTip.isVisible():
        shown = True
        break
waited = clock.elapsed()
check("it appears once you have stopped", shown, f"after {waited} ms")
if shown:
    check("not before the rest is up", waited >= TOOLTIP_REST_MS - 150,
          f"{waited} ms of {TOOLTIP_REST_MS}")
    check("and it is that item's prompt",
          QToolTip.text() == items[1].toolTip(), QToolTip.text()[:40])

print("\n--- and it goes when the menu does ---")
orb.menu.aboutToHide.emit()
# Qt fades a tooltip out over about a third of a second however it is asked
# to hide, so this is the fade rather than a delay of our own.
spin(600)
check("the text is gone", not QToolTip.isVisible())
check("and nothing is left pending",
      orb._tip_action is None and not orb._tip_timer.isActive())

print("\n--- rebuilding the menu does not leave one behind ---")
orb.menu.hovered.emit(items[0])
orb._build_menu("F9", PROMPTS)
check("the pending item was dropped with the actions it belonged to",
      orb._tip_action is None and not orb._tip_timer.isActive())

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
