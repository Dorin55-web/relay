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
check("orb remained still (no blue pulse animation)", orb.pulses == [])
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
check("orb remained still (no blue pulse animation)", orb_uia.pulses == [])
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

print("\n--- 12. Cleared / deleted box in active input (zero keystrokes, no stale history) ---")
clip_del = MockClipboard(holding="unrelated clip")
kb_del = MockKeyboard(clipboard=clip_del)
trans_del = MockTranslator(prefix="TRANSLATED: ")
orb_del = MockOrb()
fb_del = MockFeedback()

it_del = InplaceTranslator(
    config=cfg_uia,
    translator_getter=lambda: trans_del,
    feedback=fb_del,
    orb_getter=lambda: orb_del,
    keyboard=kb_del,
    clipboard=clip_del,
    uia_reader=lambda h, n: "",  # User cleared the text
)

res_del = it_del.detect_and_translate()
check("deleted box returns False immediately", res_del is False)
check("zero keystrokes on deleted box", len(kb_del.keys) == 0)
check("translator not called on deleted box", trans_del.calls == [])
check("orb did not pulse on deleted box", orb_del.pulses == [])
check("feedback chime not triggered on deleted box", fb_del.success_count == 0)
check("clipboard intact on deleted box", clip_del.value == "unrelated clip")

print("\n--- 13. read_active_input_text strictly isolates focused element without window scan ---")
from relay import uia

class MockUIAElement:
    def __init__(self, text="", control_type=50004, is_input=True):
        self.CurrentControlType = control_type
        self.CurrentIsKeyboardFocusable = is_input
        self.text = text
        self.CurrentBoundingRectangle = type("Rect", (), {"left": 10, "top": 10, "right": 200, "bottom": 50})()

    def GetCurrentPattern(self, pattern_id):
        # Emulate ValuePattern if text is set
        elem = self
        class MockVP:
            CurrentValue = elem.text
            CurrentIsReadOnly = False
            def QueryInterface(self, _):
                return self
        return MockVP()

class MockUIAAutomation:
    def __init__(self, focused_element=None):
        self.focused = focused_element
        self.element_from_handle_called = False

    def GetFocusedElement(self):
        return self.focused

    def ElementFromHandle(self, hwnd):
        self.element_from_handle_called = True
        raise AssertionError("ElementFromHandle should NEVER be called by read_active_input_text!")

# Test 13A: Focused element has text
mock_auto_with_text = MockUIAAutomation(MockUIAElement(text="salutare"))
orig_auto, orig_UIA = uia._uia()
uia._uia = lambda: (mock_auto_with_text, orig_UIA)
try:
    txt = uia.read_active_input_text(hwnd=12345)
    check("read_active_input_text returns focused text", txt == "salutare")
    check("ElementFromHandle was NOT called (no window tree scan)", not mock_auto_with_text.element_from_handle_called)
finally:
    uia._uia = lambda: (orig_auto, orig_UIA)

# Test 13B: Focused element is empty / cleared
mock_auto_empty = MockUIAAutomation(MockUIAElement(text=""))
uia._uia = lambda: (mock_auto_empty, orig_UIA)
try:
    txt_empty = uia.read_active_input_text(hwnd=12345)
    check("read_active_input_text returns empty string for empty focused control", txt_empty == "")
    check("ElementFromHandle was NOT called for empty control", not mock_auto_empty.element_from_handle_called)
finally:
    uia._uia = lambda: (orig_auto, orig_UIA)

# Test 13C: Focused element has only whitespace / replacement char
mock_auto_ws = MockUIAAutomation(MockUIAElement(text="￼  \n  "))
uia._uia = lambda: (mock_auto_ws, orig_UIA)
try:
    txt_ws = uia.read_active_input_text(hwnd=12345)
    check("read_active_input_text returns empty string for whitespace/replacement char", txt_ws == "")
finally:
    uia._uia = lambda: (orig_auto, orig_UIA)

print("\n--- 14. Clean insertion via uia_setter without blue highlight (zero Ctrl+A) ---")
clip_clean = MockClipboard(holding="clean clip intact")
kb_clean = MockKeyboard(clipboard=clip_clean)
trans_clean = MockTranslator(prefix="CLEAN_ENG: ")
orb_clean = MockOrb()
fb_clean = MockFeedback()
setter_writes = []

def mock_setter(val):
    setter_writes.append(val)
    return True

it_clean = InplaceTranslator(
    config=cfg_uia,
    translator_getter=lambda: trans_clean,
    feedback=fb_clean,
    orb_getter=lambda: orb_clean,
    keyboard=kb_clean,
    clipboard=clip_clean,
    uia_reader=lambda h, n: "traduce curat",
    uia_setter=mock_setter,
)

