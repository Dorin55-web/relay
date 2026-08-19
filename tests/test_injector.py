"""The paste. Every feature in this program ends here.

Dictation, the write window, a template from the menu, a step of a chain, a
message from a phone - all of them arrive at paste_text, and all of them are
lost or misdelivered if it is wrong. It had no suite at all.

Nothing real is touched: the clipboard and the keyboard are both stood in for,
so this can assert the order things happen in, which is where the bugs are.
"""
import sys

import context  # noqa: E402,F401
context.isolate_state()

import relay.injector as injector  # noqa: E402

report = context.Report()
check = report.check


class Clipboard:
    def __init__(self, holding="what you had before"):
        self.value = holding
        self.writes = []
        self.breaks = False

    def copy(self, text):
        if self.breaks:
            raise RuntimeError("the clipboard is busy")
        self.writes.append(text)
        self.value = text

    def paste(self):
        return self.value


class Keyboard:
    """Records keystrokes in order, and can be told to fail."""

    def __init__(self):
        self.keys = []
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
            raise RuntimeError("no keyboard")
        self.keys.append(f"press {key}")

    def release(self, key):
        self.keys.append(f"release {key}")


class Config(dict):
    restore_clipboard = True
    auto_enter = False
    paste_delay_ms = 0
    restore_delay_ms = 0


def rig(**kwargs):
    """Point the module at fakes and hand them back."""
    clip, keys = Clipboard(), Keyboard()
    injector.pyperclip = clip
    injector._keyboard = keys
    injector.time.sleep = lambda _s: None       # no real delays in a suite
    injector.foreground_window = lambda: kwargs.get("front", 1)
    injector.focus_window = lambda hwnd: kwargs.get("can_focus", True)
    injector.window_title = lambda hwnd: "Some Window"
    injector._foreground_window_title = lambda: "Some Window"
    return clip, keys


print("\n--- the ordinary paste ---")
clip, keys = rig()
config = Config()
check("it reports success", injector.paste_text("hello", config) is True)
check("the text went to the clipboard", clip.writes[0] == "hello", str(clip.writes))
check("and Ctrl+V was sent",
      keys.keys[:4] == ["+Key.ctrl", "press v", "release v", "-Key.ctrl"],
      str(keys.keys))
check("no Enter, because auto_enter is off",
      not any("enter" in k.lower() for k in keys.keys), str(keys.keys))
check("and what you had is put back", clip.value == "what you had before",
      repr(clip.value))

print("\n--- nothing to send ---")
clip, keys = rig()
check("empty text does nothing", injector.paste_text("", config) is False)
check("and touches neither", not clip.writes and not keys.keys,
      f"{clip.writes} {keys.keys}")

print("\n--- submitting ---")
clip, keys = rig()
injector.paste_text("hello", config, submit=True)
check("Enter follows the paste when asked",
      keys.keys[-2:] == ["press Key.enter", "release Key.enter"], str(keys.keys))

clip, keys = rig()
sending = Config()
sending.auto_enter = True
injector.paste_text("hello", sending)
check("and when the config says so",
      any("enter" in k.lower() for k in keys.keys), str(keys.keys))

clip, keys = rig()
injector.paste_text("a template with <blanks>", sending, submit=False)
# A template still has its blanks in it. Enter would send it half-written,
# whatever the config prefers for dictation.
check("but submit=False beats the config",
      not any("enter" in k.lower() for k in keys.keys), str(keys.keys))

print("\n--- the clipboard is left as it was found ---")
clip, keys = rig()
keeping = Config()
keeping.restore_clipboard = False
injector.paste_text("hello", keeping)
check("unless you asked it not to be", clip.value == "hello", repr(clip.value))

clip, keys = rig()
injector.paste_text("phrase", config, manage_clipboard=False)
check("nor when the caller manages it itself", clip.value == "phrase",
      repr(clip.value))

print("\n--- when the paste itself fails ---")
# The bug this found: the text is on the clipboard by then, and the early
# return skipped the restore - so a failed paste silently replaced whatever
# you were carrying, and never gave it back.
clip, keys = rig()
keys.breaks = True
check("it reports the failure", injector.paste_text("hello", config) is False)
check("and still gives your clipboard back",
      clip.value == "what you had before", repr(clip.value))

clip, keys = rig()
clip.breaks = True
check("a clipboard that will not take the text fails too",
      injector.paste_text("hello", config) is False)
check("and sends no keys at all", keys.keys == [], str(keys.keys))

print("\n--- going back to the window you were writing in ---")
clip, keys = rig(front=999)          # focus has drifted somewhere else
restored = []
injector.focus_window = lambda hwnd: restored.append(hwnd) or True
injector.paste_text("hello", config, target_hwnd=42)
check("the target is brought forward first", restored == [42], str(restored))

clip, keys = rig(front=42)
restored = []
injector.focus_window = lambda hwnd: restored.append(hwnd) or True
injector.paste_text("hello", config, target_hwnd=42)
check("and left alone when it is already there", restored == [], str(restored))

sys.exit(report.finish())
