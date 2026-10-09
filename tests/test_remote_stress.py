"""Empirical Adversarial Stress Harness for Telegram Ingestion & Photo Pipeline.

Adversarial validation of `relay/remote.py`:
- Test Category 1: Unsorted, inverted, multi-resolution, and tie-breaking photo lists.
- Test Category 2: Missing fields, corrupt items, and type errors in PhotoSize.
- Test Category 3: Empty, whitespace, Romanian, verbatim ('='), and translation faults.
- Test Category 4: getFile API failures, HTTP errors, timeouts, download exceptions,
                   and post-failure recovery without restart.
- Test Category 5: Telegram card rendering, HTML escaping, length truncation,
                   prompt history stacking, and overflow formatting.
- Test Category 6: Standalone `download_file` URL escaping, whitespace, and timeouts.
- Test Category 7: Downloader polymorphism (2-arg, 1-arg path, 1-arg URL).
- Test Category 8: Control commands (/stop, /status) while photo is pending in queue.
"""

import json
import os
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import context
context.isolate_state()

from relay import agent
from relay import remote as remote_mod
from relay.remote import (
    CARD_PROMPTS,
    CARD_PROMPT_CHARS,
    DEFAULT_PHOTO_PROMPT,
    ICON_WORKING,
    Remote,
    download_file,
)

report = context.Report()
check = report.check

MINE, OTHER = 12345, 99999
SAMPLE_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRFAKE_PHOTO_BYTES"


class StressApi:
    """Mock Telegram Bot API with configurable faults and inspection."""

    def __init__(self):
        self.updates = []
        self.sent_messages = []
        self.edited_messages = []
        self.messages_by_id = {}
        self.get_file_calls = []
        self.file_responses = {}
        self.get_file_exception = None
        self.send_message_exception = None
        self.call_history = []

    @property
    def last_card(self):
        if not self.sent_messages:
            return ""
        return self.messages_by_id.get(len(self.sent_messages), "")

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
        self.call_history.append((method, params, timeout))

        if method == "getUpdates":
            pending = self.updates
            self.updates = []
            return pending

        if method == "sendMessage":
            if self.send_message_exception:
                raise self.send_message_exception
            msg_text = params.get("text", "")
            self.sent_messages.append(msg_text)
            msg_id = len(self.sent_messages)
            self.messages_by_id[msg_id] = msg_text
            return {"message_id": msg_id}

        if method == "editMessageText":
            msg_id = params.get("message_id")
            new_text = params.get("text", "")
            self.edited_messages.append((msg_id, new_text))
            self.messages_by_id[msg_id] = new_text
            return {}

        if method == "getFile":
            if self.get_file_exception:
                raise self.get_file_exception
            file_id = params.get("file_id")
            self.get_file_calls.append(file_id)
            if file_id in self.file_responses:
                return self.file_responses[file_id]
            return {"file_id": file_id, "file_path": f"photos/{file_id}.jpg"}

        if method == "setMyCommands":
            return True

        raise AssertionError(f"Unexpected Telegram API method: {method}")


def make_test_bot(chat_id=MINE, downloader=None, translate=None, api=None):
    tmp = Path(tempfile.mkdtemp(prefix="stress-photo-"))
    token = "stress_test_token_12345"
    (tmp / "telegram.json").write_text(
        json.dumps({"token": token, "chat_id": chat_id}), encoding="utf-8"
    )
    api_inst = api or StressApi()
    queued_steps = []

    dl = downloader if downloader is not None else (lambda t, p: SAMPLE_BYTES)

    bot = Remote(
        settings={"token": token, "chat_id": chat_id, "path": tmp / "telegram.json"},
        send=lambda step, hwnd: (queued_steps.append(step) or True),
        target_getter=lambda: 100,
        log=lambda *_: None,
        api=api_inst,
        is_window=lambda _h: True,
        keeper_watching=lambda: True,
        downloader=dl,
        translate=translate,
    )
    bot.pilot.read_state = lambda _h: agent.IDLE
    bot.pilot.is_window = lambda _h: True
    bot.pilot.focus = lambda _h: True
    bot.pilot.place_caret = lambda _h, _p: True
    bot.pilot.poll_seconds = 0.01
    bot.pilot.countdown_seconds = 0.05
    bot.pilot.countdown_tick = 0.01
    bot._retry_delay = 0
    return bot, api_inst, queued_steps, tmp


