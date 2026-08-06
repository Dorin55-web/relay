"""How dense each look is, before a preset scales it.

Ported from thinking-orbs (MIT - see THIRD-PARTY-NOTICES.md). These are the
fine base rows; every shipped preset is a pair of multipliers over one of
them, applied once when the preset is resolved rather than on every frame.
"""

from .core import js_round

# Lattices come in pairs - rings and dots-per-ring - so each side takes the
# square root of the multiplier and the TOTAL count scales by it. Flat lists
# scale straight.
COUNT_PAIRS = (
    ("lat_rings", "lon_density"),
    ("rings", "lon_density"),
    ("lanes", "segs"),
)
COUNT_KEYS = ("orbit_n", "ghost_n", "node_n", "strand_n", "signals")
DENSITY_KEYS = ("icon_d",)

# Every key that sets a dot's drawn radius. Scaling all of them together is
# what keeps a dot's near/far falloff intact while the mark grows or shrinks.
RADIUS_KEYS = (
    "r_base", "r_depth", "r_active", "r_dot", "ghost_r",
    "part_r", "part_r_depth", "node_r", "node_r_depth",
)


def scale_counts(opts, scale):
    out = dict(opts)
    done = set()
    root = scale ** 0.5
    for a, b in COUNT_PAIRS:
        if a in out and b in out and a not in done and b not in done:
            out[a] = max(2, js_round(out[a] * root))
            out[b] = max(2, js_round(out[b] * root))
            done.update((a, b))
    for key in COUNT_KEYS:
        # Zero means the look opted out of that layer entirely - the ring has
        # no ghost sphere behind it - and scaling must not resurrect it as a
        # single stray dot.
        if out.get(key):
            out[key] = max(1, js_round(out[key] * scale))
    for key in DENSITY_KEYS:
        if key in out:
            out[key] = max(0.02, out[key] * scale)
    return out


def scale_radii(opts, scale):
    """Scale every key that sets a dot's drawn radius, and nothing else.

    The original also stashes the multiplier itself under `r_size_mul`, for a
    morph outline whose radius comes from spacing rather than from any single
    key here. Relay's morph reads `spread` and `r_dot` instead and never looks
    at it, so it is not carried.
    """
    out = dict(opts)
    for key in RADIUS_KEYS:
        if key in out:
            out[key] = out[key] * scale
    return out


BASE_PROFILES = {
    "globe": dict(lat_rings=17, lon_density=44, r_base=0.6, r_depth=1.7,
                  r_boost=1.0, ink_far=0.62, ink_span=0.54, rs_pow=0.6,
                  r_min=0.3),
    "orbits": dict(orbit_n=12, ghost_n=40, ghost_r=0.9, ghost_a=0.5,
                   particles=3, part_r=1.2, part_r_depth=1.6, rs_pow=0.6,
                   r_min=0.3),
    "rubik": dict(lat_rings=15, lon_density=40, move_count=14, r_base=0.6,
                  r_depth=1.7, r_active=0.3, ink_far=0.62, ink_span=0.54,
                  rs_pow=0.6, r_min=0.3),
    "wave": dict(rings=15, lon_density=40, r_base=0.6, r_depth=1.7,
                 rs_pow=0.6, r_min=0.3),
    "web": dict(node_n=30, thr=0.72, signals=5, node_r=1.4, node_r_depth=1.8,
                line_w=0.8, rs_pow=0.6, r_min=0.3),
    "braid": dict(strand_n=52, turns=3.0, ghost_n=150, r_base=1.2,
                  r_depth=1.8, rs_pow=0.6, r_min=0.3),
    "ribbon": dict(lanes=5, segs=88, ghost_n=150, r_base=1.1, r_depth=1.7,
                   rs_pow=0.6, r_min=0.3),
    # ring shares ribbon's painter. face_on cancels the camera tilt and moves
    # the undulation onto the radius, and there is no ghost sphere behind it.
    "ring": dict(lanes=5, segs=88, ghost_n=0, face_on=1, r_base=1.1,
                 r_depth=1.7, rs_pow=0.6, r_min=0.3),
    "morph": dict(r_dot=0.021, icon_d=1.0, r_min=0.25),
}
