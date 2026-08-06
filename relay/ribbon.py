"""A sash of dots undulating around a great circle - the "composing" orb.

Geometry ported from thinking-orbs (Jakub Antalik, MIT - see
THIRD-PARTY-NOTICES.md), `ribbon` mode at its 64px "composing" tuning, redrawn
here in QPainter. The multipliers that tuning applies are resolved once, up
front, into the constants below rather than recomputed on every frame.

The tuning freezes the band's tumble, which is what makes this cheap: with no
spin, the plane the sash rides never moves, so every dot's position around the
circle is fixed and only the undulation running along it depends on time. All
of that fixed geometry is built once into _RIDERS.
"""

import math

# --- the resolved 64px "composing" tuning --------------------------------
# ribbon's base profile is 5 lanes x 88 segments with 150 ghost dots, scaled
# by the preset's count multiplier of 0.25 (lattice pairs take its square
# root, flat lists take it whole) and then widened by a band multiplier of
# 3.9. Radii carry the preset's size multiplier of 0.85.
LANES = 12
SEGMENTS = 44
GHOST_DOTS = 38

FILL = 0.78              # of the half-size: how far the band reaches out
CAM_TILT = 0.3           # the camera's lean, in radians
BAND_TILT = 0.55         # and the band's, frozen because the tumble is off
LANE_GAP = 0.075         # between neighbouring strands of the sash

R_BASE = 0.935           # dot radius at the back
R_DEPTH = 1.445          # and how much more it gains coming forward
GHOST_DOT = 0.8
RS_POW = 0.6             # radii were tuned at 300px; they shrink sub-linearly
MIN_DOT = 0.3

# Clock rate, in the animation's own units per second: the tuning's 2.34,
# taken to 1.1x.
SPEED = 2.34 * 1.1

# The undulation: two waves travelling along the band at different rates, so
# the pattern never quite repeats.
WOB_SLOW = 0.16          # amplitude of the three-lobe wave
WOB_SLOW_RATE = 1.7      # and how fast it runs backwards along the band
WOB_FAST = 0.07          # the five-lobe wave riding on top
WOB_FAST_RATE = 1.1
WOB_LANE_LAG = 0.22      # each strand lags the one beside it, so the sash
                         # twists rather than moving as one stiff sheet

# Ink, in the same 0..1 the port came from: 0 is the darkest. Everything is
# drawn on a dark tile, so these are mirrored on the way out.
INK_BASE = 0.52
INK_DEPTH = 0.44         # subtracted, so near dots come out bright
INK_EDGE = 0.18          # the outer strands stay dimmer than the middle
GHOST_INK = 0.78
EDGE_THIN = 0.25         # and thinner


def _band_axes():
    """The two directions spanning the band's plane, and its normal.

    With the tumble frozen the band's long axis is simply x, which collapses
    most of the original's cross products to constants.
    """
    vy = math.cos(BAND_TILT)
    vz = math.sin(BAND_TILT)
    # u = (1, 0, 0), v = (0, vy, vz), n = u x v = (0, -vz, vy)
    return vy, vz


_VY, _VZ = _band_axes()
_COS_TILT = math.cos(CAM_TILT)
_SIN_TILT = math.sin(CAM_TILT)


def _riders():
    """Every dot of the sash, with everything that does not depend on time.

    Each entry carries its place around the circle, its offset across the
    sash, how far out towards the edge it sits, and the sine and cosine of
    both wobble phases - so a frame costs four multiplies per dot instead of
    two calls into the trigonometry library.
    """
    out = []
    half = (LANES - 1) / 2
    for lane in range(LANES):
        lane_off = (lane - half) * LANE_GAP
        edge = abs(lane - half) / max(1, half)
        for step in range(SEGMENTS):
            angle = 2 * math.pi * step / SEGMENTS
            slow = angle * 3 + lane * WOB_LANE_LAG
            fast = angle * 5
            out.append((
                math.cos(angle), math.sin(angle), lane_off, edge,
                math.sin(slow), math.cos(slow),
                math.sin(fast), math.cos(fast),
            ))
    return out