res_clean = it_clean.detect_and_translate()
check("clean insertion reported success", res_clean is True)
check("uia_setter received translated text", setter_writes == ["CLEAN_ENG: traduce curat"])
check("Ctrl+A was NOT simulated (zero blue highlight)", not any("press a" in k for k in kb_clean.keys))
check("Ctrl+V was NOT simulated", not any("press v" in k for k in kb_clean.keys))
check("zero keystrokes emitted", len(kb_clean.keys) == 0)
check("user clipboard completely untouched", clip_clean.value == "clean clip intact")
check("orb remained still (no blue pulse animation)", orb_clean.pulses == [])
check("audio chime triggered", fb_clean.success_count == 1)

print("\n--- 15. VoicePrompt toggle on empty/deleted box starts dictation cleanly ---")
vp_del = app.VoicePrompt(cfg)
vp_del._ready.set()
vp_del.recorder = MockRecorder()
vp_del.orb = MockOrb()
vp_del.inplace_translator.detect_and_translate = lambda: False
vp_del.state = app.IDLE

vp_del.toggle()
check("recorder.start() called on empty box", vp_del.recorder.started == 1)
check("state is RECORDING", vp_del.state == app.RECORDING)
check("orb is RECORDING", vp_del.orb.state == app.RECORDING)
check("orb did not pulse cyan", vp_del.orb.pulses == [])

print("\n--- 16. Multiline / tall input with ValuePattern (height > MAX_INPUT_HEIGHT) ---")
class MockTallUIAElement:
    def __init__(self, text="tall input text", has_vp=True):
        self.CurrentControlType = 50025  # Custom control type (not standard Edit/Document)
        self.CurrentIsKeyboardFocusable = True
        self.text = text
        self.has_vp = has_vp
        self.CurrentBoundingRectangle = type("Rect", (), {"left": 10, "top": 10, "right": 400, "bottom": 500})()  # height = 490 > 300

    def GetCurrentPattern(self, pattern_id):
        if not self.has_vp:
            return None
        elem = self
        class MockVP:
            @property
            def CurrentValue(self):
                return elem.text
            CurrentIsReadOnly = False
            def QueryInterface(self, _):
                return self
            def SetValue(self, val):
                elem.text = val
        return MockVP()

mock_tall = MockTallUIAElement(text="salutare din căsuță înaltă")
mock_tall_auto = MockUIAAutomation(mock_tall)
uia._uia = lambda: (mock_tall_auto, orig_UIA)
try:
    tall_text = uia.read_active_input_text()
    check("read_active_input_text reads tall element with ValuePattern", tall_text == "salutare din căsuță înaltă")
    tall_set_ok = uia.set_active_input_text("english in tall input")
    check("set_active_input_text sets tall element with ValuePattern", tall_set_ok is True)
    check("tall element text was updated", mock_tall.text == "english in tall input")
finally:
    uia._uia = lambda: (orig_auto, orig_UIA)

print("\n--- 17. UIA translator returning None safely returns False ---")
clip_none = MockClipboard(holding="orig clip none")
kb_none = MockKeyboard(clipboard=clip_none)
trans_none = MockTranslator()
trans_none.translate = lambda text: None  # Translator returns None

it_none = InplaceTranslator(
    config=cfg_uia,
    translator_getter=lambda: trans_none,
    keyboard=kb_none,
    clipboard=clip_none,
    uia_reader=lambda h, n: "romanian text here",
)
res_none = it_none.detect_and_translate()
check("translator returning None returns False", res_none is False)
check("zero keystrokes on None translation", len(kb_none.keys) == 0)
check("user clipboard untouched when translation returns None", clip_none.value == "orig clip none")

print("\n--- 18. Non-input focused element (e.g. Button) rejected by read/set ---")
class MockButtonElement:
    def __init__(self):
        self.CurrentControlType = 50000  # Button
        self.CurrentIsKeyboardFocusable = True  # Real buttons are keyboard focusable
        self.CurrentBoundingRectangle = type("Rect", (), {"left": 0, "top": 0, "right": 50, "bottom": 20})()
    def GetCurrentPattern(self, _):
        return None

mock_btn_auto = MockUIAAutomation(MockButtonElement())
uia._uia = lambda: (mock_btn_auto, orig_UIA)
try:
    btn_read = uia.read_active_input_text()
    check("read_active_input_text returns None for Button", btn_read is None)
    btn_set = uia.set_active_input_text("cannot set button")
    check("set_active_input_text returns False for Button", btn_set is False)
