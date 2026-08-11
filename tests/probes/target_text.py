"""What can we actually read out of another application's window?

The autopilot has to know when the agent in the other window has finished. Every
way of answering that is a guess except one: read what the window says. Windows
publishes that for screen readers, and UI Automation is how you ask.

Whether it answers is a different question for every app, and not one you can
settle by reading code. A terminal may expose its whole screen buffer or
nothing. An Electron app may hand over its accessibility tree or keep it off
until something asks twice. So: point this at a window and it reports what came
back.

Run it once while the agent is working, once after it has finished, and the
difference between the two snapshots is the signal the autopilot needs. This is
where the profiles in relay/agent.py came from - `Cancel (Ctrl+D)` for
Antigravity, `esc interrupt` for opencode - and where a new application's
profile comes from too.

    python tests/probes/target_text.py

Nothing here passes or fails. It prints what it found.
"""
import ctypes
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# A Chromium tree is full of characters the console cannot encode, and a
# traceback out of print() would lose a snapshot that was read correctly.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from relay import uia                                             # noqa: E402
from relay.target import (window_class, window_pid,               # noqa: E402
                          window_process, window_title)

FOCUS_SECONDS = 5
LINES_SHOWN = 30

# Something this probe prints and nothing else does. If it comes back from a
# terminal, we read our own console.
#
# Only from a terminal. Anything with a transcript can quote this text
# perfectly innocently - pasting a run of this probe into a chat window puts
# every word of it in that window's tree - and a warning that cries wolf on
# the app you are actually profiling is worse than no warning.
OWN_OUTPUT = "label this state"
TERMINAL_CLASSES = {"CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass"}

# What a window with no accessibility tree still reports: its own frame,
# which comes from Windows rather than from the application.
WINDOW_CHROME = {"minimise", "minimize", "maximise", "maximize", "restore",
                 "close"}

user32 = ctypes.windll.user32


def useful_lines(text):
    return [c for c in (uia.clean(ln) for ln in text.splitlines()) if c]


def snapshot(hwnd, label):
    started = time.perf_counter()
    text, source = uia.window_text(hwnd)
    buttons = uia.window_buttons(hwnd)
    return {
        "label": label,
        "title": window_title(hwnd),
        "class": window_class(hwnd),
        "text": text,
        "source": source,
        "buttons": buttons,
        "seconds": time.perf_counter() - started,
    }


def report(shot):
    lines = useful_lines(shot["text"])
    if OWN_OUTPUT in shot["text"] and shot["class"] in TERMINAL_CLASSES:
        print("\n  !! this snapshot contains this probe's own output.")
        print("     A Windows Terminal window is one handle for all its tabs,")
        print("     and UI Automation reads whichever tab is in front - so a")
        print("     target sharing a window with this console reads as this")
        print("     console. Put the target in a separate terminal WINDOW")
        print("     (Ctrl+Shift+N), not another tab.")

    if not lines and len(shot["buttons"]) <= len(WINDOW_CHROME):
        print("\n  !! nothing but the window frame came back.")
        print("     Chromium builds its accessibility tree only once something")
        print("     asks for it, and this request is what asked - so a cold")
        print("     window reads as empty and the next read is fine. This is")
        print("     not a state: take this one again.")

    print(f"\n=== {shot['label']} ===   ({shot['seconds']:.2f}s)")
    print(f"  title      {shot['title']!r}")
    print(f"  text       {len(shot['text'])} chars, {len(lines)} non-blank lines")
    print(f"  from       {shot['source']}")
    print(f"  buttons    {len(shot['buttons'])}")
    if lines:
        print(f"\n  last {min(LINES_SHOWN, len(lines))} lines:")
        for line in lines[-LINES_SHOWN:]:
            print(f"    | {line[:150]}")
    if shot["buttons"]:
        print("\n  button names:")
        for name in shot["buttons"][:60]:
            print(f"    - {name[:100]}")
    return lines


def compare(older, newer):
    """The whole point: what is present in one state and absent in the other."""
    print(f"\n\n########  {older['label']}  ->  {newer['label']}  ########")

    if older["title"] != newer["title"]:
        print(f"\n  the title changed: {older['title']!r} -> {newer['title']!r}")

    def only_in(a, b, what, side):
        extra = [x for x in dict.fromkeys(a) if x not in set(b)]
        print(f"\n  {what} only while {side}: {len(extra)}")
        for x in extra[:25]:
            print(f"    + {x[:150]}")

    a_lines = useful_lines(older["text"])
    b_lines = useful_lines(newer["text"])
    only_in(a_lines, b_lines, "lines", older["label"])
    only_in(b_lines, a_lines, "lines", newer["label"])
    only_in(older["buttons"], newer["buttons"], "buttons", older["label"])
    only_in(newer["buttons"], older["buttons"], "buttons", newer["label"])

    print("\n  A line or button that shows up on exactly one side is a candidate")
    print("  signal. Prefer one that is short, fixed, and clearly about state -")
    print("  'esc interrupt', 'Stop', 'Cancel' - over anything that carries")
    print("  the model's own words, which will differ every run.")


print(f"Switch to the window you want to read. Locking on in {FOCUS_SECONDS}s.")
for remaining in range(FOCUS_SECONDS, 0, -1):
    print(f"  {remaining}...", end="\r", flush=True)
    time.sleep(1)

hwnd = user32.GetForegroundWindow()
if not hwnd:
    raise SystemExit("no foreground window")

print(f"\nlocked on  {window_title(hwnd)!r}")
print(f"  hwnd {hwnd}   class {window_class(hwnd)!r}   "
      f"{window_process(hwnd)} (pid {window_pid(hwnd)})")
print("\nThe window is held by handle, so it does not need to stay in front -")
print("come back here and take a snapshot whenever it is in a state worth naming.")

# Deliberately outside the project. A snapshot is the full contents of one of
# your windows - conversation titles, file names, whatever was on screen - and
# that is not something to leave sitting in a git repository.
dump_dir = Path(tempfile.gettempdir()) / "relay-target-text"
shots = []
while True:
    label = input("\nlabel this state (busy / idle / waiting), or ENTER to stop: ").strip()
    if not label:
        break
    if not user32.IsWindow(hwnd):
        print("that window is gone")
        break
    shot = snapshot(hwnd, label)
    report(shot)
    shots.append(shot)

    dump_dir.mkdir(exist_ok=True)
    path = dump_dir / f"{len(shots):02d}-{label.replace(' ', '_')}.txt"
    path.write_text(shot["text"], encoding="utf-8", errors="replace")
    print(f"\n  full text written to {path}")

for older, newer in zip(shots, shots[1:]):
    compare(older, newer)

if not shots:
    print("\nnothing captured")
elif not any(s["text"] for s in shots):
    print("\n\nNo text came back from any snapshot. That is an answer too:")
    print("  - Chromium apps publish their tree only once something asks for it,")
    print("    and some need to be started with --force-renderer-accessibility.")
    print("  - If the buttons came through but the text did not, the state is")
    print("    still readable: watch for a Stop or Cancel button appearing.")
    print("  - If neither came through, this window needs the pixel fallback.")
