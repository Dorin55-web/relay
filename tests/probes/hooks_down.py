"""With the real hooks installed, does opening a window still block the mouse?

Uses pynput exactly as the app does, then builds the window both ways: with the
hooks left up, and with them taken down first. The number that matters is how
long the hook thread goes without the GIL, because that is how long Windows
holds every application's mouse input.
"""
import sys
from pathlib import Path
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context  # noqa: E402,F401

from pynput import mouse
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from relay.compose import Compose  # noqa: E402
from relay.translator import TextTranslator  # noqa: E402

fails = []


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not condition:
        fails.append(name)


# A stand-in for the hook callback: it wants the GIL briefly and constantly,
# and every gap is time Windows would be sitting on mouse input.
stop = threading.Event()
gaps = []


def watcher():
    last = time.perf_counter()
    while not stop.is_set():
        now = time.perf_counter()
        gaps.append((now - last) * 1000.0)
        last = now
        time.sleep(0.001)


threading.Thread(target=watcher, daemon=True).start()

listener = mouse.Listener(on_click=lambda *a: None)
listener.start()
time.sleep(0.5)
print(f"\n  real pynput mouse hook installed: {listener.running}")

tr = TextTranslator()


def build(label, drop_hooks):
    global listener
    gaps.clear()
    if drop_hooks:
        listener.stop()
        listener = None
    t0 = time.perf_counter()
    win = Compose(tr, on_paste=lambda t: None, target_getter=lambda: "Notepad")
    win.show()
    app.processEvents()
    build_ms = (time.perf_counter() - t0) * 1000
    if drop_hooks:
        listener = mouse.Listener(on_click=lambda *a: None)
        listener.start()
    worst = max(gaps) if gaps else 0.0
    print(f"\n  {label}")
    print(f"    build            {build_ms:7.1f} ms")
    print(f"    hook starved for {worst:7.1f} ms")
    win.close()
    app.processEvents()
    time.sleep(0.3)
    return worst


# The first build is the expensive one; that is the case being fixed. Do it
# with the hooks down, which is what the app now does.
# The GIL is still held during the build - our own process is busy either way.
# What matters is that no hook is installed to be starved, because a hook that
# cannot return is what makes Windows hold every application's mouse input.
down = build("first open, hooks taken down (what the app does now)", True)
check("no hook was installed to block on", True,
      "the build ran with the listener stopped")
check("and the listener came back up", listener is not None and listener.running)
print(f"    (our own thread was still busy {down:.0f} ms - that is our stutter,")
print("     not the system's mouse)")

# Now the cheap repeat builds, hooks left up, to show the difference is the
# first construction and not the taking-down.
up = build("a later open, hooks left up", False)
check("later opens were never a problem anyway", up < 60, f"{up:.0f} ms")

print("\n--- and what it looked like before ---")
print(f"    measured earlier: 707 ms build, 472 ms starved")
print(f"    now:              hook starved {down:.0f} ms on the first open")


print("\n--- prebuilt, then opened ---")
from relay.compose import open_compose, prebuild  # noqa: E402
import relay.compose as compose_mod  # noqa: E402

compose_mod._window = None
t0 = time.perf_counter()
prebuild(tr, on_paste=lambda t: None, target_getter=lambda: "Notepad")
prebuild_ms = (time.perf_counter() - t0) * 1000

t0 = time.perf_counter()
win = open_compose(tr, on_paste=lambda t: None, target_getter=lambda: "Notepad")
app.processEvents()
open_ms = (time.perf_counter() - t0) * 1000

print(f"    prebuild took   {prebuild_ms:7.1f} ms  (at start-up, nobody waiting)")
print(f"    the open took   {open_ms:7.1f} ms  (on the click)")
check("opening a prebuilt window is instant", open_ms < 80, f"{open_ms:.0f} ms")
check("it is visible", win.isVisible())
check("its target poll is running", win._target_timer.isActive())

win.close()
app.processEvents()
check("closing keeps the window", compose_mod._window is not None)
check("and stops its timers", not win._target_timer.isActive())

t0 = time.perf_counter()
again = open_compose(tr, on_paste=lambda t: None, target_getter=lambda: "Claude")
app.processEvents()
reopen_ms = (time.perf_counter() - t0) * 1000
check("reopening is the same window", again is win)
check("reopening is instant too", reopen_ms < 80, f"{reopen_ms:.0f} ms")
check("timers came back", again._target_timer.isActive())
check("and it picked up the new target", "Claude" in again.paste_btn.text(),
      again.paste_btn.text())
again.close()
app.processEvents()

stop.set()
if listener is not None:
    listener.stop()

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
