"""In-place F9 translation tests.

Verifies:
1. Clipboard sentinel detection: text present vs empty box vs whitespace-only.
2. Translation invocation and Ctrl+V injection without auto-enter.
3. User clipboard preservation and restoration across all paths.
4. VoicePrompt toggle() branching: in-place replacement vs voice dictation fallback.
5. Visual (orb pulse) and auditory (high chime) feedback triggers.
"""

import sys
import time

import context
context.isolate_state()

from pynput.keyboard import Key
from relay import __main__ as app
from relay.feedback import Feedback
from relay.inplace import InplaceTranslator, restore_clipboard, save_clipboard

report = context.Report()
check = report.check


class MockClipboard:
    """Stands in for pyperclip, recording copies and tracking current content."""

    def __init__(self, holding="original clipboard content"):
        self.value = holding
        self.writes = []
        self.breaks = False

    def copy(self, text):
        if self.breaks:
            raise RuntimeError("clipboard locked")
        self.writes.append(text)
        self.value = text

    def paste(self):
        if self.breaks:
            raise RuntimeError("clipboard locked")
        return self.value


class MockKeyboard:
    """Records keystrokes in order and simulates target window response on Ctrl+C."""

    def __init__(self, clipboard=None, target_text=None):
        self.keys = []
        self.clipboard = clipboard
        self.target_text = target_text
        self.breaks = False

    def pressed(self, key):
        keyboard = self

        class Held:
            def __enter__(self):
                keyboard.keys.append(f"+{key}")
                return keyboard

            def __exit__(self, *_):
                keyboard.keys.append(f"-{key}")
                return False

        return Held()

    def press(self, key):
        if self.breaks:
            raise RuntimeError("keyboard error")
        self.keys.append(f"press {key}")
        # When Ctrl+C is simulated, update clipboard if target_text is set
        if (
            str(key).lower() == "c"
            and "+Key.ctrl" in self.keys[-2:]
            and self.clipboard is not None
        ):
            if self.target_text is not None:
                self.clipboard.value = self.target_text

    def release(self, key):
        self.keys.append(f"release {key}")


class MockTranslator:
    def __init__(self, prefix="TRANSLATED: "):
        self.prefix = prefix
        self.calls = []
        self.breaks = False

    def translate(self, text):
        if self.breaks:
            raise RuntimeError("translation model failure")
        self.calls.append(text)
        return f"{self.prefix}{text}"


class MockOrb:
    def __init__(self):
        self.state = "idle"
        self.pulses = []

    def set_state(self, state):
        self.state = state

    def pulse(self, color="cyan", duration_ms=500):
        self.pulses.append((color, duration_ms))


class MockFeedback:
    def __init__(self):
        self.success_count = 0
        self.errors = []

    def inplace_success(self):
        self.success_count += 1

    def error(self, msg):
        self.errors.append(msg)


class Config:
    def __init__(self, **kwargs):
        self.restore_clipboard = True
        self.restore_delay_ms = 0
        self.paste_delay_ms = 0
        self.streaming = False
        self.auto_enter = False
        self.restore_caret = False
        self.beep_feedback = True
        self.print_transcript = True
        self.hotkey = "f9"
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockRecorder:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.device_name = "mock_mic"

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return "mock_audio"


print("\n--- 1. text present -> translated and replaced in-place ---")
config = Config()
clip = MockClipboard(holding="previous user notes")
kb = MockKeyboard(clipboard=clip, target_text="creează un script python")
trans = MockTranslator(prefix="create a python script: ")
orb = MockOrb()
fb = MockFeedback()

it = InplaceTranslator(
    config=config,
    translator_getter=lambda: trans,
    feedback=fb,
    orb_getter=lambda: orb,
    keyboard=kb,
    clipboard=clip,
)

res = it.detect_and_translate()
check("in-place translation reported success", res is True)
check("translator received Romanian text", trans.calls == ["creează un script python"])
check("Ctrl+A simulated", "press a" in kb.keys)
check("Ctrl+C simulated", "press c" in kb.keys)
check("Ctrl+V simulated", "press v" in kb.keys)
check("Enter was NOT simulated (no auto-submit)", not any("enter" in k.lower() for k in kb.keys))
check("user clipboard restored intact", clip.value == "previous user notes", repr(clip.value))
check("orb pulsed cyan", orb.pulses == [("cyan", 500)])
check("audio chime triggered", fb.success_count == 1)

print("\n--- 2. empty box -> fallback to voice dictation ---")
clip = MockClipboard(holding="important link")
kb = MockKeyboard(clipboard=clip, target_text=None)  # nothing in box, sentinel remains
trans = MockTranslator()
orb = MockOrb()
fb = MockFeedback()

it = InplaceTranslator(
    config=config,
    translator_getter=lambda: trans,
    feedback=fb,
    orb_getter=lambda: orb,
    keyboard=kb,
    clipboard=clip,
)

res = it.detect_and_translate()
check("empty box reports False", res is False)
check("translator not called", trans.calls == [])
check("Ctrl+V not simulated", "press v" not in kb.keys)
check("user clipboard restored intact", clip.value == "important link", repr(clip.value))
check("orb did not pulse", orb.pulses == [])
check("audio chime not triggered", fb.success_count == 0)

print("\n--- 3. whitespace-only box -> fallback to voice dictation ---")
clip = MockClipboard(holding="important link")
kb = MockKeyboard(clipboard=clip, target_text="   \n\t   ")  # whitespace only
trans = MockTranslator()
orb = MockOrb()
fb = MockFeedback()

it = InplaceTranslator(
    config=config,
    translator_getter=lambda: trans,
    feedback=fb,
    orb_getter=lambda: orb,
    keyboard=kb,
    clipboard=clip,
)

