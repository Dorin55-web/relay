"""Opening the write window must not stall the first time.

This has to be a suite of its own, and the prebuild has to be the first thing
in the process that shows a window - which is the whole point. The cost being
guarded against is paid once per process, not once per window, so a check for
it that runs after some other test has already opened something passes no
matter what the code does. An earlier version of this lived at the end of
test_compose and did exactly that: it reported 27ms and would have gone on
reporting 27ms with the fix deleted.

What it guards: building the window at start-up covers constructing the widget
tree, about 200ms, and nothing else. The first time any window in the process
is actually shown costs another 400-500 - Qt creating the native window and
realising its paint backend. Measured across all three of Relay's windows, in
whichever order: 416ms for the write window, 543 for the look picker, 432 for
the prompt editor, always to whichever went first. prebuild() now shows the
window once, off screen and without taking focus, so no click ever pays it.
"""
import sys
import time

import context  # noqa: E402,F401
context.isolate_state()

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from relay import compose as compose_mod  # noqa: E402
from relay.config import load_config  # noqa: E402
from relay.translator import TextTranslator  # noqa: E402

report = context.Report()
check = report.check

# Lazy: this constructs nothing and loads no model, so the timings below are
# the window's own and not the translator's.
translator = TextTranslator(load_config())
check("the translator is not loaded by constructing it", not translator.ready)

print("\n--- what start-up pays ---")
started = time.perf_counter()
window = compose_mod.prebuild(translator, on_paste=lambda text: None,
                              target_getter=lambda: "Notepad")
app.processEvents()
build_ms = (time.perf_counter() - started) * 1000
print(f"  prebuild, warm-up included: {build_ms:.0f} ms")

check("it is built", window is not None)
check("and left hidden", not window.isVisible())
check("not parked off screen where it was warmed",
      window.x() > -1000 and window.y() > -1000, f"{window.x()},{window.y()}")

# A window that has never been shown has no position of its own. Restoring the
# geometry from before the warm-up therefore pins it to the top-left corner -
# which is what the first attempt at this did.
screen = app.primaryScreen().availableGeometry()
centre = window.frameGeometry().center()
off = max(abs(centre.x() - screen.center().x()),
          abs(centre.y() - screen.center().y()))
check("and still centred, the way an unwarmed window opens", off < 120,
      f"{off}px off centre")

print("\n--- what the click pays ---")
started = time.perf_counter()
compose_mod.open_compose(translator, on_paste=lambda text: None,
                         target_getter=lambda: "Notepad")
app.processEvents()
open_ms = (time.perf_counter() - started) * 1000
# Without the warm-up this is 400-550ms on the machine it was written on. The
# line is drawn well clear of both, so a slower machine does not fail it and a
# regression cannot pass it.
check("the first open is quick", open_ms < 200, f"{open_ms:.0f} ms")
print(f"  first open: {open_ms:.0f} ms, against {build_ms:.0f} ms at start-up")

print("\n--- and the second is no worse ---")
compose_mod._window.close()
app.processEvents()
started = time.perf_counter()
compose_mod.open_compose(translator, on_paste=lambda text: None,
                         target_getter=lambda: "Notepad")
app.processEvents()
again_ms = (time.perf_counter() - started) * 1000
check("reopening stays quick", again_ms < 200, f"{again_ms:.0f} ms")
compose_mod._window.close()
app.processEvents()

sys.exit(report.finish())
