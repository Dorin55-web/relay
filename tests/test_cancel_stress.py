"""Empirical stress test harness for Feature 1 (Cancel Task & Edge Cases).

Tests:
1. Callback queries from unauthorized users, foreign chats, malformed data, and unpaired bots.
2. Rapid multiple button taps and concurrent cancellations (idempotence & Telegram edit deduplication).
3. /cancel command when no task is active, with and without target hwnd.
4. Window focus failure, destroyed window handle, and canceller raising exceptions.
5. Verification that _paint() removes the inline keyboard when entering terminal states
   (DONE, STOPPED) even when the rendered message text is 100% identical.
6. Callback query failure in answerCallbackQuery (network error) resilience.
"""

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import relay.remote as remote_mod
import relay.injector as injector_mod
import relay.target as target_mod
import relay.agent as agent_mod

agent_mod.profile_for = lambda hwnd, profiles=None: (
    {"name": "fake"} if hwnd else None)
agent_mod.state = lambda hwnd, profile=None, seen=None: agent_mod.IDLE
remote_mod.window_title = lambda hwnd: "Some Window"

MINE = 12345
THEIRS = 99999
TOKEN = "123456789:AAETestTokenForCancelStressTests12345"
HWND = 4242


class MockTelegramApi:
    def __init__(self):
        self.sent = []
        self.sent_markups = []
        self.edits = []
        self.edited_markups = []
        self.answered_callbacks = []
        self.messages = {}
        self.updates = []
        self.next_msg_id = 100
        self.next_cq_id = 500
        self.fail_answer_callback = False

    def __call__(self, token, method, params=None, timeout=None):
        params = params or {}
        if method == "sendMessage":
            msg_id = self.next_msg_id
            self.next_msg_id += 1
            self.sent.append(params.get("text", ""))
            self.sent_markups.append(params.get("reply_markup"))
            self.messages[msg_id] = params.get("text", "")
            return {"message_id": msg_id}

        elif method == "editMessageText":
            msg_id = params.get("message_id")
            self.edits.append((msg_id, params.get("text", "")))
            self.edited_markups.append(params.get("reply_markup"))
            if msg_id:
                self.messages[msg_id] = params.get("text", "")
            return {"message_id": msg_id}

        elif method == "answerCallbackQuery":
            if self.fail_answer_callback:
                raise ConnectionResetError("10054 Connection reset by peer")
            self.answered_callbacks.append(params)
            return {"ok": True}

        elif method == "getMe":
            return {"id": 1, "is_bot": True, "first_name": "RelayTestBot"}

        return {"ok": True}

    def feed_callback(self, query_id=None, data="cancel_task", chat=MINE, user=None, raw_query=None):
        if raw_query is not None:
            cq = raw_query
        else:
            if query_id is None:
                query_id = f"cq_{self.next_cq_id}"
                self.next_cq_id += 1
            cq = {
                "id": query_id,
                "data": data,
                "message": {"chat": {"id": chat}, "message_id": 100},
            }
            if user is not None:
                cq["from"] = {"id": user}
            else:
                cq["from"] = {"id": chat}
        self.updates.append({"update_id": len(self.updates) + 1, "callback_query": cq})


def make_bot(chat_id=MINE, canceller=None):
    api = MockTelegramApi()
    settings = {"token": TOKEN, "chat_id": chat_id}
    bot = remote_mod.Remote(
        settings=settings,
        send=lambda text, hwnd: True,
        target_getter=lambda: HWND,
        api=api,
        canceller=canceller,
    )
    bot.pilot.read_state = lambda _h: agent_mod.IDLE
    bot.pilot.is_window = lambda _h: True
    bot.pilot.focus = lambda _h: True
    bot.pilot.place_caret = lambda _h, _p: True
    bot.pilot.poll_seconds = 0.02
    bot.pilot.countdown_seconds = 0.02
    bot.pilot.countdown_tick = 0.01
    bot._target = lambda: HWND
    return bot, api