res = it.detect_and_translate()
check("whitespace box reports False", res is False)
check("translator not called", trans.calls == [])
check("Ctrl+V not simulated", "press v" not in kb.keys)
check("user clipboard restored intact", clip.value == "important link", repr(clip.value))

print("\n--- 4. translation failure -> recovers, restores clipboard, reports False ---")
clip = MockClipboard(holding="code snippet")
kb = MockKeyboard(clipboard=clip, target_text="salut")
trans = MockTranslator()
trans.breaks = True

it = InplaceTranslator(
    config=config,
    translator_getter=lambda: trans,
    feedback=fb,
    orb_getter=lambda: orb,
    keyboard=kb,
    clipboard=clip,
)

res = it.detect_and_translate()
check("translation error reports False", res is False)
check("Ctrl+V not simulated after error", "press v" not in kb.keys)
check("user clipboard restored after error", clip.value == "code snippet", repr(clip.value))

print("\n--- 5. paste error -> recovers, restores clipboard, reports False ---")
clip = MockClipboard(holding="code snippet")
kb = MockKeyboard(clipboard=clip, target_text="salut")
kb.breaks = True  # keyboard throws
trans = MockTranslator()

it = InplaceTranslator(
    config=config,
    translator_getter=lambda: trans,
    feedback=fb,
    orb_getter=lambda: orb,
    keyboard=kb,
    clipboard=clip,
)

res = it.detect_and_translate()
check("keyboard error reports False", res is False)
check("user clipboard restored after keyboard error", clip.value == "code snippet", repr(clip.value))

print("\n--- 6. clipboard restoration disabled in config ---")
config_no_restore = Config(restore_clipboard=False)
clip = MockClipboard(holding="leave me alone")
kb = MockKeyboard(clipboard=clip, target_text="salut")
trans = MockTranslator()

it = InplaceTranslator(
    config=config_no_restore,
    translator_getter=lambda: trans,
    feedback=fb,
    orb_getter=lambda: orb,
    keyboard=kb,
    clipboard=clip,
)

res = it.detect_and_translate()
check("succeeds when restore_clipboard is False", res is True)

print("\n--- 7. VoicePrompt toggle() branching ---")
from relay.config import load_config
cfg = load_config("no-such-file.json")
cfg.update(beep_feedback=False)
vp = app.VoicePrompt(cfg)
vp._ready.set()
vp.recorder = MockRecorder()
vp.orb = MockOrb()

# Case A: in-place translation detects text and succeeds
vp.inplace_translator.detect_and_translate = lambda: True
vp.state = app.IDLE
vp.toggle()
check("recorder.start() was NOT called on in-place translation", vp.recorder.started == 0)
check("state stayed IDLE", vp.state == app.IDLE)
check("orb reset to IDLE", vp.orb.state == app.IDLE)

# Case B: box is empty, fallback to voice dictation
vp.inplace_translator.detect_and_translate = lambda: False
vp.state = app.IDLE
vp.toggle()
check("recorder.start() was called on empty box fallback", vp.recorder.started == 1)
check("state transitioned to RECORDING", vp.state == app.RECORDING)

print("\n--- 8. Feedback inplace_success chime test ---")
real_fb = Feedback(cfg)
real_fb.enabled = False  # test mute path without hardware beep
real_fb.inplace_success()
check("inplace_success works cleanly when muted", True)

real_fb.enabled = True   # test enabled path without exception
real_fb.inplace_success()
check("inplace_success works cleanly when enabled", True)

print("\n--- 9. Orb pulse method check ---")
from relay.overlay import Orb
check("Orb has pulse method", hasattr(Orb, "pulse") and callable(Orb.pulse))

print("\n--- 10. Silent UIA mode (zero copy keystrokes) ---")
cfg_uia = Config()
clip_uia = MockClipboard(holding="important user clip")
kb_uia = MockKeyboard(clipboard=clip_uia)
trans_uia = MockTranslator(prefix="UIA_ENG: ")
orb_uia = MockOrb()
fb_uia = MockFeedback()

it_uia = InplaceTranslator(
    config=cfg_uia,
    translator_getter=lambda: trans_uia,
    feedback=fb_uia,
    orb_getter=lambda: orb_uia,
    keyboard=kb_uia,
    clipboard=clip_uia,
    uia_reader=lambda h, n: "scrie cod python curat",
)

res_uia = it_uia.detect_and_translate()
check("silent UIA translation reported success", res_uia is True)
check("translator received UIA text directly", trans_uia.calls == ["scrie cod python curat"])
check("Ctrl+C was NOT simulated (silent read)", not any("press c" in k for k in kb_uia.keys))
check("Ctrl+A was simulated for replacement", "press a" in kb_uia.keys)
check("Ctrl+V was simulated for replacement", "press v" in kb_uia.keys)
check("user clipboard restored intact", clip_uia.value == "important user clip")
check("orb pulsed cyan", orb_uia.pulses == [("cyan", 500)])
check("audio chime triggered", fb_uia.success_count == 1)

print("\n--- 11. Silent UIA mode: empty box (zero keystrokes) ---")
kb_empty = MockKeyboard(clipboard=clip_uia)
it_empty = InplaceTranslator(
    config=cfg_uia,
    translator_getter=lambda: trans_uia,
    feedback=fb_uia,
    orb_getter=lambda: orb_uia,
    keyboard=kb_empty,
    clipboard=clip_uia,
    uia_reader=lambda h, n: "   ",
)

res_empty = it_empty.detect_and_translate()
check("silent UIA empty box reports False", res_empty is False)
check("zero keystrokes simulated on empty box", len(kb_empty.keys) == 0)

sys.exit(report.finish())
