"""Deliver text or images into whatever window currently has focus, via clipboard + Ctrl+V."""

import os
import sys
import time
import uuid
from pathlib import Path
import ctypes
import ctypes.wintypes as wt

import pyperclip
from pynput.keyboard import Controller, Key

from .target import (
    focus_window,
    foreground_window,
    is_notification_window,
    is_terminal_window,
    window_title,
)

_keyboard = Controller()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PHOTO_PROMPT = "Please analyze and explain the code/error in this image."

GMEM_MOVEABLE = 0x0002
CF_DIB = 8


def _setup_win32_clipboard():
    if sys.platform != "win32":
        return
    try:
        k32 = ctypes.windll.kernel32
        u32 = ctypes.windll.user32

        k32.GlobalAlloc.restype = wt.HGLOBAL
        k32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]

        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [wt.HGLOBAL]

        k32.GlobalUnlock.restype = wt.BOOL
        k32.GlobalUnlock.argtypes = [wt.HGLOBAL]

        k32.GlobalFree.restype = wt.HGLOBAL
        k32.GlobalFree.argtypes = [wt.HGLOBAL]

        u32.OpenClipboard.restype = wt.BOOL
        u32.OpenClipboard.argtypes = [wt.HWND]

        u32.EmptyClipboard.restype = wt.BOOL
        u32.EmptyClipboard.argtypes = []

        u32.SetClipboardData.restype = wt.HANDLE
        u32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]

        u32.CloseClipboard.restype = wt.BOOL
        u32.CloseClipboard.argtypes = []
    except Exception:
        pass


_setup_win32_clipboard()


def _foreground_window_title():
    """Title of the window that will receive the paste, for the log.

    When text goes missing this is the difference between "Ctrl+V was sent"
    and knowing where it actually landed.
    """
    if sys.platform != "win32":
        return "?"
    return window_title(foreground_window())


def _read_clipboard():
    """Current clipboard text, or None if it holds something we can't restore."""
    try:
        return pyperclip.paste()
    except Exception:
        # Images, files, and other non-text formats raise here. Nothing to save.
        return None


def save_clipboard(config):
    """Snapshot the clipboard so a whole dictation session can restore it once."""
    return _read_clipboard() if config.restore_clipboard else None


def restore_clipboard(original, config):
    """Put back what save_clipboard() captured."""
    if original is None:
        return
    time.sleep(config.restore_delay_ms / 1000.0)
    try:
        pyperclip.copy(original)
    except Exception:
        pass


def raw_bytes_to_dib(raw_bytes: bytes) -> bytes:
    """Convert raw image bytes to CF_DIB memory block (BMP without 14-byte header)."""
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage

    img = QImage.fromData(raw_bytes)
    if img.isNull():
        raise ValueError("Could not decode image bytes with QImage")
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "BMP")
    bmp_bytes = bytes(buf.data())
    if len(bmp_bytes) < 14:
        raise ValueError("Generated BMP data is too short")
    return bmp_bytes[14:]


def copy_image_to_clipboard(raw_bytes: bytes) -> bool:
    """Put raw image bytes (JPEG/PNG/BMP) into Windows clipboard as CF_DIB."""
    if sys.platform != "win32":
        return False
    try:
        dib = raw_bytes_to_dib(raw_bytes)
    except Exception as exc:
        print(f"[clipboard] could not convert image to DIB: {exc}")
        return False

    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32

    h_mem = k32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
    if not h_mem:
        print("[clipboard] GlobalAlloc failed")
        return False

    ptr = k32.GlobalLock(h_mem)
    if not ptr:
        k32.GlobalFree(h_mem)
        print("[clipboard] GlobalLock failed")
        return False

    try:
        ctypes.memmove(ptr, dib, len(dib))
    finally:
        k32.GlobalUnlock(h_mem)

    opened = False
    for _ in range(5):
        if u32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.02)

    if not opened:
        k32.GlobalFree(h_mem)
        print("[clipboard] OpenClipboard failed")
        return False

    try:
        if not u32.EmptyClipboard():
            k32.GlobalFree(h_mem)
            print("[clipboard] EmptyClipboard failed")
            return False

        h_clip = u32.SetClipboardData(CF_DIB, h_mem)
        if not h_clip:
            k32.GlobalFree(h_mem)
            print("[clipboard] SetClipboardData failed")
            return False
        return True
    finally:
        u32.CloseClipboard()


