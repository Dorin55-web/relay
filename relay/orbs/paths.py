"""The looks built out of paths rather than lattices.

Ported from thinking-orbs (MIT - see THIRD-PARTY-NOTICES.md): orbits
("working"), braid ("weaving"), ribbon ("composing") and ring ("breathing").
Ribbon and ring share a painter - the flag that tells them apart cancels the
camera tilt and moves the undulation from the sash's offset onto its radius,
which turns a band in orbit into a ring changing shape face on.
"""

import math

from .core import fib_dir, frac, hash_d, js_round, make_proj, radius_scale


def orbits(size, t, o):
    """Particles running their paths - "working".

    No nucleus: the tuned preset runs coreless, so what is left is the ghost
    paths and the particles doing the work on them.
    """
    cx = cy = size / 2
    radius = (size / 2) * 0.82
    project = make_proj(t * 0.12, 0.3, cx, cy, 1)
    rs = radius_scale(size, o.get("rs_pow", 0.6))

    orbit_n = o.get("orbit_n", 12)
    ghost_n = o.get("ghost_n", 40)
    particles = o.get("particles", 3)
    ghost_r = o.get("ghost_r", 0.9)
    ghost_a = o.get("ghost_a", 0.5)
    part_r = o.get("part_r", 1.2)
    part_r_depth = o.get("part_r_depth", 1.6)

    dots = []
    for orb in range(orbit_n):
        h1 = hash_d(orb, 1.7)
        h2 = hash_d(orb, 5.2)
        h3 = hash_d(orb, 8.9)
        ro = radius * (0.45 + 0.52 * h1)
        th = h1 * 2 * math.pi
        phi = math.acos(2 * h2 - 1)
        # The orbit's plane, as two directions perpendicular to its normal.
        nx = math.sin(phi) * math.cos(th)
        ny = math.cos(phi)
        nz = math.sin(phi) * math.sin(th)
        ux, uy, uz = -ny, nx, 0.0
        length = max(1e-6, math.sqrt(ux * ux + uy * uy))
        ux /= length
        uy /= length
        vx = ny * uz - nz * uy
        vy = nz * ux - nx * uz
        vz = nx * uy - ny * ux
        speed = (0.25 + 0.55 * h3) * (1 if h3 > 0.5 else -1)

        for k in range(ghost_n):
            a = (k / ghost_n) * 2 * math.pi
            ca, sa = math.cos(a), math.sin(a)
            px, py, z = project((ux * ca + vx * sa) * ro,
                                (uy * ca + vy * sa) * ro,
                                (uz * ca + vz * sa) * ro)
            depth = (z / ro + 1) / 2
            dots.append((px, py, z, ghost_r * rs, 0.72,
                         ghost_a * (0.4 + 0.6 * depth)))

        for m in range(particles):
            a = t * speed + (m / particles) * 2 * math.pi + h2 * 6
            ca, sa = math.cos(a), math.sin(a)
            px, py, z = project((ux * ca + vx * sa) * ro,
                                (uy * ca + vy * sa) * ro,
                                (uz * ca + vz * sa) * ro)
            depth = (z / ro + 1) / 2
            dots.append((px, py, z, (part_r + part_r_depth * depth) * rs,
                         0.3 - 0.22 * depth, 1.0))
    return dots, ()


def braid(size, t, o):
    """Three strands plaiting around a ball - "weaving".

    Each runs pole to pole on a helix, and a radial breathing term makes them
    trade places - which is what reads as the over and under of a plait
    without anything actually being drawn in front of anything.
    """
    cx = cy = size / 2
    radius = (size / 2) * 0.76
    project = make_proj(t * 0.4, 0.3, cx, cy, 1)
    rs = radius_scale(size, o.get("rs_pow", 0.6))

    dots = []
    ghost_n = o.get("ghost_n", 150)
    for i in range(ghost_n):
        gx, gy, gz = fib_dir(i, ghost_n)
        px, py, z = project(gx * radius, gy * radius, gz * radius)
        depth = (z / radius + 1) / 2
        dots.append((px, py, z, 0.8 * rs, 0.78, 0.1 + 0.22 * depth))

    strand_n = o.get("strand_n", 52)
    turns = o.get("turns", 3)
    r_base = o.get("r_base", 1.2)
    r_depth = o.get("r_depth", 1.8)
    for s in range(3):
        phase = (s / 3) * 2 * math.pi
        for i in range(strand_n):
            # u walks pole to pole; the wrap slides the whole strand along it.
            u = (frac(i / strand_n + t * 0.045) * 2 - 1) * 0.96
            surf = math.sqrt(max(0.0, 1 - u * u))
            end_fade = min(1.0, (1 - abs(u)) / 0.1)
            a = u * math.pi * turns + phase
            weave = 1 + 0.075 * math.sin(
                u * math.pi * turns * 2 + phase * 2 + t * 0.8)
            rr = surf * radius * weave
            px, py, z = project(math.cos(a) * rr, u * radius * weave,
                                math.sin(a) * rr)
            depth = (z / radius + 1) / 2
            dots.append((px, py, z, (r_base + r_depth * depth) * rs,
                         0.55 - 0.45 * depth,
                         end_fade * (0.45 + 0.55 * depth)))
    return dots, ()


