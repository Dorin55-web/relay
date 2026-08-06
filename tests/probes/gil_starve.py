"""Does building the window starve another Python thread?

Relay keeps a low-level mouse hook (pynput, for target tracking). Windows runs
that hook for every mouse message in the system, and the callback is Python -
so it needs the GIL. A thread that cannot get the GIL cannot return from the
hook, and until it returns Windows delivers no mouse input to anybody. That is
a frozen cursor everywhere, not just in our window.

This stands in a watcher thread that should tick every millisecond, builds the
window on the main thread, and reports the longest the watcher went without a
turn. That gap is how long the real hook would have been stuck.
"""
import sys
from pathlib import Path
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context  # noqa: E402,F401

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from relay.compose import Compose  # noqa: E402
from relay.translator import TextTranslator  # noqa: E402

stop = threading.Event()
gaps = []


def watcher():
    """Stands in for the hook callback: wants the GIL, briefly, constantly."""
    last = time.perf_counter()
    while not stop.is_set():
        now = time.perf_counter()
        gaps.append((now - last) * 1000.0)
        last = now
        time.sleep(0.001)


thread = threading.Thread(target=watcher, daemon=True)
thread.start()
time.sleep(0.4)
baseline = max(gaps[10:]) if len(gaps) > 20 else 0.0
gaps.clear()

tr = TextTranslator()

print(f"\n  watcher baseline, nothing happening: {baseline:.1f} ms worst gap\n")

for attempt in (1, 2, 3):
    gaps.clear()
    t0 = time.perf_counter()
    win = Compose(tr, on_paste=lambda t: None, target_getter=lambda: "Notepad")
    win.show()
    app.processEvents()
    build_ms = (time.perf_counter() - t0) * 1000
    worst = max(gaps) if gaps else 0.0
    over = len([g for g in gaps if g > 50])
    print(f"  build {attempt}:  {build_ms:7.1f} ms to construct and show")
    print(f"            watcher's worst gap {worst:7.1f} ms"
          f"   ({over} gaps over 50ms)")
    if worst > 200:
        print("            >> the hook would have been stuck this long,")
        print("               and the whole system's mouse with it")
    win.close()
    app.processEvents()
    time.sleep(0.3)

stop.set()
print()