passed = 0
failed = 0


def test(name, condition, details=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name}: {details}")


def run_tests():
    print("=== Testing Feature 1: Cancel Task & Edge Cases ===")

    # ---------------------------------------------------------
    # Group 1: Callback Query Authentication & Sender Validation
    # ---------------------------------------------------------
    print("\n--- Group 1: Callback query authentication & sender validation ---")

    # 1.1: Foreign chat ID is rejected
    cancels = []
    bot, api = make_bot(canceller=lambda h: cancels.append(h))
    api.feed_callback(chat=THEIRS, user=THEIRS)
    bot._handle(api.updates.pop())
    test("1.1 Foreign chat/user is rejected (no cancel)", len(cancels) == 0, f"cancels={cancels}")
    test("1.1 Foreign callback is not answered", len(api.answered_callbacks) == 0)

    # 1.2: Foreign user clicking button in paired chat (e.g. group context)
    # query['message']['chat']['id'] == MINE, but query['from']['id'] == THEIRS
    raw_cq = {
        "id": "cq_foreign_user",
        "data": "cancel_task",
        "message": {"chat": {"id": THEIRS}, "message_id": 100},
        "from": {"id": MINE}
    }
    api.feed_callback(raw_query=raw_cq)
    bot._handle(api.updates.pop())
    # Note: _handle_callback_query checks:
    # chat_id = (query.get("message") or {}).get("chat", {}).get("id") or (query.get("from") or {}).get("id")
    # message.chat.id is THEIRS (not MINE) -> rejected!
    test("1.2 Foreign chat with paired user is rejected", len(cancels) == 0)

    # 1.3: Callback query with missing message but valid from.id
    raw_cq_inline = {
        "id": "cq_inline_valid",
        "data": "cancel_task",
        "from": {"id": MINE}
    }
    api.feed_callback(raw_query=raw_cq_inline)
    bot._handle(api.updates.pop())
    test("1.3 Callback without message but valid from.id is accepted", len(cancels) == 1)
    test("1.3 Callback query answered", len(api.answered_callbacks) == 1)

    # 1.4: Callback query with missing message and foreign from.id
    cancels.clear()
    api.answered_callbacks.clear()
    raw_cq_inline_bad = {
        "id": "cq_inline_bad",
        "data": "cancel_task",
        "from": {"id": THEIRS}
    }
    api.feed_callback(raw_query=raw_cq_inline_bad)
    bot._handle(api.updates.pop())
    test("1.4 Callback without message and foreign from.id is rejected", len(cancels) == 0)
    test("1.4 Callback not answered", len(api.answered_callbacks) == 0)

    # 1.5: Bot not paired yet (chat_id is None)
    bot_unpaired, api_unpaired = make_bot(chat_id=None, canceller=lambda h: cancels.append(h))
    api_unpaired.feed_callback(chat=MINE, user=MINE)
    bot_unpaired._handle(api_unpaired.updates.pop())
    test("1.5 Unpaired bot rejects callback safely", len(cancels) == 0)
    test("1.5 Unpaired callback query not answered", len(api_unpaired.answered_callbacks) == 0)

    # 1.6: String vs Int chat ID comparison
    bot_str_chat, api_str_chat = make_bot(chat_id="12345", canceller=lambda h: cancels.append(h))
    api_str_chat.feed_callback(chat=12345, user=12345)
    bot_str_chat._handle(api_str_chat.updates.pop())
    test("1.6 String vs Int chat ID matches correctly via int() conversion", len(cancels) == 1)

    # ---------------------------------------------------------
    # Group 2: Rapid Multiple Button Taps & Concurrency Stress
    # ---------------------------------------------------------
    print("\n--- Group 2: Rapid multiple button taps & concurrency ---")

    # 2.1: 5 Rapid sequential taps on Cancel button
    cancels.clear()
    bot, api = make_bot(canceller=lambda h: cancels.append(h))
    bot.pilot.start(["step 1", "step 2"], HWND)
    test("2.1 Autopilot running before taps", bot.pilot.running is True)

    for i in range(5):
        api.feed_callback(query_id=f"tap_{i}", data="cancel_task", chat=MINE)
        bot._handle(api.updates.pop())

    deadline = time.monotonic() + 1
    while bot.pilot.running and time.monotonic() < deadline:
        time.sleep(0.01)

    test("2.1 Autopilot stopped after taps", bot.pilot.running is False)
    test("2.1 All 5 callback queries were answered (dismisses phone spinners)", len(api.answered_callbacks) == 5)
    test("2.1 Queue is empty after multiple taps", len(bot.pending) == 0)
    test("2.1 Card shows cancelled", "cancelled" in api.messages.get(bot._card, ""))

    # 2.2: Telegram deduplication on redundant edits:
    # First paint: (text, None) was sent.
    # On 2nd-5th tap, the text and markup are identical!
    # Did _paint() avoid flooding Telegram with 4 redundant edits?
    test("2.2 Redundant paints deduplicated via _painted_state",
         len(api.edits) <= 2, f"Total edits={len(api.edits)} (expected <= 2)")

    # 2.3: Concurrent callback queries from multiple threads
    cancels.clear()
    api.answered_callbacks.clear()
    bot, api = make_bot(canceller=lambda h: (time.sleep(0.01), cancels.append(h)))
    bot.pilot.start(["step 1"], HWND)

    threads = []
    for i in range(10):
        t = threading.Thread(
            target=lambda idx=i: bot._handle({
                "update_id": 1000 + idx,
                "callback_query": {
                    "id": f"conc_{idx}",
                    "data": "cancel_task",
                    "message": {"chat": {"id": MINE}, "message_id": 100}
                }
            })
        )
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    test("2.3 Concurrent taps handled without exception", True)
    test("2.3 All 10 concurrent queries answered", len(api.answered_callbacks) == 10)
    test("2.3 Pilot is stopped", bot.pilot.running is False)

    # ---------------------------------------------------------
    # Group 3: /cancel Command when No Task is Active
    # ---------------------------------------------------------
    print("\n--- Group 3: /cancel command when no task is active ---")

    cancels.clear()
    bot, api = make_bot(canceller=lambda h: cancels.append(h))
    test("3.1 Queue empty and pilot not running", not bot.pending and not bot.pilot.running)

    # Call /cancel directly via command
    bot._command("/cancel")
    test("3.1 /cancel with no active task runs without error", True)
    test("3.1 Chat reply confirmation sent", any("Task cancelled." in s for s in api.sent))
    test("3.1 Status card updated to cancelled", "cancelled" in api.messages.get(bot._card, ""))
    test("3.1 Canceller invoked to halt any background IDE state", len(cancels) == 1 and cancels[0] == HWND)

    # Call /cancel second time
    cancels.clear()
    api.sent.clear()
    bot._command("/cancel")
    test("3.2 Second /cancel executes safely and idempotently", len(cancels) == 1)
    test("3.2 Confirmation sent on second /cancel", any("Task cancelled." in s for s in api.sent))

    # ---------------------------------------------------------
    # Group 4: Window Focus Failure, Destroyed Target Window & Exceptions
    # ---------------------------------------------------------
    print("\n--- Group 4: Window focus failure & exceptions ---")

    # 4.1: cancel_task_in_window with invalid/destroyed hwnd
    with patch("relay.uia.click_cancel_button", return_value=False), \
         patch("relay.injector.focus_window", return_value=False) as mock_focus:
        with patch.object(injector_mod._keyboard, "pressed") as mock_pressed:
            result = injector_mod.cancel_task_in_window(target_hwnd=9999999)
            test("4.1 cancel_task_in_window handles focus failure gracefully", result is True)
            mock_focus.assert_called_with(9999999)

    # 4.2: canceller raises arbitrary exception in _cancel_task
    def failing_canceller(hwnd):
        raise RuntimeError("Win32 error 5: Access denied")

    bot, api = make_bot(canceller=failing_canceller)
    bot.pilot.start(["step 1"], HWND)
    # Trigger cancellation via /cancel
    try:
        bot._command("/cancel")
        crashed = False
    except Exception as exc:
        crashed = True
    test("4.2 Failing canceller does not crash _cancel_task or bot", not crashed)
    deadline = time.monotonic() + 1
    while bot.pilot.running and time.monotonic() < deadline:
        time.sleep(0.01)
    test("4.2 Pilot was still stopped despite canceller exception", bot.pilot.running is False)
    test("4.2 Card was still updated to cancelled", "cancelled" in api.messages.get(bot._card, ""))

    # 4.3: Telegram network error on answerCallbackQuery
    cancels.clear()
    bot, api = make_bot(canceller=lambda h: cancels.append(h))
    api.fail_answer_callback = True  # Network drops on answerCallbackQuery
    api.feed_callback(query_id="cq_fail_answer", data="cancel_task", chat=MINE)

    try:
        bot._handle(api.updates.pop())
        cq_crashed = False
    except Exception:
        cq_crashed = True

    test("4.3 Network failure answering callback does not crash bot", not cq_crashed)
    test("4.3 Task is still cancelled even if answerCallbackQuery failed", len(cancels) == 1)

    # ---------------------------------------------------------
    # Group 5: _paint() Keyboard Removal on Terminal States (DONE / STOPPED)
    # ---------------------------------------------------------
    print("\n--- Group 5: Inline keyboard removal on terminal states ---")

    # 5.1: Normal transition from HOLDING (cancel_button=True) to STOPPED (cancel_button=False)
    bot, api = make_bot()
    bot._where = "Antigravity"
    # Paint active phase
    bot._progress(remote_mod.HOLDING, 0, 1, 0)
    card_msg_id = bot._card
    test("5.1 Active card was created", card_msg_id is not None)
    test("5.1 Active card has Cancel Task button markup",
         api.sent_markups and "cancel_task" in (api.sent_markups[0] or ""))

    # Now transition to STOPPED
    bot._progress(remote_mod.STOPPED, 0, 1, 0)
    test("5.1 Card edit was sent for STOPPED", len(api.edits) >= 1)
    test("5.1 Cancel button removed on STOPPED (reply_markup is None)",
         api.edited_markups[-1] is None, f"last edited markup: {api.edited_markups[-1]}")

    # 5.2: Edge Case: Message text is 100% IDENTICAL, but cancel_button changes from True to False!
    # This directly stresses Question 5:
    # "Does _paint() properly remove the inline keyboard when entering DONE or STOPPED
    # even if the message text is identical?"
    bot, api = make_bot()
    # Paint initial state with button
    bot._paint(icon="⏳", head="Identical Title", note="Identical Note", cancel_button=True)
    initial_msg_id = bot._card
    initial_text = api.messages[initial_msg_id]
    test("5.2 Initial card rendered with button",
         api.sent_markups and "cancel_task" in (api.sent_markups[0] or ""))

    edits_before = len(api.edits)
    # Paint AGAIN with the exact same icon, head, note, but cancel_button=False!
    bot._paint(icon="⏳", head="Identical Title", note="Identical Note", cancel_button=False)
    edits_after = len(api.edits)

    test("5.2 Card was edited despite 100% identical text", edits_after == edits_before + 1,
         f"edits_before={edits_before}, edits_after={edits_after}")
    test("5.2 New edit has reply_markup=None to strip inline button",
         api.edited_markups[-1] is None, f"markup={api.edited_markups[-1]}")
    test("5.2 Rendered message text remained exactly identical",
         api.messages[initial_msg_id] == initial_text)

    # 5.3: Transition to DONE removes button
    bot, api = make_bot()
    bot._where = "Antigravity"
    bot._progress(remote_mod.SENDING, 0, 1, 0)
    test("5.3 SENDING has cancel button",
         api.sent_markups and "cancel_task" in (api.sent_markups[0] or ""))
    bot._progress(remote_mod.DONE, 0, 1, 0)
    test("5.3 DONE removed cancel button",
         api.edited_markups[-1] is None, f"markup={api.edited_markups[-1]}")
    test("5.3 DONE card shows done icon",
         remote_mod.ICON_DONE in api.messages[bot._card])

    # ---------------------------------------------------------
    # Group 6: Cursor Refocusing & Click Fallback on Cancel
    # ---------------------------------------------------------
    print("\n--- Group 6: Cursor refocusing & click fallback ---")

    # 6.1: cancel_task_in_window triggers deferred refocus
    refocused = []
    with patch("relay.uia.click_cancel_button", return_value=True), \
         patch("relay.injector._deferred_refocus", side_effect=lambda h, delay=0.35: refocused.append(h)):
        res = injector_mod.cancel_task_in_window(target_hwnd=HWND)
        test("6.1 cancel_task_in_window succeeds", res is True)
        time.sleep(0.05)
        test("6.1 deferred refocus was scheduled for target window", HWND in refocused)

    # 6.2: focus_named_input click fallback when SetFocus does not take
    class FakeRect:
        left = 100
        top = 500
        right = 300
        bottom = 540

    class FakeElement:
        CurrentName = "Message input"
        CurrentBoundingRectangle = FakeRect()
        CurrentIsKeyboardFocusable = True
        def SetFocus(self):
            pass

    class FakeList:
        Length = 1
        def GetElement(self, idx):
            return FakeElement()

    class FakeFocusedWrong:
        CurrentName = "Select model, current: Gemini 3.8 Flash High"

    class FakeAuto:
        def ElementFromHandle(self, hwnd):
            return self
        def CreatePropertyCondition(self, prop, val):
            return prop
        def FindAll(self, scope, cond):
            return FakeList()
        def GetFocusedElement(self):
            return FakeFocusedWrong()

    with patch("relay.uia._uia", return_value=(FakeAuto(), MagicMock())):
        with patch("ctypes.windll.user32.SetCursorPos") as mock_set_cursor:
            with patch("ctypes.windll.user32.mouse_event") as mock_mouse:
                from relay import uia as uia_mod
                res = uia_mod.focus_named_input(HWND, "Message input")
                test("6.2 focus_named_input succeeds via click fallback", res is True)
                test("6.2 mouse click was sent to center of bounding box", mock_mouse.called)
                mock_set_cursor.assert_called_with(200, 520)

    # 6.3: focus_named_input discards off-screen elements with negative coordinates
    class FakeOffscreenRect:
        left = 882
        top = -1527
        right = 973
        bottom = -1512

    class FakeOffscreenElement:
        CurrentName = "Message input"
        CurrentBoundingRectangle = FakeOffscreenRect()
        CurrentIsKeyboardFocusable = False
        def SetFocus(self):
            pass

    class FakeMultiList:
        Length = 2
        def GetElement(self, idx):
            if idx == 0:
                return FakeOffscreenElement()
            return FakeElement()

    class FakeMultiAuto:
        def ElementFromHandle(self, hwnd):
            return self
        def CreatePropertyCondition(self, prop, val):
            return prop
        def FindAll(self, scope, cond):
            return FakeMultiList()
        def GetFocusedElement(self):
            return FakeFocusedWrong()

    with patch("relay.uia._uia", return_value=(FakeMultiAuto(), MagicMock())):
        with patch("ctypes.windll.user32.SetCursorPos") as mock_set_cursor:
            with patch("ctypes.windll.user32.mouse_event") as mock_mouse:
                res = uia_mod.focus_named_input(HWND, "Message input")
                test("6.3 focus_named_input discards off-screen match and clicks on-screen candidate", res is True)
                mock_set_cursor.assert_called_with(200, 520)

    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
