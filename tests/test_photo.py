"""Comprehensive test suite for Telegram photo ingestion and hybrid injection.

Covers Tiers 1-4:
- Tier 1: Feature Coverage (Update parsing, highest resolution, getFile, download handler,
          GUI injection, Terminal injection, Romanian caption translation, empty caption
          fallback, verbatim caption '=' prefix).
- Tier 2: Boundary & Corner Cases (Unsorted PhotoSize, single PhotoSize, whitespace-only caption,
          download failure/timeout handling, paths containing spaces, non-text clipboard safety).
- Tier 3: Cross-Feature Combinations (GUI x Romanian/Empty/Verbatim, Terminal x auto_enter True/False,
          paste_hybrid polymorphism with plain text strings vs photo dicts).
- Tier 4: Real-World Scenarios (Simulated E2E: Phone photo with Romanian caption -> Antigravity GUI;
          Phone photo without caption -> OpenCode terminal).
"""

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

import context  # noqa: E402,F401
context.isolate_state()

from relay import agent  # noqa: E402
from relay import autopilot as auto_mod  # noqa: E402
from relay import injector  # noqa: E402
from relay import remote as remote_mod  # noqa: E402
from relay import target  # noqa: E402
from relay.autopilot import Autopilot  # noqa: E402
from relay.injector import copy_image_to_clipboard, paste_hybrid, raw_bytes_to_dib  # noqa: E402
from relay.remote import DEFAULT_PHOTO_PROMPT, Remote, download_file  # noqa: E402
from relay.target import is_terminal_window  # noqa: E402

auto_mod.START_SECONDS = 0.4
auto_mod.STEP_TIMEOUT_SECONDS = 0.6

report = context.Report()
check = report.check

MINE, THEIRS = 111, 999
GUI_HWND = 1001
TERM_HWND = 2002

agent.profile_for = lambda hwnd, profiles=None: (
    {"name": "Antigravity", "process": "Antigravity.exe", "input": "Message input"}
    if hwnd == GUI_HWND else
    {"name": "opencode", "process": "WindowsTerminal.exe", "kind": "terminal"}
    if hwnd == TERM_HWND else {"name": "default"}
)
agent.state = lambda hwnd, profile=None, seen=None: agent.IDLE
agent.focus_input = lambda hwnd, prof=None: True
remote_mod.window_title = lambda hwnd: "Antigravity" if hwnd == GUI_HWND else "OpenCode"


def generate_png_bytes(width=16, height=16, color="red"):
    """Generate genuine binary PNG bytes using PySide6 QImage."""
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor(color))
    buf = QBuffer()
    buf.open(QIODevice.ReadWrite)
    img.save(buf, "PNG")
    return bytes(buf.data())


SAMPLE_PNG = generate_png_bytes(16, 16, "blue")
SAMPLE_PNG_2 = generate_png_bytes(32, 32, "green")


class Api:
    """Mock Telegram API supporting updates, message cards, and getFile."""

    def __init__(self):
        self.updates = []
        self.sent = []
        self.edits = []
        self.messages = {}
        self.get_file_calls = []
        self.file_map = {}
        self.fail_get_file = False
        self.fail_get_file_error = "getFile network error"
        self.calls = 0

    @property
    def card(self):
        return self.messages.get(len(self.sent), "")

    def feed_photo(self, photo_sizes, caption=None, chat=MINE, update_id=None):
        msg = {"chat": {"id": chat}, "photo": photo_sizes}
        if caption is not None:
            msg["caption"] = caption
        self.updates.append({
            "update_id": update_id if update_id is not None else len(self.updates) + 1,
            "message": msg,
        })

    def feed_text(self, text, chat=MINE, update_id=None):
        self.updates.append({
            "update_id": update_id if update_id is not None else len(self.updates) + 1,
            "message": {"chat": {"id": chat}, "text": text},
        })

    def __call__(self, token, method, params, timeout=None):
        self.calls += 1
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return out
        if method == "sendMessage":
            self.sent.append(params["text"])
            self.messages[len(self.sent)] = params["text"]
            return {"message_id": len(self.sent)}
        if method == "editMessageText":
            self.edits.append(params["text"])
            self.messages[params["message_id"]] = params["text"]
            return {}
        if method == "getFile":
            if self.fail_get_file:
                raise RuntimeError(self.fail_get_file_error)
            file_id = params.get("file_id")
            self.get_file_calls.append(file_id)
            if file_id in self.file_map:
                return self.file_map[file_id]
            return {"file_id": file_id, "file_path": f"photos/{file_id}.jpg"}
        if method == "setMyCommands":
            return True
        raise AssertionError(f"unexpected method {method}")


