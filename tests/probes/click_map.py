"""Where on the orb does Windows think a click lands?

Suspicion: a frameless translucent Qt window is a layered window, and Windows
hit-tests layered windows by their per-pixel alpha - a click on a fully
transparent pixel passes through to whatever is underneath. While the orb had
an opaque disc under the dots that never mattered. With the disc gone, the
only pixels that can be clicked are the dots themselves.

This asks the window manager directly rather than inferring: WindowFromPoint
respects layered alpha, so it answers the same question the mouse does.
"""
import ctypes
import ctypes.wintypes as w
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context  # noqa: E402,F401
context.isolate_state()

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from relay import orbs  # noqa: E402
from relay.overlay import EDGE_PAD, Orb  # noqa: E402

user32 = ctypes.windll.user32
user32.WindowFromPoint.restype = w.HWND
user32.WindowFromPoint.argtypes = [w.POINT]

AT_X, AT_Y = 320, 220          # clear of wherever the real orb is sitting
SIZE = 64


def hittable(hwnd, left, top, size, scale):
    """How much of the mark's own square the window manager gives us."""
    hits = misses = 0
    for row in range(size):
        for col in range(size):
            point = w.POINT(round((left + col) * scale),
                            round((top + row) * scale))
            if user32.WindowFromPoint(point) == hwnd:
                hits += 1
            else:
                misses += 1
    return hits, misses


def report(look):
    orb.apply_settings({"size": SIZE,
                        "idle": {"look": look, "speed": 1.0},
                        "recording": {"look": look, "speed": 1.0},
                        "processing": {"look": look, "speed": 1.0}})
    orb.repaint()
    app.processEvents()
    hwnd = int(orb.winId())
    # Where Windows says the window is, in the coordinates WindowFromPoint
    # speaks. Deriving it from Qt's geometry and a scale factor is one guess
    # too many when the whole question is about physical pixels.
    rect = w.RECT()
    user32.GetWindowRect(w.HWND(hwnd), ctypes.byref(rect))
    inset = round((rect.right - rect.left - SIZE) / 2)
    hits, misses = hittable(hwnd, rect.left + inset, rect.top + inset,
                            rect.right - rect.left - 2 * inset, 1.0)
    share = hits / (hits + misses) * 100
    print(f"  {look:<12} {share:5.1f}% of the mark's square is clickable "
          f"({hits} of {hits + misses})")
    return share


orb = Orb(on_toggle=lambda: None, on_quit=lambda: None, tooltip="F9",
          prompts_getter=lambda: [], on_pick_look=lambda: None,
          orb_settings={"size": SIZE,
                        "idle": {"look": "weaving", "speed": 1.0},
                        "recording": {"look": "weaving", "speed": 1.0},
                        "processing": {"look": "weaving", "speed": 1.0}})
orb.anchor_x, orb.anchor_y = AT_X, AT_Y
orb._apply_geometry()
orb.show()
orb.raise_()


def run():
    hwnd = int(orb.winId())
    rect = w.RECT()
    user32.GetWindowRect(w.HWND(hwnd), ctypes.byref(rect))
    print(f"\norb window {orb.width()}x{orb.height()} logical, "
          f"native rect {rect.left},{rect.top} "
          f"{rect.right - rect.left}x{rect.bottom - rect.top} (pad {EDGE_PAD})")
    centre = w.POINT((rect.left + rect.right) // 2,
                     (rect.top + rect.bottom) // 2)
    at_centre = user32.WindowFromPoint(centre)
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(at_centre, name, 64)
    print(f"the window at its centre: {at_centre} ({name.value}), "
          f"ours is {hwnd.value}\n")
    shares = [report(look) for look in orbs.LOOKS]
    print(f"\n  worst {min(shares):.1f}%, best {max(shares):.1f}%")
    orb.close()
    app.quit()


QTimer.singleShot(700, run)
app.exec()
