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
    """Stands in for Telegram. Scripted updates in, messages and edits out.

    Edits are tracked separately from sends because the difference is the
    point: a batch of prompts is meant to occupy one message that changes,
    not a running commentary of four.
    """

    def __init__(self):
        self.updates = []
        self.sent = []          # every sendMessage, in order
        self.edits = []         # every editMessageText, in order
        self.messages = {}      # id -> what it now says
        self.calls = 0

    @property
    def card(self):
        """What the most recently created message currently says."""
        return self.messages.get(len(self.sent), "")

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
            self.messages[len(self.sent)] = params["text"]
            return {"message_id": len(self.sent)}
        if method == "editMessageText":
            self.edits.append(params["text"])
            self.messages[params["message_id"]] = params["text"]
            return {}
        raise AssertionError(f"unexpected method {method}")


def make(chat_id=MINE, states=None, sent=None):
    tmp = Path(tempfile.mkdtemp(prefix="relay-remote-"))
    path = tmp / "telegram.json"
    path.write_text(json.dumps({"token": "t", "chat_id": chat_id}), encoding="utf-8")
    api = Api()
    box = sent if sent is not None else []
    alive = {"all": True}
    bot = Remote(
        settings={"token": "t", "chat_id": chat_id, "path": path},
        send=lambda text, hwnd: (box.append(text) or True),
        target_getter=lambda: HWND,
        log=lambda *_: None,
        api=api,
        is_window=lambda _h: alive["all"],
    )
    bot.alive = alive
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

# The shape of a real one: an id, a colon, and thirty-five characters.
ONE = "8936943894:" + "A" * 35

check("a token is written", remote_mod.set_token(path, ask=lambda _p: ONE))
written = json.loads(path.read_text(encoding="utf-8"))
check("and it is the one given", written["token"] == ONE, str(written))
# A new token means a new bot; the chat that claimed the old one has no
# business driving this one.
check("the pairing is cleared", written["chat_id"] is None, str(written))

# A file with something already in it, so a refusal can be seen to leave it
# alone rather than merely to return False.
path.write_text(json.dumps({"token": "old:token", "chat_id": 5}), encoding="utf-8")

check("nothing pasted changes nothing",
      remote_mod.set_token(path, ask=lambda _p: "   ") is False)
check("a half-copied paste is refused",
      remote_mod.set_token(path, ask=lambda _p: "AAGHsntS8VUE") is False)
# The one that got through. Nothing is echoed, so a paste that looks like it
# did not work gets repeated - measured at 171 characters on the first real
# setup, the same token nearly four times, written without a word.
check("a token pasted four times is refused",
      remote_mod.set_token(path, ask=lambda _p: ONE * 4) is False)

kept = json.loads(path.read_text(encoding="utf-8"))
check("and none of the three touched the file",
      kept == {"token": "old:token", "chat_id": 5}, str(kept))

check("but one pasted once goes in",
      remote_mod.set_token(path, ask=lambda _p: ONE) is True)
check("replacing what was there",
      json.loads(path.read_text(encoding="utf-8"))["token"] == ONE)


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


print("\n--- Romanian in, English out ---")
# The whole point of the program, arriving by a different door. The phone gets
# the translation back as well as the queue, because that is the only chance to
# see a mangled sentence before it is typed into an agent.
bot, api, sent, _ = make()
bot.translate = lambda text: "find the cause of the freeze"
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE},
                                         "text": "gaseste cauza inghetarii"}})
check("the English is what gets queued",
      list(bot.pending) == ["find the cause of the freeze"], str(bot.pending))
check("and it is shown back on the phone",
      "find the cause of the freeze" in api.card, api.card)

print("\n--- unless you ask for it as typed ---")
bot, api, sent, _ = make()
bot.translate = lambda text: "SHOULD NOT BE USED"
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE},
                                         "text": "=git log --oneline -5"}})
check("a leading = sends it verbatim",
      list(bot.pending) == ["git log --oneline -5"], str(bot.pending))

print("\n--- and a translation that fails does not lose the prompt ---")
# Sending it in the wrong language is a worse outcome than sending nothing
# only if you never find out. The reply says which happened.
bot, api, sent, _ = make()


def broken(text):
    raise RuntimeError("the model is not available")


bot.translate = broken
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE},
                                         "text": "gaseste cauza"}})
check("the Romanian is queued instead", list(bot.pending) == ["gaseste cauza"],
      str(bot.pending))
check("and it says so", any("Could not translate" in s for s in api.sent),
      str(api.sent))

print("\n--- commands are never translated ---")
bot, api, sent, _ = make()
translated = []
bot.translate = lambda text: translated.append(text) or text
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "/status"}})
check("the translator was not asked", translated == [], str(translated))


