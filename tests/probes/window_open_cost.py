"""Why does the write window lag on opening when the others do not?

All three are frameless Qt windows of about the same size. Two of them are
built on the click; the write window is built at start-up and only shown on
the click, so if anything it should be the quick one.

A heartbeat runs on the GUI thread at 1 ms. Every gap between beats is time
the thread spent somewhere other than the event loop, which is exactly what a
frozen cursor is made of. Each window is opened in turn and the worst gap
around it reported, so the three are measured the same way.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context  # noqa: E402,F401
context.isolate_state()

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from relay import prompts as prompts_mod  # noqa: E402
from relay.config import load_config  # noqa: E402

prompts_mod.ensure_file()
config = load_config()


class Heartbeat:
    """Gaps in the GUI thread's own attention."""

    def __init__(self):
        self.last = time.perf_counter()
        self.worst = 0.0
        self.total_lost = 0.0
        self.timer = QTimer()
        self.timer.timeout.connect(self._beat)
        self.timer.start(1)

    def _beat(self):
        now = time.perf_counter()
        gap = (now - self.last) * 1000
        self.last = now
        if gap > 16:                      # anything under a frame is normal
            self.worst = max(self.worst, gap)
            self.total_lost += gap
        return

    def reset(self):
        self.last = time.perf_counter()
        self.worst = 0.0
        self.total_lost = 0.0


beat = Heartbeat()
results = []


def settle(ms=400):
    """Let the event loop run, so the heartbeat actually beats."""
    from PySide6.QtCore import QEventLoop
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def timed(label, action):
    settle(300)
    beat.reset()
    started = time.perf_counter()
    window = action()
    wall = (time.perf_counter() - started) * 1000
    settle(500)
    results.append((label, wall, beat.worst, beat.total_lost))
    print(f"  {label:<34} {wall:>7.0f} ms call, "
          f"{beat.worst:>6.0f} ms worst freeze")
    return window


def main():
    from relay import compose as compose_mod
    from relay.look_picker import open_picker
    from relay.prompt_editor import open_editor
    from relay.translator import TextTranslator

    print("\n--- what the app does at start-up ---")
    settle(300)
    beat.reset()
    started = time.perf_counter()
    translator = TextTranslator(config)
    made = (time.perf_counter() - started) * 1000
    print(f"  TextTranslator(config)             {made:>7.0f} ms "
          f"(lazy - the model loads on first use)")

    settle(200)
    beat.reset()
    started = time.perf_counter()
    compose_mod.prebuild(translator, on_paste=lambda t: None,
                         target_getter=lambda: "Notepad")
    built = (time.perf_counter() - started) * 1000
    settle(400)
    print(f"  compose prebuild                   {built:>7.0f} ms call, "
          f"{beat.worst:>6.0f} ms worst freeze")

    print("\n--- opening each window, cold ---")
    write = timed("write window (already prebuilt)", lambda: compose_mod.open_compose(
        translator, on_paste=lambda t: None, target_getter=lambda: "Notepad"))
    picker = timed("look picker (built on the click)",
                   lambda: open_picker(config["orb"], lambda s: None))
    editor = timed("prompt editor (built on the click)", open_editor)

    for w in (write, picker, editor):
        w.close()
    settle(300)

    print("\n--- opening each again, warm ---")
    timed("write window", lambda: compose_mod.open_compose(
        translator, on_paste=lambda t: None, target_getter=lambda: "Notepad"))
    timed("look picker", lambda: open_picker(config["orb"], lambda s: None))
    timed("prompt editor", open_editor)

    print("\n  the worst freeze in each case, ranked:")
    for label, wall, worst, lost in sorted(results, key=lambda r: -r[2]):
        print(f"    {worst:>6.0f} ms   {label}")

    app.quit()


QTimer.singleShot(500, main)
app.exec()
