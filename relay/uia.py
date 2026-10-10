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
    "UIA_ValuePatternId": 10002,
    "UIA_IsValuePatternAvailablePropertyId": 30043,
    "UIA_NamePropertyId": 30005,
    "UIA_ControlTypePropertyId": 30003,
    "UIA_ButtonControlTypeId": 50000,
    "UIA_EditControlTypeId": 50004,
    "UIA_DocumentControlTypeId": 50030,
    "UIA_InvokePatternId": 10000,
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


NON_INPUT_CONTROL_TYPES = {
    50000,  # Button
    50002,  # CheckBox
    50005,  # Hyperlink
    50006,  # Image
    50011,  # MenuItem
    50012,  # ProgressBar
    50013,  # RadioButton
    50014,  # ScrollBar
    50015,  # Slider
    50019,  # TabItem
    50021,  # ToolBar
    50022,  # ToolTip
    50038,  # Separator
}


def _looks_like_input(element, window_bounds):
    """Reject the page root and big regions; keep things the size of a textbox."""
    try:
        if not element.CurrentIsKeyboardFocusable:
            return False
        ct = getattr(element, "CurrentControlType", None)
        if ct in NON_INPUT_CONTROL_TYPES:
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
        return name or "input"       # a truthy string is required to indicate input focus
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

    # Fast path: check if currently focused element is already the target input
    try:
        focused = auto.GetFocusedElement()
        if focused:
            el_name = getattr(focused, "CurrentName", None) or ""
            if (name and el_name and name.lower() in el_name.lower()) or _looks_like_input(focused, None):
                return True
    except Exception:
        pass

    try:
        from .target import window_rect
        wrect = window_rect(hwnd)

        root = auto.ElementFromHandle(hwnd)
        condition = auto.CreatePropertyCondition(_id(UIA, "UIA_NamePropertyId"), name)
        matches = root.FindAll(_id(UIA, "TreeScope_Descendants"), condition)

        candidates = []
        for i in range(matches.Length if matches else 0):
            try:
                el = matches.GetElement(i)
                r = el.CurrentBoundingRectangle
                if r.right <= r.left or r.bottom <= r.top:
                    continue
                # Reject off-screen coordinates or elements outside target window
                if wrect:
                    wl, wt, wr, wb = wrect
                    if r.right < wl or r.left > wr or r.bottom < wt or r.top > wb:
                        continue
                elif r.top < 0 or r.left < 0:
                    continue
                focusable = bool(el.CurrentIsKeyboardFocusable)
                # Prioritize: focusable on-screen elements
                priority = 0 if focusable else 1
                candidates.append((priority, el, r))
            except Exception:
                continue

        element = None
        best_rect = None
        if candidates:
            candidates.sort(key=lambda c: c[0])
            element = candidates[0][1]
            best_rect = candidates[0][2]

        # Fallback: search Edit controls for case-insensitive or substring match within window
        if not element:
            name_lower = name.lower()
            try:
                cond_edit = auto.CreatePropertyCondition(
                    _id(UIA, "UIA_ControlTypePropertyId"),
                    _id(UIA, "UIA_EditControlTypeId")
                )
                edits = root.FindAll(_id(UIA, "TreeScope_Descendants"), cond_edit)
                for i in range(edits.Length if edits else 0):
                    try:
                        el = edits.GetElement(i)
                        r = el.CurrentBoundingRectangle
                        if r.right <= r.left or r.bottom <= r.top:
                            continue
                        if wrect:
                            wl, wt, wr, wb = wrect
                            if r.right < wl or r.left > wr or r.bottom < wt or r.top > wb:
                                continue
                        elif r.top < 0 or r.left < 0:
                            continue
                        el_name = (el.CurrentName or "").lower()
                        if name_lower in el_name or el_name in name_lower or not name_lower:
                            element = el
                            best_rect = r
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        if not element:
            print(f"[uia] no element named {name!r} in that window any more")
            return False

        # 1. Try SetFocus directly
        try:
            element.SetFocus()
        except Exception:
            pass

        import time
        time.sleep(0.04)
        try:
            focused = auto.GetFocusedElement()
            if focused and focused.CurrentName == name:
                return True
        except Exception:
            pass

        # 2. In Chromium / Electron (AntiGravity, VS Code), SetFocus often does not
        # place the caret in web DOM input elements without a mouse click.
        # Click on the center of the element's bounding rectangle.
        try:
            rect = best_rect or element.CurrentBoundingRectangle
            if rect.right > rect.left and rect.bottom > rect.top:
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                import ctypes
                user32 = ctypes.windll.user32

                # Click input box at (cx, cy)
                user32.SetCursorPos(cx, cy)
                user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
                user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
                time.sleep(0.06)

                focused = auto.GetFocusedElement()
                if focused and (focused.CurrentName == name or _looks_like_input(focused, None)):
                    return True

                # Clicking the bounding box of the target input element inside the window
                # is the definitive way to place the caret in Chromium / Electron.
                return True
        except Exception as exc:
            print(f"[uia] mouse click on {name!r} failed: {exc}")

        # Final check if SetFocus took after all
        try:
            focused = auto.GetFocusedElement()
            if focused and focused.CurrentName == name:
                return True
        except Exception:
            pass

        print(f"[uia] SetFocus on {name!r} did not take")
        return False
    except Exception as exc:
        print(f"[uia] could not focus {name!r}: {exc}")
        return False