finally:
    uia._uia = lambda: (orig_auto, orig_UIA)

print("\n--- 19. Win32 Document control with NativeWindowHandle sets text via WM_SETTEXT ---")
class MockWin32DocElement:
    def __init__(self, hwnd=9999):
        self.CurrentControlType = 50030  # UIA_DocumentControlTypeId
        self.CurrentIsKeyboardFocusable = True
        self.CurrentNativeWindowHandle = hwnd
        self.CurrentBoundingRectangle = type("Rect", (), {"left": 10, "top": 10, "right": 200, "bottom": 50})()
    def GetCurrentPattern(self, _):
        return None  # No ValuePattern

mock_doc = MockWin32DocElement()
mock_doc_auto = MockUIAAutomation(mock_doc)
sent_messages = []
import ctypes
orig_send_message = getattr(ctypes.windll.user32, "SendMessageW", None)

def mock_send_message(hwnd, msg, wparam, lparam):
    sent_messages.append((hwnd, msg, wparam, lparam))
    return 1

ctypes.windll.user32.SendMessageW = mock_send_message
uia._uia = lambda: (mock_doc_auto, orig_UIA)
try:
    doc_set_ok = uia.set_active_input_text("WM_SETTEXT into document")
    check("set_active_input_text succeeds on Document control via WM_SETTEXT", doc_set_ok is True)
    check("SendMessageW received WM_SETTEXT (0x000C)", any(m[1] == 0x000C for m in sent_messages))
finally:
    ctypes.windll.user32.SendMessageW = orig_send_message
    uia._uia = lambda: (orig_auto, orig_UIA)

print("\n--- 20. Webpage document root (DocumentControlTypeId, height > 300, no HWND) rejected ---")
class MockWebDocElement:
    def __init__(self):
        self.CurrentControlType = 50030  # UIA_DocumentControlTypeId
        self.CurrentIsKeyboardFocusable = True
        self.CurrentNativeWindowHandle = 0  # No native HWND
        self.CurrentBoundingRectangle = type("Rect", (), {"left": 0, "top": 0, "right": 1920, "bottom": 1080})()  # Full screen page root
        self.text = "Entire webpage text with 50,000 words..."

    def GetCurrentPattern(self, pattern_id):
        # Implements TextPattern (like a real web page root), but NOT ValuePattern
        if pattern_id == uia._id(orig_UIA, "UIA_TextPatternId"):
            elem = self
            class MockTP:
                class DocumentRange:
                    @staticmethod
                    def GetText(_):
                        return elem.text
                def QueryInterface(self, _):
                    return self
            return MockTP()
        return None

mock_webdoc = MockWebDocElement()
mock_webdoc_auto = MockUIAAutomation(mock_webdoc)
uia._uia = lambda: (mock_webdoc_auto, orig_UIA)
try:
    webdoc_read = uia.read_active_input_text()
    check("read_active_input_text rejects webpage document root (returns empty string)", webdoc_read == "")
    webdoc_set = uia.set_active_input_text("cannot set webpage root")
    check("set_active_input_text rejects webpage document root (returns False)", webdoc_set is False)
finally:
    uia._uia = lambda: (orig_auto, orig_UIA)

print("\n--- 21. Sentinel mode translator returning None safely returns False ---")
clip_sentinel_none = MockClipboard(holding="orig sentinel clip")
kb_sentinel_none = MockKeyboard(clipboard=clip_sentinel_none, target_text="salut romanesc")
trans_sentinel_none = MockTranslator()
trans_sentinel_none.translate = lambda text: None  # Returns None

it_sentinel_none = InplaceTranslator(
    config=config,
    translator_getter=lambda: trans_sentinel_none,
    keyboard=kb_sentinel_none,
    clipboard=clip_sentinel_none,
    uia_reader=None,
)
res_sentinel_none = it_sentinel_none.detect_and_translate()
check("sentinel translator returning None returns False", res_sentinel_none is False)
check("sentinel original clipboard restored intact", clip_sentinel_none.value == "orig sentinel clip")
check("Ctrl+V was NOT simulated when translation returns None", "press v" not in kb_sentinel_none.keys)