class Clipboard:
    """Mock pyperclip clipboard."""

    def __init__(self, holding="what you had before"):
        self.value = holding
        self.writes = []
        self.breaks = False

    def copy(self, text):
        if self.breaks:
            raise RuntimeError("clipboard busy")
        self.writes.append(text)
        self.value = text

    def paste(self):
        if self.breaks:
            raise RuntimeError("clipboard unreadable")
        return self.value


class Keyboard:
    """Mock pynput keyboard controller recording keystrokes in order."""

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


def rig_injector(**kwargs):
    """Set up injector fakes for testing."""
    clip, keys = Clipboard(), Keyboard()
    injector.pyperclip = clip
    injector._keyboard = keys
    injector.time.sleep = lambda _s: None
    injector.foreground_window = lambda: kwargs.get("front", GUI_HWND)
    injector.focus_window = lambda hwnd: kwargs.get("can_focus", True)
    injector.window_title = lambda hwnd: "Test Window"
    injector._foreground_window_title = lambda: "Test Window"
    return clip, keys


def make_bot(chat_id=MINE, downloader=None, translate=None, api=None, send=None):
    """Create a fully isolated Remote instance with mock hooks."""
    tmp = Path(tempfile.mkdtemp(prefix="relay-photo-test-"))
    path = tmp / "telegram.json"
    path.write_text(json.dumps({"token": "test_token", "chat_id": chat_id}), encoding="utf-8")
    api_inst = api if api is not None else Api()
    box = []
    alive = {"all": True}

    default_downloader = downloader or (lambda token, fpath: SAMPLE_PNG)
    default_send = send or (lambda step, hwnd: (box.append(step) or True))

    bot = Remote(
        settings={"token": "test_token", "chat_id": chat_id, "path": path},
        send=default_send,
        target_getter=lambda: GUI_HWND,
        log=lambda *_: None,
        api=api_inst,
        is_window=lambda _h: alive["all"],
        keeper_watching=lambda: True,
        downloader=default_downloader,
        translate=translate,
    )
    bot.alive = alive
    bot.pilot.read_state = lambda _h: agent.IDLE
    bot.pilot.read_text = lambda _h: ""
    bot.pilot.is_window = lambda _h: True
    bot.pilot.focus = lambda _h: True
    bot.pilot.place_caret = lambda _h, _p: True
    bot.pilot.poll_seconds = 0.01
    bot.pilot.countdown_seconds = 1
    bot.pilot.countdown_tick = 0.01
    bot._retry_delay = 0
    return bot, api_inst, box, tmp


# ==============================================================================
# TIER 1: Feature Coverage (Core Functionality)
# ==============================================================================

print("\n--- Tier 1: Photo Update Parsing & Resolution Selection ---")
bot, api, _, _ = make_bot()
photos = [
    {"file_id": "thumb_1", "width": 90, "height": 90, "file_size": 2500},
    {"file_id": "medium_2", "width": 320, "height": 240, "file_size": 18000},
    {"file_id": "highest_3", "width": 1280, "height": 960, "file_size": 204800},
]
api.feed_photo(photos, caption="rezumat eroare")
updates = bot._poll()
check("update was received by poller", len(updates) == 1)
bot._handle(updates[0])

check("highest resolution file_id was selected",
      api.get_file_calls == ["highest_3"], f"Calls: {api.get_file_calls}")
check("photo step was queued in pending",
      len(bot.pending) == 1 and bot.pending[0]["type"] == "photo")
check("photo step contains binary bytes",
      bot.pending[0]["image_bytes"] == SAMPLE_PNG)

print("\n--- Tier 1: getFile API Call Verification ---")
bot, api, _, _ = make_bot()
api.feed_photo([{"file_id": "photo_target_id", "width": 800, "height": 600, "file_size": 50000}])
upd = bot._poll()[0]
bot._handle(upd)
check("getFile was called with exact file_id", api.get_file_calls == ["photo_target_id"])

