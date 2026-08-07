"""The dot must outlive the prompt editor.

Qt.Tool windows do not count as primary windows, so closing a normal window
while the dot is the only thing left quits the whole application unless
quitOnLastWindowClosed is off. That is what made the dot disappear.
"""
import sys

import context  # noqa: E402,F401
context.isolate_state()

from PySide6.QtWidgets import QApplication

from relay import prompts as prompts_mod

fails = []


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not condition:
        fails.append(name)


# PROMPTS_PATH already points at a temp directory - see context.py. This
# only has to put the ten defaults in it. orb.quit() below saves the orb's
# position too, which is the other reason that isolation matters here.
prompts_mod.ensure_file()

app = QApplication.instance() or QApplication(sys.argv)

from relay.overlay import Orb  # noqa: E402
import relay.prompt_editor as editor_mod  # noqa: E402
from relay.prompt_editor import PromptEditor  # noqa: E402

# The save below is meant to succeed, so no dialog should appear at all. This
# is here so that if one ever does, the suite fails instead of hanging on a
# modal waiting for a click. See context.silence_dialogs.
dialogs = context.silence_dialogs(editor_mod)

quit_calls = []
orb = Orb(
    on_toggle=lambda: None,
    on_quit=lambda: quit_calls.append(True),
    tooltip="F9",
    prompts_getter=prompts_mod.load,
    on_prompt=lambda text: None,
    on_edit_prompts=lambda: None,
)

print("\n--- the setting itself ---")
check("app will not quit on last window closed", not app.quitOnLastWindowClosed())

print("\n--- close the editor ---")
editor = PromptEditor()
editor.show()
app.processEvents()
check("editor is up", editor.isVisible())
check("dot is up", orb.isVisible())

editor.close()
app.processEvents()
check("editor gone", not editor.isVisible())
check("dot survived", orb.isVisible())
check("shutdown was not called", not quit_calls)

print("\n--- save-then-close, the path that broke ---")
editor = PromptEditor()
editor.show()
app.processEvents()
editor.list.setCurrentRow(0)
editor.text.setPlainText("test")
saved = editor._save()          # saves, then closes itself
app.processEvents()
check("save succeeded", saved is True)
check("and raised no dialog on the way", not dialogs, str(dialogs))
check("editor closed itself", not editor.isVisible())
check("dot still there after save", orb.isVisible())
check("still no shutdown", not quit_calls)
check("edit landed on disk", prompts_mod.load()[0]["text"] == "test")

print("\n--- with the event loop actually running ---")
# The checks above cannot fail on their own: app.quit() only leaves the event
# loop, it does not hide widgets, so isVisible() would still say True on a
# quitting app. This is the one that would have caught the bug - if closing the
# editor quits, exec() returns before the later timer ever fires.
from PySide6.QtCore import QTimer  # noqa: E402

reached = []
editor = PromptEditor()
editor.show()
QTimer.singleShot(150, editor.close)
QTimer.singleShot(600, lambda: reached.append("still alive"))
QTimer.singleShot(900, app.quit)
app.exec()
check("event loop outlived the editor closing", reached == ["still alive"])

print("\n--- and the same for a message box ---")
# QMessageBox is a window too; the failed-save path opens one.
reached.clear()
from PySide6.QtWidgets import QMessageBox  # noqa: E402

box = QMessageBox()
box.setText("transient")
box.show()
QTimer.singleShot(150, box.close)
QTimer.singleShot(600, lambda: reached.append("still alive"))
QTimer.singleShot(900, app.quit)
app.exec()
check("event loop outlived a dialog closing", reached == ["still alive"])

print("\n--- quitting still works on purpose ---")
check("timer running before quit", orb._timer.isActive())
orb.quit()
check("on_quit fired", quit_calls == [True])
check("timer stopped", not orb._timer.isActive())

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
