"""Shared primitives every look is built out of.

Ported from thinking-orbs (MIT - see THIRD-PARTY-NOTICES.md). Honestly 3D:
points are rotated, depth-shaded and sorted back to front. Depth is carried by
dot size and ink weight alone, which is what lets the whole thing be plain
filled circles - no blur, no gradients, nothing that would need a second
render pass.

A dot is (x, y, z, radius, ink, alpha) and a line is
(x1, y1, x2, y2, ink, alpha, width). Coordinates are pixels inside a
`size` x `size` box, `z` exists only to sort by, and `ink` is 0..1 with 0
darkest - the convention the drawing was designed in, on paper. Relay draws on
a dark tile, so the painter mirrors it.
"""

import math


def js_round(x):
    """Round halves upward, the way the drawings were written.

    Python rounds halves to even, so `round(2.5)` is 2 here and 3 there. Every
    count in these looks goes through a rounding step, and the sash's tuning
    lands on exactly 2.5 strands - taking it down instead of up cost it a
    third of them, and the only sign was that it looked a little thin.
    """
    return math.floor(x + 0.5)


def lerp(a, b, f):
    return a + (b - a) * f


def frac(x):
    return x - math.floor(x)


def hash_d(a, b):
    """Deterministic pseudo-random in [0, 1).

    The looks are rebuilt from scratch every frame rather than kept in state,
    so anything that needs to look random has to come out the same way every
    time it is asked - a seeded generator would drift between frames.
    """
    h = math.sin(a * 12.9898 + b * 78.233) * 43758.5453
    return h - math.floor(h)


def vnoise(x, y):
    """Value noise on a 2D lattice - smooth, deterministic, cheap."""
    xi = math.floor(x)
    yi = math.floor(y)
    fx = x - xi
    fy = y - yi
    fx = fx * fx * (3 - 2 * fx)
    fy = fy * fy * (3 - 2 * fy)
    a = hash_d(xi, yi)
    b = hash_d(xi + 1, yi)
    c = hash_d(xi, yi + 1)
    d = hash_d(xi + 1, yi + 1)
    return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy


def fib_dir(i, n):
    """Stable directions on a unit sphere.

    Spread by the golden angle, which is the one arrangement that never falls
    into visible rows however many points are used.
    """
    golden = math.pi * (3 - math.sqrt(5))
    y = 1 - (2 * (i + 0.5)) / n
    rad = math.sqrt(max(0.0, 1 - y * y))
    a = i * golden
    return rad * math.cos(a), y, rad * math.sin(a)


def angle_delta(a, b):
    """Shortest signed angular distance, wrapped to (-pi, pi]."""
    return math.atan2(math.sin(a - b), math.cos(a - b))


def make_proj(yaw, tilt, cx, cy, scale):
    """Spin, lean, and flatten. Orthographic on purpose.

    Perspective at these sizes only distorts the silhouette; the shading
    already carries all the depth the eye gets.
    """
    st = math.sin(tilt)
    ct = math.cos(tilt)
    sy = math.sin(yaw)
    cyw = math.cos(yaw)

    def project(x, y, z):
        x1 = x * cyw + z * sy
        z1 = -x * sy + z * cyw
        y1 = y * ct - z1 * st
        z2 = y * st + z1 * ct
        return cx + x1 * scale, cy - y1 * scale, z2

    return project


def radius_scale(size, pow_=0.6):
    """Dot radii were tuned on a 300px frame.

    They shrink sub-linearly from there - a lower power means they shrink
    less, which is the only thing keeping a twenty pixel mark legible.
    """
    return (size / 300.0) ** pow_
