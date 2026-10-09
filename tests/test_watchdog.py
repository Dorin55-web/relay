"""Does the watchdog catch a stall, and does it name the culprit?

A detector that stays quiet during a real freeze is worse than none, so this
causes one on purpose and checks that the report points at the code that did
it - not merely that something happened.
"""
import sys
import threading
import time

import context  # noqa: E402,F401
context.isolate_state()

from relay.watchdog import QUIET_SECONDS, REPORT_MS, Watchdog

fails = []


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not condition:
        fails.append(name)


reports = []
dog = Watchdog(on_report=reports.append).start()

print("\n--- quiet when nothing is wrong ---")
time.sleep(1.0)
check("says nothing during normal running", not reports, str(len(reports)))
check("and records no stalls", dog.stalls == 0)


def hold_the_gil_in_here():
    """Build a Qt window, which is what actually holds the GIL.

    A tight Python loop does not: CPython hands the GIL over every
    sys.getswitchinterval(), five milliseconds by default, so other threads
    keep running. Only a C call that never releases it can starve them - and
    constructing a window for the first time is exactly such a call, which is
    why it was the one that showed up.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from relay.compose import Compose
    from relay.translator import TextTranslator

    win = None
    for _ in range(3):
        win = Compose(TextTranslator(), on_paste=lambda t: None,
                      target_getter=lambda: "Notepad")
        win.show()
        app.processEvents()
        win.close()
        app.processEvents()
    return win


print("\n--- a real stall: building a window ---")
hold_the_gil_in_here()
time.sleep(0.4)

check("the stall was caught", len(reports) == 1, f"{len(reports)} reports")
if reports:
    text = reports[0]
    print("\n" + "\n".join("    " + line for line in text.splitlines()[:8]))
    print()
    check("it says how long", "no GIL for" in text)
    named = any(k in text for k in ("compose", "window", "prompt_editor",
                                    "hold_the_gil_in_here"))
    check("it names where the time went", named,
          "the report has to point somewhere, not just say 'something'")
    check("it explains what that means for the mouse",
          "hook" in text and "mouse" in text)
    check("it lists more than one thread", text.count("thread ") >= 1)

print(f"\n--- a second stall inside the {QUIET_SECONDS}s quiet window ---")
before = len(reports)
hold_the_gil_in_here()
time.sleep(0.4)
check("not reported twice for one freeze", len(reports) == before,
      f"{len(reports) - before} extra")
check("stalls are counted", dog.stalls >= 1, f"{dog.stalls} stalls")

print(f"\n--- and again after the quiet window ---")
time.sleep(QUIET_SECONDS)
hold_the_gil_in_here()
time.sleep(0.4)
print(f"    (later builds cost ~30ms, so this may legitimately not stall)")
check("the detector is still running", dog._thread.is_alive())

print("\n--- the summary ---")
summary = dog.summary()
print(f"    {summary}")
check("summary reports what it saw", "stall" in summary)

dog.stop()

print("\n--- pause and resume suppresses reports while hooks are down ---")
paused_reports = []
paused_dog = Watchdog(on_report=paused_reports.append).start()
time.sleep(0.1)
paused_dog.pause()
hold_the_gil_in_here()
time.sleep(0.3)
paused_dog.resume()
time.sleep(0.1)
check("nothing reported while paused", not paused_reports, str(len(paused_reports)))
check("stalls not counted while paused", paused_dog.stalls == 0)

with paused_dog.paused():
    hold_the_gil_in_here()
    time.sleep(0.3)
time.sleep(0.1)
check("nothing reported inside paused context", not paused_reports, str(len(paused_reports)))
check("stalls remain zero", paused_dog.stalls == 0)
paused_dog.stop()

print("\n--- system sleep/standby detection ---")
sleep_reports = []
current_time = [100.0]

def mock_clock():
    return current_time[0]

sleep_dog = Watchdog(on_report=sleep_reports.append, clock=mock_clock).start()
time.sleep(0.1)
# Advance clock by 120s (simulating waking from 2 minutes of system sleep/standby)
current_time[0] += 120.0
time.sleep(0.1)
sleep_dog.stop()
resumed_msgs = [r for r in sleep_reports if "system resumed after" in r]
check("detected system sleep/standby", len(resumed_msgs) >= 1, str(sleep_reports))
check("did not count sleep as a code stall", sleep_dog.stalls == 0)
check("did not report low-level hook frozen", not any("no GIL for" in r for r in sleep_reports))

print("\n--- it says so even when nothing is wrong ---")
# An idle session writes nothing at all, so a log that simply stops leaves the
# moment it stopped bounded only by whenever the last thing happened. That is
# how an application vanishing one evening ended up undatable to the minute.
from relay.watchdog import Watchdog  # noqa: E402

beats = []
quiet = Watchdog(on_report=beats.append, heartbeat_seconds=0.2).start()
time.sleep(0.75)
quiet.stop()
alive = [b for b in beats if b.startswith("[alive]")]
check("it beat while nothing happened", len(alive) >= 2, f"{len(alive)} beats")
check("and each one carries the worst gap since the last",
      all("ms" in b for b in alive), str(alive[:2]))

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
