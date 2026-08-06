"""Choosing what the orb draws in each of its three states.

Nine looks, each animating in its own tile, and three slots to put them in:
resting, listening, working. Clicking a tile assigns it to whichever slot is
selected and the orb on screen changes immediately - the point of a window
full of moving thumbnails is that you can see the choice rather than read it.

Nothing is written until Save. Closing without it puts back whatever was in
config.json when the window opened, so trying all nine costs nothing.

Runs on the same event loop as the orb, and is created from the menu action,
which is already on the GUI thread.
"""

import copy

from PySide6.QtCore import QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QButtonGroup, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSlider, QVBoxLayout, QWidget)

from . import orbs
from .config import SLOT_LABELS, ORB_SLOTS, normalise_orb, save_orb
from .overlay import paint_look
from .window import FramelessWindow, TitleBar

# The orb's palette, so the two read as one program.
BG = "#0e1015"
PANEL = "#15181f"
LINE = "#252a33"
TEXT = "#e8ebf0"
MUTED = "#8b93a1"

TILE_W = 118
TILE_H = 132
STAGE_H = 96             # the part of a tile the orb is drawn in
COLUMNS = 3

# Twenty a second, not thirty. Nine looks at once is nine times the drawing,
# and this window is open for a minute at a time rather than all day.
FRAME_MS = 50

_window = None