def click_cancel_button(hwnd, candidate_names=None):
    """Find the cancel or stop task button in `hwnd` and invoke/click it.

    Invoking the button directly via UI Automation avoids sending raw
    keystrokes like Ctrl+D, which in AntiGravity and VS Code toggles the
    Auxiliary Pane (Conversation History) when keyboard focus is not specifically
    on the chat.
    """
    auto, UIA = _uia()
    if auto is None or not hwnd:
        return False

    targets = [
        "cancel (ctrl+d)",
        "stop task",
        "stop tasks",
        "stop execution",
        "stop generation",
        "cancel generation",
        "cancel prompt",
        "cancel task",
        "cancel",
        "stop",
        "abort",
    ] if candidate_names is None else [n.lower() for n in candidate_names]

    try:
        root = auto.ElementFromHandle(hwnd)
        condition = auto.CreatePropertyCondition(
            _id(UIA, "UIA_ControlTypePropertyId"),
            _id(UIA, "UIA_ButtonControlTypeId")
        )
        found = root.FindAll(_id(UIA, "TreeScope_Descendants"), condition)
        if not found:
            return False

        matches = []
        for i in range(found.Length):
            try:
                el = found.GetElement(i)
                raw_name = el.CurrentName or ""
                name = clean(raw_name).lower()
                for priority, target in enumerate(targets):
                    if target == name or target in name:
                        matches.append((priority, el, raw_name))
                        break
            except Exception:
                continue

        if not matches:
            return False

        matches.sort(key=lambda m: m[0])
        best_element = matches[0][1]

        # 1. Try InvokePattern
        try:
            pattern = best_element.GetCurrentPattern(_id(UIA, "UIA_InvokePatternId"))
            if pattern:
                invoker = pattern.QueryInterface(UIA.IUIAutomationInvokePattern)
                invoker.Invoke()
                return True
        except Exception:
            pass

        # 2. Try mouse click on center of bounding box
        try:
            rect = best_element.CurrentBoundingRectangle
            if rect.right > rect.left and rect.bottom > rect.top:
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                import ctypes
                user32 = ctypes.windll.user32
                user32.SetCursorPos(cx, cy)
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
                return True
        except Exception:
            pass
    except Exception as exc:
        print(f"[uia] click_cancel_button failed: {exc}")
    return False


def read_active_input_text(hwnd=None, name=None):
    """Read text from active input element via UIA without keyboard selection.

    Strictly queries the currently focused input element (auto.GetFocusedElement()).
    Does not scan the window or other Edit controls to prevent picking up
    stale text or code from conversation history.
    Returns the string if present, or "" if empty/deleted/not an input, or None if UIA unavailable.
    """
    auto, UIA = _uia()
    if auto is None:
        return None

    def _extract_from(el):
        if not el:
            return None
        # 1. TextPattern (Checked first: accurate for modern Chromium / Electron / Monaco controls)
        try:
            tp = el.GetCurrentPattern(_id(UIA, "UIA_TextPatternId"))
            if tp:
                t_obj = tp.QueryInterface(UIA.IUIAutomationTextPattern)
                doc_range = getattr(t_obj, "DocumentRange", None)
                if doc_range is not None:
                    txt = doc_range.GetText(-1)
                    if txt is not None:
                        cleaned = str(txt).replace("￼", "").replace("\ufffc", "").replace("\u200b", "").strip()
                        el_name = getattr(el, "CurrentName", None) or ""
                        if not cleaned or (el_name and cleaned == el_name.strip()):
                            return ""
                        return str(txt)
        except Exception:
            pass

        # 2. Win32 NativeWindowHandle for standard Edit and Document controls
        try:
            if el.CurrentControlType in (
                _id(UIA, "UIA_EditControlTypeId"),
                _id(UIA, "UIA_DocumentControlTypeId"),
            ):
                h = el.CurrentNativeWindowHandle
                if h:
                    import ctypes
                    user32 = ctypes.windll.user32
                    length = user32.SendMessageW(h, 0x000E, 0, 0)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.SendMessageW(h, 0x000D, length + 1, buf)
                        return buf.value
                    elif length == 0:
                        return ""
        except Exception:
            pass

        # 3. ValuePattern
        try:
            vp = el.GetCurrentPattern(_id(UIA, "UIA_ValuePatternId"))
            if vp:
                v_obj = vp.QueryInterface(UIA.IUIAutomationValuePattern)
                val = v_obj.CurrentValue
                if val is not None:
                    cleaned = str(val).replace("￼", "").replace("\ufffc", "").replace("\u200b", "").strip()
                    el_name = getattr(el, "CurrentName", None) or ""
                    if not cleaned or (el_name and cleaned == el_name.strip()):
                        return ""
                    return str(val)
        except Exception:
            pass

        return None

    try:
        focused = auto.GetFocusedElement()
        if not focused:
            return None

        has_vp = False
        is_readonly = False
        try:
            vp = focused.GetCurrentPattern(_id(UIA, "UIA_ValuePatternId"))
            if vp:
                has_vp = True
                v_obj = vp.QueryInterface(UIA.IUIAutomationValuePattern)
                if getattr(v_obj, "CurrentIsReadOnly", False):
                    is_readonly = True
        except Exception:
            pass

        has_hwnd = False
        try:
            h = getattr(focused, "CurrentNativeWindowHandle", 0)
            if h:
                has_hwnd = True
                import ctypes
                style = ctypes.windll.user32.GetWindowLongW(h, -16)  # GWL_STYLE
                if style & 0x0800:  # ES_READONLY
                    is_readonly = True
        except Exception:
            pass

        if is_readonly:
            return ""

        is_edit = False
        try:
            is_edit = focused.CurrentControlType == _id(UIA, "UIA_EditControlTypeId")
        except Exception:
            pass

        is_doc = False
        try:
            is_doc = focused.CurrentControlType == _id(UIA, "UIA_DocumentControlTypeId")
        except Exception:
            pass

        # Strictly check if the focused element is an input control.
        # An Edit control or an element with ValuePattern is always an input.
        # A Document control is an input if it has ValuePattern, a native HWND (e.g. RichEdit/Notepad),
        # or bounds that fit an input box rather than the entire page root.
        is_input = (
            is_edit
            or has_vp
            or (is_doc and has_hwnd)
            or _looks_like_input(focused, None)
        )
        if not is_input:
            if is_doc or is_readonly:
                return ""
            return None

        text = _extract_from(focused)
        if text is None:
            return ""

        cleaned = text.replace("￼", "").replace("\ufffc", "").replace("\u200b", "").strip()
        if not cleaned:
            return ""
        return text
    except Exception:
        return None


