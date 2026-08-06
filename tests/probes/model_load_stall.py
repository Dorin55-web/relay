"""How long does the GUI thread stop for when the text model first loads?

A 60fps timer ticks throughout. Every gap between ticks longer than a frame is
a dropped frame; a gap of hundreds of milliseconds is a frozen cursor. The
translation runs exactly the way compose.py runs it - a plain worker thread -
so this measures the real thing, not an approximation of it.
"""
import sys
from pathlib import Path
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context  # noqa: E402,F401

from relay.cuda_setup import enable_cuda_dlls

enable_cuda_dlls()

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from relay.translator import TextTranslator

app = QApplication.instance() or QApplication(sys.argv)

gaps = []
state = {"last": time.perf_counter()}


def tick():
    now = time.perf_counter()
    gaps.append((now - state["last"]) * 1000.0)
    state["last"] = now


timer = QTimer()
timer.timeout.connect(tick)
timer.start(16)          # the same 60fps the orb runs at

# The realistic case: Whisper is already resident on the GPU, exactly as it is
# in the running app, and the text model has to find room beside it.
print("loading Whisper first, as the real app has it...")
from relay.config import load_config
from relay.transcriber import WhisperEngine

_engine = WhisperEngine(load_config())
_engine.load()
print("Whisper resident; now the text model loads next to it\n")

tr = TextTranslator()
result = {}


def work():
    t0 = time.perf_counter()
    try:
        result["text"] = tr.translate("Verifica tot codul si spune-mi ce ai gasit.")
    except Exception as exc:
        result["error"] = str(exc)
    result["seconds"] = time.perf_counter() - t0


def start_work():
    state["worker_started"] = time.perf_counter()
    threading.Thread(target=work, daemon=True).start()


QTimer.singleShot(700, start_work)


def finish():
    if "seconds" not in result and time.perf_counter() - state.get(
        "worker_started", 0
    ) < 120:
        QTimer.singleShot(200, finish)
        return
    app.quit()


QTimer.singleShot(1200, finish)
app.exec()

# Only the gaps after the worker started matter; the ones before are the
# baseline this is measured against.
start_index = next(
    (i for i, _ in enumerate(gaps) if i > 40), 0
)
during = gaps[start_index:]
baseline = gaps[5:start_index] or [16.0]

print()
print(f"  translation took   {result.get('seconds', 0):.1f}s")
print(f"  result             {result.get('text', result.get('error'))!r}")
print()
print(f"  baseline frame gap {max(baseline):6.1f} ms worst, "
      f"{sum(baseline)/len(baseline):5.1f} ms mean")
print(f"  during the load    {max(during):6.1f} ms worst, "
      f"{sum(during)/len(during):5.1f} ms mean")
print()

dropped = [g for g in during if g > 33]      # missed at least one frame
frozen = [g for g in during if g > 250]      # long enough to feel stuck
print(f"  frames dropped     {len(dropped)} of {len(during)}")
print(f"  stalls over 250ms  {len(frozen)}")
if frozen:
    print(f"  worst stalls       {[round(g) for g in sorted(frozen)[-6:]]}")
print()
worst = max(during)
if worst > 250:
    print(f"  >> the GUI thread stopped for {worst:.0f} ms - that is the freeze")
elif worst > 60:
    print(f"  >> {worst:.0f} ms hitch: visible, not a freeze")
else:
    print(f"  >> no stall worth seeing ({worst:.0f} ms worst)")