class LookTile(QWidget):
    """One look, animating, with its name under it."""

    picked = Signal(str)

    def __init__(self, look, size_px):
        super().__init__()
        self.look = look
        self.size_px = size_px
        self.clock = 0.0
        self.chosen = False
        self.setFixedSize(TILE_W, TILE_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(orbs.LOOK_BLURBS[look])

    def advance(self, seconds, speed):
        self.clock += orbs.speed_of(self.look, self.size_px) * speed * seconds

    def set_size(self, size_px):
        if size_px != self.size_px:
            self.size_px = size_px
            self.clock = 0.0

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.picked.emit(self.look)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        frame = QRectF(0.5, 0.5, TILE_W - 1, TILE_H - 1)
        path = QPainterPath()
        path.addRoundedRect(frame, 8, 8)
        painter.setPen(QPen(QColor(TEXT if self.chosen else LINE),
                            2 if self.chosen else 1))
        painter.setBrush(QColor(PANEL))
        painter.drawPath(path)

        # Nothing behind the mark, because there is nothing behind it on the
        # desktop either - the orb draws its dots and no backing at all.
        cx = TILE_W / 2
        cy = STAGE_H / 2 + 6
        radius = self.size_px / 2
        paint_look(painter, cx - radius, cy - radius, self.size_px,
                   self.look, self.clock)

        painter.setPen(QColor(TEXT if self.chosen else MUTED))
        painter.drawText(QRectF(0, STAGE_H + 8, TILE_W, TILE_H - STAGE_H - 8),
                         Qt.AlignHCenter | Qt.AlignTop, self.look)
        painter.end()


class LookPicker(FramelessWindow):
    border_colour = LINE

    def __init__(self, settings, on_change):
        super().__init__("Relay - Orb")
        self.on_change = on_change
        self.original = normalise_orb(settings)
        self.settings = copy.deepcopy(self.original)
        self.slot = ORB_SLOTS[0]
        self.saved = False
        self._loading = False    # guard: filling the controls is not an edit

        self._build()
        self.setStyleSheet(STYLESHEET)
        self._show_slot()
        self._show_hint()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_MS)

    # --- layout -----------------------------------------------------------

    def _build(self):
        self.slot_buttons = QButtonGroup(self)
        self.slot_buttons.setExclusive(True)
        self.slot_tabs = {}
        slots = QHBoxLayout()
        slots.setSpacing(6)
        for index, slot in enumerate(ORB_SLOTS):
            button = QPushButton(SLOT_LABELS[slot])
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(
                lambda checked=False, s=slot: self._choose_slot(s))
            self.slot_buttons.addButton(button)
            self.slot_tabs[slot] = button
            slots.addWidget(button, 1)

        self.tiles = {}
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, look in enumerate(orbs.LOOKS):
            tile = LookTile(look, min(self.settings["size"], orbs.TUNED_MAX))
            tile.picked.connect(self._choose_look)
            self.tiles[look] = tile
            grid.addWidget(tile, index // COLUMNS, index % COLUMNS)

        self.speed = QSlider(Qt.Horizontal)
        # The slider counts in hundredths, so the whole 0.1x to 3x range is
        # reachable in steps small enough that no two feel like the same one.
        self.speed.setRange(int(orbs.MIN_SPEED * 100),
                            int(orbs.MAX_SPEED * 100))
        self.speed.setSingleStep(5)
        self.speed.setPageStep(25)
        self.speed.valueChanged.connect(self._speed_changed)
        self.speed_label = QLabel()
        self.speed_label.setObjectName("value")
        self.speed_label.setFixedWidth(52)

        speed_row = QHBoxLayout()
        speed_row.setSpacing(10)
        speed_row.addWidget(self._caption("SPEED"))
        speed_row.addWidget(self.speed, 1)
        speed_row.addWidget(self.speed_label)

        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(orbs.MIN_SIZE, orbs.MAX_SIZE)
        self.size.setSingleStep(1)
        self.size.setPageStep(8)
        self.size.setToolTip(
            f"{orbs.MIN_SIZE} to {orbs.MAX_SIZE} pixels. Between "
            f"{orbs.TUNED_MIN} and {orbs.TUNED_MAX} the drawing is retuned as "
            f"it grows; past that it is magnified."
        )
        self.size.setValue(self.settings["size"])
        self.size.valueChanged.connect(self._size_changed)
        self.size_label = QLabel(f"{self.settings['size']} px")
        self.size_label.setObjectName("value")
        self.size_label.setFixedWidth(52)

        size_row = QHBoxLayout()
        size_row.setSpacing(10)
        size_row.addWidget(self._caption("SIZE"))
        size_row.addWidget(self.size, 1)
        size_row.addWidget(self.size_label)

        close = QPushButton("Close")
        close.clicked.connect(self.close)
        save = QPushButton("Save")
        # Set on the widget rather than through the sheet: on Windows the
        # native button style wins over an ID selector and the fill never
        # appears.
        save.setStyleSheet(
            f"QPushButton {{ background: {TEXT}; color: {BG}; border: none;"
            f" border-radius: 6px; padding: 7px 18px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: #ffffff; }}"
        )
        save.clicked.connect(self._save)

        self.hint = self._hint("")
        footer = QHBoxLayout()
        footer.addWidget(self.hint)
        footer.addStretch(1)
        footer.addWidget(close)
        footer.addWidget(save)

        body = QVBoxLayout()
        body.setContentsMargins(18, 6, 18, 18)
        body.setSpacing(14)
        body.addLayout(slots)
        body.addLayout(grid)
        body.addLayout(speed_row)
        body.addLayout(size_row)
        body.addStretch(1)
        body.addLayout(footer)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(self.windowTitle(), self.showMinimized,
                                 self.close))
        outer.addLayout(body, 1)

        # The window never resizes, so it has to be measured against the
        # longest thing the hint will ever say - not against whichever one it
        # happens to be showing when the layout is first asked how big it is.
        # A minimum width, not just the text: the footer has a stretch in it,
        # and the label is what gets squeezed when the layout is short.
        self.hint.setText(max(self._hints(), key=len))
        self.hint.setMinimumWidth(self.hint.sizeHint().width())
        self.setFixedSize(self.sizeHint())

    def _caption(self, text):
        label = QLabel(text)
        label.setObjectName("field")
        # Wide enough for the letter-spacing the sheet adds, which Qt does not
        # count when it measures the text - at 46 the S of SPEED was cut off.
        label.setFixedWidth(58)
        return label

    def _hint(self, text):
        label = QLabel(text)
        label.setObjectName("hint")
        return label

    # --- choices ----------------------------------------------------------

    def _choose_slot(self, slot):
        self.slot = slot
        # The tab is normally already checked by the click that got here, but
        # not when something else selects a slot - and a tab bar that does not
        # say which one is showing is worse than no tab bar.
        self.slot_tabs[slot].setChecked(True)
        self._show_slot()

    def _choose_look(self, look):
        self.settings[self.slot]["look"] = look
        self._show_slot()
        self._apply()

    def _size_changed(self, value):
        size = orbs.clamp_size(value)
        self.settings["size"] = size
        self.size_label.setText(f"{size} px")
        self._show_hint()
        # A tile is 118 pixels wide, so past the tuned maximum the preview
        # stops growing. Nothing is hidden by that: above it the drawing IS
        # the 64px one magnified, so the tile shows the same design, and the
        # orb on screen is following the slider anyway.
        for tile in self.tiles.values():
            tile.set_size(min(size, orbs.TUNED_MAX))
        self._apply()

    def _hints(self):
        """Everything the hint can say: below the span, inside it, above it.

        Between the two tuned sizes the mark is retuned as it grows - fewer
        dots drawn larger as it shrinks - and outside them there is nothing to
        retune towards, so it is simply magnified. That is a real difference in
        what the slider is doing, and invisible unless something says so.
        """
        return (
            f"Below {orbs.TUNED_MIN} px: the smallest drawing, shrunk",
            "Pick one for each state above",
            f"Above {orbs.TUNED_MAX} px: the largest drawing, magnified",
        )

    def _show_hint(self):
        below, inside, above = self._hints()
        size = self.settings["size"]
        self.hint.setText(below if size < orbs.TUNED_MIN
                          else above if size > orbs.TUNED_MAX
                          else inside)

    def _speed_changed(self, value):
        if self._loading:
            return
        self.settings[self.slot]["speed"] = orbs.clamp_speed(value / 100.0)
        self.speed_label.setText(f"{value / 100:.2f}x")
        self._apply()

    def _show_slot(self):
        """Point the controls at the slot now selected."""
        current = self.settings[self.slot]
        for look, tile in self.tiles.items():
            was = tile.chosen
            tile.chosen = look == current["look"]
            if tile.chosen != was:
                tile.update()
        self._loading = True
        self.speed.setValue(int(round(current["speed"] * 100)))
        self._loading = False
        self.speed_label.setText(f"{current['speed']:.2f}x")

    def _apply(self):
        if self.on_change is None:
            return
        try:
            self.on_change(copy.deepcopy(self.settings))
        except Exception as exc:
            print(f"[looks] could not apply: {exc}")

    def _save(self):
        try:
            self.settings = save_orb(self.settings)
            self.saved = True
        except OSError as exc:
            print(f"[looks] could not save: {exc}")
            return
        self.close()

    # --- animation --------------------------------------------------------

    def _tick(self):
        seconds = FRAME_MS / 1000.0
        # Every tile at the speed of the slot it would go into if clicked, so
        # what a tile shows is what choosing it would give you.
        speed = self.settings[self.slot]["speed"]
        for tile in self.tiles.values():
            tile.advance(seconds, speed)
            tile.update()

    # --- lifecycle --------------------------------------------------------

    def closeEvent(self, event):
        global _window
        self._timer.stop()
        if not self.saved:
            # Put back what was on screen when this opened. Every click has
            # been live until now, so without this a look tried and rejected
            # would simply stay.
            self.settings = copy.deepcopy(self.original)
            self._apply()
        _window = None
        super().closeEvent(event)


STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QWidget#shell {{
    background: {BG};
    border: 1px solid {LINE};
    border-radius: 8px;
}}
QLabel#title {{ color: {MUTED}; font-size: 12px; }}
QLabel#hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#field {{ color: {MUTED}; font-size: 11px; letter-spacing: 1px; }}
QLabel#value {{ color: {TEXT}; font-size: 12px; }}
QPushButton#chrome {{
    background: transparent;
    border: none;
    border-radius: 5px;
    color: {MUTED};
    font-size: 13px;
    padding: 0;
}}
QPushButton#chrome:hover {{ background: {LINE}; color: {TEXT}; }}
QPushButton {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background: {LINE}; }}
QPushButton:checked {{
    background: {LINE};
    border: 1px solid #3d4451;
    color: {TEXT};
}}
QSlider::groove:horizontal {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 3px;
    height: 6px;
}}
QSlider::sub-page:horizontal {{
    background: #3d4451;
    border: 1px solid {LINE};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {TEXT};
    border-radius: 6px;
    width: 12px;
    margin: -5px 0;
}}
"""


def open_picker(settings, on_change):
    """Show the picker, or raise the one already open."""
    global _window
    if _window is not None and _window.isVisible():
        _window.raise_()
        _window.activateWindow()
        return _window
    _window = LookPicker(settings, on_change)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