print("\n--- Tier 1: Download Handler Verification ---")
# Direct test of download_file with mocked urllib.request.urlopen
download_mock_called = []


class FakeUrlOpenResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.data


def fake_urlopen(req, timeout=None):
    download_mock_called.append((req.full_url, timeout))
    return FakeUrlOpenResponse(b"BINARY_FILE_PAYLOAD_XYZ")


orig_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen
try:
    downloaded = download_file("secret_bot_token", "photos/file_99.png", timeout=45)
    check("download_file fetched binary payload", downloaded == b"BINARY_FILE_PAYLOAD_XYZ")
    check("download_file formed correct URL",
          download_mock_called[0][0] == "https://api.telegram.org/file/botsecret_bot_token/photos/file_99.png")
    check("download_file honored timeout", download_mock_called[0][1] == 45)
finally:
    urllib.request.urlopen = orig_urlopen

# Verify Remote uses injected downloader
downloader_received = []


def custom_downloader(token, file_path):
    downloader_received.append((token, file_path))
    return b"CUSTOM_DOWNLOADED_BYTES"


bot, api, _, _ = make_bot(downloader=custom_downloader)
api.feed_photo([{"file_id": "custom_fid", "width": 640, "height": 480, "file_size": 30000}])
bot._handle(bot._poll()[0])
check("injected downloader was called",
      downloader_received == [("test_token", "photos/custom_fid.jpg")])
check("queued step has custom downloaded bytes",
      bot.pending[0]["image_bytes"] == b"CUSTOM_DOWNLOADED_BYTES")

print("\n--- Tier 1: GUI Target Window Injection ---")
clip, keys = rig_injector(front=GUI_HWND)
config = Config()
copied_images = []
orig_copy_image = injector.copy_image_to_clipboard
injector.copy_image_to_clipboard = lambda raw: (copied_images.append(raw) or True)
orig_is_term = injector.is_terminal_window
injector.is_terminal_window = lambda hwnd, prof=None: False

step = {
    "type": "photo",
    "image_bytes": SAMPLE_PNG,
    "caption": "Please analyze this UI glitch",
}
res = paste_hybrid(step, config, target_hwnd=GUI_HWND, submit=True)
check("paste_hybrid reports success for GUI", res is True)
check("image bytes passed to copy_image_to_clipboard", copied_images == [SAMPLE_PNG])
check("caption copied to clipboard", clip.writes[0] == "Please analyze this UI glitch", str(clip.writes))
check("clipboard restored after GUI paste", clip.value == "what you had before", str(clip.value))
# Keystrokes must include Ctrl+V for image, Ctrl+V for text, and Enter
ctrl_v_count = "".join(keys.keys).count("press v")
check("two Ctrl+V operations sent (image then caption)", ctrl_v_count == 2, f"Keys: {keys.keys}")
check("Enter key sent when submit=True",
      any("enter" in k.lower() for k in keys.keys), f"Keys: {keys.keys}")

print("\n--- Tier 1: Terminal Target Window Injection ---")
clip, keys = rig_injector(front=TERM_HWND)
config = Config()
injector.is_terminal_window = lambda hwnd, prof=None: True

term_step = {
    "type": "photo",
    "image_bytes": SAMPLE_PNG,
    "caption": "Check this compilation error",
}
created_inbox_files = []
res_term = paste_hybrid(term_step, config, target_hwnd=TERM_HWND, submit=True)
check("paste_hybrid reports success for Terminal", res_term is True)
saved_path = term_step.get("path")
check("file was saved to disk for terminal reference",
      saved_path is not None and Path(saved_path).exists(), str(saved_path))
if saved_path and Path(saved_path).exists():
    check("saved file content matches raw image bytes", Path(saved_path).read_bytes() == SAMPLE_PNG)
    posix_path = Path(saved_path).resolve().as_posix()
    expected_cmd = f'"{posix_path}" Check this compilation error'
    check("terminal received quoted POSIX path command", clip.writes[0] == expected_cmd, str(clip.writes))
    check("clipboard restored after terminal paste", clip.value == "what you had before", str(clip.value))
    check("terminal received Ctrl+V for command", "".join(keys.keys).count("press v") == 1)
    check("terminal received Enter key when submit=True", any("enter" in k.lower() for k in keys.keys))
    # Cleanup
    try:
        Path(saved_path).unlink()
    except Exception:
        pass

