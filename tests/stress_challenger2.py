"""Empirical stress-testing harness for Challenger 2.

Tests:
1. Window classification (various process names, window classes, profile kinds: GUI vs Terminal).
2. Win32 CF_DIB structure integrity (BITMAPINFOHEADER biSize=40, valid bitmap dimensions, 64-bit pointer safety).
3. Image clipboard copying and retrieval (real Windows clipboard Win32 / PySide6 API).
4. Terminal file saving in inbox/ (paths with spaces, unicode characters, quoted POSIX formatting).
5. Polymorphic dispatch in paste_hybrid (plain strings vs photo dicts, submit=True vs submit=False).
"""

import os
import sys
import struct
import tempfile
import time
import uuid
from pathlib import Path

# Add project root and tests to path
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import context
context.isolate_state()

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import relay.target as target_mod
from relay.target import is_terminal_window, TERMINAL_PROCESSES, TERMINAL_CLASSES
import relay.injector as injector_mod
from relay.injector import (
    raw_bytes_to_dib,
    copy_image_to_clipboard,
    paste_hybrid,
    DEFAULT_PHOTO_PROMPT,
    CF_DIB,
)

# Ensure QApplication exists for QClipboard / QImage operations
app = QApplication.instance()
if app is None:
    app = QApplication([])

report = context.Report()
check = report.check


def make_test_image(width=32, height=32, color="blue", format_name="PNG"):
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor(color))
    buf = QBuffer()
    buf.open(QIODevice.ReadWrite)
    img.save(buf, format_name)
    return bytes(buf.data())


# ==============================================================================
# SECTION 1: Window Classification
# ==============================================================================
print("\n=== [1] Window Classification Stress Tests ===")

# Test 1.1: Known terminal processes (case variations, path prefixes)
test_term_procs = [
    "cmd.exe", "CMD.EXE", "Cmd.Exe",
    "powershell.exe", "PowerShell.EXE",
    "pwsh.exe", "PWSH.EXE",
    "windowsterminal.exe", "WindowsTerminal.exe",
    "conhost.exe", "mintty.exe", "alacritty.exe",
    "wezterm.exe", "wezterm-gui.exe", "kitty.exe",
    "hyper.exe", "warp.exe"
]

for proc in test_term_procs:
    orig_proc = target_mod.window_process
    orig_class = target_mod.window_class
    target_mod.window_process = lambda h, p=proc: p
    target_mod.window_class = lambda h: "SomeClass"
    try:
        res = is_terminal_window(12345, profile=None)
        check(f"process {proc} classified as terminal", res is True, f"got {res}")
    finally:
        target_mod.window_process = orig_proc
        target_mod.window_class = orig_class

# Test 1.2: Known terminal classes
test_term_classes = [
    "ConsoleWindowClass",
    "CASCADIA_HOSTING_WINDOW_CLASS",
    "mintty",
    "Alacritty"
]

for cls in test_term_classes:
    orig_proc = target_mod.window_process
    orig_class = target_mod.window_class
    target_mod.window_process = lambda h: "unknown.exe"
    target_mod.window_class = lambda h, c=cls: c
    try:
        res = is_terminal_window(12345, profile=None)
        check(f"class {cls} classified as terminal", res is True, f"got {res}")
    finally:
        target_mod.window_process = orig_proc
        target_mod.window_class = orig_class

# Test 1.3: Known GUI processes & classes
test_gui_cases = [
    ("Antigravity.exe", "Chrome_WidgetWin_1"),
    ("Cursor.exe", "Chrome_WidgetWin_1"),
    ("Code.exe", "Chrome_WidgetWin_1"),
    ("Claude.exe", "Chrome_WidgetWin_1"),
    ("chrome.exe", "Chrome_WidgetWin_1"),
    ("notepad.exe", "Notepad"),
    ("devenv.exe", "HwndWrapper[DefaultDomain;;...]"),
]

for proc, cls in test_gui_cases:
    orig_proc = target_mod.window_process
    orig_class = target_mod.window_class
    target_mod.window_process = lambda h, p=proc: p
    target_mod.window_class = lambda h, c=cls: c
    try:
        res = is_terminal_window(12345, profile=None)
        check(f"GUI proc={proc} cls={cls} classified as NOT terminal", res is False, f"got {res}")
    finally:
        target_mod.window_process = orig_proc
        target_mod.window_class = orig_class

# Test 1.4: Profile precedence
# Case 1.4.1: profile kind='terminal' overrides even GUI process
profile_term = {"kind": "terminal", "process": "Antigravity.exe"}
check("profile kind='terminal' returns True", is_terminal_window(12345, profile=profile_term) is True)

