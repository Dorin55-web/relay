"""The frameless window chrome both of Relay's windows wear.

This was written twice, once per window, and the copies drifted: the prompt
editor got the cursor feedback that tells you where the resize edges are and
the write window did not, while both kept the press handler that starts a
resize. Clicking near an edge there began a drag with no warning it was about
to, and the cursor belonged to the system until it finished.

One base class, so that cannot happen again.
"""

import ctypes
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

RESIZE_MARGIN = 6        # grab strip around the frameless edge
TITLE_HEIGHT = 38

# Windows 11 rounds ordinary windows by itself, but a frameless Qt window is a
# WS_POPUP and DWM leaves those square. The stylesheet's border-radius only
# rounds the painted background, not the window, so the corner has to be asked
# for. https://learn.microsoft.com/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_ROUND = 2


def colorref(hex_colour):
    """#rrggbb -> the 0x00bbggrr integer DWM wants."""
    value = hex_colour.lstrip("#")
    red, green, blue = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return (blue << 16) | (green << 8) | red


class TitleBar(QWidget):
    """Replaces the Windows caption, so the whole window is one piece.

    Dragging hands straight over to the compositor with startSystemMove rather
    than moving the window from mouse deltas: that is what keeps Aero snap, the
    double-click behaviour and multi-monitor DPI changes working, none of which
    are worth reimplementing.
    """

    def __init__(self, title, on_minimise, on_close):
        super().__init__()
        self.setFixedHeight(TITLE_HEIGHT)

        label = QLabel(title)
        label.setObjectName("title")

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 7, 0)
        row.setSpacing(2)
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(self._button("–", "Minimise", on_minimise))
        row.addWidget(self._button("✕", "Close", on_close))

    def _button(self, glyph, tip, slot):
        button = QPushButton(glyph)
        button.setObjectName("chrome")
        button.setToolTip(tip)
        button.setFixedSize(30, 26)
        button.setCursor(Qt.ArrowCursor)
        button.clicked.connect(slot)
        return button

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        # The very top of this bar is also the window's top resize edge. Let it
        # through rather than starting a move, or the top corners could only
        # ever be dragged, never resized.
        if event.position().y() <= RESIZE_MARGIN:
            event.ignore()
            return
        handle = self.window().windowHandle()
        if handle is not None:
            handle.startSystemMove()


class FramelessWindow(QWidget):
    """A window that draws its own frame: rounded, resizable, Escape closes.

    Subclasses set their own title, size and border colour, then build their
    contents with a TitleBar as the first row.
    """

    border_colour = "#2f3542"

    def __init__(self, title):
        super().__init__()
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("shell")
        self.setMouseTracking(True)
        self._rounded = False
        # What the cursor is showing, so it is only changed on a transition.
        self._cursor_shape = None

    # --- native corners ---------------------------------------------------

    def showEvent(self, event):
        # Needs a real HWND, which only exists once the window is being shown.
        super().showEvent(event)
        if not self._rounded:
            self._rounded = True
            self._round_corners()

    def _round_corners(self):
        """A no-op before Windows 11, which leaves the window square as it was."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi
            preference = ctypes.c_int(DWMWCP_ROUND)
            dwm.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(preference), ctypes.sizeof(preference),
            )
            border = ctypes.c_uint(colorref(self.border_colour))
            dwm.DwmSetWindowAttribute(
                hwnd, DWMWA_BORDER_COLOR,
                ctypes.byref(border), ctypes.sizeof(border),
            )
        except Exception as exc:
            print(f"[window] could not round the corners: {exc}")

    # --- resizing ---------------------------------------------------------

    def _edges_at(self, pos):
        """Which window edges the pointer is close enough to drag."""
        edges = Qt.Edges()
        if pos.x() <= RESIZE_MARGIN:
            edges |= Qt.LeftEdge
        elif pos.x() >= self.width() - RESIZE_MARGIN:
            edges |= Qt.RightEdge
        if pos.y() <= RESIZE_MARGIN:
            edges |= Qt.TopEdge
        elif pos.y() >= self.height() - RESIZE_MARGIN:
            edges |= Qt.BottomEdge
        return edges

    def _edge_cursor(self, edges):
        horizontal = bool(edges & (Qt.LeftEdge | Qt.RightEdge))
        vertical = bool(edges & (Qt.TopEdge | Qt.BottomEdge))
        if horizontal and vertical:
            falling = bool(edges & Qt.TopEdge) == bool(edges & Qt.LeftEdge)
            return Qt.SizeFDiagCursor if falling else Qt.SizeBDiagCursor
        if horizontal:
            return Qt.SizeHorCursor
        if vertical:
            return Qt.SizeVerCursor
        return None

    def _resize_edges_at(self, pos):
        """Edges to drag from here, or none if this is not bare window.

        Being within the margin is not enough. The strip runs alongside the
        text boxes and the row of buttons, and a resize started from there is
        one nobody asked for: the pointer belongs to the compositor until the
        drag ends, which reads exactly like the window having seized it.

        The test is what a click there would mean, not merely whether a widget
        is present. Anything that can take focus - a text box, a list, a button
        - is somewhere you meant to click. The title bar and the labels cannot,
        and must stay transparent here: the bar spans the whole top edge, so
        treating any child as a blocker kills the top edge and both top corners
        outright.

        The cheap test comes first. Mouse tracking is on, so this runs on every
        pixel of movement, and childAt walks the whole child tree. Nearly all of
        those events are nowhere near an edge, and for those the arithmetic
        answers on its own.
        """
        edges = self._edges_at(pos)
        if not edges:
            return edges
        child = self.childAt(pos.toPoint())
        if child is not None and child.focusPolicy() != Qt.NoFocus:
            return Qt.Edges()
        return edges

    def mouseMoveEvent(self, event):
        """Show what a click here would do, before it does it.

        Without this the press handler still starts a resize, so a click near
        an edge begins a drag with nothing having said it would.

        Only when the shape actually changes. The first version set the cursor
        on every event, which for a sweep across the window is over a thousand
        calls that each ask the window manager for the same answer - enough,
        while the window is still being built and its fonts and stylesheet
        loaded, to leave the pointer visibly stuck.
        """
        shape = self._edge_cursor(self._resize_edges_at(event.position()))
        if shape != self._cursor_shape:
            self._cursor_shape = shape
            if shape is None:
                self.unsetCursor()
            else:
                self.setCursor(QCursor(shape))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edges = self._resize_edges_at(event.position())
            handle = self.windowHandle()
            if edges and handle is not None:
                # Hand the drag to the compositor rather than chasing the mouse
                # ourselves: it gets the snapping and the minimum size right,
                # and never lags behind the pointer.
                print(f"[window] resize from {int(edges)} at "
                      f"{event.position().x():.0f},{event.position().y():.0f}")
                handle.startSystemResize(edges)
                return
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        # Or the resize cursor follows the pointer out of the window.
        if self._cursor_shape is not None:
            self._cursor_shape = None
            self.unsetCursor()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        # There is no system menu on a frameless window, so Escape is the only
        # keyboard way out.
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