# Restore injector stubs
injector.copy_image_to_clipboard = orig_copy_image
injector.is_terminal_window = orig_is_term

print("\n--- Tier 1: Caption Translation (Romanian -> English) ---")
translations = {"Eroare la pornirea aplicației": "Error starting the application"}
bot, api, _, _ = make_bot(translate=lambda t: translations.get(t, t))
api.feed_photo([{"file_id": "p_ro", "width": 500, "height": 500, "file_size": 20000}],
               caption="Eroare la pornirea aplicației")
bot._handle(bot._poll()[0])
check("Romanian caption translated to English",
      bot.pending[0]["caption"] == "Error starting the application")
check("status card shows translated prompt",
      "Error starting the application" in api.card, f"Card: {api.card}")

print("\n--- Tier 1: Empty Caption Fallback to Default Prompt ---")
bot, api, _, _ = make_bot()
api.feed_photo([{"file_id": "p_empty", "width": 500, "height": 500, "file_size": 20000}], caption=None)
bot._handle(bot._poll()[0])
check("empty caption falls back to DEFAULT_PHOTO_PROMPT",
      bot.pending[0]["caption"] == DEFAULT_PHOTO_PROMPT)
check("status card shows DEFAULT_PHOTO_PROMPT",
      DEFAULT_PHOTO_PROMPT in api.card, f"Card: {api.card}")

print("\n--- Tier 1: Verbatim Caption ('=' Prefix Bypasses Translation) ---")
bot, api, _, _ = make_bot(translate=lambda t: "SHOULD_NOT_BE_CALLED")
api.feed_photo([{"file_id": "p_verb", "width": 500, "height": 500, "file_size": 20000}],
               caption="=cat /var/log/syslog | grep error")
bot._handle(bot._poll()[0])
check("leading '=' bypasses translation and strips prefix",
      bot.pending[0]["caption"] == "cat /var/log/syslog | grep error")


# ==============================================================================
# TIER 2: Boundary & Corner Cases
# ==============================================================================

print("\n--- Tier 2: Unsorted PhotoSize List ---")
bot, api, _, _ = make_bot()
# Largest resolution is in the middle of the array
unsorted_photos = [
    {"file_id": "small_first", "width": 100, "height": 100, "file_size": 5000},
    {"file_id": "huge_middle", "width": 1920, "height": 1080, "file_size": 500000},
    {"file_id": "small_last", "width": 320, "height": 240, "file_size": 15000},
]
api.feed_photo(unsorted_photos)
bot._handle(bot._poll()[0])
check("unsorted array selects highest resolution photo",
      api.get_file_calls == ["huge_middle"], f"Selected: {api.get_file_calls}")

print("\n--- Tier 2: Single PhotoSize Entry ---")
bot, api, _, _ = make_bot()
api.feed_photo([{"file_id": "single_entry", "width": 640, "height": 480, "file_size": 40000}])
bot._handle(bot._poll()[0])
check("single entry in list is selected correctly",
      api.get_file_calls == ["single_entry"], f"Selected: {api.get_file_calls}")

print("\n--- Tier 2: Whitespace-Only Caption ---")
bot, api, _, _ = make_bot()
api.feed_photo([{"file_id": "p_ws", "width": 400, "height": 400, "file_size": 10000}],
               caption="   \t\n  \r  ")
bot._handle(bot._poll()[0])
check("whitespace-only caption treated as empty and falls back to default",
      bot.pending[0]["caption"] == DEFAULT_PHOTO_PROMPT)

print("\n--- Tier 2: Download Failure & Timeout Handling ---")
# 1. getFile failure
bot, api, _, _ = make_bot()
api.fail_get_file = True
api.fail_get_file_error = "Connection reset by peer (10054)"
api.feed_photo([{"file_id": "p_fail", "width": 300, "height": 300, "file_size": 10000}])
bot._handle(bot._poll()[0])
check("getFile failure does not crash Remote", True)
check("no corrupt step queued on getFile failure", len(bot.pending) == 0)
check("user alerted via Telegram on getFile failure",
      any("could not be downloaded" in m for m in api.sent), f"Sent: {api.sent}")

# 2. Downloader timeout failure
def failing_downloader(token, fpath):
    raise urllib.error.URLError("timed out")


