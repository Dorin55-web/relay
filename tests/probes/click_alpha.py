"""What is the faintest backing Windows will still let you click?

The fix for the orb falling through is to paint something under the dots. The
question is how little it can be: too low and the hit test still passes
through, too high and there is a visible smudge where the transparency was
meant to be.

The alpha=255 row is the control. If that one is not fully clickable then the
window is simply covered and none of the other rows mean anything.
"""
import ctypes
import ctypes.wintypes as w
import sys

from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

app = QApplication.instance() or QApplication(sys.argv)

user32 = ctypes.windll.user32
user32.WindowFromPoint.restype = w.HWND
user32.WindowFromPoint.argtypes = [w.POINT]

SIZE = 64
ALPHAS = (0, 1, 2, 3, 5, 8, 16, 255)


class Patch(QWidget):
    def __init__(self):
        super().__init__()
        self.alpha = 0
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(80, 620, SIZE, SIZE)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, self.alpha))
        painter.drawEllipse(QPointF(SIZE / 2, SIZE / 2), SIZE / 2, SIZE / 2)
        painter.end()


patch = Patch()
patch.show()
patch.raise_()


def measure(alpha):
    patch.alpha = alpha
    patch.repaint()
    app.processEvents()
    hwnd = int(patch.winId())
    rect = w.RECT()
    user32.GetWindowRect(w.HWND(hwnd), ctypes.byref(rect))
    hits = total = 0
    for row in range(rect.top + 4, rect.bottom - 4, 3):
        for col in range(rect.left + 4, rect.right - 4, 3):
            hits += user32.WindowFromPoint(w.POINT(col, row)) == hwnd
            total += 1
    return hits / total * 100


def run():
    hwnd = int(patch.winId())
    rect = w.RECT()
    user32.GetWindowRect(w.HWND(hwnd), ctypes.byref(rect))
    patch.alpha = 255
    patch.repaint()
    app.processEvents()
    centre = w.POINT((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    other = user32.WindowFromPoint(centre)
    name = ctypes.create_unicode_buffer(80)
    user32.GetClassNameW(other, name, 80)
    print(f"\npatch {hwnd} at {rect.left},{rect.top} "
          f"{rect.right - rect.left}x{rect.bottom - rect.top}; "
          f"solid, its centre reports {other} ({name.value})")

    print("\n  alpha   clickable")
    for alpha in ALPHAS:
        print(f"  {alpha:>5}   {measure(alpha):5.1f}%")
    patch.close()
    app.quit()


QTimer.singleShot(600, run)
app.exec()