print("\n--- 22. InplaceTranslator on WebDoc / Non-input falls back to dictation without Sentinel ---")
mock_webdoc_auto2 = MockUIAAutomation(MockWebDocElement())
uia._uia = lambda: (mock_webdoc_auto2, orig_UIA)
try:
    clip_doc = MockClipboard(holding="orig user clip")
    kb_doc = MockKeyboard(clipboard=clip_doc, target_text="Webpage text copied on Ctrl+C")
    trans_doc = MockTranslator()
    it_doc = InplaceTranslator(
        config=cfg_uia,
        translator_getter=lambda: trans_doc,
        keyboard=kb_doc,
        clipboard=clip_doc,
        uia_reader=uia.read_active_input_text,
        uia_setter=uia.set_active_input_text,
    )
    res_doc = it_doc.detect_and_translate()
    check("detect_and_translate on WebDoc returns False", res_doc is False)
    check("zero keystrokes simulated (no Ctrl+A/Ctrl+C Sentinel bypass)", len(kb_doc.keys) == 0)
    check("translator not called on WebDoc", len(trans_doc.calls) == 0)
    check("user clipboard left completely untouched", clip_doc.value == "orig user clip")
finally:
    uia._uia = lambda: (orig_auto, orig_UIA)

print("\n--- 23. Read-only Edit control rejected by read/set (zero keystrokes) ---")
class MockReadOnlyEditElement:
    def __init__(self, text="read only chat message"):
        self.CurrentControlType = 50004  # Edit
        self.CurrentIsKeyboardFocusable = True
        self.text = text
        self.CurrentBoundingRectangle = type("Rect", (), {"left": 10, "top": 10, "right": 200, "bottom": 50})()
    def GetCurrentPattern(self, pattern_id):
        elem = self
        class MockVP:
            CurrentValue = elem.text
            CurrentIsReadOnly = True  # Read-only!
            def QueryInterface(self, _):
                return self
            def SetValue(self, val):
                raise RuntimeError("Cannot write to read-only control")
        return MockVP()

mock_ro = MockReadOnlyEditElement()
mock_ro_auto = MockUIAAutomation(mock_ro)
uia._uia = lambda: (mock_ro_auto, orig_UIA)
try:
    ro_read = uia.read_active_input_text()
    check("read_active_input_text returns empty string for read-only control", ro_read == "")
    ro_set = uia.set_active_input_text("cannot overwrite")
    check("set_active_input_text returns False for read-only control", ro_set is False)

    clip_ro = MockClipboard("ro clip")
    kb_ro = MockKeyboard(clipboard=clip_ro, target_text="read only text")
    trans_ro = MockTranslator()
    it_ro = InplaceTranslator(
        config=cfg_uia,
        translator_getter=lambda: trans_ro,
        keyboard=kb_ro,
        clipboard=clip_ro,
        uia_reader=uia.read_active_input_text,
        uia_setter=uia.set_active_input_text,
    )
    res_ro = it_ro.detect_and_translate()
    check("detect_and_translate on read-only control returns False", res_ro is False)
    check("zero keystrokes on read-only control", len(kb_ro.keys) == 0)
    check("translator not called on read-only control", len(trans_ro.calls) == 0)
finally:
    uia._uia = lambda: (orig_auto, orig_UIA)

print("\n--- 24. Erased box with stale ValuePattern: TextPattern priority returns empty ---")
class MockStaleChromiumElement:
    """Simulates Chromium/Monaco after text was cleared: TextPattern is empty, ValuePattern holds stale cached text."""
    def __init__(self, stale_text="Text vechi sters", current_name="Message input"):
        self.CurrentControlType = 50004  # Edit
        self.CurrentIsKeyboardFocusable = True
        self.CurrentName = current_name
        self.stale_text = stale_text
        self.CurrentBoundingRectangle = type("Rect", (), {"left": 10, "top": 10, "right": 300, "bottom": 80})()

    def GetCurrentPattern(self, pattern_id):
        elem = self
        if pattern_id == uia._id(orig_UIA, "UIA_TextPatternId"):
            class MockTP:
                class DocumentRange:
                    @staticmethod
                    def GetText(_):
                        return "\n"  # Empty / just a newline in Monaco
                def QueryInterface(self, _):
                    return self
            return MockTP()
        elif pattern_id == uia._id(orig_UIA, "UIA_ValuePatternId"):
            class MockVP:
                CurrentValue = elem.stale_text  # Stale text retained in Chromium's cache!
                CurrentIsReadOnly = False
                def QueryInterface(self, _):
                    return self
            return MockVP()
        return None

