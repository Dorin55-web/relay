"""The three lattice looks: searching, solving and listening.

Ported from thinking-orbs (MIT - see THIRD-PARTY-NOTICES.md). All three lay
dots out on a sphere by latitude and longitude and then move them differently:
a scanning meridian, quarter turns of a puzzle, or a waveform through the
rings.
"""

import math

from .core import angle_delta, hash_d, js_round, make_proj, radius_scale


def globe(size, t, o):
    """A lattice under a sweeping scan - "searching"."""
    spin = 0.5
    cx = cy = size / 2
    radius = (size / 2) * 0.82
    tilt = 0.4 + 0.06 * math.sin(t * 0.35)
    project = make_proj(t * spin, tilt, cx, cy, radius)
    # The scan sweeps relative to the spin, so the multiplier scales the rate
    # it moves at against the ball rather than against the screen.
    scan = t * (spin + (1.7 - spin) * o.get("scan_mul", 1))
    rs = radius_scale(size, o.get("rs_pow", 0.6))
    dim_base = o.get("dim_base", 1)

    r_base = o.get("r_base", 0.6)
    r_depth = o.get("r_depth", 1.7)
    r_boost = o.get("r_boost", 1)
    ink_far = o.get("ink_far", 0.62)
    ink_span = o.get("ink_span", 0.54)

    dots = []
    lat_rings = o.get("lat_rings", 17)
    lon_density = o.get("lon_density", 44)
    for li in range(lat_rings + 1):
        lat = -math.pi / 2 + (li / lat_rings) * math.pi
        cos_lat = math.cos(lat)
        sin_lat = math.sin(lat)
        lon_count = max(1, js_round(abs(cos_lat) * lon_density))
        for lj in range(lon_count):
            lon = (lj / lon_count) * 2 * math.pi
            px, py, z = project(cos_lat * math.cos(lon), sin_lat,
                                cos_lat * math.sin(lon))
            depth = (z + 1) / 2
            # The scan reads as a ripple in dot size rather than a shine,
            # which is the only way it survives being drawn in flat fills.
            d = angle_delta(lon + t * spin, scan)
            boost = math.exp(-(d * d) / 0.18) * max(0.0, z)
            dots.append((
                px, py, z,
                (r_base + r_depth * depth + r_boost * boost) * rs,
                ink_far - ink_span * depth,
                dim_base + (1 - dim_base) * min(1.0, boost),
            ))
    return dots, ()


# --- the solver's heartbeat ------------------------------------------------
# Quick eased quarter turns scramble the ball, then replay in reverse so
# everything clicks back to solved, rests a moment, and goes again.

