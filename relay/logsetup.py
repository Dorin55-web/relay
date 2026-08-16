"""Keep output working when there is no console, and worth reading afterwards.

Launched via pythonw.exe (the whole point of hiding the console) sys.stdout and
sys.stderr are None, and the first print() would raise AttributeError and kill
the tool. Redirect both to a log file before anything prints.

The file is also the only account of what happened when something goes wrong
hours later, so it carries three things the console does not need:

**A time on every line.** Only the session banner was stamped, so when the app
vanished one evening the log could say what it had been doing but not when it
stopped - and "when" was the whole question.

**A note on the way out.** `atexit` runs when the interpreter shuts down, for
any ordinary end: a clean quit, an unhandled exception, sys.exit. It does not
run when something outside terminates the process. So the presence or absence
of that one line separates "it quit" from "it was killed", which nothing in
the log could distinguish before.

**One generation kept.** The old file was deleted once it grew past the limit,
which threw away the history of the very sessions worth reading.
"""

import atexit
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "relay.log"
PREVIOUS_LOG_PATH = PROJECT_ROOT / "relay.previous.log"
MAX_LOG_BYTES = 1_000_000


class _Tee:
    """Write to the log file and, when present, the real console too.

    The file gets a timestamp at the start of every line; the console does
    not, because there you are watching it happen and the clock is the wall.
    """

    def __init__(self, stream, console):
        self.stream = stream
        self.console = console
        # print() arrives in pieces - the text, then the newline - so whether
        # the next character starts a line has to be carried between calls.
        self._new_line = True

    def _stamped(self, data):
        out = []
        for part in data.splitlines(keepends=True):
            if self._new_line and part.strip():
                out.append(time.strftime("%H:%M:%S "))
            out.append(part)
            self._new_line = part.endswith("\n")
        return "".join(out)

    def write(self, data):
        try:
            self.stream.write(self._stamped(data))
            self.stream.flush()
        except Exception:
            pass
        if self.console is not None:
            try:
                self.console.write(data)
            except Exception:
                pass
        return len(data)

    def flush(self):
        for target in (self.stream, self.console):
            if target is not None:
                try:
                    target.flush()
                except Exception:
                    pass

    def isatty(self):
        return False


def _rotate():
    """Keep the last full log rather than deleting it.

    Deleting was cheaper and lost exactly the thing you go looking for: the
    sessions before the one that went wrong.
    """
    try:
        if not LOG_PATH.exists() or LOG_PATH.stat().st_size <= MAX_LOG_BYTES:
            return
        os.replace(LOG_PATH, PREVIOUS_LOG_PATH)
    except OSError:
        try:
            LOG_PATH.unlink()      # rather than let it grow without bound
        except OSError:
            pass


def setup_output():
    """Point stdout/stderr at the log file. Safe to call once, early."""
    _rotate()

    try:
        handle = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None  # read-only location; leave the streams alone

    sys.stdout = _Tee(handle, sys.__stdout__)
    sys.stderr = _Tee(handle, sys.__stderr__)

    # Runs for every ordinary end and for none of the sudden ones. See the
    # module docstring: this line's absence is the evidence.
    atexit.register(lambda: print("[exit] python is shutting down"))
    return LOG_PATH