bot, api, _, _ = make_bot(downloader=failing_downloader)
api.feed_photo([{"file_id": "p_timeout", "width": 300, "height": 300, "file_size": 10000}])
bot._handle(bot._poll()[0])
check("download timeout does not crash Remote", True)
check("no corrupt step queued on download timeout", len(bot.pending) == 0)
check("user alerted via Telegram on download failure",
      any("could not be downloaded" in m for m in api.sent), f"Sent: {api.sent}")

print("\n--- Tier 2: Image File Path Containing Spaces ---")
clip, keys = rig_injector(front=TERM_HWND)
config = Config()
injector.is_terminal_window = lambda hwnd, prof=None: True

temp_space_dir = Path(tempfile.mkdtemp(prefix="relay test spaces-"))
space_img = temp_space_dir / "my screenshot image.png"
space_img.write_bytes(SAMPLE_PNG)

step_spaces = {
    "type": "photo",
    "image_bytes": SAMPLE_PNG,
    "caption": "test caption with spaces in path",
    "path": str(space_img),
}
paste_hybrid(step_spaces, config, target_hwnd=TERM_HWND, submit=False)
expected_posix = space_img.resolve().as_posix()
check("quoted POSIX path wraps spaces safely in quotes",
      clip.writes[0] == f'"{expected_posix}" test caption with spaces in path',
      str(clip.writes))
try:
    space_img.unlink()
    temp_space_dir.rmdir()
except Exception:
    pass

print("\n--- Tier 2: Non-Text Clipboard Restoration Safety ---")
clip, keys = rig_injector(front=GUI_HWND)
clip.value = "initial_text"
injector.copy_image_to_clipboard = lambda raw: True
injector.is_terminal_window = lambda hwnd, prof=None: False

# Test raw_bytes_to_dib directly with genuine image bytes
dib_bytes = raw_bytes_to_dib(SAMPLE_PNG)
check("raw_bytes_to_dib produces valid CF_DIB structure", len(dib_bytes) > 40)
# In BITMAPINFOHEADER, biSize is DWORD (40)
check("DIB header begins with biSize=40", dib_bytes[:4] == b"\x28\x00\x00\x00")

# Test non-text clipboard restoration safety
orig_read_clip = injector._read_clipboard
injector._read_clipboard = lambda: None  # stands in for binary/image clipboard
config = Config()
step_non_text = {"type": "photo", "image_bytes": SAMPLE_PNG, "caption": "prompt"}
res = paste_hybrid(step_non_text, config, target_hwnd=GUI_HWND, submit=False)
check("paste_hybrid succeeds when original clipboard was non-text", res is True)
injector._read_clipboard = orig_read_clip


# ==============================================================================
# TIER 3: Cross-Feature Combinations
# ==============================================================================

print("\n--- Tier 3: Pairwise GUI Target with Romanian vs Empty vs Verbatim ---")
clip, keys = rig_injector(front=GUI_HWND)
injector.copy_image_to_clipboard = lambda raw: True
injector.is_terminal_window = lambda hwnd, prof=None: False
config = Config()

# P1: GUI + Romanian caption
paste_hybrid({"type": "photo", "image_bytes": SAMPLE_PNG, "caption": "Rezolvă problema"},
             config, target_hwnd=GUI_HWND, submit=False)
check("P1: GUI with caption pasted caption after image",
      clip.writes[0] == "Rezolvă problema", str(clip.writes))
check("P1: submit=False sends no Enter",
      not any("enter" in k.lower() for k in keys.keys))

# P2: GUI + Empty caption (default prompt) + submit=True
clip, keys = rig_injector(front=GUI_HWND)
paste_hybrid({"type": "photo", "image_bytes": SAMPLE_PNG, "caption": DEFAULT_PHOTO_PROMPT},
             config, target_hwnd=GUI_HWND, submit=True)
check("P2: GUI with default prompt pasted DEFAULT_PHOTO_PROMPT",
      clip.writes[0] == DEFAULT_PHOTO_PROMPT, str(clip.writes))
check("P2: submit=True sends Enter",
      any("enter" in k.lower() for k in keys.keys))

# P3: GUI + Verbatim caption
clip, keys = rig_injector(front=GUI_HWND)
paste_hybrid({"type": "photo", "image_bytes": SAMPLE_PNG, "caption": "git status --short"},
             config, target_hwnd=GUI_HWND, submit=False)
