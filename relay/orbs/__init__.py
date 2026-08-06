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
from .presets import (BLENDED_SIZE, LOOK_BLURBS, LOOKS, MAX_SPEED, MIN_SPEED,
                      SIZES, TUNED_SIZES, clamp_speed, nearest_size, resolve)

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
    """One frame of a look, as (dots, lines), unsorted.

    The floor under the dot radii is applied here rather than in each drawing,
    which is where the original keeps it too - in its shared painter. It is
    not a detail: at twenty pixels the sash puts seventy-three of its dots
    under a third of a pixel, and without the floor they antialias down to a
    grey film instead of drawing as dots.
    """
    mode, _speed, opts = resolve(look, size)
    dots, lines = _DRAW[mode](size, clock, opts)
    floor = opts.get("r_min", 0.3)
    return [d if d[3] >= floor else (d[0], d[1], d[2], floor, d[4], d[5])
            for d in dots], lines


def speed_of(look, size):
    """How fast this look's clock runs, before the user's dial."""
    return resolve(look, size)[1]


def is_look(name):
    return name in LOOKS


__all__ = [
    "BLENDED_SIZE", "LOOKS", "LOOK_BLURBS", "MAX_SPEED", "MIN_SPEED", "SIZES",
    "TUNED_SIZES", "clamp_speed", "frame", "is_look", "nearest_size",
    "resolve", "speed_of",
]