# ==============================================================================
# CATEGORY 1: Resolution Selection & PhotoSize Edge Cases
# ==============================================================================
print("\n=== Category 1: Resolution Selection & PhotoSize Edge Cases ===")

# 1.1 Inverted resolution list (largest first, smallest last)
bot, api, box, _ = make_test_bot()
inverted_sizes = [
    {"file_id": "huge_first", "width": 3840, "height": 2160, "file_size": 1500000},
    {"file_id": "medium_second", "width": 1280, "height": 720, "file_size": 120000},
    {"file_id": "thumb_third", "width": 320, "height": 180, "file_size": 15000},
]
api.feed_photo(inverted_sizes)
bot._handle(bot._poll()[0])
check("1.1 Inverted list selects highest resolution photo",
      api.get_file_calls == ["huge_first"], f"Got: {api.get_file_calls}")

# 1.2 Multi-resolution list (5 entries in shuffled order)
bot, api, box, _ = make_test_bot()
multi_sizes = [
    {"file_id": "res_320", "width": 320, "height": 240, "file_size": 10000},
    {"file_id": "res_2560", "width": 2560, "height": 1440, "file_size": 800000},
    {"file_id": "res_90", "width": 90, "height": 90, "file_size": 2000},
    {"file_id": "res_1920", "width": 1920, "height": 1080, "file_size": 400000},
    {"file_id": "res_800", "width": 800, "height": 600, "file_size": 50000},
]
api.feed_photo(multi_sizes)
bot._handle(bot._poll()[0])
check("1.2 Multi-resolution shuffled list selects 2560x1440",
      api.get_file_calls == ["res_2560"], f"Got: {api.get_file_calls}")

# 1.3 Resolution tie-breaker: Identical pixel area, different file_size
bot, api, box, _ = make_test_bot()
tie_sizes = [
    {"file_id": "low_quality", "width": 1024, "height": 768, "file_size": 40000},
    {"file_id": "high_quality", "width": 1024, "height": 768, "file_size": 120000},
]
api.feed_photo(tie_sizes)
bot._handle(bot._poll()[0])
check("1.3 Resolution tie-break prefers larger file_size (higher quality)",
      api.get_file_calls == ["high_quality"], f"Got: {api.get_file_calls}")

# 1.4 Identical resolution AND identical file_size
bot, api, box, _ = make_test_bot()
identical_sizes = [
    {"file_id": "dup_1", "width": 640, "height": 480, "file_size": 30000},
    {"file_id": "dup_2", "width": 640, "height": 480, "file_size": 30000},
]
api.feed_photo(identical_sizes)
bot._handle(bot._poll()[0])
check("1.4 Identical area and file_size resolves deterministically",
      api.get_file_calls == ["dup_1"], f"Got: {api.get_file_calls}")

# 1.5 Zero resolution (both 0x0), differentiated solely by file_size
bot, api, box, _ = make_test_bot()
zero_res_sizes = [
    {"file_id": "zero_small", "width": 0, "height": 0, "file_size": 500},
    {"file_id": "zero_large", "width": 0, "height": 0, "file_size": 9500},
]
api.feed_photo(zero_res_sizes)
bot._handle(bot._poll()[0])
check("1.5 Zero dimensions fallback differentiates by file_size",
      api.get_file_calls == ["zero_large"], f"Got: {api.get_file_calls}")


# ==============================================================================
# CATEGORY 2: Missing Fields, Corrupt Items & Type Errors in PhotoSize
# ==============================================================================
print("\n=== Category 2: Missing Fields & Malformed PhotoSize Entries ===")

# 2.1 Missing width (None and key omitted)
bot, api, box, _ = make_test_bot()
missing_width = [
    {"file_id": "p_no_w1", "height": 500, "file_size": 1000},
    {"file_id": "p_w_none", "width": None, "height": 500, "file_size": 2000},
]
api.feed_photo(missing_width)
bot._handle(bot._poll()[0])
check("2.1 Missing/None width defaults to 0 and does not crash",
      api.get_file_calls == ["p_w_none"], f"Got: {api.get_file_calls}")

# 2.2 Missing height (None and key omitted)
bot, api, box, _ = make_test_bot()
missing_height = [
    {"file_id": "p_no_h1", "width": 600, "file_size": 3000},
    {"file_id": "p_h_none", "width": 600, "height": None, "file_size": 4000},
]
api.feed_photo(missing_height)
bot._handle(bot._poll()[0])
check("2.2 Missing/None height defaults to 0 and does not crash",
      api.get_file_calls == ["p_h_none"], f"Got: {api.get_file_calls}")

