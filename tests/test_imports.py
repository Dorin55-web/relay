"""Every module loads, and everything loaded late is still where it was.

Most of Relay is imported the moment it is needed and not before: the write
window, the chain window, the look picker, the prompt editor and the phone link
all cost Qt widgets or a network thread, and a session that never opens them
should not carry them. That is the right shape, and it has one cost - a
misspelled name in one of those modules is not a start-up error, it is a menu
item that does nothing at the moment you click it, weeks later.

So this imports the lot and looks up every name that is reached across a module
boundary. It is the cheapest suite here and the only one that would notice a
window being renamed out from under its caller.
"""
import contextlib
import importlib
import io
import pkgutil
import sys

import context  # noqa: E402,F401
context.isolate_state()

import relay  # noqa: E402

report = context.Report()
check = report.check

# Wanted from another module, at the moment a menu is clicked or a chain sends.
# Kept here rather than derived, so that removing one is a decision rather than
# a green test run.
REACHED_ACROSS = {
    "relay.audio": ["AudioRecorder", "END_OF_SESSION", "list_devices", "mic_test",
                    "record_fixed", "refresh_devices", "sd"],
    "relay.agent": ["BUSY", "IDLE", "WAITING", "UNKNOWN", "STOPS", "read", "state",
                    "profile_for", "focus_input", "recognised_windows",
                    "load_profiles", "write_default_profiles"],
    "relay.autopilot": ["Autopilot", "COUNTING", "DONE", "HOLDING", "SENDING",
                        "STARTING", "STOPPED", "WAITING"],
    "relay.chain": ["open_chain", "running_window"],
    "relay.compose": ["open_compose", "prebuild"],
    "relay.config": ["load_config", "write_default_config", "save_orb",
                     "normalise_orb", "ORB_SLOTS", "SLOT_LABELS"],
    "relay.cuda_setup": ["enable_cuda_dlls", "cuda_device_count"],
    "relay.feedback": ["Feedback"],
    "relay.injector": ["paste_text", "save_clipboard", "restore_clipboard"],
    "relay.keeper": ["Keeper", "MUTEX_NAME", "relay_is_running", "settings"],
    "relay.logsetup": ["setup_output", "LOG_PATH", "PREVIOUS_LOG_PATH"],
    "relay.look_picker": ["open_picker"],
    "relay.orbs": ["frame", "speed_of", "is_look", "clamp_size", "clamp_speed",
                   "LOOKS", "LOOK_BLURBS"],
    "relay.overlay": ["Orb", "POSITION_FILE"],
    "relay.prompt_editor": ["open_editor"],
    "relay.prompts": ["load", "save", "ensure_file", "open_for_editing",
                      "MAX_PROMPTS", "PROMPTS_PATH"],
    "relay.remote": ["Remote", "load_settings", "write_template", "set_token",
                     "SETTINGS_PATH", "COMMANDS", "RESTART_MARKER"],
    "relay.single_instance": ["already_running", "MUTEX_NAME"],
    "relay.sphere": ["dots", "rings_for", "dot_radius", "dot_alpha"],
    "relay.target": ["TargetTracker", "focus_window", "foreground_window",
                     "window_title", "window_rect", "window_process"],
    "relay.transcriber": ["WhisperEngine"],
    "relay.translator": ["TextTranslator", "split_lines"],
    "relay.uia": ["clean", "window_text", "window_buttons", "focus_named_input",
                  "focused_input"],
    "relay.watchdog": ["Watchdog"],
    "relay.window": ["FramelessWindow", "EDGE"],
}


print("\n--- every module imports ---")
names = [f"relay.{m.name}" for m in pkgutil.iter_modules(relay.__path__)]
names += [f"relay.orbs.{m.name}"
          for m in pkgutil.iter_modules([relay.__path__[0] + "/orbs"])]
loaded = {}
for name in sorted(names):
    if name == "relay.__main__":
        continue          # imported below, once, and it starts logging
    try:
        loaded[name] = importlib.import_module(name)
        failed = None
    except Exception as exc:
        failed = f"{type(exc).__name__}: {exc}"
    check(name, failed is None, str(failed))

try:
    loaded["relay.__main__"] = importlib.import_module("relay.__main__")
    failed = None
except Exception as exc:
    failed = f"{type(exc).__name__}: {exc}"
check("relay.__main__", failed is None, str(failed))

check("that is all of them", len(loaded) >= 20, str(len(loaded)))


print("\n--- and everything reached from another module is still there ---")
for module_name, wanted in sorted(REACHED_ACROSS.items()):
    module = loaded.get(module_name)
    if module is None:
        check(f"{module_name} loaded", False, "not imported")
        continue
    missing = [name for name in wanted if not hasattr(module, name)]
    check(f"{module_name}", not missing, str(missing))


print("\n--- the entry point offers what run.bat and the docs call for ---")
app = loaded["relay.__main__"]
usage = io.StringIO()
try:
    with contextlib.redirect_stdout(usage):
        app.main(["--help"])
    left = "it did not exit"
except SystemExit as exc:
    left = exc.code
check("--help exits rather than starting anything", left == 0, str(left))
offered = usage.getvalue()
for flag in ["--list-devices", "--mic-test", "--selftest", "--write-config",
             "--set-token", "--check-cuda", "--no-ui", "--config"]:
    check(f"{flag} is offered", flag in offered, offered[:80])

bad = io.StringIO()
try:
    with contextlib.redirect_stderr(bad):
        app.main(["--not-a-flag"])
    refused = None
except SystemExit as exc:
    refused = exc.code
check("and a flag it does not know is refused", refused == 2, str(refused))


print("\n--- the three states have one spelling ---")
# The orb, the config and the state machine all name them, and a mismatch is a
# look that silently never gets used.
from relay.config import ORB_SLOTS  # noqa: E402

check("idle", app.IDLE in ORB_SLOTS, app.IDLE)
check("recording", app.RECORDING in ORB_SLOTS, app.RECORDING)
check("processing", app.PROCESSING in ORB_SLOTS, app.PROCESSING)
check("and nothing else is a slot", len(ORB_SLOTS) == 3, str(ORB_SLOTS))


print("\n--- the phases the queue reports are the ones the phone draws ---")
from relay import autopilot as auto_mod  # noqa: E402
from relay import remote as remote_mod   # noqa: E402

phases = {auto_mod.HOLDING, auto_mod.WAITING, auto_mod.COUNTING,
          auto_mod.SENDING, auto_mod.STARTING, auto_mod.DONE, auto_mod.STOPPED}
check("seven distinct phases", len(phases) == 7, str(sorted(phases)))
for phase in phases:
    check(f"{phase} is imported by the phone side",
          hasattr(remote_mod, phase.upper()), phase)

sys.exit(report.finish())
