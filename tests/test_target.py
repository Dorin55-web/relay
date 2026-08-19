"""Which window everything gets written into.

The tracker is the reason dictation lands where you were typing rather than
wherever focus happens to be a second later, and it is what the chain window
and the phone both ask. It had no suite.

What it refuses matters more than what it accepts. A target captured wrongly
is not a visible failure - it is a paragraph appearing in somebody else's
application, once, later, with no clue as to why.
"""
import sys

import context  # noqa: E402,F401
context.isolate_state()

import relay.target as target_mod  # noqa: E402
from relay.target import TargetTracker  # noqa: E402

report = context.Report()
check = report.check

OURS, THEIRS = 4242, 9999


class Windows:
    """Stands in for what Win32 would say about a window."""

    def __init__(self):
        self.front = 100
        self.titles = {100: "Claude"}
        self.classes = {100: "Chrome_WidgetWin_1"}
        self.pids = {100: THEIRS}
        self.rects = {100: (0, 0, 1200, 800)}
        self.visible = {100: True}
        self.exists = {100: True}

    def add(self, hwnd, title="A Window", cls="Chrome_WidgetWin_1",
            pid=THEIRS, rect=(0, 0, 900, 600), visible=True):
        self.titles[hwnd] = title
        self.classes[hwnd] = cls
        self.pids[hwnd] = pid
        self.rects[hwnd] = rect
        self.visible[hwnd] = visible
        self.exists[hwnd] = True
        return hwnd

    def install(self):
        target_mod.foreground_window = lambda: self.front
        target_mod.window_title = lambda h: self.titles.get(h, "(no title)")
        target_mod.window_class = lambda h: self.classes.get(h, "")
        target_mod.window_pid = lambda h: self.pids.get(h, THEIRS)
        target_mod.window_rect = lambda h: self.rects.get(h)

        class User32:
            IsWindowVisible = staticmethod(lambda h: self.visible.get(h, False))
            IsWindow = staticmethod(lambda h: self.exists.get(h, False))

        target_mod._u32 = lambda: User32
        return self


def tracker(windows):
    windows.install()
    made = TargetTracker()
    made._own_pid = OURS
    return made


print("\n--- it takes the window you clicked into ---")
windows = Windows()
spy = tracker(windows)
spy._capture()
check("captured", spy.hwnd == 100, str(spy.hwnd))
check("and named", spy.title == "Claude", spy.title)


print("\n--- and refuses the ones that are never where you type ---")
# Each of these was worth a line of code, and each failure would look the same
# from outside: text appearing somewhere you did not mean.
cases = [
    ("our own orb", dict(pid=OURS)),
    ("the taskbar", dict(cls="Shell_TrayWnd")),
    ("the desktop", dict(cls="Progman")),
    ("a window nobody can see", dict(visible=False)),
    # Hidden service windows - GameInputServiceWindow and its like - take the
    # foreground for an instant. Letting one become the target hijacks every
    # dictation after it.
    ("something tiny", dict(rect=(0, 0, 60, 20))),
]
for name, how in cases:
    windows = Windows()
    windows.front = windows.add(200, title="Impostor", **how)
    spy = tracker(windows)
    spy._capture()
    check(f"not {name}", spy.hwnd is None, f"took {spy.title!r}")


print("\n--- a window with no title is still somewhere you type ---")
# Filtering on an empty title rather than on the class would make every UWP
# application untargetable.
windows = Windows()
windows.front = windows.add(300, title="(no title)", cls="Windows.UI.Core.CoreWindow")
spy = tracker(windows)
spy._capture()
check("taken", spy.hwnd == 300, str(spy.hwnd))
check("and called something", spy.title == "Windows.UI.Core.CoreWindow",
      spy.title)


print("\n--- the target holds still ---")
# Following the foreground would mean a glance at another window, a
# notification, or an Alt+Tab silently moving where your words go. It changes
# on a click and at no other time.
windows = Windows()
spy = tracker(windows)
spy._capture()
windows.front = windows.add(400, title="Somewhere Else")
check("still the first one", spy.current() == 100, str(spy.current()))
spy._capture()
check("until you click", spy.current() == 400, str(spy.current()))


print("\n--- a window that has closed is not a target ---")
windows = Windows()
spy = tracker(windows)
spy._capture()
windows.exists[100] = False
check("it is dropped", spy.current() is None, str(spy.current()))
check("and forgotten, not remembered as gone",
      spy.hwnd is None and spy.title == "", f"{spy.hwnd} {spy.title!r}")


print("\n--- restoring focus ---")
windows = Windows()
spy = tracker(windows)
spy._capture()
asked = []
target_mod.focus_window = lambda h: asked.append(h) or True
check("goes to the remembered one", spy.restore() is True)
check("by handle", asked == [100], str(asked))

windows.exists[100] = False
asked.clear()
check("and does nothing once it is gone", spy.restore() is False)
check("without asking Windows to focus a dead handle", asked == [], str(asked))

sys.exit(report.finish())
