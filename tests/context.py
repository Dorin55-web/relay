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
