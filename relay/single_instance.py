"""Refuse to start twice.

With both an auto-start entry and a desktop shortcut it is easy to end up with
two copies running: two orbs, two keyboard hooks, and one F9 press starting two
recordings on the same microphone. A named mutex is the cheap Windows way to
notice we are already up.
"""

import ctypes
import sys

# Local\ scopes the mutex to this logon session, which is what we want, and
# avoids the access-denied that Global\ can hit outside an elevated process.
MUTEX_NAME = "Local\\RelaySingleInstance"
ERROR_ALREADY_EXISTS = 183

_handle = None


def already_running():
    """True if another instance holds the mutex. Keeps a reference either way."""
    global _handle
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        _handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return kernel32.GetLastError() == ERROR_ALREADY_EXISTS
    except Exception:
        return False  # never block startup over a failed check
