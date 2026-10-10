"""In-place Romanian-to-English translation in the active window.

Activated by F9 when the focused control already contains Romanian text.
Uses a non-destructive clipboard sentinel to read the existing text,
translates it locally via Opus-MT, replaces it via Ctrl+V, and restores
the user's original clipboard. If the box is empty, falls back to voice dictation.
"""

import time
import uuid
from typing import Any, Callable, Optional

import pyperclip
from pynput.keyboard import Controller, Key

from .injector import restore_clipboard, save_clipboard


class InplaceTranslator:
    """Detects text in active control, translates ro -> en, and replaces in-place."""

    def __init__(
        self,
        config,
        translator_getter: Optional[Callable[[], Any]] = None,
        feedback=None,
        orb_getter: Optional[Callable[[], Any]] = None,
        keyboard: Optional[Controller] = None,
        clipboard=None,
    ):
        self.config = config
        self.translator_getter = translator_getter
        self.feedback = feedback
        self.orb_getter = orb_getter
        self._keyboard = keyboard if keyboard is not None else Controller()
        self._clipboard = clipboard if clipboard is not None else pyperclip

    def _save(self):
        """Capture original clipboard content."""
        if self._clipboard is not pyperclip:
            if self.config and not getattr(self.config, "restore_clipboard", True):
                return None
            try:
                return self._clipboard.paste()
            except Exception:
                return None
        return save_clipboard(self.config)

    def _restore(self, original, delay: bool = True):
        """Restore original clipboard content."""
        if original is None:
            return
        if self._clipboard is not pyperclip:
            if delay:
                d = getattr(self.config, "restore_delay_ms", 0) if self.config else 0
                if d > 0:
                    time.sleep(d / 1000.0)
            try:
                self._clipboard.copy(original)
            except Exception:
                pass
            return

        if delay:
            restore_clipboard(original, self.config)
        else:
            try:
                self._clipboard.copy(original)
            except Exception:
                pass

    def detect_and_translate(self) -> bool:
        """Inspects active box using sentinel protocol.

        Returns True if in-place translation succeeded (do not record audio).
        Returns False if box was empty or whitespace (fallback to voice dictation).
        """
        original = self._save()
        sentinel = f"__RELAY_SENTINEL_{uuid.uuid4().hex}__"

        try:
            self._clipboard.copy(sentinel)
        except Exception as exc:
            print(f"[inplace] could not write sentinel: {exc}")
            self._restore(original, delay=False)
            return False

        try:
            # Select all in active input
            with self._keyboard.pressed(Key.ctrl):
                self._keyboard.press("a")
                self._keyboard.release("a")

            time.sleep(0.015)

            # Copy selected text
            with self._keyboard.pressed(Key.ctrl):
                self._keyboard.press("c")
                self._keyboard.release("c")

            # Poll clipboard for update (up to ~80ms)
            copied = sentinel
            for _ in range(8):
                time.sleep(0.01)
                try:
                    curr = self._clipboard.paste()
                    if curr != sentinel:
                        copied = curr
                        break
                except Exception:
                    pass

            # Check if text is present and non-empty
            if copied == sentinel or not copied or not copied.strip():
                # Empty box or nothing selected; restore clipboard and fallback
                self._restore(original, delay=False)
                return False

            # Translate Romanian text
            started = time.perf_counter()
            translator = (
                self.translator_getter()
                if callable(self.translator_getter)
                else self.translator_getter
            )

            if translator is None:
                print("[inplace] no translator available")
                self._restore(original, delay=False)
                return False

            english = translator.translate(copied)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            print(f"[inplace] translated {len(copied)} chars ro -> en in {elapsed_ms:.0f}ms")

            # Copy translated English text to clipboard
            self._clipboard.copy(english)

            paste_delay = getattr(self.config, "paste_delay_ms", 15) if self.config else 15
            if paste_delay > 0:
                time.sleep(paste_delay / 1000.0)

            # Replace selected text via Ctrl+V (selection is still active)
            # DO NOT send Enter (user reviews before submitting)
            with self._keyboard.pressed(Key.ctrl):
                self._keyboard.press("v")
                self._keyboard.release("v")

            # Restore original clipboard after short delay
            self._restore(original, delay=True)

            # Visual and audio confirmation
            if self.orb_getter:
                try:
                    orb = self.orb_getter()
                    if orb is not None and hasattr(orb, "pulse"):
                        orb.pulse(color="cyan", duration_ms=500)
                except Exception as exc:
                    print(f"[inplace] orb pulse error: {exc}")

            if self.feedback is not None and hasattr(self.feedback, "inplace_success"):
                try:
                    self.feedback.inplace_success()
                except Exception as exc:
                    print(f"[inplace] audio feedback error: {exc}")

            return True

        except Exception as exc:
            print(f"[inplace] error during detect_and_translate: {exc}")
            self._restore(original, delay=False)
            return False


__all__ = ["InplaceTranslator", "save_clipboard", "restore_clipboard"]