def ribbon(size, t, o):
    """A sash undulating on a great circle - "composing", and "breathing".

    The tuned presets freeze the tumble, which is what leaves the travelling
    undulation as the only thing moving.
    """
    cx = cy = size / 2
    radius = (size / 2) * 0.78
    spin = o.get("spin", 1)
    cam_tilt = 0.3
    project = make_proj(t * 0.1 * spin, cam_tilt, cx, cy, 1)
    rs = radius_scale(size, o.get("rs_pow", 0.6))
    face_on = o.get("face_on")

    dots = []
    ghost_n = o.get("ghost_n", 150)
    for i in range(ghost_n):
        gx, gy, gz = fib_dir(i, ghost_n)
        px, py, z = project(gx * radius, gy * radius, gz * radius)
        depth = (z / radius + 1) / 2
        dots.append((px, py, z, 0.8 * rs, 0.78, 0.1 + 0.22 * depth))

    # The band's plane. The projection squashes its circle vertically by the
    # cosine of the two tilts together; face-on cancels the camera's, so the
    # band reads as a true circle rather than a leaning ellipse.
    ya = t * 0.24 * spin
    ta = -cam_tilt if face_on else 0.55 + 0.3 * math.sin(t * 0.18) * spin
    ux, uy, uz = math.cos(ya), 0.0, math.sin(ya)
    sin_ta = math.sin(ta)
    vx, vy, vz = -uz * sin_ta, math.cos(ta), ux * sin_ta
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx

    # The radial lobes swell past the radius, so the base is pulled in by most
    # of the amplitude first. The silhouette then stays in frame however far
    # the deformation is pushed.
    wob_mul = o.get("wob_mul", 1)
    wob_amp = 0.23 * wob_mul
    base_r = radius / (1 + 0.85 * wob_amp) if face_on else radius

    lanes = max(1, js_round(o.get("lanes", 5) * o.get("band_mul", 1)))
    segs = o.get("segs", 88)
    r_base = o.get("r_base", 1.1)
    r_depth = o.get("r_depth", 1.7)
    half = (lanes - 1) / 2
    for w in range(lanes):
        lane_off = (w - half) * 0.075
        edge = abs(w - half) / max(1, half)
        for k in range(segs):
            a = (k / segs) * 2 * math.pi
            wob = (0.16 * math.sin(a * 3 - t * 1.7 + w * 0.22)
                   + 0.07 * math.sin(a * 5 + t * 1.1)) * wob_mul
            # A wobble along the normal is undone by the re-normalisation
            # below - the point lands back on the sphere, so the silhouette is
            # pinned and the deformation can only ever pull dots inward.
            # Face-on modulates the in-plane radius instead, so its lobes
            # genuinely swell out and pinch in.
            radial = 1 + wob if face_on else 1
            off = lane_off if face_on else lane_off + wob
            ca, sa = math.cos(a), math.sin(a)
            x = ux * ca + vx * sa + nx * off
            y = uy * ca + vy * sa + ny * off
            z = uz * ca + vz * sa + nz * off
            length = math.sqrt(x * x + y * y + z * z) or 1.0
            scale = base_r * radial / length
            px, py, zr = project(x * scale, y * scale, z * scale)
            depth = (zr / radius + 1) / 2
            dots.append((
                px, py, zr,
                (r_base + r_depth * depth) * (1 - 0.25 * edge) * rs,
                0.52 - 0.44 * depth + 0.18 * edge,
                0.4 + 0.6 * depth,
            ))
    return dots, ()
