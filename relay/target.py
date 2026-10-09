"""Remember where you were writing, and go back there before pasting.

Without this the text lands wherever focus happens to be at paste time. Click
away for a moment, or let a notification steal focus, and a phrase goes into the
wrong window. Tracking the last window you actually worked in - and restoring it
just before Ctrl+V - makes the target stick.
"""

import ctypes
import ctypes.wintypes as wt
import os
import threading

SW_RESTORE = 9


class _RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                ("right", wt.LONG), ("bottom", wt.LONG)]

# The desktop and taskbar are never somewhere you type. Filtering on class
# rather than on an empty title matters: plenty of real windows (UWP apps in
# particular) report no title, and rejecting those would make them untargetable.
SHELL_CLASSES = {
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Progman",
    "WorkerW",
    "NotifyIconOverflowWindow",
    "TaskListThumbnailWnd",
    "ForegroundStaging",
}


def _u32():
    return ctypes.windll.user32


def window_title(hwnd):
    try:
        user32 = _u32()
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value or "(no title)"
    except Exception:
        return "?"


def window_rect(hwnd):
    rect = _RECT()
    if not _u32().GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


class _MONITORINFOEXW(ctypes.Structure):
    # cbSize has to be filled in before the call: it is how GetMonitorInfoW
    # tells this structure from the shorter one that has no device name on it.
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT),
                ("dwFlags", wt.DWORD), ("szDevice", ctypes.c_wchar * 32)]


MONITOR_DEFAULTTONEAREST = 2


def monitor_rect(hwnd):
    """The screen a window is on: its name, and its bounds in real pixels.

    Both halves are needed to say where a window is in the coordinates Qt
    speaks, which is what taking a picture of one requires. Qt anchors each
    screen's logical origin to that screen's physical origin, so a conversion
    that does not know where the monitor begins is only right on the primary
    one. The name - `\\\\.\\DISPLAY1` - is what pairs it back with the QScreen
    that reports the same name.

    Returns (name, (left, top, right, bottom)), or None.
    """
    user32 = _u32()
    # Named types, unlike everywhere else in this file, and for a reason: a
    # monitor handle is a pointer, and ctypes assumes a function returns a
    # C int. On 64-bit Windows that truncates it, and the truncated handle
    # is rejected by the next call - so every window would look as though it
    # were on no screen at all.
    user32.MonitorFromWindow.restype = wt.HANDLE
    user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
    user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.c_void_p]

    handle = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not handle:
        return None
    info = _MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
    if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
        return None
    bounds = info.rcMonitor
    return info.szDevice, (bounds.left, bounds.top, bounds.right, bounds.bottom)


def window_class(hwnd):
    try:
        buffer = ctypes.create_unicode_buffer(256)
        _u32().GetClassNameW(hwnd, buffer, 256)
        return buffer.value
    except Exception:
        return ""


def window_pid(hwnd):
    pid = ctypes.c_ulong()
    _u32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_process(hwnd):
    """Executable behind a window, e.g. 'Antigravity.exe'.

    Which app a window belongs to is the first half of recognising it. The
    title is the other half, and on its own neither is enough: every terminal
    is WindowsTerminal.exe, and a title alone is whatever the app felt like.
    """
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, window_pid(hwnd))
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


TERMINAL_PROCESSES = {
    "windowsterminal.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "conhost.exe",
    "mintty.exe",
    "alacritty.exe",
    "wezterm-gui.exe",
    "wezterm.exe",
    "kitty.exe",
    "hyper.exe",
    "warp.exe",
}

TERMINAL_CLASSES = {
    "ConsoleWindowClass",
    "CASCADIA_HOSTING_WINDOW_CLASS",
    "mintty",
    "Alacritty",
}


def is_terminal_window(hwnd, profile=None) -> bool:
    """Return True if hwnd is a terminal/console window, False if GUI.

    Classifies target windows to route prompts appropriately: terminal windows
    receive local file path references, while GUI windows receive native image
    clipboard paste via Ctrl+V.
    """
    if profile is None and hwnd:
        try:
            from .agent import profile_for
            profile = profile_for(hwnd)
        except Exception:
            profile = None

    if profile is not None and isinstance(profile, dict):
        kind = profile.get("kind")
        if kind == "terminal":
            return True
        if kind == "gui":
            return False
        # If profile has no "input" and its process is a known terminal process:
        prof_proc = (profile.get("process") or "").lower()
        if not profile.get("input"):
            if prof_proc in TERMINAL_PROCESSES:
                return True
            if hwnd:
                proc = (window_process(hwnd) or "").lower()
                if proc in TERMINAL_PROCESSES:
                    return True
        elif profile.get("input"):
            return False

    if hwnd:
        proc = (window_process(hwnd) or "").lower()
        if proc in TERMINAL_PROCESSES:
            return True
        cls = window_class(hwnd) or ""
        if cls in TERMINAL_CLASSES:
            return True

    return False


def foreground_window():
    try:
        return _u32().GetForegroundWindow()
    except Exception:
        return None


