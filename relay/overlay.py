"""A small always-on-top indicator, wearing a different look in each state.

Resting, listening and working each get their own drawing from `orbs` and
their own speed, chosen in the picker window and kept in config.json. Changing
state cross-fades from one to the next rather than cutting: the two drawings
share no geometry at all, so a cut reads as the orb being replaced.

Speaking hurries whichever look is on. That is one handle rather than nine
per-look ones, and it is the only one all of them have in common - a lattice
has rings to ripple, a constellation has none.

The taskbar and window icon is the still globe in `sphere` instead - a
different drawing for a different job. An icon never moves and has to survive
sixteen pixels, where these looks are hundreds of sub-pixel dots and no shape.

Built on PySide6 rather than tkinter for real alpha. The window is padded by
SHADOW_PAD on every side so the shadow has somewhere to fall; all drawing is
inset by that margin, and the saved position always refers to the visible orb
rather than the padded window.
"""

import ctypes
import json
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import (QAction, QColor, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import orbs, sphere
from .config import DEFAULTS, normalise_orb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSITION_FILE = PROJECT_ROOT / ".orb_position.json"
ICON_PATH = PROJECT_ROOT / "assets" / "relay.ico"

DEFAULT_ORB_SIZE = DEFAULTS["orb"]["size"]
SHADOW_PAD = 12          # room around the artwork for the drop shadow
GLOBE_FILL = 0.82        # how much of the circle the icon's dots reach into
MIN_DOT_PIXELS = 0.55    # floor, so a 16px icon still draws dots not haze

# The taskbar wants a square tile, which is what every other app icon is.
ICON_INSET = 0.03
ICON_RADIUS = 0.23
ICON_LIFT = 2.4          # the icon is drawn brighter than the orb sits at

FRAME_MS = 33            # 30fps. These looks move slowly enough to read at
                         # this rate, and the orb animates all day: sixty
                         # would double the cost of that for no visible gain.
CROSSFADE_FRAMES = 9     # about a third of a second, both looks drawn at once

# How much louder speech hurries the look along, at full level.
VOICE_URGENCY = 0.6

# The level is smoothed asymmetrically: quick to follow a syllable starting,
# slow to let go, so the orb rides speech instead of flickering with it.
LEVEL_ATTACK = 0.35
LEVEL_RELEASE = 0.06
LEVEL_CURVE = 0.7        # quiet speech still moves the orb visibly


TILE_TOP_RGB = (32, 36, 46)      # the look is drawn over this gradient
TILE_BOTTOM_RGB = (13, 15, 20)
TILE_ALPHA = 232

# One colour, in every state. The look changing is the signal, so the orb
# never needs a hue to say anything.
ACCENT = QColor("#e8ebf0")

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def paint_look(painter, left, top, size, look, clock, opacity=1.0):
    """One look, drawn into a size-by-size box at left/top.

    Lines go down before dots, so the nodes of the constellation sit on their
    own wires; the dots then go far to near, which is the whole of the depth
    sorting these need. `opacity` scales everything, which is what makes a
    cross-fade possible without either drawing knowing about the other.
    """
    dots, lines = orbs.frame(look, size, clock)

    for x1, y1, x2, y2, ink, alpha, width in lines:
        painter.setPen(QPen(_ink(ink, alpha * opacity), width))
        painter.drawLine(QPointF(left + x1, top + y1),
                         QPointF(left + x2, top + y2))

    painter.setPen(Qt.NoPen)
    for x, y, _z, r, ink, alpha in sorted(dots, key=lambda d: d[2]):
        painter.setBrush(_ink(ink, alpha * opacity))
        painter.drawEllipse(QPointF(left + x, top + y), r, r)


def _ink(ink, alpha):
    """The drawings are inked for paper, 0 darkest. This is a dark tile."""
    grey = round((1.0 - min(1.0, max(0.0, ink))) * 255)
    colour = QColor(grey, grey, grey)
    colour.setAlphaF(min(1.0, max(0.0, alpha)))
    return colour


def paint_globe(painter, cx, cy, diameter, lift=1.0):
    """The dotted sphere, centred on cx/cy - the icon's drawing.

    Far dots are drawn first so the near ones land on top of them, which is
    the whole of the depth sorting a sphere this size needs. `lift` brightens
    the lot, because an icon has to be legible in a taskbar rather than sit
    quietly on a desktop.
    """
    radius = diameter / 2
    reach = radius * GLOBE_FILL
    rings, equator = sphere.rings_for(diameter)
    # Fewer dots means more room for each. Without this the small icons draw
    # sub-pixel dots and come out as a grey haze.
    spread = sphere.EQUATOR_DOTS / equator
    painter.setPen(Qt.NoPen)
    for x, y, depth in sorted(
            sphere.dots(rings=rings, equator=equator), key=lambda d: d[2]):
        colour = QColor(ACCENT)
        colour.setAlphaF(min(1.0, sphere.dot_alpha(depth) * lift))
        painter.setBrush(colour)
        # And never below half a pixel, or antialiasing turns the dot into a
        # faint stain rather than a dot.
        r = max(MIN_DOT_PIXELS, sphere.dot_radius(depth, spread) * radius)
        painter.drawEllipse(QPointF(cx + x * reach, cy - y * reach), r, r)


def _icon_pixmap(size):
    """The app icon at one size: the same globe on the square tile a taskbar
    button wants."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)

    rect = QRectF(s * ICON_INSET, s * ICON_INSET,
                  s * (1 - 2 * ICON_INSET), s * (1 - 2 * ICON_INSET))
    path = QPainterPath()
    path.addRoundedRect(rect, s * ICON_RADIUS, s * ICON_RADIUS)

    tile = QLinearGradient(0.0, rect.top(), 0.0, rect.bottom())
    tile.setColorAt(0.0, QColor(*TILE_TOP_RGB))
    tile.setColorAt(1.0, QColor(*TILE_BOTTOM_RGB))
    painter.setPen(Qt.NoPen)
    painter.setBrush(tile)
    painter.drawPath(path)

    if size >= 32:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 26), max(1.0, s * 0.008)))
        painter.drawPath(path)

    # A still globe, but brighter than the orb sits at: an icon is an identity
    # mark in a taskbar, not something being unobtrusive on a desktop.
    paint_globe(painter, s / 2, s / 2, s * 0.94, lift=ICON_LIFT)
    painter.end()
    return pixmap


def build_icon():
    """A multi-size icon, so the taskbar gets one drawn for its own size."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(_icon_pixmap(size))
    return icon

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


class Orb(QWidget):
    def __init__(self, on_toggle, on_quit, tooltip="F9",
                 prompts_getter=None, on_prompt=None, on_edit_prompts=None,
                 on_compose=None, on_pick_look=None, orb_settings=None):
        self.app = QApplication.instance() or QApplication(sys.argv)
        # Qt quits once the last primary window closes, and a Qt.Tool window -
        # which the dot is - does not count as one. Without this, closing the
        # prompt editor takes the whole app down with it and the dot vanishes.
        self.app.setQuitOnLastWindowClosed(False)
        super().__init__()

        self.on_toggle = on_toggle
        self.on_quit = on_quit
        self.on_prompt = on_prompt
        self.on_edit_prompts = on_edit_prompts
        self.on_compose = on_compose
        self.on_pick_look = on_pick_look
        self.menu = None
        self._hotkey_label = tooltip
        self._prompts_getter = prompts_getter or (lambda: [])

        self.state = "idle"
        self.level_getter = lambda: 0.0
        self._frame = 0
        self._press_global = None
        self._press_origin = None
        self._level = 0.0

        self.orb_settings = normalise_orb(orb_settings)
        self.orb_size = self.orb_settings["size"]
        self._look = self._look_for("idle")
        # Each look's clock is its own, and starts at zero when it comes on:
        # they are not on a common timeline, and there is nothing to be gained
        # by carrying one look's phase into another's.
        self._clock = 0.0
        # What is fading out behind the current look, if anything.
        self._leaving = None

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
        self._apply_geometry()
        self.app.setWindowIcon(build_icon())
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

        if self.on_compose is not None:
            write_act = QAction("Write instead...", self)
            write_act.setToolTip(
                "Type Romanian and send the English where you were writing"
            )
            write_act.triggered.connect(self._fire_compose)
            self.menu.addAction(write_act)

        if self.on_pick_look is not None:
            look_act = QAction("Change how it looks...", self)
            look_act.setToolTip("A different drawing for resting, "
                                "listening and working")
            look_act.triggered.connect(self._fire_pick_look)
            self.menu.addAction(look_act)

        self.menu.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self.quit)
        self.menu.addAction(quit_act)

    # --- position ---------------------------------------------------------

    def _load_position(self):
        try:
            data = json.loads(POSITION_FILE.read_text(encoding="utf-8"))
            return self._on_screen(int(data["x"]), int(data["y"]))
        except Exception:
            screen = self.app.primaryScreen().availableGeometry()
            return (screen.right() - self.orb_size - 40,
                    screen.bottom() - self.orb_size - 60)

    def _on_screen(self, x, y):
        """Pull a saved position back inside the display it was saved on.

        The position is the orb's top-left corner, so a saved one from when
        the orb was smaller now hangs off the edge by the difference - and a
        monitor that has since been unplugged puts it nowhere at all.
        """
        screen = self.app.screenAt(QPoint(x, y))
        area = (screen or self.app.primaryScreen()).availableGeometry()
        return (
            max(area.left(), min(x, area.right() - self.orb_size)),
            max(area.top(), min(y, area.bottom() - self.orb_size)),
        )

    def _save_position(self):
        try:
            POSITION_FILE.write_text(
                json.dumps({"x": self.anchor_x, "y": self.anchor_y}), encoding="utf-8"
            )
        except OSError:
            pass

    def _screen_geometry(self):
        """The screen the orb is actually on, not just the primary one."""
        screen = self.app.screenAt(QPoint(int(self.anchor_x), int(self.anchor_y)))
        return (screen or self.app.primaryScreen()).availableGeometry()

    def _apply_geometry(self):
        """A fixed square. The orb never changes size, so nothing here moves."""
        self.setGeometry(
            int(self.anchor_x) - SHADOW_PAD,
            int(self.anchor_y) - SHADOW_PAD,
            self.orb_size + 2 * SHADOW_PAD,
            self.orb_size + 2 * SHADOW_PAD,
        )

    def _centre(self):
        half = SHADOW_PAD + self.orb_size / 2
        return half, half

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

    def _fire_compose(self):
        if self.on_compose is None:
            return
        try:
            self.on_compose()
        except Exception as exc:
            print(f"[orb] could not open the write window: {exc}")

    def _fire_pick_look(self):
        if self.on_pick_look is None:
            return
        try:
            self.on_pick_look()
        except Exception as exc:
            print(f"[orb] could not open the look picker: {exc}")

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
        if state == self.state:
            return
        self.state = state
        self._change_look(self._look_for(state))

    def flash(self, kind):
        """Kept so callers need not care, but deliberately does nothing.

        Success and failure used to tint the dot green or red. The look
        changing back already says the dictation finished, and the log says how
        it went, so a colour change here only added noise.
        """

    # --- looks ------------------------------------------------------------

    def _look_for(self, state):
        return self.orb_settings[state]["look"]

    def _speed_for(self, state):
        return self.orb_settings[state]["speed"]

    def _change_look(self, look):
        """Start showing a different drawing, fading the old one out under it."""
        if look == self._look:
            return
        self._leaving = (self._look, self._clock, CROSSFADE_FRAMES)
        self._look = look
        self._clock = 0.0

    def apply_settings(self, settings):
        """Take a new set of looks, live - the picker calls this as you click.

        The orb keeps running while the picker is open, so this has to cope
        with the size changing underneath it: the window is resized around the
        same anchor, and the position is left where the user put it.
        """
        self.orb_settings = normalise_orb(settings)
        if self.orb_settings["size"] != self.orb_size:
            self.orb_size = self.orb_settings["size"]
            self.anchor_x, self.anchor_y = self._on_screen(
                self.anchor_x, self.anchor_y)
            self._apply_geometry()
            self._save_position()
        self._change_look(self._look_for(self.state))
        self.update()

    # --- animation --------------------------------------------------------

    def _tick(self):
        self._frame += 1
        target = self._target_level()
        # Fast towards a louder level, slow away from it. Symmetric smoothing
        # either lags the start of a word or chatters through the gaps between
        # them; there is no single rate that does both.
        glide = LEVEL_ATTACK if target > self._level else LEVEL_RELEASE
        self._level += (target - self._level) * glide

        seconds = FRAME_MS / 1000.0
        self._clock += self._rate(self._look) * seconds
        if self._leaving is not None:
            look, clock, left = self._leaving
            left -= 1
            self._leaving = (
                (look, clock + self._rate(look) * seconds, left)
                if left > 0 else None
            )
        self.update()

    def _rate(self, look):
        """How fast a look's clock runs: its own tuning, the dial, the voice.

        Hurrying it is the one handle every look has. Nine drawings with
        nothing in common geometrically - a lattice has rings to ripple, a
        constellation has none - and a per-look response would be nine
        separate inventions on top of a tuning that was already finished.
        """
        return (orbs.speed_of(look, self.orb_size)
                * self._speed_for(self.state)
                * (1.0 + VOICE_URGENCY * self._level))

    def _target_level(self):
        """How loud it is, before smoothing. Only listening has an answer."""
        if self.state == "recording":
            level = max(0.0, min(1.0, self.level_getter()))
            return level ** LEVEL_CURVE
        return 0.0

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        cx, cy = self._centre()
        left = cx - self.orb_size / 2
        top = cy - self.orb_size / 2

        self._paint_tile(painter, cx, cy)
        if self._leaving is not None:
            look, clock, remaining = self._leaving
            fading = remaining / CROSSFADE_FRAMES
            paint_look(painter, left, top, self.orb_size, look, clock, fading)
            paint_look(painter, left, top, self.orb_size, self._look,
                       self._clock, 1.0 - fading)
        else:
            paint_look(painter, left, top, self.orb_size, self._look,
                       self._clock)
        painter.end()

    def _paint_tile(self, painter, cx, cy):
        """The disc the sash turns over, plus a soft shadow beneath it.

        Not in the drawing this was taken from, which sits on a page whose
        colour it already knows. A desktop overlay lands on anything, and
        light-grey dots on a white window are not there at all.
        """
        radius = self.orb_size / 2

        # Cheap drop shadow: a few offset discs at low alpha. Qt's graphics
        # effect would need a second render pass for the same look.
        painter.setPen(Qt.NoPen)
        for i, alpha in enumerate((16, 12, 8)):
            spread = (i + 1) * 3
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawEllipse(
                QPointF(cx, cy + 3), radius + spread, radius + spread
            )

        gradient = QLinearGradient(0.0, cy - radius, 0.0, cy + radius)
        gradient.setColorAt(0.0, QColor(*TILE_TOP_RGB, TILE_ALPHA))
        gradient.setColorAt(1.0, QColor(*TILE_BOTTOM_RGB, TILE_ALPHA))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

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
