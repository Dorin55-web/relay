"""One look turning into another by moving, rather than fading through it.

The nine drawings share no geometry - a lattice, a constellation and an
outline have nothing in common but being made of dots - so the first version
of this simply drew the outgoing one at falling opacity over the incoming one
at rising opacity. That works, and reads as two pictures overlaid for a third
of a second.

They are all dots, though, and dots can go somewhere. Here each dot of the old
mark is paired with a place in the new one and travels to it, so the orb
reorganises itself instead of dissolving.

Pairing is by angle around the centre. Nearest-neighbour would be the obvious
choice and is unaffordable - 566 dots against 484 is a quarter of a million
distance tests per frame - while pairing by the order the drawings happen to
emit dots in sends them across the mark at random and reads as noise. Sorting
both by angle costs n log n and gives every dot the partner nearest to the way
it is already facing, so nothing crosses the middle without reason.
"""

import math

# How far the dots are drawn towards the centre at the midpoint, as a fraction
# of their distance from it. Without this they slide along straight chords and
# the whole thing reads as a slide; with it the mark gathers and opens again,
# which is what makes it look like one object changing rather than two.
GATHER = 0.14

# Where the counts differ, the dots with no partner have to arrive or leave on
# their own. They do it in the half of the transition nearest their own end,
# so nothing appears in the middle out of nowhere.
FADE_SHARE = 0.55


def ease(f):
    """Smoothstep: starts and ends at rest, quickest in the middle."""
    f = max(0.0, min(1.0, f))
    return f * f * (3 - 2 * f)


def _by_angle(dots, size):
    centre = size / 2
    return sorted(dots, key=lambda d: math.atan2(d[1] - centre, d[0] - centre))


def _split(dots, count):
    """`count` dots spread evenly through the list, and every one left over.

    A partition, not two samples. Picking the travellers and the leftovers
    independently gives two overlapping selections, and the transition then
    does not start from the mark it is supposed to be leaving - it starts from
    some other arrangement of the same number of dots.
    """
    total = len(dots)
    if count >= total:
        return list(dots), []
    if count <= 0:
        return [], list(dots)
    # i * total // count is strictly increasing for count <= total, so these
    # are distinct indices and the two halves cover the list exactly once.
    taken = [False] * total
    for i in range(count):
        taken[i * total // count] = True
    return ([d for d, t in zip(dots, taken) if t],
            [d for d, t in zip(dots, taken) if not t])


def _lerp_dot(a, b, f, pull, size):
    centre = size / 2
    x = a[0] + (b[0] - a[0]) * f
    y = a[1] + (b[1] - a[1]) * f
    # The gather, applied about the centre so it never moves the mark sideways.
    x = centre + (x - centre) * pull
    y = centre + (y - centre) * pull
    return (
        x, y,
        a[2] + (b[2] - a[2]) * f,
        a[3] + (b[3] - a[3]) * f,
        a[4] + (b[4] - a[4]) * f,
        a[5] + (b[5] - a[5]) * f,
    )


def blend(size, leaving, arriving, f):
    """The mark part way from one look to another, as (dots, lines).

    `f` runs 0 at the old look to 1 at the new one, and is eased here rather
    than by the caller so every caller gets the same motion.
    """
    f = ease(f)
    pull = 1.0 - GATHER * math.sin(math.pi * f)

    old = _by_angle(leaving[0], size)
    new = _by_angle(arriving[0], size)
    if not old:
        return arriving[0], arriving[1]
    if not new:
        return leaving[0], leaving[1]

    # The shorter side travels whole; the longer one sends as many as the
    # shorter can receive and leaves the rest to fade on the spot.
    paired = min(len(old), len(new))
    from_old, stayed = _split(old, paired)
    to_new, unclaimed = _split(new, paired)
    travelling = [
        _lerp_dot(a, b, f, pull, size)
        for a, b in zip(from_old, to_new)
    ]

    # Those left over go in the half of the transition nearest their own end,
    # so nothing is ever seen to appear from nowhere in the middle.
    extra = []
    keep = max(0.0, 1.0 - f / FADE_SHARE)
    show = max(0.0, (f - (1.0 - FADE_SHARE)) / FADE_SHARE)
    for dots, fade in ((stayed, keep), (unclaimed, show)):
        if fade <= 0.0:
            continue
        for dot in dots:
            x = size / 2 + (dot[0] - size / 2) * pull
            y = size / 2 + (dot[1] - size / 2) * pull
            extra.append((x, y, dot[2], dot[3], dot[4], dot[5] * fade))

    # Only one look draws lines, and a line between two dots that are each on
    # their way somewhere else is a scribble. They fade with their own look
    # instead of being interpolated.
    lines = []
    for x1, y1, x2, y2, ink, alpha, width in leaving[1]:
        lines.append((x1, y1, x2, y2, ink, alpha * (1.0 - f), width))
    for x1, y1, x2, y2, ink, alpha, width in arriving[1]:
        lines.append((x1, y1, x2, y2, ink, alpha * f, width))

    return travelling + extra, lines