# Case 1.4.2: profile kind='gui' overrides even terminal process
profile_gui = {"kind": "gui", "process": "cmd.exe"}
check("profile kind='gui' returns False", is_terminal_window(12345, profile=profile_gui) is False)

# Case 1.4.3: profile with no 'input' and terminal process
profile_term_proc = {"process": "powershell.exe"}
check("profile without input + terminal process returns True",
      is_terminal_window(12345, profile=profile_term_proc) is True)

# Case 1.4.4: profile with 'input' element (GUI editor)
profile_with_input = {"input": "Editor", "process": "cmd.exe"}
check("profile with input returns False even if process is cmd.exe",
      is_terminal_window(12345, profile=profile_with_input) is False)

# Case 1.4.5: hwnd=None handling
check("hwnd=None and profile=None returns False", is_terminal_window(None, None) is False)
check("hwnd=None and profile kind='terminal' returns True",
      is_terminal_window(None, {"kind": "terminal"}) is True)
check("hwnd=None and profile kind='gui' returns False",
      is_terminal_window(None, {"kind": "gui"}) is False)

# Case 1.4.6: Malformed profile (not a dict: int, list, str, None)
check("profile as string ignored safely", is_terminal_window(None, profile="not_a_dict") is False)
check("profile as int ignored safely", is_terminal_window(None, profile=123) is False)


# ==============================================================================
# SECTION 2: Win32 CF_DIB Structure Integrity
# ==============================================================================
print("\n=== [2] Win32 CF_DIB Structure Integrity Stress Tests ===")

# Test across various resolutions, aspect ratios, color formats
image_specs = [
    (1, 1, "white", "PNG"),
    (16, 16, "red", "PNG"),
    (100, 50, "green", "JPEG"),
    (512, 512, "magenta", "BMP"),
    (1920, 1080, "cyan", "PNG"),
]

