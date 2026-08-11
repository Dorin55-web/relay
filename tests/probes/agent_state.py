"""Watch the detector decide, live, while you use the application.

target_text.py answered what a window publishes. This answers the question
after it: given the profile written from those measurements, does Relay call
the state correctly every time, without being told when to look?

Point it at Antigravity or opencode, then work normally. Every change of state
prints a line, with the evidence that caused it. What you want to see is the
flip to `busy` the moment you send a prompt, and the flip to `idle` the moment
the answer finishes - and nothing at all in between.

    python tests/probes/agent_state.py

The failure worth watching for is a flip to `idle` while the agent is only
pausing. That is the one that would make an autopilot talk over itself.
"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from relay import agent                                          # noqa: E402
from relay.target import window_process, window_title            # noqa: E402

FOCUS_SECONDS = 5
POLL_SECONDS = 1.0

user32 = ctypes.windll.user32

print(f"Switch to the window you want to watch. Locking on in {FOCUS_SECONDS}s.")
for remaining in range(FOCUS_SECONDS, 0, -1):
    print(f"  {remaining}...", end="\r", flush=True)
    time.sleep(1)

hwnd = user32.GetForegroundWindow()
if not hwnd:
    raise SystemExit("no foreground window")

print(f"\nlocked on  {window_title(hwnd)!r}   {window_process(hwnd)}")

profile = agent.profile_for(hwnd)
if profile is None:
    print("\nNo profile recognises this window. To write one it needs:")
    print(f"  process         {window_process(hwnd)!r}")
    print(f"  title_contains  something stable out of {window_title(hwnd)!r}")
    print("\nRun target_text.py against it to find the busy and idle strings.")
    raise SystemExit(1)

print(f"profile    {profile['name']}")
print(f"  busy when  {profile.get('busy')}")
print(f"  idle when  {profile.get('idle')}")
print("\nWatching. Ctrl+C to stop.\n")

last = None
since = time.time()
changes = 0
try:
    while True:
        if not user32.IsWindow(hwnd):
            print("\nthat window is gone")
            break

        started = time.perf_counter()
        seen = agent.read(hwnd)
        now = agent.state(hwnd, profile, seen)
        cost = time.perf_counter() - started

        if now != last:
            held = time.time() - since
            stamp = time.strftime("%H:%M:%S")
            if last is not None:
                print(f"  {stamp}  {last} -> {now}    (held {held:.1f}s)")
            else:
                print(f"  {stamp}  starts {now}")
            # What the window actually said, so a wrong call can be traced to
            # the string that caused it rather than argued about.
            if now == agent.UNKNOWN:
                tail = [ln for ln in seen["text"].splitlines() if ln][-3:]
                print(f"            neither rule matched. last lines: {tail}")
            changes += 1
            last, since = now, time.time()

        print(f"    {last}   {cost * 1000:.0f}ms   "
              f"{len(seen['text'])} chars, {len(seen['buttons'])} buttons   ",
              end="\r", flush=True)
        time.sleep(POLL_SECONDS)
except KeyboardInterrupt:
    print(f"\n\nstopped after {changes} state change(s)")