def paste_text(text, config, manage_clipboard=True, target_hwnd=None, submit=None):
    """Put `text` in the focused input.

    Sequence matters: the clipboard must still hold our text when the target app
    reads it. Restoring the previous contents too early makes the app paste the
    old value instead.

    When streaming, phrases arrive every couple of seconds and the caller
    handles the clipboard once per session, so pass manage_clipboard=False -
    otherwise every phrase would pay the restore delay.

    `submit` overrides config.auto_enter for one paste. Prompt templates arrive
    with `<blanks>` still in them, so sending Enter would fire off a half-written
    prompt even for someone who wants auto_enter on their dictation.
    """
    if not text:
        return False

    # Go back to where you were last writing, in case focus has drifted since.
    if target_hwnd and is_notification_window(target_hwnd):
        target_hwnd = None
    if target_hwnd and foreground_window() != target_hwnd:
        drifted_to = _foreground_window_title()   # read before we change it
        restored = focus_window(target_hwnd)
        print(
            f"[paste] focus had drifted to {drifted_to!r}; "
            f"restored {window_title(target_hwnd)!r}: {restored}"
        )
        if restored:
            time.sleep(0.08)  # let the app settle its caret before Ctrl+V

    print(f"[paste] target window: {_foreground_window_title()}")

    original = save_clipboard(config) if manage_clipboard else None

    try:
        pyperclip.copy(text)
    except Exception as exc:
        print(f"[paste] could not write to clipboard: {exc}")
        return False

    # From here the clipboard is holding our text, so whatever happens next it
    # has to be handed back. It was not: a Ctrl+V that failed returned early,
    # and the paragraph you had been carrying was quietly replaced by a phrase
    # that never went anywhere.
    try:
        # Writing the clipboard goes through OLE and isn't instant; pasting
        # immediately can land before the new contents are visible.
        time.sleep(config.paste_delay_ms / 1000.0)

        with _keyboard.pressed(Key.ctrl):
            _keyboard.press("v")
            _keyboard.release("v")

        if config.auto_enter if submit is None else submit:
            time.sleep(0.12)
            _keyboard.press(Key.enter)
            _keyboard.release(Key.enter)
        return True
    except Exception as exc:
        print(f"[paste] could not send Ctrl+V: {exc}")
        return False
    finally:
        # Give the target app time to consume the paste before putting the old
        # clipboard contents back.
        restore_clipboard(original, config)


