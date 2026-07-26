"""Find and focus a text box through UI Automation.

Chromium-based apps expose no Win32 caret, so there is no way to ask them where
the text cursor was. They do publish an accessibility tree, in which a text box
appears as a named, keyboard-focusable element with its own bounds - enough to
focus it directly, with no synthetic clicking and no guessing from where you
last clicked.
"""

import threading

_local = threading.local()
_unavailable = False

CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"

# An input is small relative to its window. The document root and the message
# list are focusable too, and focusing those puts the cursor nowhere useful.
MAX_INPUT_HEIGHT = 300
MAX_INPUT_AREA_FRACTION = 0.4


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