def _solve_cycle(t, count, slot, rest):
    cycle = 2 * count * slot + rest
    tc = t % cycle
    amount = [0.0] * count
    active = -1
    if tc < 2 * count * slot:
        index = int(tc // slot)
        p = (tc - index * slot) / slot
        eased = 1 - (1 - min(1.0, p / 0.7)) ** 3
        if index < count:
            for i in range(index):
                amount[i] = 1.0
            amount[index] = eased
            active = index
        else:
            u = 2 * count - 1 - index
            for i in range(u):
                amount[i] = 1.0
            amount[u] = 1 - eased
            active = u
    return amount, active


def _moves(count):
    out = []
    for i in range(count):
        axis = min(2, int(hash_d(i, 2.3) * 3))
        lo = -1.0 + 0.5 * min(3, int(hash_d(i, 5.9) * 4))
        direction = 1 if hash_d(i, 7.7) < 0.5 else -1
        out.append((axis, lo, lo + 0.5, direction * math.pi / 2))
    return out


def _apply_moves(x, y, z, moves, amount, active):
    in_active = False
    for i, (axis, lo, hi, angle) in enumerate(moves):
        if amount[i] <= 0:
            continue
        coord = x if axis == 0 else (y if axis == 1 else z)
        if coord < lo or coord >= hi:
            continue
        if i == active:
            in_active = True
        a = angle * amount[i]
        ca = math.cos(a)
        sa = math.sin(a)
        if axis == 0:
            y, z = y * ca - z * sa, y * sa + z * ca
        elif axis == 1:
            x, z = x * ca + z * sa, -x * sa + z * ca
        else:
            x, y = x * ca - y * sa, x * sa + y * ca
    return x, y, z, in_active


def rubik(size, t, o):
    """Bands twisting in quarter turns and clicking back - "solving"."""
    cx = cy = size / 2
    radius = (size / 2) * 0.82
    project = make_proj(t * 0.55, 0.35 + 0.1 * math.sin(t * 0.9), cx, cy, radius)
    rs = radius_scale(size, o.get("rs_pow", 0.6))
    move_count = o.get("move_count", 14)
    moves = _moves(move_count)
    amount, active = _solve_cycle(t, move_count, 0.42, 1.2)

    r_base = o.get("r_base", 0.6)
    r_depth = o.get("r_depth", 1.7)
    r_active = o.get("r_active", 0.3)
    ink_far = o.get("ink_far", 0.62)
    ink_span = o.get("ink_span", 0.54)

    dots = []
    lat_rings = o.get("lat_rings", 15)
    lon_density = o.get("lon_density", 40)
    for li in range(lat_rings + 1):
        lat = -math.pi / 2 + (li / lat_rings) * math.pi
        cos_lat = math.cos(lat)
        sin_lat = math.sin(lat)
        lon_count = max(1, js_round(abs(cos_lat) * lon_density))
        for lj in range(lon_count):
            lon = (lj / lon_count) * 2 * math.pi
            x, y, z, in_active = _apply_moves(
                cos_lat * math.cos(lon), sin_lat, cos_lat * math.sin(lon),
                moves, amount, active)
            px, py, zr = project(x, y, z)
            depth = (zr + 1) / 2
            # The band being turned inks a shade darker - the hand doing it.
            dots.append((
                px, py, zr,
                (r_base + r_depth * depth + (r_active if in_active else 0)) * rs,
                ink_far - ink_span * depth - (0.14 if in_active else 0),
                1.0,
            ))
    return dots, ()


def wave(size, t, o):
    """A waveform rolling through the rings - "listening"."""
    cx = cy = size / 2
    # 0.76 base times 1.15: the undulation pulls the sphere inward, so this
    # would otherwise read about a seventh smaller than the other lattices.
    radius = (size / 2) * 0.874
    project = make_proj(t * 0.18, 0.38, cx, cy, 1)
    rs = radius_scale(size, o.get("rs_pow", 0.6))

    r_base = o.get("r_base", 0.6)
    r_depth = o.get("r_depth", 1.7)

    dots = []
    rings = o.get("rings", 15)
    lon_density = o.get("lon_density", 40)
    for ri in range(rings + 1):
        lat = -math.pi / 2 + (ri / rings) * math.pi
        cos_lat = math.cos(lat)
        sin_lat = math.sin(lat)
        # Two waves at different tempi, so the pattern never quite repeats.
        w = (0.62 * math.sin(t * 2.1 - ri * 0.52)
             + 0.38 * math.sin(t * 1.27 + ri * 0.83))
        rr = radius * (0.88 + 0.105 * w)
        crest = max(0.0, w)
        lon_count = max(1, js_round(abs(cos_lat) * lon_density))
        for lj in range(lon_count):
            lon = (lj / lon_count) * 2 * math.pi
            px, py, z = project(cos_lat * math.cos(lon) * rr, sin_lat * rr,
                                cos_lat * math.sin(lon) * rr)
            depth = (z / radius + 1) / 2
            dots.append((
                px, py, z,
                (r_base + r_depth * depth) * (1 + 0.4 * crest) * rs,
                0.66 - 0.56 * depth - 0.1 * crest,
                1.0,
            ))
    return dots, ()
