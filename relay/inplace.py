"""In-place Romanian-to-English translation in the active window.

Activated by F9 when the focused control already contains Romanian text.
1. Silent UIA mode (primary): Reads the text without any selection highlight,
   translates on GPU in background, and replaces atomically via rapid Ctrl+A + Ctrl+V
   in ~5ms so no blue selection highlight is ever displayed.
2. Sentinel mode (fallback): If UIA is unavailable in the control, uses a clipboard
   sentinel but deselects immediately with Right Arrow before translating, then
   replaces atomically via rapid Ctrl+A + Ctrl+V.
If the box is empty, falls back to voice dictation without visual disruption.
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
        target_getter: Optional[Callable[[], Any]] = None,
        uia_reader: Optional[Callable[..., Optional[str]]] = None,
        uia_setter: Optional[Callable[..., bool]] = None,
    ):
        self.config = config
        self.translator_getter = translator_getter
        self.feedback = feedback
        self.orb_getter = orb_getter
        self._keyboard = keyboard if keyboard is not None else Controller()
        self._clipboard = clipboard if clipboard is not None else pyperclip
        self.target_getter = target_getter
        if uia_reader is not None:
            self.uia_reader = uia_reader
        else:
            try:
                from .uia import read_active_input_text
                self.uia_reader = read_active_input_text
            except Exception:
                self.uia_reader = None

        if uia_setter is not None:
            self.uia_setter = uia_setter
        else:
            try:
                from .uia import set_active_input_text
                self.uia_setter = set_active_input_text
            except Exception:
                self.uia_setter = None

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

    def _notify_success(self):
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

    def detect_and_translate(self) -> bool:
        """Inspects active box and translates Romanian text to English in-place.

        Returns True if in-place translation succeeded (do not record audio).
        Returns False if box was empty or whitespace (fallback to voice dictation).
        """
        target = self.target_getter() if callable(self.target_getter) else self.target_getter
        hwnd = getattr(target, "hwnd", None)
        input_name = getattr(target, "input_name", None)

        # 1. Attempt silent UIA text extraction without keyboard selection
        uia_text = None
        if self.uia_reader is not None:
            try:
                uia_text = self.uia_reader(hwnd, input_name)
            except Exception as exc:
                print(f"[inplace] uia_reader error: {exc}")
                uia_text = None

        if uia_text is not None:
            stripped = uia_text.strip()
            if not stripped:
                # Empty box: silent fallback to voice dictation without ANY keystrokes
                return False

            # Translate Romanian text while user screen stays completely normal
            started = time.perf_counter()
            translator = (
                self.translator_getter()
                if callable(self.translator_getter)
                else self.translator_getter
            )
            if translator is None:
                print("[inplace] no translator available")
                return False

            try:
                english = translator.translate(uia_text)
            except Exception as exc:
                print(f"[inplace] translation error: {exc}")
                return False

            if english is None:
                print("[inplace] translator returned None")
                return False

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            print(f"[inplace] translated {len(uia_text)} chars ro -> en in {elapsed_ms:.0f}ms (silent UIA)")

            # Clean replacement without blue highlight:
            # First attempt direct UIA insertion (ValuePattern.SetValue)
            replaced = False
            if self.uia_setter is not None:
                try:
                    replaced = bool(self.uia_setter(english))
                except Exception as exc:
                    print(f"[inplace] uia_setter error: {exc}")
                    replaced = False

            if not replaced:
                # Fallback: rapid atomic replacement without delay between Ctrl+A and Ctrl+V
                original = self._save()
                try:
                    self._clipboard.copy(english)
                    paste_delay = getattr(self.config, "paste_delay_ms", 10) if self.config else 10
                    if paste_delay > 0:
                        time.sleep(paste_delay / 1000.0)

                    # Atomic replacement in-place:
                    # Press Ctrl+A and Ctrl+V back-to-back with no delay so the compositor
                    # does not display an intermediate blue selection state.
                    with self._keyboard.pressed(Key.ctrl):
                        self._keyboard.press("a")
                        self._keyboard.release("a")
                        self._keyboard.press("v")
                        self._keyboard.release("v")

                    self._restore(original, delay=True)
                except Exception as exc:
                    print(f"[inplace] error during paste: {exc}")
                    self._restore(original, delay=False)
                    return False

            self._notify_success()
            return True

        # 2. Fallback: Clipboard Sentinel if UIA is unavailable in this control
        return self._detect_and_translate_sentinel()

    def _detect_and_translate_sentinel(self) -> bool:
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
                try:
                    self._keyboard.press(Key.right)
                    self._keyboard.release(Key.right)
                except Exception:
                    pass
                return False

            # IMMEDIATELY deselect so blue highlight does NOT linger during translation!
            try:
                self._keyboard.press(Key.right)
                self._keyboard.release(Key.right)
            except Exception:
                pass

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
            if english is None:
                print("[inplace] translator returned None")
                self._restore(original, delay=False)
                return False

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            print(f"[inplace] translated {len(copied)} chars ro -> en in {elapsed_ms:.0f}ms (sentinel)")

            # Copy translated English text to clipboard
            self._clipboard.copy(english)

            paste_delay = getattr(self.config, "paste_delay_ms", 10) if self.config else 10
            if paste_delay > 0:
                time.sleep(paste_delay / 1000.0)

            # Replace selected text via Ctrl+A + Ctrl+V back-to-back with zero delay
            with self._keyboard.pressed(Key.ctrl):
                self._keyboard.press("a")
                self._keyboard.release("a")
                self._keyboard.press("v")
                self._keyboard.release("v")

            # Restore original clipboard after short delay
            self._restore(original, delay=True)

            self._notify_success()
            return True

        except Exception as exc:
            print(f"[inplace] error during sentinel translation: {exc}")
            self._restore(original, delay=False)
            return False


__all__ = ["InplaceTranslator", "save_clipboard", "restore_clipboard"]
