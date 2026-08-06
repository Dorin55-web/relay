"""The shipped tunings, and the in-between size Relay adds.

Ported from thinking-orbs (MIT - see THIRD-PARTY-NOTICES.md). Nine looks at
two sizes, each a speed for the shared clock plus count and radius
multipliers over the base profile. Resolved once per (look, size) and cached,
so the drawing code only ever sees plain numbers.

The two sizes are separate designs rather than one scaled - a mark for a chat
avatar and a mark for a line of text want different densities, not the same
density at different radii. Relay's size slider is continuous, so every size
between them is blended from both; those blends are Relay's, not the original's
tuning.
"""

from .profiles import BASE_PROFILES, scale_counts, scale_radii

LOOK_TO_MODE = {
    "working": "orbits",
    "searching": "globe",
    "solving": "rubik",
    "listening": "wave",
    "connecting": "web",
    "weaving": "braid",
    "composing": "ribbon",
    "breathing": "ring",
    "shaping": "morph",
}

# In the order they are worth looking at, which is the order the picker shows.
LOOKS = tuple(LOOK_TO_MODE)

# A line each, for the picker. Long enough to say what the thing does, short
# enough to sit under a thumbnail.
LOOK_BLURBS = {
    "working": "particles running their orbits",
    "searching": "a globe under a sweeping scan",
    "solving": "bands twisting and clicking back",
    "listening": "a waveform rolling through the rings",
    "connecting": "a constellation wiring itself up",
    "weaving": "three strands plaiting over a ball",
    "composing": "a sash undulating on its band",
    "breathing": "a ring swelling and pinching",
    "shaping": "an outline cycling through shapes",
}

# The two sizes the drawings were actually tuned at. Between them the tuning
# is interpolated, so any size in this span is a real design rather than one
# of the two scaled. Outside it there is nothing to interpolate towards, and
# `frame` magnifies the nearer tuning instead of extrapolating - see there for
# why extrapolating is not an option.
TUNED_MIN = 20
TUNED_MAX = 64
TUNED_SIZES = (TUNED_MIN, TUNED_MAX)

# What the size slider may ask for. The bottom is where the orb stops being
# something you can aim a mouse at; the top is well past what a dot floating
# over other windows wants to be.
MIN_SIZE = 16
MAX_SIZE = 120

# What the user's speed dial may ask for, on top of a look's own tuning.
MIN_SPEED = 0.1
MAX_SPEED = 3.0

# label, speed, count, size, extra
_TUNINGS = {
    "orbits": {
        64: (1.885, 1.0, 1.0, {}),
        20: (3.9, 0.238, 2.4, {}),
    },
    "globe": {
        64: (2.015, 0.42, 1.15, {"scan_mul": 4.08, "dim_base": 0.45}),
        20: (2.665, 0.105, 1.75, {"scan_mul": 4.335, "dim_base": 0.45}),
    },
    "rubik": {
        64: (1.82, 0.35, 1.05, {}),
        20: (1.95, 0.088, 1.9, {}),
    },
    "wave": {
        64: (4.388, 0.341, 1.0, {}),
        20: (3.998, 0.105, 1.6, {}),
    },
    "web": {
        64: (3.315, 1.35, 0.95, {}),
        20: (6.63, 0.25, 1.52, {}),
    },
    "braid": {
        64: (1.625, 0.5, 1.0, {}),
        20: (2.75, 0.1125, 1.36, {}),
    },
    "ribbon": {
        64: (2.34, 0.25, 0.85, {"spin": 0, "band_mul": 3.9, "wob_mul": 1}),
        20: (3.12, 0.051, 1.073, {"spin": 0, "band_mul": 4.94, "wob_mul": 1}),
    },
    "ring": {
        64: (3.24, 0.25, 0.956,
             {"spin": 0, "band_mul": 3.627, "wob_mul": 0.368}),
        20: (3.78, 0.028, 1.622,
             {"spin": 0, "band_mul": 3.968, "wob_mul": 0.565}),
    },
    "morph": {
        64: (2.405, 0.702, 0.395, {"spread": 1.45}),
        20: (2.08, 0.53, 1.011, {"spread": 1.45}),
    },
}


def _blend(small, large, f):
    """Between the two tunings, on a log scale where that makes sense.

    Counts, radii and speeds are all multipliers, and halfway between 0.05 and
    0.25 is 0.11, not 0.15 - averaging them straight lands much closer to the
    large tuning than to the middle. Anything that can be zero (a spin that is
    switched off) is blended straight instead, because a log scale has no
    room for it.
    """
    if small > 0 and large > 0:
        return small * (large / small) ** f
    return small + (large - small) * f


def _blended_tuning(mode, size):
    small_speed, small_count, small_size, small_extra = _TUNINGS[mode][TUNED_MIN]
    big_speed, big_count, big_size, big_extra = _TUNINGS[mode][TUNED_MAX]
    f = (size - TUNED_MIN) / (TUNED_MAX - TUNED_MIN)
    extra = {
        key: _blend(small_extra[key], big_extra.get(key, small_extra[key]), f)
        for key in small_extra
    }
    return (
        _blend(small_speed, big_speed, f),
        _blend(small_count, big_count, f),
        _blend(small_size, big_size, f),
        extra,
    )


_cache = {}


def resolve(look, size):
    """A look at a size, as (mode, speed, opts). Cached; opts are plain."""
    key = (look, size)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    mode = LOOK_TO_MODE[look]
    if size in _TUNINGS[mode]:
        speed, count, radius, extra = _TUNINGS[mode][size]
    else:
        speed, count, radius, extra = _blended_tuning(mode, size)

    opts = dict(BASE_PROFILES[mode])
    if count != 1:
        opts = scale_counts(opts, count)
    if radius != 1:
        opts = scale_radii(opts, radius)
    opts.update(extra)

    resolved = (mode, speed, opts)
    _cache[key] = resolved
    return resolved


def clamp_speed(speed):
    return max(MIN_SPEED, min(MAX_SPEED, float(speed)))


def clamp_size(size):
    """Whole pixels, inside what the slider offers.

    Whole because the tunings are resolved per size and cached, and a slider
    that produced fractions would fill that cache with values nobody can tell
    apart.
    """
    return max(MIN_SIZE, min(MAX_SIZE, int(round(float(size)))))


def tuned_size(size):
    """The size whose tuning to draw with, for a mark this big."""
    return max(TUNED_MIN, min(TUNED_MAX, size))