def paste_hybrid(step, config, target_hwnd=None, submit=None, manage_clipboard=True):
    """Deliver either text or image prompt to the target window.

    Routes to terminal file referencing or GUI clipboard injection depending
    on the target window type and step payload.
    """
    if isinstance(step, str):
        return paste_text(step, config, manage_clipboard=manage_clipboard, target_hwnd=target_hwnd, submit=submit)

    if not isinstance(step, dict) or step.get("type") != "photo":
        if isinstance(step, dict) and "text" in step:
            return paste_text(step["text"], config, manage_clipboard=manage_clipboard, target_hwnd=target_hwnd, submit=submit)
        return False

    raw_bytes = step.get("image_bytes")
    if not raw_bytes:
        print("[paste_hybrid] no image_bytes provided in photo step")
        return False

    caption = (step.get("caption") or "").strip() or DEFAULT_PHOTO_PROMPT

    from .agent import focus_input, profile_for

    target = target_hwnd or foreground_window()
    profile = profile_for(target) if target else None

    if is_terminal_window(target, profile):
        # Terminal branch:
        # 1. Save image_bytes to inbox/img_{timestamp}_{uuid[:6]}.png
        file_path = step.get("path")
        if not file_path or not Path(file_path).exists():
            inbox_dir = PROJECT_ROOT / "inbox"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            uid = uuid.uuid4().hex[:6]
            file_path = inbox_dir / f"img_{ts}_{uid}.png"
            file_path.write_bytes(raw_bytes)
            step["path"] = str(file_path)

        posix_path = Path(file_path).resolve().as_posix()
        command = f'"{posix_path}" {caption}'
        return paste_text(command, config, manage_clipboard=manage_clipboard, target_hwnd=target_hwnd, submit=submit)

    # GUI branch:
    if target_hwnd and is_notification_window(target_hwnd):
        target_hwnd = None
    if target_hwnd and foreground_window() != target_hwnd:
        drifted_to = _foreground_window_title()
        restored = focus_window(target_hwnd)
        print(
            f"[paste] focus had drifted to {drifted_to!r}; "
            f"restored {window_title(target_hwnd)!r}: {restored}"
        )
        if restored:
            time.sleep(0.08)

    target_for_input = target_hwnd or foreground_window()
    if target_for_input and profile:
        focus_input(target_for_input, profile)

    print(f"[paste] target window: {_foreground_window_title()}")

    original = save_clipboard(config) if manage_clipboard else None

    try:
        # Step 1: Copy image & paste
        if not copy_image_to_clipboard(raw_bytes):
            print("[paste_hybrid] could not copy image to clipboard")
            return False

        time.sleep(config.paste_delay_ms / 1000.0)

        with _keyboard.pressed(Key.ctrl):
            _keyboard.press("v")
            _keyboard.release("v")

        # Step 2: Settle delay for GUI app to render attachment chip (~0.30s)
        time.sleep(0.30)

        # Step 3: Copy caption text to clipboard & paste
        try:
            pyperclip.copy(caption)
        except Exception as exc:
            print(f"[paste_hybrid] could not write caption to clipboard: {exc}")
            return False

        time.sleep(config.paste_delay_ms / 1000.0 if config.paste_delay_ms else 0.05)

        with _keyboard.pressed(Key.ctrl):
            _keyboard.press("v")
            _keyboard.release("v")

        # Step 4: Submission
        if config.auto_enter if submit is None else submit:
            time.sleep(0.05)
            _keyboard.press(Key.enter)
            _keyboard.release(Key.enter)

        return True
    except Exception as exc:
        print(f"[paste_hybrid] failed to inject: {exc}")
        return False
    finally:
        if manage_clipboard:
            restore_clipboard(original, config)


def _deferred_refocus(hwnd, input_name=None, delay=0.35):
    """Best-effort refocus of the target agent input box after task cancellation."""
    try:
        time.sleep(delay)
        if not input_name:
            try:
                from .agent import profile_for
                prof = profile_for(hwnd)
                input_name = (prof or {}).get("input") or "Message input"
            except Exception:
                input_name = "Message input"
        from . import uia
        uia.focus_named_input(hwnd, input_name)
    except Exception:
        pass


def cancel_task_in_window(target_hwnd=None) -> bool:
    """Cancels active task by invoking the Cancel/Stop button via UIA, falling back to keystroke.

    Invoking via UI Automation avoids sending raw keystrokes like Ctrl+D, which in
    AntiGravity and VS Code toggles the Auxiliary Pane (Conversation History) when
    keyboard focus is not specifically on the chat.
    """
    target = target_hwnd or foreground_window()
    if not target:
        return False

    # 1. First, try finding and clicking the Cancel/Stop button directly via UI Automation.
    try:
        from . import uia
        if uia.click_cancel_button(target):
            import threading
            threading.Thread(target=_deferred_refocus, args=(target,), daemon=True).start()
            return True
    except Exception as exc:
        print(f"[cancel] uia button click failed: {exc}")

    # 2. Fallback: bring window to foreground and inject keystroke
    focus_window(target)
    time.sleep(0.08)
    try:
        with _keyboard.pressed(Key.ctrl):
            _keyboard.press("d")
            _keyboard.release("d")
        import threading
        threading.Thread(target=_deferred_refocus, args=(target,), daemon=True).start()
        return True
    except Exception as exc:
        print(f"[cancel] could not send Ctrl+D: {exc}")
        return False