def focus_window(hwnd):
    """Bring `hwnd` to the foreground, working around Windows' restrictions.

    A process may only call SetForegroundWindow freely when it already owns the
    foreground. Attaching our input queue to both the current foreground thread
    and the target's makes Windows treat the call as coming from inside, which
    is the standard way to hand focus back to a window we do not own.
    """
    user32 = _u32()
    kernel32 = ctypes.windll.kernel32

    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.GetForegroundWindow() == hwnd:
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    current = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(hwnd, None)
    fg = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)

    attached = []
    for thread in {target, fg}:
        if thread and thread != current and user32.AttachThreadInput(current, thread, True):
            attached.append(thread)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        for thread in attached:
            user32.AttachThreadInput(current, thread, False)

    return user32.GetForegroundWindow() == hwnd


class TargetTracker:
    """Remembers the window you last clicked into, and only that.

    Following the foreground window would be wrong: glancing at another app,
    an Alt+Tab, or a notification stealing focus would all silently move the
    target. A click is the one unambiguous signal that you chose where to
    write, so the target changes then and at no other time - it stays put
    across as many dictations as you like until you click somewhere else.
    """

    def __init__(self, settle_seconds=0.15):
        self.settle_seconds = settle_seconds
        self.hwnd = None
        self.title = ""
        self._own_pid = os.getpid()
        self._listener = None
        # Accessible name of the text box you last clicked into, e.g. "Prompt".
        # Remembering the element rather than a screen position is what makes
        # this survive the window moving, resizing, or reflowing.
        self.input_name = None

    def _is_ours(self, hwnd):
        try:
            return window_pid(hwnd) == self._own_pid
        except Exception:
            return False

    def _capture(self, click_xy=None):
        try:
            hwnd = foreground_window()
            # Never target our own orb, nor the desktop and taskbar.
            if not hwnd or self._is_ours(hwnd):
                return
            if window_class(hwnd) in SHELL_CLASSES:
                return
            # Hidden service windows (GameInputServiceWindow and friends) can
            # momentarily hold the foreground. They are invisible and tiny, and
            # letting one become the target silently hijacks every dictation.
            if not _u32().IsWindowVisible(hwnd):
                return
            bounds = window_rect(hwnd)
            if not bounds or bounds[2] - bounds[0] < 120 or bounds[3] - bounds[1] < 60:
                return
            title = window_title(hwnd)
            if title == "(no title)":
                title = window_class(hwnd) or "(untitled window)"
            if hwnd != self.hwnd:
                print(f"[target] now writing into: {title}")
            self.hwnd, self.title = hwnd, title

            # Remember the text box only when the click actually landed in one.
            # An earlier version stored the click position instead, which meant
            # clicking a button or a message updated the "where you write" spot
            # just as readily - and the next dictation was replayed there.
            from . import uia

            found = uia.focused_input(window_rect(hwnd))
            if found:
                if found != self.input_name:
                    print(f"[target] text box: {found!r}")
                self.input_name = found
        except Exception:
            pass

    def _on_click(self, x, y, button, pressed):
        # Runs in a low-level mouse hook: hand off immediately, and give the
        # click a moment to actually move focus before reading it. The position
        # travels with the deferred call, since the pointer will have moved on.
        if pressed:
            return
        threading.Timer(self.settle_seconds, self._capture, args=((x, y),)).start()

    def start(self):
        from pynput import mouse

        self._capture()  # so there is a target before the first click
        self._listener = mouse.Listener(on_click=self._on_click)
        self._listener.start()
        return self

    def pause(self):
        """Take the mouse hook down for a moment.

        Windows runs a low-level hook for every mouse message in the system,
        and this one's callback is Python, so it needs the GIL to return. Any
        stretch where our process holds the GIL is a stretch where the hook
        cannot return - and until it does, Windows delivers no mouse input to
        anybody. Building a window for the first time is such a stretch. With
        no hook installed there is nothing to block.
        """
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def resume(self):
        """Put it back. A stopped pynput listener cannot restart, so make one."""
        if self._listener is not None:
            return
        from pynput import mouse

        self._listener = mouse.Listener(on_click=self._on_click)
        self._listener.start()

    def stop(self):
        if self._listener is not None:
            self._listener.stop()

    def current(self):
        """The remembered window, or None if it has since been closed."""
        if self.hwnd and _u32().IsWindow(self.hwnd):
            return self.hwnd
        if self.hwnd:
            print(f"[target] {self.title!r} is gone; using whatever has focus")
            self.hwnd, self.title = None, ""
        return None

    def restore(self):
        """Put focus back on the remembered window. True if it is now focused."""
        hwnd = self.current()
        return focus_window(hwnd) if hwnd else False

    def restore_caret(self):
        """Put the text cursor back in the box you last typed in.

        Asks UI Automation to focus the remembered element. Nothing is clicked,
        so there is no chance of hitting a button or a link, and the box is
        located afresh each time rather than from a stale screen position.
        """
        hwnd = self.current()
        if hwnd is None or not self.input_name:
            return False

        from . import uia

        if uia.focus_named_input(hwnd, self.input_name):
            print(f"[target] cursor restored to {self.input_name!r}")
            return True
        return False
