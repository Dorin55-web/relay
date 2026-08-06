"""What one mouse move over the window costs.

Mouse tracking is on, so every pixel of movement is an event on the thread that
draws. This sends a realistic sweep and counts the work each event does, plus
how long the whole flood takes - which is how long the cursor has to catch up.
"""
import sys
from pathlib import Path
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context  # noqa: E402,F401

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from relay.compose import Compose  # noqa: E402
from relay.translator import TextTranslator  # noqa: E402

counts = {"childAt": 0, "setCursor": 0, "unsetCursor": 0}

tr = TextTranslator()
win = Compose(tr, on_paste=lambda t: None, target_getter=lambda: "Notepad")

real_childAt = win.childAt
real_set = win.setCursor
real_unset = win.unsetCursor


def counting_childAt(*a, **k):
    counts["childAt"] += 1
    return real_childAt(*a, **k)


def counting_set(*a, **k):
    counts["setCursor"] += 1
    return real_set(*a, **k)


def counting_unset(*a, **k):
    counts["unsetCursor"] += 1
    return real_unset(*a, **k)


win.childAt = counting_childAt
win.setCursor = counting_set
win.unsetCursor = counting_unset

win.show()
app.processEvents()

# A sweep across the window the way a hand moves: a few hundred pixels, one
# event per pixel, which is what a mouse actually reports.
w, h = win.width(), win.height()
path = [(x, h // 2) for x in range(10, w - 10, 1)]
path += [(w // 2, y) for y in range(10, h - 10, 1)]

t0 = time.perf_counter()
for x, y in path:
    event = QMouseEvent(
        QEvent.MouseMove, QPointF(x, y), QPointF(x, y),
        Qt.NoButton, Qt.NoButton, Qt.NoModifier,
    )
    win.mouseMoveEvent(event)
elapsed = (time.perf_counter() - t0) * 1000

print()
print(f"  mouse events sent      {len(path)}")
print(f"  time on the GUI thread {elapsed:.1f} ms  "
      f"({elapsed / len(path):.3f} ms each)")
print()
print(f"  childAt calls          {counts['childAt']}")
print(f"  setCursor calls        {counts['setCursor']}")
print(f"  unsetCursor calls      {counts['unsetCursor']}")
print()

churn = counts["setCursor"] + counts["unsetCursor"]
print(f"  cursor changed         {churn} times for {len(path)} moves")
if churn > len(path) * 0.5:
    print("  >> the cursor is being reset on nearly every event, whether or")
    print("     not the shape changed - that is the flood")
else:
    print("  >> only on transitions, which is the handful there should be")

# Cheap is no good if it stopped working. Walk out through the right edge and
# back, and the cursor has to change exactly twice.
print("\n  crossing an edge and coming back:")
counts["setCursor"] = counts["unsetCursor"] = 0
crossing = [(x, h // 2) for x in range(w - 30, w - 1)]
crossing += [(x, h // 2) for x in range(w - 1, w - 30, -1)]
for x, y in crossing:
    event = QMouseEvent(
        QEvent.MouseMove, QPointF(x, y), QPointF(x, y),
        Qt.NoButton, Qt.NoButton, Qt.NoModifier,
    )
    win.mouseMoveEvent(event)

print(f"    events                 {len(crossing)}")
print(f"    setCursor              {counts['setCursor']}")
print(f"    unsetCursor            {counts['unsetCursor']}")
ok = counts["setCursor"] == 1 and counts["unsetCursor"] == 1
print(f"    >> {'once in, once out - correct' if ok else 'WRONG: expected 1 and 1'}")

win.close()
sys.exit(0 if ok else 1)