check("P3: GUI with verbatim prompt pasted verbatim string",
      clip.writes[0] == "git status --short", str(clip.writes))

print("\n--- Tier 3: Pairwise Terminal Target with auto_enter True vs False ---")
injector.is_terminal_window = lambda hwnd, prof=None: True

# P4: Terminal + auto_enter=True (via submit=True)
clip, keys = rig_injector(front=TERM_HWND)
t_step = {"type": "photo", "image_bytes": SAMPLE_PNG, "caption": "build"}
paste_hybrid(t_step, config, target_hwnd=TERM_HWND, submit=True)
check("P4: Terminal submit=True triggers Enter",
      any("enter" in k.lower() for k in keys.keys))
if t_step.get("path") and Path(t_step["path"]).exists():
    Path(t_step["path"]).unlink()

# P5: Terminal + auto_enter=False (submit=False)
clip, keys = rig_injector(front=TERM_HWND)
t_step_2 = {"type": "photo", "image_bytes": SAMPLE_PNG, "caption": "build"}
paste_hybrid(t_step_2, config, target_hwnd=TERM_HWND, submit=False)
check("P5: Terminal submit=False suppresses Enter",
      not any("enter" in k.lower() for k in keys.keys))
if t_step_2.get("path") and Path(t_step_2["path"]).exists():
    Path(t_step_2["path"]).unlink()

print("\n--- Tier 3: paste_hybrid Polymorphism (Text String vs Photo Dict) ---")
clip, keys = rig_injector(front=GUI_HWND)
# Plain text string (classic dictation / template)
res_str = paste_hybrid("Plain text message from voice dictation", config, target_hwnd=GUI_HWND)
check("paste_hybrid seamlessly handles plain str steps", res_str is True)
check("plain text was directly copied and pasted",
      clip.writes[0] == "Plain text message from voice dictation", str(clip.writes))
check("only one Ctrl+V for plain text", "".join(keys.keys).count("press v") == 1)

# Unknown or invalid dict type
res_invalid = paste_hybrid({"type": "unknown_media"}, config)
check("unknown step dict returns False safely", res_invalid is False)


# ==============================================================================
# TIER 4: Real-World Scenarios & Queue Drainage
# ==============================================================================

print("\n--- Autopilot Unit Support for Structured Photo Dict Steps ---")
# 1. Autopilot.start accepts photo dicts without AttributeError
pilot_unit = Autopilot(send=lambda s, h: True, log=lambda *_: None)
pilot_unit.read_state = lambda _h: agent.IDLE
pilot_unit.is_window = lambda _h: True
start_ok = pilot_unit.start([{"type": "photo", "image_bytes": SAMPLE_PNG, "caption": "cap"}], GUI_HWND)
check("Autopilot.start filters dict steps without AttributeError", start_ok is True)
pilot_unit.stop()
if pilot_unit._thread:
    pilot_unit._thread.join(timeout=1.0)

# 2. Autopilot._send_step handles photo dict preview logging without KeyError
pilot_send_unit = Autopilot(
    send=lambda s, h: True,
    focus=lambda h: True,
    place_caret=lambda h, p: True,
    log=lambda *_: None,
)
pilot_send_unit.title = "Antigravity"
pilot_send_unit.profile = {"name": "Antigravity"}
pilot_send_unit.hwnd = GUI_HWND
pilot_send_unit.steps = [{"type": "photo", "caption": "unit test caption"}]
pilot_send_unit.index = 0
send_step_ok = pilot_send_unit._send_step({"type": "photo", "caption": "unit test caption"})
check("Autopilot._send_step formats preview and dispatches dict without KeyError", send_step_ok is True)


print("\n--- Dedicated Queue Drainage Integration: bot._drain() -> Autopilot -> Injector ---")
clip, keys = rig_injector(front=GUI_HWND)
copied_to_clipboard = []
injector.copy_image_to_clipboard = lambda b: (copied_to_clipboard.append(b) or True)
injector.is_terminal_window = lambda hwnd, prof=None: False

state_drain = {"step_sent": False, "seen_busy": 0}
def drain_read_state(_h):
    if not state_drain["step_sent"]:
        return agent.IDLE
    if state_drain["seen_busy"] < 2:
        state_drain["seen_busy"] += 1
        return agent.BUSY
    return agent.IDLE

drain_config = Config()
drain_dispatched = []