# 2.3 Missing file_size (None and key omitted)
bot, api, box, _ = make_test_bot()
missing_fsize = [
    {"file_id": "p_no_size", "width": 400, "height": 400},
    {"file_id": "p_size_none", "width": 500, "height": 500, "file_size": None},
]
api.feed_photo(missing_fsize)
bot._handle(bot._poll()[0])
check("2.3 Missing/None file_size does not crash and selects largest area",
      api.get_file_calls == ["p_size_none"], f"Got: {api.get_file_calls}")

# 2.4 Missing file_id entirely (key absent, None, empty string)
bot, api, box, _ = make_test_bot()
no_file_id_sizes = [
    {"width": 800, "height": 600, "file_size": 10000},  # no file_id key
]
api.feed_photo(no_file_id_sizes)
bot._handle(bot._poll()[0])
check("2.4 Missing file_id caught gracefully without crash",
      len(bot.pending) == 0)
check("2.4 User notified when photo has no file_id",
      any("could not be downloaded: photo has no file_id" in m for m in api.sent_messages),
      f"Sent: {api.sent_messages}")

# 2.5 Empty photo list []
bot, api, box, _ = make_test_bot()
api.feed_photo([])
bot._handle(bot._poll()[0])
check("2.5 Empty photo list rejected gracefully as non-text",
      len(bot.pending) == 0)
check("2.5 User informed that non-text was received",
      any("I can only read text" in m for m in api.sent_messages),
      f"Sent: {api.sent_messages}")

# 2.6 Corrupted list items: non-dict items in photos array (e.g. [None], ["bad_str"], [123])
bot, api, box, _ = make_test_bot()
api.feed_photo([None, "not_a_dict", 12345])
bot._handle(bot._poll()[0])
check("2.6 Non-dict photo items caught without crash", len(bot.pending) == 0)
check("2.6 User notified about invalid photo format",
      any("could not be downloaded" in m for m in api.sent_messages))

# 2.7 Malformed width type (e.g. string "not_a_number" causing ValueError)
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "malformed_w", "width": "invalid_int", "height": 100}])
bot._handle(bot._poll()[0])
check("2.7 Non-integer width string caught without crashing", len(bot.pending) == 0)
check("2.7 User informed about photo download error",
      any("could not be downloaded" in m for m in api.sent_messages))


# ==============================================================================
# CATEGORY 3: Caption Variations & Translation Faults
# ==============================================================================
print("\n=== Category 3: Caption Variations & Translation Faults ===")

# 3.1 Caption is None -> DEFAULT_PHOTO_PROMPT
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "cap_none", "width": 100, "height": 100}], caption=None)
bot._handle(bot._poll()[0])
check("3.1 caption=None uses DEFAULT_PHOTO_PROMPT",
      bot.pending[0]["caption"] == DEFAULT_PHOTO_PROMPT)

# 3.2 Caption is empty string "" -> DEFAULT_PHOTO_PROMPT
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "cap_empty", "width": 100, "height": 100}], caption="")
bot._handle(bot._poll()[0])
check("3.2 caption='' uses DEFAULT_PHOTO_PROMPT",
      bot.pending[0]["caption"] == DEFAULT_PHOTO_PROMPT)

# 3.3 Caption is whitespace only ("   \r\n\t  ") -> DEFAULT_PHOTO_PROMPT
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "cap_ws", "width": 100, "height": 100}], caption="   \r\n\t  ")
bot._handle(bot._poll()[0])
check("3.3 Whitespace-only caption uses DEFAULT_PHOTO_PROMPT",
      bot.pending[0]["caption"] == DEFAULT_PHOTO_PROMPT)

# 3.4 Romanian caption cleanly translated
bot, api, box, _ = make_test_bot(
    translate=lambda txt: "Explain this code trace" if "explică" in txt.lower() else txt
)
api.feed_photo([{"file_id": "cap_ro", "width": 100, "height": 100}],
               caption="Te rog explică acest cod")
bot._handle(bot._poll()[0])
check("3.4 Romanian caption translated to English",
      bot.pending[0]["caption"] == "Explain this code trace")