for w, h, col, fmt in image_specs:
    raw = make_test_image(w, h, col, fmt)
    dib = raw_bytes_to_dib(raw)

    check(f"CF_DIB ({w}x{h} {fmt}) is at least 40 bytes", len(dib) >= 40)
    # Parse BITMAPINFOHEADER:
    # DWORD biSize; LONG biWidth; LONG biHeight; WORD biPlanes; WORD biBitCount;
    # DWORD biCompression; DWORD biSizeImage; LONG biXPelsPerMeter; LONG biYPelsPerMeter;
    # DWORD biClrUsed; DWORD biClrImportant;
    biSize, biWidth, biHeight, biPlanes, biBitCount, biCompression, biSizeImage, biXPPM, biYPPM, biClrUsed, biClrImp = \
        struct.unpack("<IiiHHIIiiII", dib[:40])

    check(f"biSize == 40 for {w}x{h}", biSize == 40, f"got {biSize}")
    check(f"biWidth == {w}", biWidth == w, f"got {biWidth}")
    check(f"abs(biHeight) == {h}", abs(biHeight) == h, f"got {biHeight}")
    check(f"biPlanes == 1", biPlanes == 1, f"got {biPlanes}")
    check(f"biBitCount in (24, 32)", biBitCount in (24, 32), f"got {biBitCount}")
    # Verify calculated minimum payload size
    row_stride = ((biWidth * biBitCount + 31) // 32) * 4
    expected_min_len = 40 + row_stride * abs(biHeight)
    check(f"DIB buffer length ({len(dib)}) >= header + pixel rows ({expected_min_len})",
          len(dib) >= expected_min_len)

# Test 2.2: 64-bit pointer setup validation in injector
import ctypes
import ctypes.wintypes as wt

k32 = ctypes.windll.kernel32
u32 = ctypes.windll.user32

check("GlobalAlloc.restype is HGLOBAL", k32.GlobalAlloc.restype in (wt.HGLOBAL, ctypes.c_void_p))
check("GlobalLock.restype is c_void_p (64-bit pointer safe)", k32.GlobalLock.restype == ctypes.c_void_p)
check("GlobalUnlock.restype is BOOL", k32.GlobalUnlock.restype == wt.BOOL)
check("GlobalFree.restype is HGLOBAL", k32.GlobalFree.restype in (wt.HGLOBAL, ctypes.c_void_p))
check("SetClipboardData.restype is HANDLE", k32.SetClipboardData.restype in (wt.HANDLE, ctypes.c_void_p) if hasattr(k32, "SetClipboardData") else u32.SetClipboardData.restype in (wt.HANDLE, ctypes.c_void_p))

# Test 2.3: Real GlobalAlloc / GlobalLock 64-bit memory block roundtrip
test_size = 1024
h_test = k32.GlobalAlloc(0x0002, test_size)
check("GlobalAlloc returned valid non-null handle", bool(h_test))
if h_test:
    ptr_test = k32.GlobalLock(h_test)
    check("GlobalLock returned valid 64-bit address pointer", bool(ptr_test) and isinstance(ptr_test, int))
    if ptr_test:
        test_payload = b"TEST_CF_DIB_PAYLOAD_INTEGRITY_12345678"
        ctypes.memmove(ptr_test, test_payload, len(test_payload))
        readback = ctypes.string_at(ptr_test, len(test_payload))
        check("memmove wrote and readback matches identically", readback == test_payload)
    k32.GlobalUnlock(h_test)
    freed = k32.GlobalFree(h_test)
    check("GlobalFree succeeded (returns NULL on success)", freed is None or freed == 0)


# ==============================================================================
# SECTION 3: Live Image Clipboard Copying and Retrieval
# ==============================================================================
print("\n=== [3] Live Windows Clipboard Copy and Retrieval Stress Tests ===")

# Test 3.1: Live copy_image_to_clipboard with genuine image bytes
test_png_live = make_test_image(64, 48, "yellow", "PNG")
copy_ok = copy_image_to_clipboard(test_png_live)
check("copy_image_to_clipboard succeeded on live Windows clipboard", copy_ok is True)

# Retrieve back from clipboard using PySide6 QClipboard
q_clip = QApplication.clipboard()
clip_img = q_clip.image()
check("QClipboard retrieved non-null image", not clip_img.isNull())
check("QClipboard image dimensions match (64x48)",
      clip_img.width() == 64 and clip_img.height() == 48,
      f"got {clip_img.width()}x{clip_img.height()}")
pixel_color = clip_img.pixelColor(10, 10)
check("retrieved image pixel color matches yellow",
      pixel_color.red() > 200 and pixel_color.green() > 200 and pixel_color.blue() < 50,
      f"got rgb({pixel_color.red()}, {pixel_color.green()}, {pixel_color.blue()})")

# Test 3.2: Corrupt / Invalid image bytes
check("empty bytes rejected cleanly", copy_image_to_clipboard(b"") is False)
check("random non-image bytes rejected cleanly", copy_image_to_clipboard(b"CORRUPT_NOT_AN_IMAGE_DATA") is False)
check("truncated PNG bytes rejected cleanly", copy_image_to_clipboard(test_png_live[:20]) is False)


# ==============================================================================
# SECTION 4: Terminal File Saving in inbox/
# ==============================================================================
print("\n=== [4] Terminal File Saving Stress Tests ===")

class DummyConfig(dict):
    restore_clipboard = False
    auto_enter = False
    paste_delay_ms = 0
    restore_delay_ms = 0

cfg = DummyConfig()

# Rig injector keyboard and pyperclip to avoid sending live keystrokes to active window during terminal paste
class MockClipboard:
    def __init__(self):
        self.writes = []
        self.value = ""
    def copy(self, text):
        self.writes.append(text)
        self.value = text
    def paste(self):
        return self.value

class MockKeyboard:
    def __init__(self):
        self.keys = []
    def pressed(self, k):
        class Held:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return Held()
    def press(self, k):
        self.keys.append(f"press {k}")
    def release(self, k):
        self.keys.append(f"release {k}")

mock_clip = MockClipboard()
mock_keys = MockKeyboard()
injector_mod.pyperclip = mock_clip
injector_mod._keyboard = mock_keys
injector_mod.time.sleep = lambda _s: None

# Test 4.1: Unicode Romanian characters in caption
orig_is_term = injector_mod.is_terminal_window
injector_mod.is_terminal_window = lambda hwnd, prof=None: True

unicode_caption = "Verifică fișierul cu diacritice: ș, ț, â, î, ă și emoji 🚀"
term_step_unicode = {
    "type": "photo",
    "image_bytes": test_png_live,
    "caption": unicode_caption,
}

ok_unicode = paste_hybrid(term_step_unicode, cfg, target_hwnd=9999, submit=False)
check("paste_hybrid succeeded for unicode terminal step", ok_unicode is True)
saved_path_u = term_step_unicode.get("path")
check("file was created in inbox/", saved_path_u and Path(saved_path_u).exists())
if saved_path_u and Path(saved_path_u).exists():
    check("saved file bytes match original bytes", Path(saved_path_u).read_bytes() == test_png_live)
    posix_path_u = Path(saved_path_u).resolve().as_posix()
    expected_cmd_u = f'"{posix_path_u}" {unicode_caption}'
    check("pasted command contains quoted POSIX path and preserved unicode caption",
          mock_clip.writes[-1] == expected_cmd_u, f"got {mock_clip.writes[-1]!r}")
    try:
        Path(saved_path_u).unlink()
    except Exception:
        pass

# Test 4.2: Path containing spaces and special characters
temp_space_dir = Path(tempfile.mkdtemp(prefix="relay test spaces in path-"))
custom_space_file = temp_space_dir / "my special image (version 2) [test].png"
custom_space_file.write_bytes(test_png_live)

term_step_space = {
    "type": "photo",
    "image_bytes": test_png_live,
    "caption": "test spaces",
    "path": str(custom_space_file),
}
ok_space = paste_hybrid(term_step_space, cfg, target_hwnd=9999, submit=True)
check("paste_hybrid succeeded for pre-existing file in path with spaces", ok_space is True)
posix_path_space = custom_space_file.resolve().as_posix()
expected_cmd_space = f'"{posix_path_space}" test spaces'
check("quoted POSIX path properly encapsulates path with spaces",
      mock_clip.writes[-1] == expected_cmd_space, f"got {mock_clip.writes[-1]!r}")
try:
    custom_space_file.unlink()
    temp_space_dir.rmdir()
except Exception:
    pass


# ==============================================================================
# SECTION 5: Polymorphic Dispatch in paste_hybrid
# ==============================================================================
print("\n=== [5] Polymorphic Dispatch in paste_hybrid Stress Tests ===")

# Test 5.1: Plain text string
mock_clip.writes.clear()
mock_keys.keys.clear()
res_str = paste_hybrid("Hello world from dictation", cfg, target_hwnd=9999, submit=False)
check("plain string dispatched successfully", res_str is True)
check("plain string written to clipboard", mock_clip.writes[-1] == "Hello world from dictation")
check("submit=False did not send Enter", not any("enter" in k.lower() for k in mock_keys.keys))

# Test 5.2: Plain string with submit=True
mock_clip.writes.clear()
mock_keys.keys.clear()
res_str_sub = paste_hybrid("Command to run", cfg, target_hwnd=9999, submit=True)
check("plain string with submit=True succeeded", res_str_sub is True)
check("submit=True sent Enter", any("enter" in k.lower() for k in mock_keys.keys))

# Test 5.3: Photo step without caption (falls back to DEFAULT_PHOTO_PROMPT)
mock_clip.writes.clear()
mock_keys.keys.clear()
term_no_cap = {
    "type": "photo",
    "image_bytes": test_png_live,
}
res_no_cap = paste_hybrid(term_no_cap, cfg, target_hwnd=9999, submit=False)
check("photo step without caption dispatched successfully", res_no_cap is True)
check("terminal command contains DEFAULT_PHOTO_PROMPT",
      DEFAULT_PHOTO_PROMPT in mock_clip.writes[-1], f"got {mock_clip.writes[-1]!r}")
if term_no_cap.get("path") and Path(term_no_cap["path"]).exists():
    try:
        Path(term_no_cap["path"]).unlink()
    except Exception:
        pass

# Test 5.4: Photo step with empty / whitespace caption
mock_clip.writes.clear()
term_ws_cap = {
    "type": "photo",
    "image_bytes": test_png_live,
    "caption": "   \n\t  ",
}
res_ws = paste_hybrid(term_ws_cap, cfg, target_hwnd=9999, submit=False)
check("whitespace caption falls back to DEFAULT_PHOTO_PROMPT",
      DEFAULT_PHOTO_PROMPT in mock_clip.writes[-1], f"got {mock_clip.writes[-1]!r}")
if term_ws_cap.get("path") and Path(term_ws_cap["path"]).exists():
    try:
        Path(term_ws_cap["path"]).unlink()
    except Exception:
        pass

# Test 5.5: Invalid inputs
check("empty string returns False", paste_hybrid("", cfg) is False)
check("None returns False", paste_hybrid(None, cfg) is False)
check("dict with unknown type returns False", paste_hybrid({"type": "audio"}, cfg) is False)
check("photo dict missing image_bytes returns False", paste_hybrid({"type": "photo"}, cfg) is False)
check("dict with text key works as text paste",
      paste_hybrid({"text": "fallback text"}, cfg, target_hwnd=9999) is True)
check("fallback text written to clipboard", mock_clip.writes[-1] == "fallback text")

# Restore stubs
injector_mod.is_terminal_window = orig_is_term

print("\n=== All Challenger 2 Stress Tests Completed ===")
sys.exit(report.finish())