def drain_send_step(step, hwnd):
    drain_dispatched.append(step)
    state_drain["step_sent"] = True
    return paste_hybrid(step, drain_config, target_hwnd=hwnd, submit=True)

bot, api, _, _ = make_bot(send=drain_send_step)
bot.pilot.read_state = drain_read_state

# Queue photo update
api.feed_photo(
    [{"file_id": "drain_hd_1", "width": 1920, "height": 1080, "file_size": 250000}],
    caption="Fix memory leak"
)
upds = bot._poll()
bot._handle(upds[0])
check("step is in bot.pending prior to drain", len(bot.pending) == 1)

# Execute _drain()
bot._drain()
check("bot._drain() emptied pending queue into Autopilot", len(bot.pending) == 0)
check("Autopilot thread is running", bot.pilot.running is True)
check("status card updated to sending",
      remote_mod.ICON_SENDING in api.card or "sending" in api.card, f"Card: {api.card}")

# Wait for completion
deadline = time.monotonic() + 3.0
while bot.pilot.running and time.monotonic() < deadline:
    time.sleep(0.02)

check("Autopilot completed successfully", not bot.pilot.running)
check("step was dispatched to send callback", len(drain_dispatched) == 1)
check("dispatched step has photo type", drain_dispatched[0]["type"] == "photo")
check("image bytes placed on clipboard by injector", copied_to_clipboard == [SAMPLE_PNG])
check("caption copied to clipboard by injector", clip.writes[0] == "Fix memory leak", str(clip.writes))
check("injector executed two Ctrl+V operations", "".join(keys.keys).count("press v") == 2)
check("injector sent submission Enter key", any("enter" in k.lower() for k in keys.keys))
check("status card updated to done", remote_mod.ICON_DONE in api.card or "done" in api.card, f"Card: {api.card}")


print("\n--- Tier 4: Scenario A (Phone Screenshot with Romanian Caption -> Antigravity GUI) ---")
# 1. Phone sends photo of code with Romanian caption: "Verifică de ce această funcție returnează None"
sim_photos = [
    {"file_id": "thumb_s1", "width": 120, "height": 90, "file_size": 3200},
    {"file_id": "hd_s1", "width": 1920, "height": 1080, "file_size": 420000},
]
romanian_caption = "Verifică de ce această funcție returnează None"
english_translation = "Check why this function returns None"

clip, keys = rig_injector(front=GUI_HWND)
copied_to_clipboard = []
injector.copy_image_to_clipboard = lambda b: (copied_to_clipboard.append(b) or True)
injector.is_terminal_window = lambda hwnd, prof=None: False

antigravity_profile = {
    "name": "Antigravity",
    "process": "Antigravity.exe",
    "input": "Message input",
}
agent.profile_for = lambda hwnd, profiles=None: antigravity_profile

state_a = {"step_sent": False, "seen_busy": 0}
def read_state_a(_h):
    if not state_a["step_sent"]:
        return agent.IDLE
    if state_a["seen_busy"] < 2:
        state_a["seen_busy"] += 1
        return agent.BUSY
    return agent.IDLE

config_a = Config()
dispatched_a = []

def send_scenario_a(step, hwnd):
    dispatched_a.append(step)
    state_a["step_sent"] = True
    return paste_hybrid(step, config_a, target_hwnd=hwnd, submit=True)

bot, api, _, _ = make_bot(
    downloader=lambda token, fpath: SAMPLE_PNG,
    translate=lambda text: english_translation if text == romanian_caption else text,
    send=send_scenario_a,
)
bot.pilot.read_state = read_state_a

api.feed_photo(sim_photos, caption=romanian_caption)
updates = bot._poll()
bot._handle(updates[0])

check("Scenario A: Poller extracted highest resolution photo hd_s1",
      api.get_file_calls == ["hd_s1"])
check("Scenario A: Step queued with translated English caption",
      len(bot.pending) == 1 and bot.pending[0]["caption"] == english_translation)
check("Scenario A: Status card shows photo camera emoji and translated prompt",
      f"📷 {english_translation}" in api.card, f"Card: {api.card}")

# 2. Genuine queue drainage via bot._drain()
bot._drain()
check("Scenario A: Pending queue emptied by _drain", len(bot.pending) == 0)

