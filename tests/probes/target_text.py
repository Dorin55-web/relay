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
difference between the two snapshots is the signal the autopilot needs.

    python tests/probes/target_text.py

Nothing here passes or fails. It prints what it found.
"""
import ctypes
import ctypes.wintypes as wt
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

from relay import uia                                        # noqa: E402
from relay.target import window_class, window_pid, window_title  # noqa: E402

FOCUS_SECONDS = 5
LINES_SHOWN = 30
MAX_BUTTONS = 400

user32 = ctypes.windll.user32

# The generated comtypes module normally carries these, but the numbers are
# fixed by the UIA spec and a missing constant should not stop the probe.
IDS = {
    "UIA_TextPatternId": 10014,
    "UIA_IsTextPatternAvailablePropertyId": 30040,
    "UIA_NamePropertyId": 30005,
    "UIA_ControlTypePropertyId": 30003,
    "UIA_ButtonControlTypeId": 50000,
    "TreeScope_Descendants": 4,
}


def uia_id(UIA, name):
    return getattr(UIA, name, IDS[name])


def process_name(pid):
    """Executable behind a window, so the report names the app, not a number."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "?"
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return "?"
    finally:
        kernel32.CloseHandle(handle)


def read_text(auto, UIA, root):
    """All text the window publishes, and which element published it.

    The root element usually supports nothing. In Windows Terminal the text
    provider hangs off the terminal control inside; in a Chromium app it is
    the document. So ask for every descendant that claims a TextPattern and
    take whichever gave the most, rather than assuming where it lives.
    """
    condition = auto.CreatePropertyCondition(
        uia_id(UIA, "UIA_IsTextPatternAvailablePropertyId"), True)
    try:
        found = root.FindAll(uia_id(UIA, "TreeScope_Descendants"), condition)
    except Exception as exc:
        return "", f"FindAll failed: {exc}", 0

    candidates = []
    count = found.Length if found else 0
    for i in range(count):
        element = found.GetElement(i)
        try:
            pattern = element.GetCurrentPattern(uia_id(UIA, "UIA_TextPatternId"))
            if not pattern:
                continue
            pattern = pattern.QueryInterface(UIA.IUIAutomationTextPattern)
            # -1 means no limit. A terminal can hand back its whole scrollback.
            text = pattern.DocumentRange.GetText(-1) or ""
            if text.strip():
                candidates.append((len(text), element.CurrentName or "(unnamed)", text))
        except Exception:
            continue

    if not candidates:
        return "", "no element returned any text", count
    candidates.sort(key=lambda c: -c[0])
    return candidates[0][2], candidates[0][1], count


def read_buttons(auto, UIA, root):
    """Names of every button in the tree.

    A cache request is not an optimisation here. Reading Name off an element
    is a call into the other process; on an Electron tree that is thousands of
    round trips and the probe appears to hang. Asking for the names up front
    fetches them in one.
    """
    condition = auto.CreatePropertyCondition(
        uia_id(UIA, "UIA_ControlTypePropertyId"),
        uia_id(UIA, "UIA_ButtonControlTypeId"))
    cache = auto.CreateCacheRequest()
    cache.AddProperty(uia_id(UIA, "UIA_NamePropertyId"))
    try:
        found = root.FindAllBuildCache(
            uia_id(UIA, "TreeScope_Descendants"), condition, cache)
    except Exception as exc:
        return [f"(FindAllBuildCache failed: {exc})"]

    names = []
    for i in range(min(found.Length if found else 0, MAX_BUTTONS)):
        try:
            name = clean(found.GetElement(i).CachedName or "")
        except Exception:
            continue
        if name:
            names.append(name)
    return names


def snapshot(hwnd, label):
    auto, UIA = uia._uia()
    if auto is None:
        raise SystemExit("UI Automation is unavailable - nothing to probe")

    started = time.perf_counter()
    root = auto.ElementFromHandle(hwnd)
    text, source, providers = read_text(auto, UIA, root)
    buttons = read_buttons(auto, UIA, root)
    return {
        "label": label,
        "title": window_title(hwnd),
        "text": text,
        "source": source,
        "providers": providers,
        "buttons": buttons,
        "seconds": time.perf_counter() - started,
    }


def clean(line):
    """Readable on a console, and comparable between snapshots.

    Chromium marks every embedded object with U+FFFC, and a tree walked twice
    does not place them identically. Left in, they turn lines that are really
    the same into differences, which is the one thing this probe must not do.
    """
    line = line.replace("￼", " ").replace("​", "")
    line = "".join(c if c.isprintable() or c == "\t" else " " for c in line)
    return " ".join(line.split())


def useful_lines(text):
    return [c for c in (clean(ln) for ln in text.splitlines()) if c]


def report(shot):
    lines = useful_lines(shot["text"])
    print(f"\n=== {shot['label']} ===   ({shot['seconds']:.2f}s)")
    print(f"  title      {shot['title']!r}")
    print(f"  text       {len(shot['text'])} chars, {len(lines)} non-blank lines")
    print(f"  from       {shot['source']}  ({shot['providers']} element(s) offer text)")
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
    print("  'esc to interrupt', 'Stop', 'Cancel' - over anything that carries")
    print("  the model's own words, which will differ every run.")


print(f"Switch to the window you want to read. Locking on in {FOCUS_SECONDS}s.")
for remaining in range(FOCUS_SECONDS, 0, -1):
    print(f"  {remaining}...", end="\r", flush=True)
    time.sleep(1)

hwnd = user32.GetForegroundWindow()
if not hwnd:
    raise SystemExit("no foreground window")

pid = window_pid(hwnd)
print(f"\nlocked on  {window_title(hwnd)!r}")
print(f"  hwnd {hwnd}   class {window_class(hwnd)!r}   {process_name(pid)} (pid {pid})")
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
