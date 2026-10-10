"""Where does a click actually start a resize, and where must it not?

The complaint was the window seizing the mouse. A resize handed to the
compositor holds the pointer until the drag ends, so every place one can start
by accident is a place the cursor appears stuck. This walks the whole border
and reports which pixels are live.
"""
import sys

import context  # noqa: E402,F401
context.isolate_state()

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from relay.prompt_editor import PromptEditor  # noqa: E402
from relay.window import RESIZE_MARGIN  # noqa: E402

fails = []


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not condition:
        fails.append(name)


def survey(win, label):
    """Every point on the border strip: live, or blocked by a control."""
    win.show()
    app.processEvents()
    w, h = win.width(), win.height()
    live, blocked = [], []
    step = 4
    points = []
    for x in range(0, w, step):
        points += [(x, 2), (x, h - 3)]
    for y in range(0, h, step):
        points += [(2, y), (w - 3, y)]

    for x, y in points:
        pos = QPointF(x, y)
        if win._resize_edges_at(pos):
            live.append((x, y))
        elif win._edges_at(pos):
            blocked.append((x, y))

    print(f"\n  {label}: {w}x{h}")
    print(f"    border points sampled  {len(points)}")
    print(f"    start a resize         {len(live)}")
    print(f"    blocked by a control   {len(blocked)}")
    return live, blocked


print("\n--- the prompt editor gets the border and control guards ---")
editor = PromptEditor()
live, blocked = survey(editor, "prompt editor")

check("some of the border still resizes", len(live) > 0,
      "a frameless window has to be resizable somewhere")

# Corners work
corners = {
    "top-left": (QPointF(2, 2), Qt.LeftEdge | Qt.TopEdge),
    "top-right": (QPointF(editor.width() - 3, 2), Qt.RightEdge | Qt.TopEdge),
    "bottom-left": (QPointF(2, editor.height() - 3), Qt.LeftEdge | Qt.BottomEdge),
    "bottom-right": (QPointF(editor.width() - 3, editor.height() - 3),
                     Qt.RightEdge | Qt.BottomEdge),
    "top edge": (QPointF(editor.width() / 2, 2), Qt.TopEdge),
    "left edge": (QPointF(2, editor.height() / 2), Qt.LeftEdge),
}
for name, (point, expected) in corners.items():
    check(f"the {name} still resizes",
          editor._resize_edges_at(point) == expected,
          f"got {editor._resize_edges_at(point)}, wanted {expected}")

for w, n in ((editor.list, "list"), (editor.text, "prompt box")):
    c = w.mapTo(editor, w.rect().center())
    check(f"no resize on the editor {n}", not editor._resize_edges_at(QPointF(c)))

print("\n--- the middle was never live and still is not ---")
middle = QPointF(editor.width() / 2, editor.height() / 2)
check("editor: centre does nothing", not editor._resize_edges_at(middle))
inside = QPointF(RESIZE_MARGIN + 6, editor.height() / 2)
check("editor: just inside the margin does nothing",
      not editor._resize_edges_at(inside))

print("\n--- a window has a visible edge, all the way round ---")
# Reported as "the top border is missing". It was not missing: it was drawn in
# the same grey as the panels inside, one pixel of #252a33 against a #141414
# desktop, and the top edge has no content beside it to imply where the window
# ends. This checks the edge is painted and that it is not the inside grey.
from relay.window import EDGE                        # noqa: E402
from relay.prompt_editor import BG, LINE             # noqa: E402

check("the edge is its own colour", EDGE not in (BG, LINE), f"{EDGE}")

editor.resize(700, 500)
editor.show()
app.processEvents()
image = editor.grab().toImage()
ratio = image.width() / editor.width()
mid_x, mid_y = image.width() // 2, image.height() // 2
edges = {
    "top": image.pixelColor(mid_x, 0).name(),
    "bottom": image.pixelColor(mid_x, image.height() - 1).name(),
    "left": image.pixelColor(0, mid_y).name(),
    "right": image.pixelColor(image.width() - 1, mid_y).name(),
}
for side, colour in edges.items():
    check(f"the {side} edge is drawn", colour.lower() == EDGE.lower(), colour)

# window.py exists because this chrome was written twice and the copies
# drifted. Four windows wear it now, and one left behind is one that looks
# like a different program.
print("\n--- and all four wear the same one ---")
from relay.chain import ChainWindow                  # noqa: E402
from relay.look_picker import LookPicker             # noqa: E402
from relay.notes import NotesWindow                  # noqa: E402

for window_class in (PromptEditor, LookPicker, ChainWindow, NotesWindow):
    check(f"{window_class.__name__}", window_class.border_colour == EDGE,
          window_class.border_colour)

editor.close()
app.processEvents()

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