deadline = time.monotonic() + 3.0
while bot.pilot.running and time.monotonic() < deadline:
    time.sleep(0.02)

check("Scenario A: Pilot finished execution", not bot.pilot.running)
check("Scenario A: Step dispatched to injector", len(dispatched_a) == 1)
check("Scenario A: Image bytes placed on clipboard", copied_to_clipboard == [SAMPLE_PNG])
check("Scenario A: Translated caption placed on clipboard", clip.writes[0] == english_translation, str(clip.writes))
check("Scenario A: Settle sequence executed (2 Ctrl+V operations)",
      "".join(keys.keys).count("press v") == 2)
check("Scenario A: Submission Enter sent", any("enter" in k.lower() for k in keys.keys))
check("Scenario A: Status card reflects completion", remote_mod.ICON_DONE in api.card or "done" in api.card, api.card)


print("\n--- Tier 4: Scenario B (Phone Photo with No Caption -> OpenCode Terminal) ---")
# 1. Phone sends photo of terminal output with NO caption
sim_photos_b = [
    {"file_id": "term_photo_1", "width": 800, "height": 600, "file_size": 65000},
]

clip_b, keys_b = rig_injector(front=TERM_HWND)
injector.is_terminal_window = lambda hwnd, prof=None: True

opencode_profile = {
    "name": "opencode",
    "process": "WindowsTerminal.exe",
    "title_contains": ["OC |", "OpenCode"],
    "kind": "terminal",
}
agent.profile_for = lambda hwnd, profiles=None: opencode_profile

state_b = {"step_sent": False, "seen_busy": 0}
def read_state_b(_h):
    if not state_b["step_sent"]:
        return agent.IDLE
    if state_b["seen_busy"] < 2:
        state_b["seen_busy"] += 1
        return agent.BUSY
    return agent.IDLE

config_b = Config()
dispatched_b = []

def send_scenario_b(step, hwnd):
    dispatched_b.append(step)
    state_b["step_sent"] = True
    return paste_hybrid(step, config_b, target_hwnd=hwnd, submit=True)

bot, api, _, _ = make_bot(
    downloader=lambda token, fpath: SAMPLE_PNG_2,
    send=send_scenario_b,
)
bot.target_getter = lambda: TERM_HWND
bot.pilot.read_state = read_state_b

api.feed_photo(sim_photos_b, caption=None)
updates = bot._poll()
bot._handle(updates[0])

check("Scenario B: Poller extracted term_photo_1", api.get_file_calls == ["term_photo_1"])
check("Scenario B: Step queued with DEFAULT_PHOTO_PROMPT",
      bot.pending[0]["caption"] == DEFAULT_PHOTO_PROMPT)
check("Scenario B: Status card shows default prompt preview",
      f"📷 {DEFAULT_PHOTO_PROMPT}" in api.card, f"Card: {api.card}")

# 2. Genuine queue drainage via bot._drain()
bot._drain()
check("Scenario B: Pending queue emptied by _drain", len(bot.pending) == 0)

deadline = time.monotonic() + 3.0
while bot.pilot.running and time.monotonic() < deadline:
    time.sleep(0.02)

check("Scenario B: Pilot finished execution", not bot.pilot.running)
check("Scenario B: Step dispatched to terminal injector", len(dispatched_b) == 1)

b_saved_path = dispatched_b[0].get("path")
check("Scenario B: Image file saved in inbox directory",
      b_saved_path and Path(b_saved_path).exists())
if b_saved_path and Path(b_saved_path).exists():
    check("Scenario B: Saved image content matches SAMPLE_PNG_2",
          Path(b_saved_path).read_bytes() == SAMPLE_PNG_2)
    posix_path_b = Path(b_saved_path).resolve().as_posix()
    expected_term_cmd = f'"{posix_path_b}" {DEFAULT_PHOTO_PROMPT}'
    check("Scenario B: Quoted POSIX path command formatted and pasted",
          clip_b.writes[0] == expected_term_cmd, str(clip_b.writes))
    check("Scenario B: Exactly one Ctrl+V sent to terminal",
          "".join(keys_b.keys).count("press v") == 1)
    check("Scenario B: Enter sent to terminal", any("enter" in k.lower() for k in keys_b.keys))
    try:
        Path(b_saved_path).unlink()
    except Exception:
        pass


print("\n--- Summary ---")
sys.exit(report.finish())
