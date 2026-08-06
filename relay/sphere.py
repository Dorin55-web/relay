"""A globe drawn out of dots, with your voice rolling through its rings.

The shape is a sphere sampled along rings of latitude and projected flat, so
the dots crowd towards the edge the way points on a ball do. Depth is carried
by brightness and dot size rather than by perspective: the far side is dimmer
and smaller, which reads as roundness at sizes where perspective would not.

Nothing moves at rest. The rings only ripple while there is a level to ripple
them, so the thing sitting on screen all day is a still object.
"""

import math

# Rings of latitude, pole to pole. Enough to read as a sphere, few enough that
# a 48px orb does not turn into a smudge.
RINGS = 13
# Dots on the widest ring. Every other ring gets its share of these, scaled by
# how wide it is, so the spacing along each ring stays about even.
EQUATOR_DOTS = 24

# Every ring is turned a little further round than the one below it, by an
# angle that never repeats. Without it each ring starts at the same longitude
# and the dots line up into vertical columns - the sphere reads as a grid
# rather than as a ball.
# Small on purpose. A golden-angle twist breaks the columns completely but
# scatters the rings with them, and the rings are what the wave travels along -
# scramble those and there is nothing left to watch.
RING_TWIST = 0.35

# How far a ring is pushed out at full level, as a fraction of the radius.
WAVE_DEPTH = 0.22
# Position alone is not enough. At 48px a fifth of the radius is four pixels,
# which nobody sees; a ring riding the wave also brightens and swells, and that
# is what actually reads as the orb listening rather than resting.
WAVE_LIFT = 0.95       # extra brightness at full level, taking the
                       # front of a riding ring to about full white
WAVE_SWELL = 0.5       # extra dot size at full level
# Rings the wave spans. Below one whole wavelength it reads as a wobble rather
# than as something travelling.
WAVE_LENGTH = 5.0

# Dot radius as a fraction of the sphere's. The first attempt at these was
# three times larger and the dots merged into a solid disc: they have to stay
# well under half the gap between neighbours, which at the equator is about
# 0.14 of the radius.
MIN_DOT = 0.014        # at the back
MAX_DOT = 0.038        # and at the front
# The resting sphere is deliberately dim, front dots included. Leaving them at
# full brightness looks fine on its own but leaves the wave nowhere to go: a
# ring riding it can then only grow, not light up, and at 48px growing is four
# pixels nobody sees. Half-bright at rest is what makes listening read.
BACK_ALPHA = 0.08      # how much of the accent colour the far side keeps
FRONT_ALPHA = 0.52     # and the near side, at rest


def rings_for(diameter):
    """Rings and dots for a globe this many pixels across.

    Two hundred dots on a sixteen pixel icon are sub-pixel smears that add up
    to a grey blob. Below the sizes the orb is drawn at, the mark has to lose
    detail rather than keep it and go muddy - the same reason the old mark
    dropped its glow under 32px.
    """
    if diameter >= 40:
        return RINGS, EQUATOR_DOTS
    if diameter >= 28:
        return 9, 16
    if diameter >= 20:
        return 7, 12
    return 5, 8


def _ring_latitudes(rings=None):
    """Ring centres, evenly spaced in latitude rather than in height.

    Even spacing in height would bunch the rings at the poles once projected;
    even in latitude keeps them looking regular from the front, which is the
    only side anyone sees.
    """
    rings = rings or RINGS
    return [
        -math.pi / 2 + math.pi * (i + 0.5) / rings
        for i in range(rings)
    ]


def dots(level_at, phase=0.0, rotation=0.0, rings=None, equator=None):
    """Every dot of the globe, as (x, y, depth, ring) in a unit circle.

    `x` and `y` land in [-1, 1]. `depth` runs 0 at the back to 1 at the front,
    and `level` is what that ring is riding, 0..1 - both go into brightness and
    size. `level_at(ring)` returns 0..1; pass one that always returns 0 for a
    sphere at rest.
    """
    rings = rings or RINGS
    equator = equator or EQUATOR_DOTS
    out = []
    for index, latitude in enumerate(_ring_latitudes(rings)):
        cos_lat = math.cos(latitude)
        # Ring radius, pushed out by whatever the caller says is happening at
        # this latitude. The push is along the surface normal, so a ring near
        # the pole moves less in x than one at the equator - which is what
        # makes the wave look like it is travelling over a ball.
        level = max(0.0, min(1.0, level_at(index)))
        push = 1.0 + WAVE_DEPTH * level
        ring_radius = cos_lat * push
        y = math.sin(latitude) * push

        count = max(4, int(round(equator * cos_lat)))
        twist = index * RING_TWIST
        for step in range(count):
            longitude = 2 * math.pi * step / count + rotation + twist
            x = ring_radius * math.cos(longitude)
            # Towards the viewer is +z; fold it into 0..1.
            z = ring_radius * math.sin(longitude)
            depth = (z / max(1e-6, ring_radius) + 1.0) / 2.0 if ring_radius else 0.5
            out.append((x, y, depth, index, level))
    return out


def wave(level_history, phase):
    """A level_at function: the voice, rolling from one pole to the other.

    Each ring reads a different point of the history, so the row of levels the
    meter used to show side by side is now wrapped around a ball. `phase`
    advances the reading point, which is what makes it travel.
    """
    if not level_history:
        return lambda ring: 0.0
    count = len(level_history)

    def level_at(ring):
        # Which sample this ring is showing, walking the history as the phase
        # advances. Wrapped, so the wave leaves one pole as it enters the other.
        position = ring / max(1, RINGS - 1) * WAVE_LENGTH - phase
        index = int(position) % count
        blend = position - math.floor(position)
        here = level_history[index]
        following = level_history[(index + 1) % count]
        return here + (following - here) * blend

    return level_at


def dot_radius(depth, level=0.0, spread=1.0):
    """Front dots are bigger, and so are the ones riding the wave.

    `spread` is how much room each dot has compared with the full-size globe.
    A sixteen pixel icon carries a third of the dots, so each may be three
    times fatter - without it the fractions here work out well under a pixel
    and the mark renders as a smudge.
    """
    base = MIN_DOT + (MAX_DOT - MIN_DOT) * depth
    return base * spread * (1.0 + WAVE_SWELL * level)


def dot_alpha(depth, level=0.0):
    """And brighter. The far side never vanishes, or the ball reads as a bowl."""
    base = BACK_ALPHA + (FRONT_ALPHA - BACK_ALPHA) * depth
    return min(1.0, base * (1.0 + WAVE_LIFT * level))