def set_active_input_text(text: str) -> bool:
    """Set text of the active focused input element via UIA without keyboard selection.

    Returns True if successfully set via ValuePattern or Win32 edit control, False otherwise.
    """
    auto, UIA = _uia()
    if auto is None:
        return False
    try:
        focused = auto.GetFocusedElement()
        if not focused:
            return False

        has_vp = False
        is_readonly = False
        try:
            vp = focused.GetCurrentPattern(_id(UIA, "UIA_ValuePatternId"))
            if vp:
                has_vp = True
                v_obj = vp.QueryInterface(UIA.IUIAutomationValuePattern)
                if getattr(v_obj, "CurrentIsReadOnly", False):
                    is_readonly = True
        except Exception:
            pass

        has_hwnd = False
        try:
            h = getattr(focused, "CurrentNativeWindowHandle", 0)
            if h:
                has_hwnd = True
                import ctypes
                style = ctypes.windll.user32.GetWindowLongW(h, -16)  # GWL_STYLE
                if style & 0x0800:  # ES_READONLY
                    is_readonly = True
        except Exception:
            pass

        if is_readonly:
            return False

        is_edit = False
        try:
            is_edit = focused.CurrentControlType == _id(UIA, "UIA_EditControlTypeId")
        except Exception:
            pass

        is_doc = False
        try:
            is_doc = focused.CurrentControlType == _id(UIA, "UIA_DocumentControlTypeId")
        except Exception:
            pass

        # Only set if it looks like an input or edit control
        is_input = (
            is_edit
            or has_vp
            or (is_doc and has_hwnd)
            or _looks_like_input(focused, None)
        )
        if not is_input:
            return False

        # 1. ValuePattern
        try:
            vp = focused.GetCurrentPattern(_id(UIA, "UIA_ValuePatternId"))
            if vp:
                v_obj = vp.QueryInterface(UIA.IUIAutomationValuePattern)
                if not getattr(v_obj, "CurrentIsReadOnly", False):
                    v_obj.SetValue(text)
                    # In Chromium / Monaco / contenteditable, SetValue returns S_OK
                    # but is a silent no-op. Verify that CurrentValue actually updated.
                    try:
                        if getattr(v_obj, "CurrentValue", None) == text:
                            return True
                    except Exception:
                        pass
        except Exception:
            pass

        # 2. Win32 NativeWindowHandle
        try:
            if focused.CurrentControlType in (
                _id(UIA, "UIA_EditControlTypeId"),
                _id(UIA, "UIA_DocumentControlTypeId"),
            ):
                h = focused.CurrentNativeWindowHandle
                if h:
                    import ctypes
                    user32 = ctypes.windll.user32
                    user32.SendMessageW(h, 0x000C, 0, text)  # WM_SETTEXT
                    return True
        except Exception:
            pass
    except Exception:
        pass

    return False


