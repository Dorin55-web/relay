"""Read another application's window through UI Automation.

Chromium-based apps expose no Win32 caret, so there is no way to ask them where
the text cursor was. They do publish an accessibility tree, in which a text box
appears as a named, keyboard-focusable element with its own bounds - enough to
focus it directly, with no synthetic clicking and no guessing from where you
last clicked.

The same tree answers a second question: what does that window currently say?
Windows publishes it for screen readers, so it costs nothing to ask, and it is
how the autopilot tells an agent that is still working from one that has
finished. Measured on a Chromium app: 13500 characters and 77 buttons in 0.35s.
On Windows Terminal: the visible screen, in 0.04s.
"""

import threading

_local = threading.local()
_unavailable = False

CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"

# An input is small relative to its window. The document root and the message
# list are focusable too, and focusing those puts the cursor nowhere useful.
MAX_INPUT_HEIGHT = 300
MAX_INPUT_AREA_FRACTION = 0.4

MAX_BUTTONS = 400

# The generated comtypes module normally carries these, but the numbers are
# fixed by the UIA spec and a missing constant should not stop a read.
IDS = {
    "UIA_TextPatternId": 10014,
    "UIA_IsTextPatternAvailablePropertyId": 30040,
    "UIA_NamePropertyId": 30005,
    "UIA_ControlTypePropertyId": 30003,
    "UIA_ButtonControlTypeId": 50000,
    "TreeScope_Descendants": 4,
}


def _id(UIA, name):
    return getattr(UIA, name, IDS[name])


def clean(line):
    """Readable, and comparable between two reads of the same window.

    Chromium marks every embedded object with U+FFFC, and a tree walked twice
    does not place them identically. Left in, they turn lines that are really
    the same into differences - which would make a state detector fire on its
    own noise.
    """
    line = line.replace("￼", " ").replace("​", "")
    line = "".join(c if c.isprintable() or c == "\t" else " " for c in line)
    return " ".join(line.split())


def window_text(hwnd):
    """Everything the window publishes as text, and which element published it.

    The root element usually supports nothing. In Windows Terminal the text
    provider hangs off the terminal control inside; in a Chromium app it is the
    document. So ask every descendant that claims a TextPattern and keep
    whichever gave the most, rather than assuming where it lives.

    Returns (text, source_name). Empty text is a real answer, not a failure.
    """
    auto, UIA = _uia()
    if auto is None:
        return "", "UI Automation unavailable"
    try:
        root = auto.ElementFromHandle(hwnd)
        condition = auto.CreatePropertyCondition(
            _id(UIA, "UIA_IsTextPatternAvailablePropertyId"), True)
        found = root.FindAll(_id(UIA, "TreeScope_Descendants"), condition)
    except Exception as exc:
        return "", f"could not read: {exc}"

    best = ("", "no element returned any text")
    for i in range(found.Length if found else 0):
        try:
            element = found.GetElement(i)
            pattern = element.GetCurrentPattern(_id(UIA, "UIA_TextPatternId"))
            if not pattern:
                continue
            pattern = pattern.QueryInterface(UIA.IUIAutomationTextPattern)
            text = pattern.DocumentRange.GetText(-1) or ""   # -1 means no limit
            if text.strip() and len(text) > len(best[0]):
                best = (text, element.CurrentName or "(unnamed)")
        except Exception:
            continue
    return best


def window_buttons(hwnd):
    """Names of every button in the window.

    A cache request is not an optimisation here. Reading Name off an element is
    a call into the other process; on a Chromium tree that is thousands of round
    trips and the read appears to hang. Asking for the names up front fetches
    them in one.
    """
    auto, UIA = _uia()
    if auto is None:
        return []
    try:
        root = auto.ElementFromHandle(hwnd)
        condition = auto.CreatePropertyCondition(
            _id(UIA, "UIA_ControlTypePropertyId"),
            _id(UIA, "UIA_ButtonControlTypeId"))
        cache = auto.CreateCacheRequest()
        cache.AddProperty(_id(UIA, "UIA_NamePropertyId"))
        found = root.FindAllBuildCache(
            _id(UIA, "TreeScope_Descendants"), condition, cache)
    except Exception:
        return []

    names = []
    for i in range(min(found.Length if found else 0, MAX_BUTTONS)):
        try:
            name = clean(found.GetElement(i).CachedName or "")
        except Exception:
            continue
        if name:
            names.append(name)
    return names


def _uia():
    """Per-thread automation object. COM must be initialised on each thread."""
    global _unavailable
    if _unavailable:
        return None, None
    if getattr(_local, "auto", None) is None:
        try:
            import comtypes
            import comtypes.client

            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as UIA

            try:
                comtypes.CoInitialize()
            except Exception:
                pass
            _local.auto = comtypes.client.CreateObject(
                CLSID_CUIAutomation, interface=UIA.IUIAutomation
            )
            _local.uia = UIA
        except Exception as exc:
            print(f"[uia] unavailable ({exc}); falling back to window focus only")
            _unavailable = True
            return None, None
    return _local.auto, _local.uia


def _looks_like_input(element, window_bounds):
    """Reject the page root and big regions; keep things the size of a textbox."""
    try:
        if not element.CurrentIsKeyboardFocusable:
            return False
        rect = element.CurrentBoundingRectangle
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0 or height > MAX_INPUT_HEIGHT:
            return False
        if window_bounds:
            win_w = window_bounds[2] - window_bounds[0]
            win_h = window_bounds[3] - window_bounds[1]
            if win_w > 0 and win_h > 0:
                if width * height > MAX_INPUT_AREA_FRACTION * win_w * win_h:
                    return False
        return True
    except Exception:
        return False


def focused_input(window_bounds=None):
    """Name of the focused element, if it looks like somewhere you type."""
    auto, _ = _uia()
    if auto is None:
        return None
    try:
        element = auto.GetFocusedElement()
        if not _looks_like_input(element, window_bounds):
            return None
        name = element.CurrentName
        return name or None       # a name is required to find it again later
    except Exception:
        return None


def focus_named_input(hwnd, name):
    """Give keyboard focus back to the named element inside `hwnd`.

    Returns True only when focus actually landed there, so the caller can tell
    a real success from a silent no-op.
    """
    auto, UIA = _uia()
    if auto is None or not name:
        return False
    try:
        root = auto.ElementFromHandle(hwnd)
        condition = auto.CreatePropertyCondition(UIA.UIA_NamePropertyId, name)
        element = root.FindFirst(UIA.TreeScope_Descendants, condition)
        if not element:
            print(f"[uia] no element named {name!r} in that window any more")
            return False
        element.SetFocus()
        focused = auto.GetFocusedElement()
        if focused.CurrentName == name:
            return True
        print(f"[uia] SetFocus on {name!r} did not take")
        return False
    except Exception as exc:
        print(f"[uia] could not focus {name!r}: {exc}")
        return False
