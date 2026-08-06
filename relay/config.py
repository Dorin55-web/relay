"""Settings, loaded from config.json over a set of defaults."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

DEFAULTS = {
    # --- trigger ---
    "hotkey": "f9",                  # single key, no modifiers (see injector.py)
    # --- speech to text ---
    "model_size": "large-v3",        # NOT large-v3-turbo: it is not trained on translate
    "device": "cuda",                # "cuda" | "cpu" | "auto"
    "compute_type": "float16",       # falls back automatically if unsupported
    "source_language": "ro",         # pinned; auto-detect is unreliable on short clips
    "task": "translate",             # "translate" -> English out, "transcribe" -> as spoken
    # 1 = greedy. On the short phrases streaming produces, beam search costs
    # decode time for almost no quality gain.
    "beam_size": 1,
    # --- audio ---
    "input_device": None,            # null = follow the current Windows default
    # A Bluetooth headset carries its mic on the hands-free profile, which
    # cannot run at the same time as the stereo one. Opening it tears down your
    # music, and the renegotiation back is what leaves the headset dead. That
    # mic is 8-16 kHz mono anyway, against 48 kHz for a built-in array, so
    # avoiding it is better for Whisper as well. Set false to use it regardless.
    "avoid_bluetooth_mic": True,
    "sample_rate": 16000,            # Whisper's native rate
    "max_recording_seconds": 120,
    "min_recording_seconds": 0.3,
    # Only meant to catch dead audio. Quiet mics deliver real speech at 0.002,
    # so keep this low and let Whisper's VAD decide what is actually silence.
    "silence_rms_threshold": 0.0005,
    # --- live dictation ---
    # Paste each phrase as you finish it instead of everything at the end.
    "streaming": True,
    "phrase_silence_seconds": 0.7,   # pause that ends a phrase
    "max_phrase_seconds": 15,        # flush anyway if you never pause
    "min_phrase_seconds": 0.4,       # shorter than this is a cough, not a phrase
    "vad_speech_multiplier": 3.0,    # speech = this much above the noise floor
    "vad_min_rms": 0.0008,           # absolute floor, for very quiet mics
    # --- output ---
    # Paste into the window you were last writing in, even if focus moved since.
    "remember_target": True,
    # Replay your last click there when a dictation starts, so the text cursor
    # lands back in the box. Chromium apps expose no caret to Windows, so this
    # is the only way to avoid clicking into the prompt box yourself each time.
    "restore_caret": True,
    "auto_enter": False,             # do not submit; you review and press Enter
    "restore_clipboard": True,
    "paste_delay_ms": 120,           # clipboard write -> Ctrl+V
    "restore_delay_ms": 500,         # Ctrl+V -> clipboard restore
    # --- feedback ---
    "beep_feedback": True,
    "print_transcript": True,
    # --- the orb ---
    # One look per state, each with its own speed. Edited from the orb's
    # right-click menu rather than by hand, but readable and hand-editable all
    # the same. See relay/orbs for what the names draw.
    "orb": {
        "size": 64,
        "idle": {"look": "breathing", "speed": 1.0},
        "recording": {"look": "listening", "speed": 1.3},
        "processing": {"look": "working", "speed": 1.0},
    },
}

# The three things the orb can be doing, in the order the picker lists them,
# with the words a person would use for them.
ORB_SLOTS = ("idle", "recording", "processing")
SLOT_LABELS = {
    "idle": "Resting",
    "recording": "Listening",
    "processing": "Working",
}


class Config(dict):
    """Plain dict with attribute access, so cfg.hotkey works alongside cfg["hotkey"]."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def load_config(path=None):
    """Read config.json if present, layering it over DEFAULTS."""
    cfg = Config(DEFAULTS)
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH

    if config_path.is_file():
        try:
            user_values = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[config] {config_path.name} could not be read ({exc}); using defaults")
            return cfg
        unknown = set(user_values) - set(DEFAULTS)
        if unknown:
            print(f"[config] ignoring unknown key(s): {', '.join(sorted(unknown))}")
        cfg.update({k: v for k, v in user_values.items() if k in DEFAULTS})

    cfg["orb"] = normalise_orb(cfg.get("orb"))
    return cfg


def normalise_orb(settings):
    """Fill in and sanity-check the orb section.

    Every other setting is a single value, so layering the user's file over the
    defaults is enough. This one is a nested dict, and a shallow update
    replaces the whole thing - a file that names a look for one state would
    otherwise leave the other two with nothing at all. Each slot is merged on
    its own, and anything that is not a look Relay can draw falls back rather
    than crashing the orb at startup.
    """
    from . import orbs

    base = DEFAULTS["orb"]
    settings = settings if isinstance(settings, dict) else {}

    try:
        size = orbs.clamp_size(settings.get("size", base["size"]))
    except (TypeError, ValueError):
        size = base["size"]
    out = {"size": size}
    for slot in ORB_SLOTS:
        chosen = settings.get(slot)
        chosen = chosen if isinstance(chosen, dict) else {}
        look = chosen.get("look", base[slot]["look"])
        if not orbs.is_look(look):
            print(f"[config] no orb look called {look!r}; "
                  f"using {base[slot]['look']}")
            look = base[slot]["look"]
        try:
            speed = orbs.clamp_speed(chosen.get("speed", base[slot]["speed"]))
        except (TypeError, ValueError):
            speed = base[slot]["speed"]
        out[slot] = {"look": look, "speed": speed}
    return out


def save_orb(settings, path=None):
    """Write the orb section back, leaving the rest of the file alone.

    Rewriting the whole config from the loaded values would bake every default
    into the file as though the user had chosen it, and lose any comment-like
    ordering they had. Only this one key is touched.
    """
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (json.JSONDecodeError, OSError):
        raw = {}
    raw["orb"] = normalise_orb(settings)
    config_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return raw["orb"]


def write_default_config(path=None):
    """Create config.json with the defaults, for the user to edit."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    config_path.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
    return config_path
