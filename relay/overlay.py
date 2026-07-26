"""A small always-on-top indicator: a quiet dot that opens into a live waveform.

Idle it is a single soft dot. Click it and the capsule animates open, showing
the last second of your voice as bars that scroll right to left - the heights
come from a history buffer, so the row reflects what you actually said.

Built on PySide6 rather than tkinter for real alpha: soft glows, a translucent
capsule and a drop shadow, plus an animated open/close. The window is padded by
SHADOW_PAD on every side so the shadow has somewhere to fall; all drawing is
inset by that margin, and the saved position always refers to the visible dot
rather than the padded window.
"""

import ctypes
import json
import math
import sys
from collections import deque
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import (QAction, QColor, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSITION_FILE = PROJECT_ROOT / ".orb_position.json"
ICON_PATH = PROJECT_ROOT / "assets" / "relay.ico"

DOT_BOX = 48             # visible size of the collapsed tile
CAPSULE_W = 100          # visible width when open
CAPSULE_H = 48
SHADOW_PAD = 12          # room around the artwork for the drop shadow

DOT_R_OPEN = 6.0         # the dot shrinks once the live bars take over
GLOW_R = 21.0            # halo scales with the dot, or it looks crowded

# The icon's mark, as fractions of the tile, so the thing floating on screen and
# the thing in the taskbar are the same drawing at two sizes.
ICON_INSET = 0.03
ICON_RADIUS = 0.23
ICON_DOT_X = 0.252
ICON_DOT_R = 0.108
ICON_BAR_FIRST = 0.472
ICON_BAR_STEP = 0.170
ICON_BAR_W = 0.088
ICON_BAR_H = (0.40, 0.64, 0.30)

BAR_COUNT = 8            # fewer bars, or they overlap in the narrow capsule
BAR_WIDTH = 3.0
SAMPLE_EVERY = 4         # frames per bar; the row then spans about half a second
FRAME_MS = 16            # 60fps: Qt can afford it and the motion reads smoother

CAPSULE_RGB = (14, 16, 21)
TILE_TOP_RGB = (32, 36, 46)      # same gradient as assets/relay.ico
TILE_BOTTOM_RGB = (13, 15, 20)
CAPSULE_ALPHA = 232

# One colour, in every state. The capsule opening and closing is the signal for
# start and finish, so the dot never needs to change hue to say anything.
ACCENT = QColor("#e8ebf0")

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080


def _make_non_activating(widget):
    """Belt and braces on top of Qt's own flags.

    Qt.WindowDoesNotAcceptFocus and WA_ShowWithoutActivating cover most of it,
    but the Win32 style is what actually guarantees a click never moves the
    foreground window - and if it does move, our Ctrl+V lands on this widget
    instead of the prompt box you were aiming at.
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        user32 = ctypes.windll.user32
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = get_long(hwnd, GWL_EXSTYLE)
        set_long(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    except Exception as exc:
        print(f"[orb] could not set non-activating style: {exc}")


def _blend(a, b, t):
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


class Orb(QWidget):
    def __init__(self, on_toggle, on_quit, tooltip="F9",
                 prompts_getter=None, on_prompt=None, on_edit_prompts=None):
        self.app = QApplication.instance() or QApplication(sys.argv)
        # Qt quits once the last primary window closes, and a Qt.Tool window -
        # which the dot is - does not count as one. Without this, closing the
        # prompt editor takes the whole app down with it and the dot vanishes.
        self.app.setQuitOnLastWindowClosed(False)
        if ICON_PATH.is_file():
            self.app.setWindowIcon(QIcon(str(ICON_PATH)))
        super().__init__()

        self.on_toggle = on_toggle
        self.on_quit = on_quit
        self.on_prompt = on_prompt
        self.on_edit_prompts = on_edit_prompts
        self.menu = None
        self._hotkey_label = tooltip
        self._prompts_getter = prompts_getter or (lambda: [])

        self.state = "idle"
        self.level_getter = lambda: 0.0
        self._phase = 0.0
        self._frame = 0
        self._expanded = False
        self._grow_left = False
        self._press_global = None
        self._press_origin = None
        self._history = deque([0.0] * BAR_COUNT, maxlen=BAR_COUNT)
        # 0 = closed, 1 = fully open; drives width so the capsule unfurls.
        self._open = 0.0
        self._settled_painted = False

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Relay")

        self.anchor_x, self.anchor_y = self._load_position()
        self._apply_geometry(force=True)
        self.show()
        _make_non_activating(self)

        self._build_menu(tooltip, self._current_prompts())

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_MS)

    # --- menu -------------------------------------------------------------

    def _current_prompts(self):
        """Never let a broken prompt file stop the menu from opening."""
        try:
            return list(self._prompts_getter() or [])
        except Exception as exc:
            print(f"[orb] could not read the prompts: {exc}")
            return []

    def _build_menu(self, tooltip, prompts):
        """Prompts first, then the two things the dot itself does.

        The templates are numbered and grouped by the kind of work they start,
        so the one you want is found by position rather than by reading ten
        similar sentences. Hovering shows the whole prompt: the labels are short
        enough to scan, which means they are too short to be unambiguous.

        Rebuilt on every right-click rather than once at startup, so editing
        prompts.json takes effect at the next click instead of the next launch.
        clear() deletes the old actions, so this does not leak one menu per use.
        """
        if getattr(self, "menu", None) is None:
            self.menu = QMenu(self)
            self.menu.setToolTipsVisible(True)
        else:
            self.menu.clear()

        section = None
        for i, prompt in enumerate(prompts, start=1):
            if prompt["section"] and prompt["section"] != section:
                section = prompt["section"]
                self.menu.addSection(section)
            act = QAction(f"{i}.  {prompt['label']}", self)
            act.setToolTip(prompt["text"])
            # Default arguments, not closure capture: every lambda in this loop
            # would otherwise see the last prompt in the list.
            act.triggered.connect(
                lambda checked=False, text=prompt["text"]: self._fire_prompt(text)
            )
            self.menu.addAction(act)

        if prompts:
            self.menu.addSeparator()
            edit_act = QAction("Edit prompts...", self)
            edit_act.triggered.connect(self._fire_edit_prompts)
            self.menu.addAction(edit_act)

        self.menu.addSeparator()
        dictate_act = QAction(f"Dictate  ({tooltip})", self)
        dictate_act.triggered.connect(self._fire_toggle)
        self.menu.addAction(dictate_act)

        self.menu.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self.quit)
        self.menu.addAction(quit_act)

    # --- position ---------------------------------------------------------

    def _load_position(self):
        try:
            data = json.loads(POSITION_FILE.read_text(encoding="utf-8"))
            return int(data["x"]), int(data["y"])
        except Exception:
            screen = self.app.primaryScreen().availableGeometry()
            return screen.right() - DOT_BOX - 40, screen.bottom() - DOT_BOX - 60

    def _save_position(self):
        try:
            POSITION_FILE.write_text(
                json.dumps({"x": self.anchor_x, "y": self.anchor_y}), encoding="utf-8"
            )
        except OSError:
            pass

    def _visible_width(self):
        return DOT_BOX + (CAPSULE_W - DOT_BOX) * self._open

    def _screen_geometry(self):
        """The screen the dot is actually on, not just the primary one."""
        screen = self.app.screenAt(QPoint(int(self.anchor_x), int(self.anchor_y)))
        return (screen or self.app.primaryScreen()).availableGeometry()

    def _choose_direction(self):
        """Open away from the closer edge. True means grow to the left.

        Decided once, when the capsule starts to open, so it cannot flip
        mid-animation. Re-evaluated on every open, which is what makes it
        follow the dot after you drag it to the other side of the screen.
        """
        screen = self._screen_geometry()
        needed = CAPSULE_W - DOT_BOX
        room_right = screen.right() - (self.anchor_x + DOT_BOX)
        room_left = self.anchor_x - screen.left()

        if room_right >= needed and room_left < needed:
            return False        # only the right fits
        if room_left >= needed and room_right < needed:
            return True         # only the left fits
        # Either both sides fit or neither does; open towards the roomier one,
        # which is the same as opening away from the nearer edge.
        return room_left > room_right

    def _apply_geometry(self, force=False):
        """Size the window to the current open fraction, keeping the dot still."""
        width = self._visible_width()
        grow = width - DOT_BOX

        if force:
            self._grow_left = self._choose_direction()

        x = self.anchor_x - grow if self._grow_left else self.anchor_x
        self.setGeometry(
            int(x) - SHADOW_PAD,
            int(self.anchor_y) - SHADOW_PAD,
            int(width) + 2 * SHADOW_PAD,
            CAPSULE_H + 2 * SHADOW_PAD,
        )

    def _dot_center(self):
        """Where the dot sits inside the padded window."""
        cy = SHADOW_PAD + CAPSULE_H / 2
        if self._grow_left:
            return SHADOW_PAD + self._visible_width() - DOT_BOX / 2, cy
        return SHADOW_PAD + DOT_BOX / 2, cy

    # --- input ------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_origin = QPoint(self.anchor_x, self.anchor_y)
        elif event.button() == Qt.RightButton:
            self._build_menu(self._hotkey_label, self._current_prompts())
            self.menu.popup(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self._press_global is None:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        self.anchor_x = self._press_origin.x() + delta.x()
        self.anchor_y = self._press_origin.y() + delta.y()
        self._apply_geometry()

    def mouseReleaseEvent(self, event):
        if self._press_global is None or event.button() != Qt.LeftButton:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        moved = abs(delta.x()) > 4 or abs(delta.y()) > 4
        self._press_global = None
        if moved:
            self._save_position()
        else:
            self._fire_toggle()

    def _fire_toggle(self):
        try:
            self.on_toggle()
        except Exception:
            pass

    def _fire_prompt(self, text):
        if self.on_prompt is None:
            return
        try:
            self.on_prompt(text)
        except Exception as exc:
            print(f"[orb] could not insert prompt: {exc}")

    def _fire_edit_prompts(self):
        if self.on_edit_prompts is None:
            return
        try:
            self.on_edit_prompts()
        except Exception as exc:
            print(f"[orb] could not open the prompt file: {exc}")

    # --- state, called from worker threads --------------------------------

    def set_state(self, state):
        # Only touches plain attributes; the timer repaints on the GUI thread.
        self.state = state
        self._settled_painted = False

    def flash(self, kind):
        """Kept so callers need not care, but deliberately does nothing.

        Success and failure used to tint the dot green or red. The capsule
        closing already says the dictation finished, and the log says how it
        went, so a colour change here only added noise.
        """

    # --- animation --------------------------------------------------------

    def _tick(self):
        self._phase += 0.06
        self._frame += 1

        want_open = 1.0 if self.state in ("recording", "processing") else 0.0
        if want_open > 0.0 and self._open <= 0.001:
            # Pick the side now, while still closed, so the choice reflects
            # wherever the dot has been dragged since the last dictation.
            self._grow_left = self._choose_direction()
        if abs(self._open - want_open) > 0.001:
            # Ease towards the target instead of snapping, so the capsule
            # unfurls and folds away rather than blinking between two sizes.
            self._open += (want_open - self._open) * 0.28
            if abs(self._open - want_open) <= 0.01:
                self._open = want_open
            self._apply_geometry()

        if self._frame % SAMPLE_EVERY == 0:
            if self.state == "recording":
                self._history.append(max(0.0, min(1.0, self.level_getter())))
            elif self.state == "processing":
                self._history.append(0.30 + 0.22 * math.sin(self._phase * 2.6))
            else:
                self._history.append(0.0)

        # At rest the dot is a static circle, so redrawing it 60 times a second
        # would burn CPU for no visible change on something that stays open all
        # day. Paint the settled frame once, then stop until something moves.
        settled = self.state == "idle" and self._open <= 0.001
        if settled and self._settled_painted:
            return
        self._settled_painted = settled
        self.update()

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        accent = ACCENT

        # The tile is always there now; it only widens into a capsule. Idle it
        # carries the icon's own mark, so what floats on screen is recognisably
        # the same thing as the taskbar button.
        self._paint_capsule(painter)

        if self._open > 0.5:
            self._paint_bars(painter, accent)
        if self._open < 0.5:
            self._paint_icon_bars(painter, accent)

        cx, cy = self._mark_dot_center()
        self._paint_glow(painter, accent, cx, cy)

        idle_r = DOT_BOX * ICON_DOT_R
        radius = idle_r + (DOT_R_OPEN - idle_r) * self._open
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)
        painter.end()

    def _mark_dot_center(self):
        """Where the dot sits: inside the icon when idle, at the capsule's end
        once it opens. Interpolated, so it slides rather than jumps."""
        _, cy = self._dot_center()
        tile_left = self._tile_left()
        idle_x = tile_left + DOT_BOX * ICON_DOT_X
        open_x, _ = self._dot_center()
        return idle_x + (open_x - idle_x) * self._open, cy

    def _tile_left(self):
        return SHADOW_PAD + (self._visible_width() - DOT_BOX if self._grow_left else 0)

    def _paint_icon_bars(self, painter, accent):
        """The icon's three static bars, for the resting state."""
        fade = int(255 * min(1.0, (0.5 - self._open) * 2))
        if fade <= 0:
            return
        left = self._tile_left()
        _, cy = self._dot_center()
        for i, height in enumerate(ICON_BAR_H):
            x = left + DOT_BOX * (ICON_BAR_FIRST + i * ICON_BAR_STEP)
            half = DOT_BOX * height / 2
            colour = QColor(accent)
            colour.setAlpha(int((255 if i == 1 else 200) * fade / 255))
            painter.setPen(QPen(colour, DOT_BOX * ICON_BAR_W,
                                Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(x, cy - half), QPointF(x, cy + half))

    def _paint_capsule(self, painter):
        """Translucent rounded background plus a soft shadow beneath it."""
        rect = QRectF(
            float(SHADOW_PAD), float(SHADOW_PAD),
            self._visible_width(), float(CAPSULE_H),
        )
        # Rounded square at rest, full pill once open: the corner travels with
        # the width, so the icon unrolls into the meter instead of morphing.
        idle_radius = DOT_BOX * ICON_RADIUS
        radius = idle_radius + (CAPSULE_H / 2 - idle_radius) * self._open

        # Cheap drop shadow: a few offset rounded rects at low alpha. Qt's
        # graphics effect would need a second render pass for the same look.
        for i, alpha in enumerate((16, 12, 8)):
            spread = (i + 1) * 3
            shadow = rect.adjusted(-spread, -spread + 3, spread, spread + 3)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(shadow, radius + spread, radius + spread)

        # The icon's vertical gradient, not a flat fill: at rest this tile is
        # the icon, and the two should not be noticeably different objects.
        tile = QLinearGradient(0.0, rect.top(), 0.0, rect.bottom())
        top = QColor(*TILE_TOP_RGB, CAPSULE_ALPHA)
        bottom = QColor(*TILE_BOTTOM_RGB, CAPSULE_ALPHA)
        tile.setColorAt(0.0, top)
        tile.setColorAt(1.0, bottom)
        painter.setBrush(tile)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawRoundedRect(rect, radius, radius)

    def _paint_glow(self, painter, accent, cx, cy):
        """A true radial falloff - the thing tkinter could not do."""
        # One strength for every state; only your voice moves it, so the halo
        # breathes with the level without ever changing what colour it is.
        strength = 70
        gradient = QRadialGradient(cx, cy, GLOW_R)
        inner = QColor(accent)
        inner.setAlpha(int(strength * (0.5 + 0.5 * min(1.0, self.level_getter()))))
        edge = QColor(accent)
        edge.setAlpha(0)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(1.0, edge)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(QPoint(int(cx), int(cy)), int(GLOW_R), int(GLOW_R))

    def _paint_bars(self, painter, accent):
        """Level history as bars scrolling right to left."""
        cx, cy = self._dot_center()
        # Tighter insets than before: in a capsule this narrow, the old 15-16px
        # margins ate most of the room the bars need.
        if self._grow_left:
            left = SHADOW_PAD + 14
            right = cx - 15
        else:
            left = cx + 15
            right = SHADOW_PAD + self._visible_width() - 14

        span = right - left
        if span <= 2:
            return
        step = span / (BAR_COUNT - 1)
        max_half = (CAPSULE_H - 16) / 2
        fade = int(255 * min(1.0, (self._open - 0.5) * 2))

        for i, level in enumerate(self._history):
            half = max(0.5, max_half * (level ** 0.7))
            x = left + i * step
            # Same hue throughout; only brightness follows the level, so a
            # quiet bar reads as dimmer rather than as a different colour.
            dim = _blend(accent, QColor(*CAPSULE_RGB), 0.45)
            colour = _blend(dim, accent, min(1.0, half / max_half))
            colour.setAlpha(fade)
            painter.setPen(QPen(colour, BAR_WIDTH, Qt.SolidLine, Qt.RoundCap))
            # Float coordinates, not int: the step is fractional, so truncating
            # made the gaps alternate 2px/3px, and quantised bar heights to
            # whole pixels so quiet speech moved them in visible jumps.
            painter.drawLine(QPointF(x, cy - half), QPointF(x, cy + half))

    # --- lifecycle --------------------------------------------------------

    def run(self):
        self.app.exec()

    def quit(self):
        self._save_position()
        try:
            self.on_quit()
        except Exception:
            pass
        self._timer.stop()
        self.close()
        self.app.quit()
