"""Puts the project on the path, and keeps the tests off your real files.

Import this first in every suite, before anything from `relay`:

    import context                    # noqa: F401
    context.isolate_state()

These are not unit tests against mocks. They build real Qt widgets against the
real modules, which is the only way most of what they check can be checked at
all - whether Windows will let you click a transparent pixel, whether a
tooltip waits, whether closing one window takes the app down with it. The cost
of that is that the modules under test write to disk, and the tests have to say
where.
"""

import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

_isolated = None


def isolate_state():
    """Point everything that writes to disk at a throwaway directory.

    Not a nicety. `test_looks` grows the orb to 120 pixels, which pushes the
    saved anchor off the edge of the screen; the clamp that pulls it back then
    writes the new position out. Measured before this existed: running the
    suite moved the real orb 31 pixels to the left. A test run that rearranges
    your desktop is a test run you stop doing.

    Returns the directory, and is safe to call more than once.
    """
    global _isolated
    if _isolated is not None:
        return _isolated

    from relay import overlay, prompts

    _isolated = Path(tempfile.mkdtemp(prefix="relay-tests-"))
    overlay.POSITION_FILE = _isolated / "orb_position.json"
    prompts.PROMPTS_PATH = _isolated / "prompts.json"
    return _isolated


def silence_dialogs(module, answer_yes=True):
    """Stop a modal message box from waiting for a click nobody can give.

    QMessageBox.warning and friends run their own event loop and do not return
    until dismissed. A suite that triggers one stops dead - and looks like a
    slow test rather than a stuck one. test_editor exercises exactly that path
    on purpose, and its timing wandered between 6s, 33s, 205s and a 240s
    timeout depending on whether a stray keypress happened to land on the
    dialog.

    Replaces the name in the module under test, so the code being tested is
    untouched, and returns the list the calls are recorded in - which lets a
    test assert that the warning it expected actually happened, rather than
    only that the function returned False.
    """
    from PySide6.QtWidgets import QMessageBox

    seen = []

    class Recorded:
        Yes, No = QMessageBox.Yes, QMessageBox.No
        Ok, Cancel = QMessageBox.Ok, QMessageBox.Cancel

        @staticmethod
        def _note(kind, reply):
            def dialog(_parent, title, text, *args, **kwargs):
                seen.append((kind, title, text))
                return reply
            return dialog

    Recorded.warning = Recorded._note("warning", QMessageBox.Ok)
    Recorded.critical = Recorded._note("critical", QMessageBox.Ok)
    Recorded.information = Recorded._note("information", QMessageBox.Ok)
    Recorded.question = Recorded._note(
        "question", QMessageBox.Yes if answer_yes else QMessageBox.No)

    module.QMessageBox = Recorded
    return seen


class Report:
    """The pass/fail bookkeeping every suite here does the same way."""

    def __init__(self):
        self.failed = []

    def check(self, name, condition, detail=""):
        mark = "OK  " if condition else "FAIL"
        print(f"  {mark}  {name}{'  ' + detail if detail else ''}")
        if not condition:
            self.failed.append(name)
        return bool(condition)

    def finish(self):
        print("\n" + ("ALL PASS" if not self.failed
                      else f"FAILED: {self.failed}"))
        return 1 if self.failed else 0