# 3.5 Verbatim caption with '=' prefix: bypasses translation, strips leading '='
translate_called = []
bot, api, box, _ = make_test_bot(
    translate=lambda txt: (translate_called.append(txt) or "TRANSLATED_WRONG")
)
api.feed_photo([{"file_id": "cap_verb", "width": 100, "height": 100}],
               caption="=cat /etc/passwd | grep root")
bot._handle(bot._poll()[0])
check("3.5 Verbatim '=' bypasses translator", len(translate_called) == 0)
check("3.5 Verbatim '=' strips leading prefix and preserves prompt",
      bot.pending[0]["caption"] == "cat /etc/passwd | grep root")

# 3.6 Degenerate verbatim captions ("=", "=   ") fall back to DEFAULT_PHOTO_PROMPT
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "cap_just_eq", "width": 100, "height": 100}], caption="=   ")
bot._handle(bot._poll()[0])
check("3.6 Degenerate verbatim '=   ' falls back to DEFAULT_PHOTO_PROMPT",
      bot.pending[0]["caption"] == DEFAULT_PHOTO_PROMPT)

# 3.7 Multiple leading equals ("==--verbose") strips only the first '='
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "cap_multi_eq", "width": 100, "height": 100}], caption="==--verbose")
bot._handle(bot._poll()[0])
check("3.7 Multiple equals strips only single prefix character",
      bot.pending[0]["caption"] == "=--verbose")

# 3.8 Translator throws exception during caption translation
def broken_translate(txt):
    raise RuntimeError("Translator service unavailable (HTTP 503)")

bot, api, box, _ = make_test_bot(translate=broken_translate)
api.feed_photo([{"file_id": "cap_ro_err", "width": 100, "height": 100}],
               caption="Eroare critică în modul")
bot._handle(bot._poll()[0])
check("3.8 Translation failure does not crash Remote", len(bot.pending) == 1)
check("3.8 Translation failure keeps original Romanian caption",
      bot.pending[0]["caption"] == "Eroare critică în modul")
check("3.8 User notified about translation failure",
      any("Could not translate that" in m for m in api.sent_messages))

# 3.9 Translator returns empty string: preserves user's prompt text per _to_english contract
bot, api, box, _ = make_test_bot(translate=lambda txt: "")
api.feed_photo([{"file_id": "cap_ro_empty", "width": 100, "height": 100}],
               caption="mesaj original utilizator")
bot._handle(bot._poll()[0])
check("3.9 Empty translation result preserves user Romanian prompt",
      bot.pending[0]["caption"] == "mesaj original utilizator")

# 3.10 Non-string caption (e.g. integer or list injected in update)
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "cap_int", "width": 100, "height": 100}], caption=12345)
bot._handle(bot._poll()[0])
check("3.10 Non-string caption does not crash Remote", len(bot.pending) == 0)
check("3.10 Non-string caption notifies user of download error",
      any("could not be downloaded" in m for m in api.sent_messages))


# ==============================================================================
# CATEGORY 4: getFile Failures, Timeouts, Download Errors & Recovery
# ==============================================================================
print("\n=== Category 4: getFile / Download Failures & Bot Recovery ===")