_RIDERS = _riders()


def _ghosts():
    """A faint scatter of dots on the sphere the band rides, so the sash reads
    as sitting on something round rather than floating.

    Spread by the golden angle, which is the one arrangement that never falls
    into visible rows however many points are used.
    """
    golden = math.pi * (3 - math.sqrt(5))
    out = []
    for i in range(GHOST_DOTS):
        y = 1 - 2 * (i + 0.5) / GHOST_DOTS
        ring = math.sqrt(max(0.0, 1 - y * y))
        angle = i * golden
        out.append((ring * math.cos(angle), y, ring * math.sin(angle)))
    return out


_GHOSTS = _ghosts()


def radius_scale(size):
    """Dot radii were tuned on a 300px frame and shrink sub-linearly from
    there, or a small orb loses its dots entirely."""
    return (size / 300.0) ** RS_POW


def _project(x, y, z):
    """Lean the whole thing towards the viewer and flatten it.

    Orthographic on purpose: perspective at this size only distorts the
    silhouette without adding any depth that the shading does not already
    carry.
    """
    return x, y * _COS_TILT - z * _SIN_TILT, y * _SIN_TILT + z * _COS_TILT


def dots(t, size, wobble=1.0):
    """The whole orb at time `t` seconds, as (x, y, depth, radius, ink, alpha).

    `x` and `y` are pixels from the centre, `radius` is in pixels, and `ink`
    is 0..1 with 0 darkest - the caller mirrors it onto whatever it is drawing
    on. `depth` runs 0 at the back to 1 at the front; the caller only needs it
    to sort. `wobble` deepens the undulation, which is the one handle the
    caller has: the tuning already draws the near dots at full alpha, so
    brightening them has nowhere left to go.
    """
    reach = size / 2 * FILL
    rs = radius_scale(size)
    out = []

    for gx, gy, gz in _GHOSTS:
        px, py, pz = _project(gx * reach, gy * reach, gz * reach)
        depth = (pz / reach + 1) / 2
        out.append((px, -py, depth, max(MIN_DOT, GHOST_DOT * rs),
                    GHOST_INK, 0.1 + 0.22 * depth))

    slow_phase = -WOB_SLOW_RATE * t
    fast_phase = WOB_FAST_RATE * t
    slow_sin, slow_cos = math.sin(slow_phase), math.cos(slow_phase)
    fast_sin, fast_cos = math.sin(fast_phase), math.cos(fast_phase)
    slow_amp = WOB_SLOW * wobble
    fast_amp = WOB_FAST * wobble

    for cos_a, sin_a, lane_off, edge, s3, c3, s5, c5 in _RIDERS:
        # sin(A + phase), with the phase's sine and cosine already taken once
        # for the whole frame.
        wob = (slow_amp * (s3 * slow_cos + c3 * slow_sin)
               + fast_amp * (s5 * fast_cos + c5 * fast_sin))
        off = lane_off + wob

        x = cos_a
        y = _VY * sin_a - _VZ * off
        z = _VZ * sin_a + _VY * off
        # Back onto the sphere. This is why the wobble reads as a sash being
        # pulled about rather than as the ball changing shape: the silhouette
        # stays pinned to the same circle however hard the band is pushed.
        length = math.sqrt(x * x + y * y + z * z) or 1.0
        scale = reach / length

        px, py, pz = _project(x * scale, y * scale, z * scale)
        depth = (pz / reach + 1) / 2
        out.append((
            px, -py, depth,
            max(MIN_DOT, (R_BASE + R_DEPTH * depth) * (1 - EDGE_THIN * edge) * rs),
            INK_BASE - INK_DEPTH * depth + INK_EDGE * edge,
            0.4 + 0.6 * depth,
        ))
    return out
