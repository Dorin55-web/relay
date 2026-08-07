"""Nine looks for the orb, from thinking-orbs.

Ported from https://github.com/Jakubantalik/thinking-orbs (MIT - see
THIRD-PARTY-NOTICES.md), redrawn in QPainter. None of the original ships here:
it is React and 2D canvas, and running it would have meant PySide6-Addons and
a Chromium subprocess for a sixty-four pixel dot. The drawings are theirs, so
their licence travels with them.

    dots, lines = frame("composing", 64, clock)

`clock` is the look's own time, which the caller advances at `speed_of(look,
size)` times its speed dial. Coordinates are pixels inside a `size` x `size`
box; ink is 0..1 with 0 darkest, and the painter mirrors it onto the tile.
"""

from . import lattice, morph, paths, web
from .blend import blend
from .presets import (LOOK_BLURBS, LOOKS, MAX_SIZE, MAX_SPEED, MIN_SIZE,
                      MIN_SPEED, TUNED_MAX, TUNED_MIN, TUNED_SIZES,
                      clamp_size, clamp_speed, resolve, tuned_size)

_DRAW = {
    "globe": lattice.globe,
    "rubik": lattice.rubik,
    "wave": lattice.wave,
    "orbits": paths.orbits,
    "braid": paths.braid,
    "ribbon": paths.ribbon,
    "ring": paths.ribbon,      # same painter; face_on is what tells them apart
    "web": web.web,
    "morph": morph.morph,
}


def frame(look, size, clock):
    """One frame of a look at any size, as (dots, lines), unsorted.

    Between the two tuned sizes the tuning itself is interpolated, so the mark
    is a real design at every step rather than one of the two stretched.

    Outside them it is drawn at the nearer tuning and magnified. Carrying the
    interpolation on past the ends is not an option: the multipliers grow with
    size faster than linearly, and at 120 pixels the sash would resolve to
    nine thousand dots - forty times the frame cost, for a mark nobody could
    read anyway. Magnifying keeps the design exactly as it was tuned, only
    larger, which is what someone dragging a size slider is asking for.

    The floor under the dot radii is applied here rather than in each drawing,
    which is where the original keeps it too - in its shared painter. It is
    not a detail: at twenty pixels the sash puts seventy-three of its dots
    under a third of a pixel, and without the floor they antialias down to a
    grey film instead of drawing as dots. It goes on last, after any
    magnification, because it is a floor in real pixels.
    """
    drawn_at = tuned_size(size)
    mode, _speed, opts = resolve(look, drawn_at)
    dots, lines = _DRAW[mode](drawn_at, clock, opts)
    floor = opts.get("r_min", 0.3)

    scale = size / drawn_at
    if scale != 1.0:
        dots = [(x * scale, y * scale, z, r * scale, ink, a)
                for x, y, z, r, ink, a in dots]
        lines = [(x1 * scale, y1 * scale, x2 * scale, y2 * scale,
                  ink, a, max(0.6, w * scale))
                 for x1, y1, x2, y2, ink, a, w in lines]

    return [d if d[3] >= floor else (d[0], d[1], d[2], floor, d[4], d[5])
            for d in dots], lines


def speed_of(look, size):
    """How fast this look's clock runs, before the user's dial.

    From the tuning it is drawn at, so magnifying the mark does not also
    change how fast it moves.
    """
    return resolve(look, tuned_size(size))[1]


def is_look(name):
    return name in LOOKS


__all__ = [
    "LOOKS", "LOOK_BLURBS", "MAX_SIZE", "MAX_SPEED", "MIN_SIZE", "MIN_SPEED",
    "TUNED_MAX", "TUNED_MIN", "TUNED_SIZES", "blend", "clamp_size",
    "clamp_speed", "frame", "is_look", "resolve", "speed_of", "tuned_size",
]
