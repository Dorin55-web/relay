"""An outline cycling circle to triangle to square - "shaping".

Ported from thinking-orbs (MIT - see THIRD-PARTY-NOTICES.md). Each shape is a
closed path measured by arc length, starting at top centre. Every frame the
two neighbouring paths are blended and the dots are laid out EVENLY along the
blend, so the spacing stays uniform at every instant - during the change as
much as during the hold. Walking one shape's dots towards the other's instead
would bunch them in the corners.
"""

import math

from .core import js_round

HOLD = 1.4
CHANGE = 0.9
SEGMENT = HOLD + CHANGE
# How finely the blended outline is measured before dots are spaced along it.
SAMPLES = 160


def _smooth(x):
    return x * x * (3 - 2 * x)


def _poly(verts):
    """A closed polygon, addressed by fraction of its perimeter."""
    lengths = []
    total = 0.0
    for i, a in enumerate(verts):
        b = verts[(i + 1) % len(verts)]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        lengths.append(length)
        total += length

    def at(f):
        target = f * total
        i = 0
        while target > lengths[i] and i < len(verts) - 1:
            target -= lengths[i]
            i += 1
        a = verts[i]
        b = verts[(i + 1) % len(verts)]
        ff = min(1.0, target / lengths[i]) if lengths[i] else 0.0
        return a[0] + (b[0] - a[0]) * ff, a[1] + (b[1] - a[1]) * ff

    return at


def _circle(f):
    a = -math.pi / 2 + f * 2 * math.pi
    return math.cos(a) * 0.24, math.sin(a) * 0.24


_TRIANGLE = _poly(((0.0, -0.26), (0.24, 0.16), (-0.24, 0.16)))
# Five vertices rather than four, so this path also starts at top centre and
# the dots do not slide round the outline as the shapes change.
_SQUARE = _poly(((0, -0.2), (0.2, -0.2), (0.2, 0.2), (-0.2, 0.2), (-0.2, -0.2)))
_CYCLE = (_circle, _TRIANGLE, _SQUARE)


def morph(size, t, o):
    count = len(_CYCLE)
    tc = t % (SEGMENT * count)
    k = int(tc // SEGMENT)
    local = tc - k * SEGMENT
    m = _smooth((local - HOLD) / CHANGE) if local > HOLD else 0.0
    spread = o.get("spread", 1)

    path_a = _CYCLE[k]
    path_b = _CYCLE[(k + 1) % count]
    points = []
    for i in range(SAMPLES):
        f = i / SAMPLES
        ax, ay = path_a(f)
        bx, by = path_b(f)
        points.append((((ax + (bx - ax) * m) * spread),
                       ((ay + (by - ay) * m) * spread)))

    lengths = []
    total = 0.0
    for i, a in enumerate(points):
        b = points[(i + 1) % SAMPLES]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        lengths.append(length)
        total += length

    # The radius comes from the size knob alone; the count sets the gaps.
    # Formed shapes breathe a little, the whole outline together.
    n = max(6, js_round(34 * o.get("icon_d", 1)))
    radius = max(0.35, o.get("r_dot", 0.021) * 1.35 * spread * size)
    pulse = 1 + 0.02 * math.sin(local * 3.1)

    dots = []
    centre = size / 2
    seg = 0
    acc = 0.0
    for i in range(n):
        target = (i / n) * total
        while acc + lengths[seg] < target and seg < SAMPLES - 1:
            acc += lengths[seg]
            seg += 1
        a = points[seg]
        b = points[(seg + 1) % SAMPLES]
        f = min(1.0, (target - acc) / lengths[seg]) if lengths[seg] else 0.0
        x = (a[0] + (b[0] - a[0]) * f) * pulse
        y = (a[1] + (b[1] - a[1]) * f) * pulse
        dots.append((centre + x * size, centre + y * size, 0.0,
                     radius, 0.1, 1.0))
    return dots, ()