# 4.1 getFile throws RuntimeError (e.g. Telegram 502 Bad Gateway)
bot, api, box, _ = make_test_bot()
api.get_file_exception = RuntimeError("Telegram 502 Bad Gateway")
api.feed_photo([{"file_id": "f_502", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("4.1 getFile 502 error caught gracefully", len(bot.pending) == 0)
check("4.1 User informed about getFile failure",
      any("could not be downloaded: Telegram 502 Bad Gateway" in m for m in api.sent_messages))

# 4.2 getFile throws urllib.error.HTTPError (404 Not Found)
bot, api, box, _ = make_test_bot()
http_404 = urllib.error.HTTPError(
    url="https://api.telegram.org/botTOKEN/getFile",
    code=404, msg="Not Found", hdrs={}, fp=None
)
api.get_file_exception = http_404
api.feed_photo([{"file_id": "f_404", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("4.2 getFile HTTP 404 caught without crash", len(bot.pending) == 0)
check("4.2 User informed of HTTP 404 error",
      any("could not be downloaded: HTTP Error 404" in m for m in api.sent_messages))

# 4.3 getFile throws socket.timeout / TimeoutError
bot, api, box, _ = make_test_bot()
api.get_file_exception = TimeoutError("Connection timed out waiting for getFile")
api.feed_photo([{"file_id": "f_timeout", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("4.3 getFile timeout caught without crash", len(bot.pending) == 0)
check("4.3 User informed of getFile timeout",
      any("could not be downloaded: Connection timed out" in m for m in api.sent_messages))

# 4.4 getFile returns None or empty dict {}
bot, api, box, _ = make_test_bot()
api.file_responses["f_empty_dict"] = {}
api.feed_photo([{"file_id": "f_empty_dict", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("4.4 getFile returning empty dict caught gracefully", len(bot.pending) == 0)
check("4.4 User notified of missing file_path",
      any("getFile returned no file_path" in m for m in api.sent_messages))

# 4.5 getFile returns dict with missing/None file_path
bot, api, box, _ = make_test_bot()
api.file_responses["f_none_path"] = {"file_id": "f_none_path", "file_path": None}
api.feed_photo([{"file_id": "f_none_path", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("4.5 getFile returning None file_path caught gracefully", len(bot.pending) == 0)
check("4.5 User notified of missing file_path",
      any("getFile returned no file_path" in m for m in api.sent_messages))

# 4.6 Binary download handler throws urllib.error.URLError
def failing_dl_urlerror(token, path):
    raise urllib.error.URLError("Network unreachable")

bot, api, box, _ = make_test_bot(downloader=failing_dl_urlerror)
api.feed_photo([{"file_id": "f_dl_fail", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("4.6 Downloader URLError caught without crash", len(bot.pending) == 0)
check("4.6 User notified about binary download failure",
      any("Network unreachable" in m for m in api.sent_messages))

# 4.7 Binary download handler throws ConnectionResetError
def failing_dl_reset(token, path):
    raise ConnectionResetError("Connection reset by remote host")

bot, api, box, _ = make_test_bot(downloader=failing_dl_reset)
api.feed_photo([{"file_id": "f_reset", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("4.7 Downloader ConnectionResetError caught without crash", len(bot.pending) == 0)
check("4.7 User notified of ConnectionResetError",
      any("Connection reset by remote host" in m for m in api.sent_messages))

# 4.8 Post-failure recovery verification:
bot, api, box, _ = make_test_bot()
api.get_file_exception = RuntimeError("Temporary outage")
api.feed_photo([{"file_id": "broken_1", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("4.8 First message failed as expected", len(bot.pending) == 0)

# Clear error condition and send valid photo
api.get_file_exception = None
api.feed_photo([{"file_id": "healthy_2", "width": 800, "height": 600}], caption="Fixed now")
bot._handle(bot._poll()[0])
check("4.8 Post-failure valid photo processed immediately",
      len(bot.pending) == 1 and bot.pending[0]["caption"] == "Fixed now")

# Send valid text message
api.feed_text("Subsequent text command")
bot._handle(bot._poll()[0])
check("4.8 Post-failure valid text message processed immediately",
      len(bot.pending) == 2 and bot.pending[1] == "Subsequent text command")


# ==============================================================================
# CATEGORY 5: Telegram Card Rendering & Prompt History Formatting
# ==============================================================================
print("\n=== Category 5: Card Rendering, HTML Escaping & Prompt History ===")

# 5.1 Photo indicator emoji and prompt formatting on status card
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "card_p1", "width": 200, "height": 200}], caption="Check this graph")
bot._handle(bot._poll()[0])
check("5.1 Photo indicator emoji '📷' present in card Asked section",
      "📷 Check this graph" in api.last_card, f"Card: {api.last_card}")
check("5.1 Card has working icon and header",
      f"{ICON_WORKING} <b>waiting for a free window</b>" in api.last_card)

# 5.2 HTML injection escaping in caption
bot, api, box, _ = make_test_bot()
raw_caption = "<script>alert('xss')</script> & 'quotes' <div class=\"test\">"
api.feed_photo([{"file_id": "card_html", "width": 200, "height": 200}], caption=raw_caption)
bot._handle(bot._poll()[0])
card_text = api.last_card
check("5.2 '<' escaped to '&lt;' in card", "&lt;script&gt;" in card_text)
check("5.2 '>' escaped to '&gt;' in card", "&lt;/script&gt;" in card_text)
check("5.2 '&' escaped to '&amp;' in card", "&amp; 'quotes'" in card_text)
check("5.2 Raw unescaped '<script>' NOT in card", "<script>" not in card_text)

# 5.3 Long caption truncation with ellipsis
bot, api, box, _ = make_test_bot()
long_caption = "Word " * 50  # 250 characters, well over CARD_PROMPT_CHARS (160)
api.feed_photo([{"file_id": "card_long", "width": 200, "height": 200}], caption=long_caption)
bot._handle(bot._poll()[0])
check("5.3 Long prompt truncated with ellipsis on card",
      "\u2026" in api.last_card, f"Card: {api.last_card}")
asked_line = [ln for ln in api.last_card.split("\n") if "📷" in ln][0]
check("5.3 Truncated line within bounds",
      len(asked_line) <= CARD_PROMPT_CHARS + 20, f"Line len: {len(asked_line)}")

# 5.4 Multiple photo prompts accumulation (up to CARD_PROMPTS = 4)
bot, api, box, _ = make_test_bot()
for i in range(1, 4):
    api.feed_photo([{"file_id": f"p_multi_{i}", "width": 100, "height": 100}],
                   caption=f"Photo step {i}")
    bot._handle(bot._poll()[0])

check("5.4 All 3 queued photo prompts visible in status card",
      all(f"📷 Photo step {i}" in api.last_card for i in range(1, 4)),
      f"Card: {api.last_card}")

# 5.5 Overflow of prompts (> CARD_PROMPTS = 4)
bot, api, box, _ = make_test_bot()
for i in range(1, 7):  # 6 prompts total (4 shown, 2 overflow)
    api.feed_photo([{"file_id": f"p_over_{i}", "width": 100, "height": 100}],
                   caption=f"Batch item {i}")
    bot._handle(bot._poll()[0])

check("5.5 First 4 prompts shown on card",
      all(f"📷 Batch item {i}" in api.last_card for i in range(1, 5)))
check("5.5 Overflow indicator '...and 2 more' rendered on card",
      "...and 2 more" in api.last_card, f"Card: {api.last_card}")
check("5.5 5th and 6th items not directly listed on card",
      "📷 Batch item 5" not in api.last_card and "📷 Batch item 6" not in api.last_card)

# 5.6 Interleaved photo and text prompts in card history
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "inter_1", "width": 100, "height": 100}], caption="First photo")
bot._handle(bot._poll()[0])
api.feed_text("Second text prompt")
bot._handle(bot._poll()[0])
api.feed_photo([{"file_id": "inter_3", "width": 100, "height": 100}], caption="Third photo")
bot._handle(bot._poll()[0])

check("5.6 Interleaved photo and text prompts preserved in order",
      "📷 First photo\nSecond text prompt\n📷 Third photo" in api.last_card,
      f"Card: {api.last_card}")

# 5.7 Card reuse during active batch vs new card after completion
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "cycle_1", "width": 100, "height": 100}], caption="Chain 1 photo")
bot._handle(bot._poll()[0])
first_card_id = len(api.sent_messages)
check("5.7 First card created via sendMessage", first_card_id == 1)

# During same chain, adding another photo edits the existing card
api.feed_photo([{"file_id": "cycle_2", "width": 100, "height": 100}], caption="Chain 1 photo 2")
bot._handle(bot._poll()[0])
check("5.7 Card edited rather than newly sent during active batch",
      len(api.sent_messages) == 1 and len(api.edited_messages) >= 1)

# Simulate chain completion: pending_own_card reset to False
bot.pending_own_card = False
api.feed_photo([{"file_id": "cycle_3", "width": 100, "height": 100}], caption="Chain 2 photo")
bot._handle(bot._poll()[0])
check("5.7 New card created via sendMessage for new chain",
      len(api.sent_messages) == 2, f"Total messages sent: {len(api.sent_messages)}")


# ==============================================================================
# CATEGORY 6: Standalone download_file Function Tests
# ==============================================================================
print("\n=== Category 6: Standalone download_file Function Tests ===")

mock_requests = []


class MockUrlOpenResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.payload


def mock_urlopen(req, timeout=None):
    mock_requests.append((req.full_url, timeout))
    return MockUrlOpenResponse(b"MOCK_DOWNLOADED_IMAGE_DATA")


orig_urlopen = urllib.request.urlopen
urllib.request.urlopen = mock_urlopen
try:
    res = download_file("secret_token_abc", "photos/my target screenshot.png", timeout=50)
    check("6.1 download_file returns binary content", res == b"MOCK_DOWNLOADED_IMAGE_DATA")
    check("6.1 Spaces in path percent-encoded to %20",
          "photos/my%20target%20screenshot.png" in mock_requests[0][0],
          f"URL: {mock_requests[0][0]}")
    check("6.1 Correct base URL and token formatted",
          mock_requests[0][0].startswith("https://api.telegram.org/file/botsecret_token_abc/"))
    check("6.1 Custom timeout honored", mock_requests[0][1] == 50)

    # 6.2 Leading and trailing whitespace stripped from file_path
    mock_requests.clear()
    download_file("tok", "   photos/trimmed.jpg\n  ", timeout=30)
    check("6.2 Whitespace in file_path stripped",
          mock_requests[0][0] == "https://api.telegram.org/file/bottok/photos/trimmed.jpg")

    # 6.3 Unicode characters in file_path percent-encoded
    mock_requests.clear()
    download_file("tok", "photos/fișier_română.jpg", timeout=30)
    check("6.3 Unicode path properly percent-encoded",
          "fi%C8%99ier_rom%C3%A2n%C4%83.jpg" in mock_requests[0][0],
          f"URL: {mock_requests[0][0]}")

finally:
    urllib.request.urlopen = orig_urlopen


# ==============================================================================
# CATEGORY 7: Downloader Polymorphism (2-arg, 1-arg path, 1-arg URL)
# ==============================================================================
print("\n=== Category 7: Downloader Injected Signatures ===")

# 7.1 Downloader accepting (token, file_path)
called_2arg = []
bot, api, box, _ = make_test_bot(
    downloader=lambda t, p: (called_2arg.append((t, p)) or b"BYTES_2ARG")
)
api.feed_photo([{"file_id": "f_2arg", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("7.1 2-arg downloader called with token and path",
      called_2arg == [("stress_test_token_12345", "photos/f_2arg.jpg")])
check("7.1 Step received bytes from 2-arg downloader",
      bot.pending[0]["image_bytes"] == b"BYTES_2ARG")

# 7.2 Downloader accepting only (file_path)
called_1arg_path = []
def dl_path_only(p):
    called_1arg_path.append(p)
    return b"BYTES_1ARG_PATH"

bot, api, box, _ = make_test_bot(downloader=dl_path_only)
api.feed_photo([{"file_id": "f_1arg_path", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("7.2 1-arg (path) downloader called with path",
      called_1arg_path == ["photos/f_1arg_path.jpg"])
check("7.2 Step received bytes from 1-arg path downloader",
      bot.pending[0]["image_bytes"] == b"BYTES_1ARG_PATH")

# 7.3 Downloader accepting (url) when path-only raises TypeError
called_url = []
def dl_url_only(arg1):
    # Only accepts argument if it looks like a full URL
    if not str(arg1).startswith("https://"):
        raise TypeError("Expected full URL")
    called_url.append(arg1)
    return b"BYTES_URL"

bot, api, box, _ = make_test_bot(downloader=dl_url_only)
api.feed_photo([{"file_id": "f_url", "width": 100, "height": 100}])
bot._handle(bot._poll()[0])
check("7.3 1-arg (URL) downloader called with full constructed URL",
      len(called_url) == 1 and called_url[0].startswith("https://api.telegram.org/file/bot"))
check("7.3 Step received bytes from URL downloader",
      bot.pending[0]["image_bytes"] == b"BYTES_URL")


# ==============================================================================
# CATEGORY 8: Control Commands While Photo Pending in Queue
# ==============================================================================
print("\n=== Category 8: Control Commands With Queued Photo ===")

# 8.1 /stop cancels pending photo step and clears queue
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "stop_p", "width": 100, "height": 100}], caption="Doomed photo")
bot._handle(bot._poll()[0])
check("8.1 Photo step successfully queued", len(bot.pending) == 1)

# User sends /stop
api.feed_text("/stop")
bot._handle(bot._poll()[0])
check("8.1 /stop cancels queue and clears pending photo", len(bot.pending) == 0)
check("8.1 User informed that queue was stopped",
      any("queue is empty" in m.lower() for m in api.sent_messages),
      f"Sent: {api.sent_messages}")

# 8.2 /status works cleanly while photo is waiting
bot, api, box, _ = make_test_bot()
api.feed_photo([{"file_id": "status_p", "width": 100, "height": 100}], caption="Status photo")
bot._handle(bot._poll()[0])
api.feed_text("/status")
bot._handle(bot._poll()[0])
check("8.2 /status does not crash or corrupt pending photo queue", len(bot.pending) == 1)


print("\n=== Empirical Stress Test Summary ===")
sys.exit(report.finish())
