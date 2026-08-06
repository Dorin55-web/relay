"""A still globe drawn out of dots - the mark on the taskbar and the title bar.

The shape is a sphere sampled along rings of latitude and projected flat, so
the dots crowd towards the edge the way points on a ball do. Depth is carried
by brightness and dot size rather than by perspective: the far side is dimmer
and smaller, which reads as roundness at sizes where perspective would not.

This is the icon only, and an icon never moves. The orb on screen is the sash
in `ribbon`, which is a different drawing for a different job - the sash was
tuned at 64 pixels and frozen at 16 it is five hundred sub-pixel dots and no
shape at all, which is exactly what an icon cannot be.
"""

import math

# Rings of latitude, pole to pole. Enough to read as a sphere, few enough that
# a small tile does not turn into a smudge.
RINGS = 13
# Dots on the widest ring. Every other ring gets its share of these, scaled by
# how wide it is, so the spacing along each ring stays about even.
EQUATOR_DOTS = 24

# Every ring is turned a little further round than the one below it. Without
# it each ring starts at the same longitude and the dots line up into vertical
# columns - the sphere reads as a grid rather than as a ball.
RING_TWIST = 0.35

# Dot radius as a fraction of the sphere's. The first attempt at these was
# three times larger and the dots merged into a solid disc: they have to stay
# well under half the gap between neighbours, which at the equator is about
# 0.14 of the radius.
MIN_DOT = 0.014        # at the back
MAX_DOT = 0.038        # and at the front
BACK_ALPHA = 0.08      # how much of the accent colour the far side keeps
FRONT_ALPHA = 0.52     # and the near side


def rings_for(diameter):
    """Rings and dots for a globe this many pixels across.

    Two hundred dots on a sixteen pixel icon are sub-pixel smears that add up
    to a grey blob. Below the sizes the mark is drawn at, it has to lose
    detail rather than keep it and go muddy.
    """
    if diameter >= 40:
        return RINGS, EQUATOR_DOTS
    if diameter >= 28:
        return 9, 16
    if diameter >= 20:
        return 7, 12
    return 5, 8


def _ring_latitudes(rings):
    """Ring centres, evenly spaced in latitude rather than in height.

    Even spacing in height would bunch the rings at the poles once projected;
    even in latitude keeps them looking regular from the front, which is the
    only side anyone sees.
    """
    return [
        -math.pi / 2 + math.pi * (i + 0.5) / rings
        for i in range(rings)
    ]


def dots(rings=None, equator=None):
    """Every dot of the globe, as (x, y, depth) in a unit circle.

    `x` and `y` land in [-1, 1], and `depth` runs 0 at the back to 1 at the
    front - it feeds both brightness and size.
    """
    rings = rings or RINGS
    equator = equator or EQUATOR_DOTS
    out = []
    for index, latitude in enumerate(_ring_latitudes(rings)):
        cos_lat = math.cos(latitude)
        y = math.sin(latitude)

        count = max(4, int(round(equator * cos_lat)))
        twist = index * RING_TWIST
        for step in range(count):
            longitude = 2 * math.pi * step / count + twist
            x = cos_lat * math.cos(longitude)
            # Towards the viewer is +z; fold it into 0..1.
            z = cos_lat * math.sin(longitude)
            depth = (z / max(1e-6, cos_lat) + 1.0) / 2.0 if cos_lat else 0.5
            out.append((x, y, depth))
    return out


def dot_radius(depth, spread=1.0):
    """Front dots are bigger.

    `spread` is how much room each dot has compared with the full-size globe.
    A sixteen pixel icon carries a third of the dots, so each may be three
    times fatter - without it the fractions here work out well under a pixel
    and the mark renders as a smudge.
    """
    return (MIN_DOT + (MAX_DOT - MIN_DOT) * depth) * spread


def dot_alpha(depth):
    """And brighter. The far side never vanishes, or the ball reads as a bowl."""
    return BACK_ALPHA + (FRONT_ALPHA - BACK_ALPHA) * depth
