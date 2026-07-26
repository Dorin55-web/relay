"""Keep output working when there is no console.

Launched via pythonw.exe (the whole point of hiding the console) sys.stdout and
sys.stderr are None, and the first print() would raise AttributeError and kill
the tool. Redirect both to a log file before anything prints.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "relay.log"
MAX_LOG_BYTES = 1_000_000


class _Tee:
    """Write to the log file and, when present, the real console too."""

    def __init__(self, stream, console):
        self.stream = stream
        self.console = console

    def write(self, data):
        try:
            self.stream.write(data)
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


def setup_output():
    """Point stdout/stderr at the log file. Safe to call once, early."""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
            LOG_PATH.unlink()
    except OSError:
        pass

    try:
        handle = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None  # read-only location; leave the streams alone

    sys.stdout = _Tee(handle, sys.__stdout__)
    sys.stderr = _Tee(handle, sys.__stderr__)
    return LOG_PATH
