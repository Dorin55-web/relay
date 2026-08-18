"""The phone side, without a phone and without the network.

Every Telegram call goes through one injected function, so this drives the
whole thing on scripted messages: pairing, refusing strangers, queueing,
turning several messages into one chain, and the replies that come back.

The checks that matter most are the ones about who is allowed to talk to it. A
bot that types into your laptop and presses Enter, while the window it types
into is an agent with a shell, is a way to run commands on this machine. It
answers exactly one chat and ignores every other in silence.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

from relay import agent                     # noqa: E402
from relay import remote as remote_mod      # noqa: E402
from relay.remote import Remote             # noqa: E402

report = context.Report()
check = report.check

HWND = 4242
MINE, THEIRS = 111, 999


class Api:
    """Stands in for Telegram. Hands out scripted updates, records replies."""

    def __init__(self):
        self.updates = []
        self.sent = []
        self.calls = 0

    def feed(self, text, chat=MINE, update_id=None):
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
            return {"message_id": len(self.sent)}
        raise AssertionError(f"unexpected method {method}")


def make(chat_id=MINE, states=None, sent=None):
    tmp = Path(tempfile.mkdtemp(prefix="relay-remote-"))
    path = tmp / "telegram.json"
    path.write_text(json.dumps({"token": "t", "chat_id": chat_id}), encoding="utf-8")
    api = Api()
    box = sent if sent is not None else []
    bot = Remote(
        settings={"token": "t", "chat_id": chat_id, "path": path},
        send=lambda text, hwnd: (box.append(text) or True),
        target_getter=lambda: HWND,
        log=lambda *_: None,
        api=api,
    )
    bot.pilot.read_state = states or (lambda _h: agent.IDLE)
    bot.pilot.is_window = lambda _h: True
    bot.pilot.focus = lambda _h: True
    bot.pilot.place_caret = lambda _h, _p: True
    bot.pilot.poll_seconds = 0.02
    bot.pilot.countdown_seconds = 1
    bot.pilot.countdown_tick = 0.02
    return bot, api, box, path


agent.profile_for = lambda hwnd, profiles=None: (
    {"name": "fake"} if hwnd else None)
agent.state = lambda hwnd, profile=None, seen=None: agent.IDLE
remote_mod.window_title = lambda hwnd: "Some Window"


print("\n--- the settings file ---")
tmp = Path(tempfile.mkdtemp(prefix="relay-settings-"))
path = tmp / "telegram.json"
check("a template is written once", remote_mod.write_template(path) is True)
check("and not written over", remote_mod.write_template(path) is False)
check("an empty token means there is nothing to run",
      remote_mod.load_settings(path) is None)
check("a missing file too", remote_mod.load_settings(tmp / "nope.json") is None)

path.write_text(json.dumps({"token": "abc", "chat_id": 7}), encoding="utf-8")
loaded = remote_mod.load_settings(path)
check("a filled one loads", loaded and loaded["token"] == "abc"
      and loaded["chat_id"] == 7, str(loaded))


print("\n--- setting the token without editing the file by hand ---")
tmp = Path(tempfile.mkdtemp(prefix="relay-token-"))
path = tmp / "telegram.json"

check("a token is written", remote_mod.set_token(path, ask=lambda _p: "123:ABCdef"))
written = json.loads(path.read_text(encoding="utf-8"))
check("and it is the one given", written["token"] == "123:ABCdef", str(written))
# A new token means a new bot; the chat that claimed the old one has no
# business driving this one.
check("the pairing is cleared", written["chat_id"] is None, str(written))

path.write_text(json.dumps({"token": "123:ABC", "chat_id": 5}), encoding="utf-8")
check("nothing pasted changes nothing",
      remote_mod.set_token(path, ask=lambda _p: "   ") is False)
check("a half-copied paste is refused",
      remote_mod.set_token(path, ask=lambda _p: "AAGHsntS8VUE") is False)
kept = json.loads(path.read_text(encoding="utf-8"))
check("and the file is left alone by both", kept["chat_id"] == 5, str(kept))


print("\n--- the first message claims the bot ---")
bot, api, _, path = make(chat_id=None)
api.feed("hello", chat=MINE)
for update in api.updates[:]:
    bot._handle(update)
api.updates.clear()
check("it is paired", bot.chat_id == MINE, str(bot.chat_id))
check("and said so", any("Paired" in s for s in api.sent), str(api.sent))
on_disk = json.loads(path.read_text(encoding="utf-8"))
check("the number is written down", on_disk.get("chat_id") == MINE, str(on_disk))
check("the claiming message is not treated as a prompt", not bot.pending,
      str(bot.pending))


print("\n--- and nobody else gets a word ---")
# Not even an error. A stranger who guessed the bot name learns nothing about
# whether it is running, or what it is attached to.
bot, api, sent, _ = make()
bot._handle({"update_id": 1,
             "message": {"chat": {"id": THEIRS}, "text": "rm -rf /"}})
check("nothing queued", not bot.pending, str(bot.pending))
check("and nothing said back", api.sent == [], str(api.sent))

bot._drain()
time.sleep(0.1)
check("nothing was sent to the window", sent == [], str(sent))


print("\n--- a message becomes a step ---")
bot, api, sent, _ = make()
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "first"}})
check("queued", list(bot.pending) == ["first"], str(bot.pending))
check("and acknowledged", any("Queued" in s for s in api.sent), str(api.sent))


print("\n--- several in a row become one chain, in order ---")
for text in ("second", "third"):
    bot._handle({"update_id": 9, "message": {"chat": {"id": MINE}, "text": text}})
check("three waiting", list(bot.pending) == ["first", "second", "third"],
      str(bot.pending))

# A real window goes busy when it is sent something and finishes a moment
# later; one that stays idle makes the queue wait out its start timeout.
state = {"now": agent.IDLE}


def on_send(text, hwnd):
    sent.append(text)
    state["now"] = agent.BUSY
    import threading
    threading.Timer(0.15, lambda: state.update(now=agent.IDLE)).start()
    return True


bot.pilot.send = on_send
bot.pilot.read_state = lambda _h: state["now"]
bot._drain()
check("the queue was emptied into the chain", not bot.pending, str(bot.pending))
deadline = time.monotonic() + 8
while bot.pilot.running and time.monotonic() < deadline:
    time.sleep(0.02)
check("all three went, in order", sent == ["first", "second", "third"], str(sent))
check("and it reported finishing", any("All 3 done" in s for s in api.sent),
      str(api.sent[-3:]))


print("\n--- commands ---")
bot, api, sent, _ = make()
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "/status"}})
check("status names the window and how it reads",
      any("Some Window" in s and "fake" in s for s in api.sent), str(api.sent))

bot.pending.append("something")
bot._handle({"update_id": 2, "message": {"chat": {"id": MINE}, "text": "/stop"}})
check("stop empties the queue", not bot.pending, str(bot.pending))
check("and says so", any("empty" in s for s in api.sent), str(api.sent[-1:]))

api.sent.clear()
bot._handle({"update_id": 3, "message": {"chat": {"id": MINE}, "text": "/wat"}})
check("an unknown command is answered, not obeyed",
      api.sent and "only know" in api.sent[0], str(api.sent))


print("\n--- a window it cannot read ---")
was = agent.profile_for
agent.profile_for = lambda hwnd, profiles=None: None
bot, api, sent, _ = make()
bot.pending.append("do a thing")
bot._drain()
check("nothing was sent", sent == [], str(sent))
check("the queue was not left half full", not bot.pending, str(bot.pending))
check("and the phone was told why",
      any("does not know how to read" in s for s in api.sent), str(api.sent))
agent.profile_for = was


print("\n--- it tells you when the agent stops to ask you something ---")
# The one interruption worth making. Nobody is in the room to notice.
bot, api, sent, _ = make()
bot.pilot.steps = ["a", "b"]
bot._progress(remote_mod.WAITING, 0, 2, None)
bot._progress(remote_mod.WAITING, 0, 2, None)
check("said once", sum("stopped to ask" in s for s in api.sent) == 1, str(api.sent))

sys.exit(report.finish())
