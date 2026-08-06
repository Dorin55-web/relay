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

from relay.compose import Compose  # noqa: E402
from relay.prompt_editor import PromptEditor  # noqa: E402
from relay.translator import TextTranslator  # noqa: E402
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


print("\n--- the write window ---")
tr = TextTranslator()
compose = Compose(tr, on_paste=lambda t: None, target_getter=lambda: "Notepad")
live, blocked = survey(compose, "write window")

check("some of the border still resizes", len(live) > 0,
      "a frameless window has to be resizable somewhere")
print(f"    (controls reach the strip nowhere: {len(blocked)} blocked points)")

# A press that lands on a control must never resize, whatever the margin says.
# Below the buttons is bare window and should still resize - every window does
# from its bottom edge - so the point to check is one *on* a control.
for widget, name in ((compose.paste_btn, "Send button"),
                     (compose.source, "Romanian box"),
                     (compose.output, "English box")):
    centre = widget.mapTo(compose, widget.rect().center())
    check(f"no resize on the {name}",
          not compose._resize_edges_at(QPointF(centre)))
    edge = widget.mapTo(compose, widget.rect().topLeft())
    check(f"nor at the edge of the {name}",
          not compose._resize_edges_at(QPointF(edge.x() + 1, edge.y() + 1)))

# Every corner and edge of the bare window still has to work, including the
# top ones the title bar spans.
corners = {
    "top-left": (QPointF(2, 2), Qt.LeftEdge | Qt.TopEdge),
    "top-right": (QPointF(compose.width() - 3, 2), Qt.RightEdge | Qt.TopEdge),
    "bottom-left": (QPointF(2, compose.height() - 3), Qt.LeftEdge | Qt.BottomEdge),
    "bottom-right": (QPointF(compose.width() - 3, compose.height() - 3),
                     Qt.RightEdge | Qt.BottomEdge),
    "top edge": (QPointF(compose.width() / 2, 2), Qt.TopEdge),
    "left edge": (QPointF(2, compose.height() / 2), Qt.LeftEdge),
}
for name, (point, expected) in corners.items():
    check(f"the {name} still resizes",
          compose._resize_edges_at(point) == expected,
          f"got {compose._resize_edges_at(point)}, wanted {expected}")

print("\n--- the prompt editor gets the same guard ---")
editor = PromptEditor()
live2, blocked2 = survey(editor, "prompt editor")
check("editor resizes somewhere", len(live2) > 0)

check("its corners work too",
      editor._resize_edges_at(QPointF(2, 2)) == (Qt.LeftEdge | Qt.TopEdge))
check("and its top edge, under the title bar",
      editor._resize_edges_at(QPointF(editor.width() / 2, 2)) == Qt.TopEdge)
for w, n in ((editor.list, "list"), (editor.text, "prompt box")):
    c = w.mapTo(editor, w.rect().center())
    check(f"no resize on the editor {n}", not editor._resize_edges_at(QPointF(c)))

print("\n--- the middle was never live and still is not ---")
for win, name in ((compose, "write"), (editor, "editor")):
    middle = QPointF(win.width() / 2, win.height() / 2)
    check(f"{name}: centre does nothing", not win._resize_edges_at(middle))
    inside = QPointF(RESIZE_MARGIN + 6, win.height() / 2)
    check(f"{name}: just inside the margin does nothing",
          not win._resize_edges_at(inside))

compose.close()
editor.close()
app.processEvents()

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