mock_stale = MockStaleChromiumElement()
mock_stale_auto = MockUIAAutomation(mock_stale)
uia._uia = lambda: (mock_stale_auto, orig_UIA)
try:
    stale_read = uia.read_active_input_text()
    check("read_active_input_text ignores stale ValuePattern and returns empty string", stale_read == "")

    clip_stale = MockClipboard("stale test clip")
    kb_stale = MockKeyboard(clipboard=clip_stale)
    trans_stale = MockTranslator()
    it_stale = InplaceTranslator(
        config=cfg_uia,
        translator_getter=lambda: trans_stale,
        keyboard=kb_stale,
        clipboard=clip_stale,
        uia_reader=uia.read_active_input_text,
    )
    res_stale = it_stale.detect_and_translate()
    check("detect_and_translate returns False on erased box with stale cache", res_stale is False)
    check("zero keystrokes emitted on erased box", len(kb_stale.keys) == 0)
    check("translator was NOT called on erased box", len(trans_stale.calls) == 0)
finally:
    uia._uia = lambda: (orig_auto, orig_UIA)

print("\n--- 25. set_active_input_text returns False when ValuePattern.SetValue is a no-op (Chromium DOM) ---")
class MockNoOpSetValueElement:
    """Simulates Chromium/Monaco DOM where SetValue returns S_OK but does not update CurrentValue."""
    def __init__(self, initial_text="text initial in romana"):
        self.CurrentControlType = 50004
        self.CurrentIsKeyboardFocusable = True
        self.CurrentBoundingRectangle = type("Rect", (), {"left": 10, "top": 10, "right": 300, "bottom": 80})()
        self.val = initial_text

    def GetCurrentPattern(self, pattern_id):
        elem = self
        if pattern_id == uia._id(orig_UIA, "UIA_ValuePatternId"):
            class MockVP:
                @property
                def CurrentValue(self):
                    return elem.val  # Value stays unchanged (silent no-op in Chromium)
                CurrentIsReadOnly = False
                def QueryInterface(self, _):
                    return self
                def SetValue(self, val):
                    pass  # Silent no-op
            return MockVP()
        return None

mock_noop = MockNoOpSetValueElement()
mock_noop_auto = MockUIAAutomation(mock_noop)
uia._uia = lambda: (mock_noop_auto, orig_UIA)
try:
    noop_result = uia.set_active_input_text("english replacement")
    check("set_active_input_text returns False when SetValue is silent no-op", noop_result is False)
finally:
    uia._uia = lambda: (orig_auto, orig_UIA)

print("\n--- 26. InplaceTranslator falls back to atomic keystroke replacement when uia_setter fails ---")
clip_fb = MockClipboard(holding="orig user clipboard")
kb_fb = MockKeyboard(clipboard=clip_fb)
trans_fb = MockTranslator(prefix="TRANSLATED: ")
orb_fb = MockOrb()
fb_fb = MockFeedback()

def failing_uia_setter(val):
    return False  # Emulate Chromium failure

it_fb = InplaceTranslator(
    config=cfg_uia,
    translator_getter=lambda: trans_fb,
    feedback=fb_fb,
    orb_getter=lambda: orb_fb,
    keyboard=kb_fb,
    clipboard=clip_fb,
    uia_reader=lambda h, n: "mesaj in romana",
    uia_setter=failing_uia_setter,
)
res_fb = it_fb.detect_and_translate()
check("detect_and_translate succeeds via fallback replacement", res_fb is True)
check("translator received Romanian text", trans_fb.calls == ["mesaj in romana"])
check("fallback replacement simulated Ctrl+A", "press a" in kb_fb.keys)
check("fallback replacement simulated Ctrl+V", "press v" in kb_fb.keys)
check("user clipboard restored intact", clip_fb.value == "orig user clipboard")
check("orb remained still (no blue pulse animation)", orb_fb.pulses == [])
check("feedback success chime called", fb_fb.success_count == 1)

print("\n--- 27. Optimistic UI does NOT prematurely turn orb blue in IDLE state ---")
vp_opt = app.VoicePrompt(cfg)
vp_opt._ready.set()
vp_opt.state = app.IDLE
vp_opt.orb = MockOrb()
vp_opt.orb.state = "idle"

vp_opt._optimistic_ui()
check("orb state remains idle (no premature blue ring on F9 press)", vp_opt.orb.state == "idle")

vp_opt.state = app.RECORDING
vp_opt._optimistic_ui()
check("orb state becomes processing when stopping recording", vp_opt.orb.state == app.PROCESSING)

print("\n--- 28. _send_ctrl_a_v helper function ---")
from relay.inplace import _send_ctrl_a_v
mock_kb_direct = MockKeyboard()
res_direct = _send_ctrl_a_v(keyboard=mock_kb_direct)
check("_send_ctrl_a_v executes without exception", res_direct is True)

sys.exit(report.finish())
