"""Audible and console status. There's no window, so the beeps are the UI.

The beep plays through whatever output you're wearing, which is exactly where
you want the confirmation when you're using a headset.
"""

import sys
import threading

try:
    import winsound

    _HAVE_WINSOUND = sys.platform == "win32"
except ImportError:
    _HAVE_WINSOUND = False


def _beep_async(tones):
    """Play tones off-thread; winsound.Beep blocks for its full duration."""
    if not _HAVE_WINSOUND:
        return

    def run():
        try:
            for frequency, milliseconds in tones:
                winsound.Beep(frequency, milliseconds)
        except Exception:
            pass  # a failed beep must never interrupt a dictation

    threading.Thread(target=run, daemon=True).start()


class Feedback:
    def __init__(self, config):
        self.enabled = config.beep_feedback
        self.print_transcript = config.print_transcript

    def recording_started(self, device_name):
        print(f"\n[REC] recording from: {device_name}   (press the hotkey again to stop)")
        if self.enabled:
            _beep_async([(660, 90), (990, 90)])  # rising = started

    def recording_stopped(self):
        print("[...] processing")
        if self.enabled:
            _beep_async([(990, 90), (660, 90)])  # falling = stopped

    def success(self, text):
        # Keeping the text on screen means a failed paste is still recoverable
        # by hand, so this stays on unless explicitly disabled.
        print(f"[OK] pasted: {text}\n" if self.print_transcript else "[OK] pasted\n")
        if self.enabled:
            _beep_async([(880, 70)])

    def nothing_heard(self):
        print("[--] nothing recognised; ignoring\n")
        if self.enabled:
            _beep_async([(440, 120)])

    def error(self, message):
        print(f"[!!] {message}\n")
        if self.enabled:
            _beep_async([(330, 180), (250, 220)])  # low buzz = failure
