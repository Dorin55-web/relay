"""A constellation wiring itself up - "connecting".

Ported from thinking-orbs (MIT - see THIRD-PARTY-NOTICES.md). Nodes drift over
the sphere under slow noise; any pair closer than the threshold grows an edge
between them, and bright packets run along pairs that get re-picked as they
go. The only look that draws lines, which is why every drawing here returns a
list of them even though the other eight always return none.
"""

import math

from .core import fib_dir, frac, hash_d, lerp, make_proj, radius_scale, vnoise


def web(size, t, o):
    cx = cy = size / 2
    radius = (size / 2) * 0.8 * o.get("spread", 1)
    # The projector carries the radius as its scale, so the node vectors stay
    # unit length and the distances below are in unit-sphere space.
    project = make_proj(t * 0.12, 0.32, cx, cy, radius)
    rs = radius_scale(size, o.get("rs_pow", 0.6))

    node_n = o.get("node_n", 30)
    thr = o.get("thr", 0.72)
    node_r = o.get("node_r", 1.4)
    node_r_depth = o.get("node_r_depth", 1.8)
    line_w = max(0.6, o.get("line_w", 0.8) * rs)

    nodes = []
    for i in range(node_n):
        dx, dy, dz = fib_dir(i, node_n)
        x = dx + 0.3 * (vnoise(i * 0.31 + 9, t * 0.24) - 0.5) * 2
        y = dy + 0.3 * (vnoise(i * 0.53 + 27, t * 0.21) - 0.5) * 2
        z = dz + 0.3 * (vnoise(i * 0.77 + 55, t * 0.27) - 0.5) * 2
        length = math.sqrt(x * x + y * y + z * z) or 1.0
        nodes.append((x / length, y / length, z / length))

    screen = [project(*node) for node in nodes]

    lines = []
    for i in range(node_n):
        ax, ay, az = nodes[i]
        x1, y1, z1 = screen[i]
        for j in range(i + 1, node_n):
            bx, by, bz = nodes[j]
            dx = ax - bx
            dy = ay - by
            dz = az - bz
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if dist >= thr:
                continue
            x2, y2, z2 = screen[j]
            depth = ((z1 + z2) / 2 + 1) / 2
            lines.append((x1, y1, x2, y2, 0.42,
                          (1 - dist / thr) * (0.3 + 0.55 * depth), line_w))

    dots = []
    for i in range(node_n):
        px, py, z = screen[i]
        depth = (z + 1) / 2
        pulse = 1 + 0.25 * math.sin(t * 1.4 + i * 2.7)
        dots.append((px, py, z, (node_r + node_r_depth * depth) * pulse * rs,
                     0.55 - 0.45 * depth, 1.0))

    # The packets. Which pair each one runs between is re-picked every time it
    # arrives, from a hash of the leg number - deterministic, so the same
    # frame always draws the same thing however often it is asked for.
    signals = o.get("signals", 5)
    for s in range(signals):
        leg = math.floor(t * 0.55 + s * 7.31)
        a = int(hash_d(leg, s * 3.1 + 1.7) * node_n)
        b = int(hash_d(leg, s * 5.7 + 4.2) * node_n)
        if a == b:
            continue
        f = frac(t * 0.55 + s * 7.31)
        x = lerp(nodes[a][0], nodes[b][0], f)
        y = lerp(nodes[a][1], nodes[b][1], f)
        z = lerp(nodes[a][2], nodes[b][2], f)
        length = max(1e-6, math.sqrt(x * x + y * y + z * z))
        px, py, zr = project(x / length, y / length, z / length)
        depth = (zr + 1) / 2
        dots.append((px, py, zr, (node_r * 1.5 + node_r_depth * depth) * rs,
                     0.05, 0.5 + 0.5 * depth))

    return dots, lines
