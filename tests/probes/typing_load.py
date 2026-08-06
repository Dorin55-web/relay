"""What typing a paragraph actually costs.

Nothing here waits politely: every pause fires a translation of the whole text
so far, on its own thread, and they queue on one lock in front of the GPU. This
counts the threads and the work while a 60fps timer measures whether the GUI
thread is still getting a turn.
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
live = {"now": 0, "peak": 0, "spawned": 0, "sentences": 0}
lock = threading.Lock()


def tick():
    now = time.perf_counter()
    gaps.append((now - state["last"]) * 1000.0)
    state["last"] = now


timer = QTimer()
timer.timeout.connect(tick)
timer.start(16)

tr = TextTranslator()
tr.load()
print(f"model ready on {tr.device}\n")

real_translate = tr.translate
# Count what actually reaches the model, not what reaches translate(): the
# cache sits between the two, so measuring the outer call would report the
# work the cache exists to avoid.
class CountingTranslator:
    """Wraps the real one: its methods are read-only C attributes."""

    def __init__(self, inner):
        self._inner = inner

    def translate_batch(self, batch, *args, **kwargs):
        with lock:
            live["sentences"] += len(batch)
        return self._inner.translate_batch(batch, *args, **kwargs)


tr._translator = CountingTranslator(tr._translator)


def counting_translate(text):
    with lock:
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
    try:
        return real_translate(text)
    finally:
        with lock:
            live["now"] -= 1


tr.translate = counting_translate

# A paragraph typed the way anyone types one: a clause, a pause to think, the
# next clause. Every pause is long enough to fire the debounce.
CLAUSES = [
    "Am nevoie sa verifici tot codul.",
    "Vreau sa imi spui unde sunt problemele.",
    "Nu scrie nimic inca.",
    "Fa-mi un plan structurat mai intai.",
    "Apoi implementeaza-l pas cu pas.",
    "La final ruleaza testele si spune-mi ce a iesit.",
]

typed = []
done = threading.Event()


def type_next(index=0):
    if index >= len(CLAUSES):
        QTimer.singleShot(3000, done.set)
        return
    typed.append(CLAUSES[index])
    text = " ".join(typed)
    live["spawned"] += 1
    threading.Thread(target=lambda t=text: tr.translate(t), daemon=True).start()
    # 500ms apart: just past the 450ms debounce, which is what a thinking
    # pause looks like.
    QTimer.singleShot(500, lambda: type_next(index + 1))


QTimer.singleShot(300, type_next)


def wait():
    if done.is_set():
        app.quit()
        return
    QTimer.singleShot(100, wait)


QTimer.singleShot(400, wait)
app.exec()

during = gaps[20:]
needed = len([c for c in CLAUSES])

print(f"  clauses typed          {len(CLAUSES)}")
print(f"  translations started   {live['spawned']}")
print(f"  peak running at once   {live['peak']}")
print(f"  sentences translated   {live['sentences']}  "
      f"(only {needed} were ever new)")
print(f"  wasted work            {live['sentences'] - needed} sentences "
      f"re-translated for nothing")
print()
print(f"  worst frame gap        {max(during):.1f} ms")
print(f"  frames over 33ms       {len([g for g in during if g > 33])} of {len(during)}")
print(f"  stalls over 250ms      {len([g for g in during if g > 250])}")
print()
if live["peak"] > 1:
    print(f"  >> {live['peak']} translations were on the GPU at the same time,")
    print("     each one redoing sentences the last had already done")
