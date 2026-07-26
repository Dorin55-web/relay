"""Deliver text into whatever window currently has focus, via clipboard + Ctrl+V."""

import sys
import time

import pyperclip
from pynput.keyboard import Controller, Key

from .target import focus_window, foreground_window, window_title

_keyboard = Controller()


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


def paste_text(text, config, manage_clipboard=True, target_hwnd=None):
    """Put `text` in the focused input.

    Sequence matters: the clipboard must still hold our text when the target app
    reads it. Restoring the previous contents too early makes the app paste the
    old value instead.

    When streaming, phrases arrive every couple of seconds and the caller
    handles the clipboard once per session, so pass manage_clipboard=False -
    otherwise every phrase would pay the restore delay.
    """
    if not text:
        return False

    # Go back to where you were last writing, in case focus has drifted since.
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

    # Writing the clipboard goes through OLE and isn't instant; pasting
    # immediately can land before the new contents are visible.
    time.sleep(config.paste_delay_ms / 1000.0)

    try:
        with _keyboard.pressed(Key.ctrl):
            _keyboard.press("v")
            _keyboard.release("v")
    except Exception as exc:
        print(f"[paste] could not send Ctrl+V: {exc}")
        return False

    if config.auto_enter:
        time.sleep(0.05)
        _keyboard.press(Key.enter)
        _keyboard.release(Key.enter)

    # Give the target app time to consume the paste before putting the old
    # clipboard contents back.
    restore_clipboard(original, config)

    return True
