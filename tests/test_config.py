"""Settings: what a hand-edited config.json is allowed to do to Relay.

This file is the one part of the project a person edits with a text editor, so
every test here is really the same question: what happens when the edit is
wrong? A missing key, a look that does not exist, a speed of 50, a trailing
comma. None of those may stop Relay from starting, and none of them may cost
the user the rest of their settings.
"""
import json
import sys
import tempfile
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

from relay import config as config_mod  # noqa: E402
from relay.config import DEFAULTS, load_config, normalise_orb, save_orb  # noqa: E402

report = context.Report()
check = report.check

HERE = Path(tempfile.mkdtemp(prefix="relay-config-"))


def write(name, text):
    path = HERE / name
    path.write_text(text, encoding="utf-8")
    return path


print("\n--- no file at all is a working configuration ---")
cfg = load_config(HERE / "does-not-exist.json")
check("every default is present", set(cfg) >= set(DEFAULTS), str(set(DEFAULTS) - set(cfg)))
check("and the orb is filled in", set(cfg["orb"]) == {"size", "idle", "recording", "processing"},
      str(cfg["orb"]))
check("attribute access works", cfg.hotkey == DEFAULTS["hotkey"], cfg.hotkey)


print("\n--- your values win, and only yours change ---")
path = write("mine.json", json.dumps({"hotkey": "f4", "auto_enter": True}))
cfg = load_config(path)
check("the key you set", cfg.hotkey == "f4", cfg.hotkey)
check("and the one next to it", cfg.auto_enter is True, str(cfg.auto_enter))
check("everything else is the default",
      cfg.sample_rate == DEFAULTS["sample_rate"], str(cfg.sample_rate))


print("\n--- a key Relay does not know is ignored, not fatal ---")
path = write("typo.json", json.dumps({"hotkeyy": "f4", "sample_rate": 8000}))
cfg = load_config(path)
check("the typo does not become a setting", "hotkeyy" not in cfg)
check("the hotkey is still the default", cfg.hotkey == DEFAULTS["hotkey"], cfg.hotkey)
check("and the good key beside it still lands", cfg.sample_rate == 8000, str(cfg.sample_rate))


print("\n--- a trailing comma costs you nothing but that run ---")
# The single most likely hand-edit mistake. It must not stop the application.
path = write("broken.json", '{"hotkey": "f4",}')
cfg = load_config(path)
check("Relay still starts", cfg.hotkey == DEFAULTS["hotkey"], cfg.hotkey)
check("with a whole orb", cfg["orb"]["idle"]["look"] == DEFAULTS["orb"]["idle"]["look"])


print("\n--- the orb section merges slot by slot ---")
# The one nested setting. A shallow update would let a file that names a look
# for one state leave the other two with no look at all.
out = normalise_orb({"idle": {"look": "listening"}})
check("the slot you named", out["idle"]["look"] == "listening", out["idle"]["look"])
check("keeps its own default speed",
      out["idle"]["speed"] == DEFAULTS["orb"]["idle"]["speed"], str(out["idle"]["speed"]))
check("and the slots you did not are untouched",
      out["processing"] == DEFAULTS["orb"]["processing"], str(out["processing"]))


print("\n--- and refuses what it cannot draw ---")
out = normalise_orb({"size": 999, "idle": {"look": "banana", "speed": 40},
                     "recording": "not a dict"})
check("a look that does not exist falls back",
      out["idle"]["look"] == DEFAULTS["orb"]["idle"]["look"], out["idle"]["look"])
check("the size is clamped", out["size"] == 120, str(out["size"]))
check("the speed is clamped", out["speed"] if False else out["idle"]["speed"] == 3.0,
      str(out["idle"]["speed"]))
check("a slot that is not an object is replaced whole",
      out["recording"] == DEFAULTS["orb"]["recording"], str(out["recording"]))

out = normalise_orb({"size": "big", "idle": {"speed": "fast"}})
check("a size that is not a number falls back", out["size"] == DEFAULTS["orb"]["size"],
      str(out["size"]))
check("and so does a speed", out["idle"]["speed"] == DEFAULTS["orb"]["idle"]["speed"],
      str(out["idle"]["speed"]))


print("\n--- saving the orb leaves the rest of the file alone ---")
path = write("keep.json", json.dumps({"hotkey": "f4", "paste_delay_ms": 900}, indent=2))
save_orb({"size": 40, "idle": {"look": "listening", "speed": 1.5}}, path)
raw = json.loads(path.read_text(encoding="utf-8"))
check("your hotkey survives", raw.get("hotkey") == "f4", str(raw.get("hotkey")))
check("your delay survives", raw.get("paste_delay_ms") == 900, str(raw.get("paste_delay_ms")))
check("and the orb is what you chose", raw["orb"]["idle"]["look"] == "listening",
      str(raw["orb"]))


print("\n--- saving over a file it cannot read does not destroy it ---")
# The path that loses your settings: a trailing comma makes load_config fall
# back to defaults, and then the first use of the look picker rewrites the file
# from those defaults. Everything you had typed in there is gone, over a comma.
path = write("valuable.json", '{"hotkey": "f4", "paste_delay_ms": 900,}')
before = path.read_text(encoding="utf-8")
try:
    save_orb({"size": 40}, path)
    raised = None
except Exception as exc:
    raised = exc
after = path.read_text(encoding="utf-8") if path.exists() else ""
kept = path.with_name("valuable.broken.json")
check("the settings you wrote are still somewhere",
      "paste_delay_ms" in after or (kept.exists() and "paste_delay_ms" in
                                    kept.read_text(encoding="utf-8")),
      f"after={after[:60]!r} kept={kept.exists()}")
check("and you are told where", kept.exists() or raised is not None,
      f"raised={raised!r}")


print("\n--- a file that is not an object at all ---")
path = write("list.json", "[1, 2, 3]")
cfg = load_config(path)
check("does not crash the load", cfg.hotkey == DEFAULTS["hotkey"], cfg.hotkey)
save_orb({"size": 40}, path)
raw = json.loads(path.read_text(encoding="utf-8"))
check("and is replaced by something loadable", isinstance(raw, dict) and "orb" in raw,
      str(raw)[:60])


print("\n--- write_default_config produces a file that loads back the same ---")
path = HERE / "fresh.json"
config_mod.write_default_config(path)
cfg = load_config(path)
check("the round trip changes nothing",
      all(cfg[k] == DEFAULTS[k] for k in DEFAULTS if k != "orb"),
      str([k for k in DEFAULTS if k != "orb" and cfg[k] != DEFAULTS[k]]))
check("including the orb", cfg["orb"] == normalise_orb(DEFAULTS["orb"]), str(cfg["orb"]))


print("\n--- the config you actually ship loads clean ---")
# Not a synthetic file: the one in the project root, as edited.
real = load_config(config_mod.DEFAULT_CONFIG_PATH)
check("it has every key", set(real) >= set(DEFAULTS))
check("its orb looks are real looks",
      all(config_mod.normalise_orb(real["orb"])[s]["look"] == real["orb"][s]["look"]
          for s in config_mod.ORB_SLOTS),
      str(real["orb"]))

sys.exit(report.finish())