print("\n--- a message becomes a step ---")
bot, api, sent, _ = make()
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "first"}})
check("queued", list(bot.pending) == ["first"], str(bot.pending))
check("and shown on a card", "first" in api.card, api.card)
check("one message, not several", len(api.sent) == 1, str(api.sent))


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
# The whole batch lived in the one message it started in. Four separate
# notifications for one prompt is what this replaced.
check("still one message for the batch", len(api.sent) == 1, str(api.sent))
check("rewritten as it went", len(api.edits) >= 2, str(len(api.edits)))
check("and it ends up saying done", "done" in api.card, api.card)


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


print("\n--- choosing the window from the phone ---")
# The point of the feature is not being at the laptop, and the target is
# otherwise whatever you last clicked into - which you can only change by being
# there. This is the command that closes that gap.
WINDOWS = [
    (10, "Claude", {"name": "Claude"}),
    (20, "OC | Greeting", {"name": "opencode"}),
]
agent.recognised_windows = lambda: list(WINDOWS)

bot, api, sent, _ = make()
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "/target"}})
listing = api.sent[-1]
check("both windows are offered", "1. Claude" in listing and "2. OC | Greeting"
      in listing, listing)
check("with how each one reads", "opencode" in listing, listing)

api.sent.clear()
bot._handle({"update_id": 2,
             "message": {"chat": {"id": MINE}, "text": "/target 2"}})
check("the second one is pinned", bot.chosen == 20, str(bot.chosen))
check("and it says which", "OC | Greeting" in api.sent[0], str(api.sent))

api.sent.clear()
bot._handle({"update_id": 3,
             "message": {"chat": {"id": MINE}, "text": "/target 9"}})
check("a number out of range changes nothing", bot.chosen == 20, str(bot.chosen))
check("and says the range", "between 1 and 2" in api.sent[0], str(api.sent))

api.sent.clear()
bot._handle({"update_id": 4,
             "message": {"chat": {"id": MINE}, "text": "/target 0"}})
check("zero goes back to following your clicks", bot.chosen is None,
      str(bot.chosen))

# A pinned handle that has since closed would send every message to a window
# that is not there.
bot.chosen = 20
bot.alive["all"] = False
api.sent.clear()
check("a closed pinned window is dropped", bot._target() == HWND,
      str(bot._target()))
check("and forgotten", bot.chosen is None, str(bot.chosen))
check("with a word about it",
      any("has closed" in s for s in api.sent), str(api.sent))
bot.alive["all"] = True


print("\n--- a window it cannot read ---")
was = agent.profile_for
agent.profile_for = lambda hwnd, profiles=None: None
bot, api, sent, _ = make()
bot.pending.append("do a thing")
bot._drain()
check("nothing was sent", sent == [], str(sent))
check("the queue was not left half full", not bot.pending, str(bot.pending))
check("and the phone was told why",
      "does not know how to read" in api.card, api.card)
agent.profile_for = was


print("\n--- the summary that comes back after a step ---")
# The question it answers is "did that work". An error twenty lines up is the
# answer even when the last line looks calm, so failures are lifted out rather
# than left to be spotted in the tail.
bot, api, sent, _ = make()
bot._result(0, [
    "Reading the files now",
    "Copy message",                     # chrome, not an answer
    "ModuleNotFoundError: No module named 'foo'",
    "Trying a different import",
    "Show message actions",             # chrome
    "Done, all three tests pass",
])
summary = api.card
check("the failure is lifted out",
      "Worth a look" in summary and "ModuleNotFoundError" in summary, summary)
check("the tail is there too", "all three tests pass" in summary, summary)
check("and the buttons are not",
      "Copy message" not in summary and "Show message actions" not in summary,
      summary)

bot, api, sent, _ = make()
bot._result(1, ["Everything went fine", "Nothing to report"])
check("a clean step says nothing about trouble",
      "Worth a look" not in api.card, api.card)
check("but does say what it ended with",
      "Nothing to report" in api.card, api.card)

bot, api, sent, _ = make()
bot._result(2, [])
check("and a step that changed nothing on screen says that",
      "Nothing new appeared" in api.card, api.card)
check("no card starts with a blank line",
      api.card == api.card.lstrip(), repr(api.card[:40]))


print("\n--- it tells you when the agent stops to ask you something ---")
# The one interruption worth making. Nobody is in the room to notice.
bot, api, sent, _ = make()
bot._where = "Claude"
bot._progress(remote_mod.WAITING, 0, 2, None)
bot._progress(remote_mod.WAITING, 0, 2, None)
check("the card says it", "stopped to ask you something" in api.card, api.card)
# The queue reports its phase every second for as long as it lasts, and every
# edit counts against a rate limit at Telegram's end.
check("and saying it twice costs nothing",
      len(api.sent) + len(api.edits) == 1, f"{api.sent} {api.edits}")

sys.exit(report.finish())
