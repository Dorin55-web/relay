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
        self.sent_markups = []  # reply_markup for each sendMessage
        self.edited_markups = [] # reply_markup for each editMessageText
        self.answered_callbacks = [] # calls to answerCallbackQuery
        self.messages = {}      # id -> what it now says
        self.confirmed = None   # the offset it last said it was done with
        self.published = None   # the command list it registered
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

    def feed_voice(self, file_id="voice_1", chat=MINE, update_id=None, is_audio=False):
        key = "audio" if is_audio else "voice"
        self.updates.append({
            "update_id": update_id if update_id is not None else len(self.updates) + 1,
            "message": {"chat": {"id": chat}, key: {"file_id": file_id}},
        })

    def feed_callback(self, query_id="cq_1", data="cancel_task", chat=MINE, update_id=None):
        self.updates.append({
            "update_id": update_id if update_id is not None else len(self.updates) + 1,
            "callback_query": {
                "id": query_id,
                "from": {"id": chat, "first_name": "User"},
                "message": {"message_id": len(self.sent), "chat": {"id": chat}},
                "data": data,
            },
        })

    def __call__(self, token, method, params, timeout=None):
        self.calls += 1
        if method == "getUpdates":
            # timeout=0 is not a poll, it is "I am finished with those".
            if params.get("timeout") == 0:
                self.confirmed = params.get("offset")
                return []
            out, self.updates = self.updates, []
            return out
        if method == "setMyCommands":
            self.published = json.loads(params["commands"])
            return True
        if method == "sendMessage":
            self.sent.append(params["text"])
            self.sent_markups.append(params.get("reply_markup"))
            self.messages[len(self.sent)] = params["text"]
            return {"message_id": len(self.sent)}
        if method == "editMessageText":
            self.edits.append(params["text"])
            self.edited_markups.append(params.get("reply_markup"))
            self.messages[params["message_id"]] = params["text"]
            return {}
        if method == "sendPhoto":
            self.sent.append(params.get("caption", ""))
            return {"message_id": len(self.sent)}
        if method == "answerCallbackQuery":
            self.answered_callbacks.append(params)
            return True
        if method == "getFile":
            return {"file_id": params.get("file_id"), "file_path": f"voice/{params.get('file_id')}.oga"}
        raise AssertionError(f"unexpected method {method}")


def make(chat_id=MINE, states=None, sent=None, watched=True, canceller=None,
         downloader=None, speech_translator=None):
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
        # Said, not looked up: whether a keeper is watching this machine is
        # not a thing a test may depend on.
        keeper_watching=lambda: watched,
        canceller=canceller,
        downloader=downloader,
        speech_translator=speech_translator,
    )
    bot.alive = alive
    bot.pilot.read_state = states or (lambda _h: agent.IDLE)
    bot.pilot.is_window = lambda _h: True
    bot.pilot.focus = lambda _h: True
    bot.pilot.place_caret = lambda _h, _p: True
    bot.pilot.poll_seconds = 0.02
    bot.pilot.countdown_seconds = 1
    bot.pilot.countdown_tick = 0.02
    bot._retry_delay = 0
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
check("with the icon that says so at the front",
      api.card.startswith(remote_mod.ICON_DONE), api.card[:20])


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


print("\n--- and the choice survives a restart ---")
# It did not. A window chosen from the phone lived in memory only, so the next
# restart handed prompts silently back to whatever had last been clicked - and
# the first anyone knew was a refusal naming a window nobody had chosen.
bot, api, sent, path = make()
bot._handle({"update_id": 1,
             "message": {"chat": {"id": MINE}, "text": "/target 1"}})
check("the pin is on disk", json.loads(path.read_text(encoding="utf-8"))
      .get("target", {}).get("title") == "Claude",
      path.read_text(encoding="utf-8"))

# A new process, reading the same file.
settings = remote_mod.load_settings(path)
check("and loads back", settings["target"]["title"] == "Claude", str(settings))

after = Remote(settings=settings, send=lambda *a: True,
               target_getter=lambda: HWND, log=lambda *_: None,
               api=Api(), is_window=lambda _h: True)
check("nothing is pinned yet", after.chosen is None)
check("but the window is found again", after._target() == 10, str(after._target()))

# An application that puts the current document in its title is not called the
# same thing twice, so the profile carries the choice when the name has moved.
WINDOWS[0] = (11, "Claude - a different conversation", {"name": "Claude"})
again = Remote(settings=settings, send=lambda *a: True,
               target_getter=lambda: HWND, log=lambda *_: None,
               api=Api(), is_window=lambda _h: True)
check("even when it has been renamed", again._target() == 11, str(again._target()))
WINDOWS[0] = (10, "Claude", {"name": "Claude"})

print("\n--- and letting go is remembered too ---")
bot._handle({"update_id": 2,
             "message": {"chat": {"id": MINE}, "text": "/target 0"}})
check("the file is cleared",
      json.loads(path.read_text(encoding="utf-8")).get("target") is None,
      path.read_text(encoding="utf-8"))


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
      "1 problem" in summary and "ModuleNotFoundError" in summary, summary)
check("the tail is there too", "all three tests pass" in summary, summary)
check("and the buttons are not",
      "Copy message" not in summary and "Show message actions" not in summary,
      summary)

bot, api, sent, _ = make()
bot._result(1, ["Everything went fine", "Nothing to report"])
check("a clean step says nothing about trouble",
      "problem" not in api.card, api.card)
check("but does say what it ended with",
      "What it said" in api.card and "Nothing to report" in api.card,
      api.card)

bot, api, sent, _ = make()
bot._result(2, [])
check("and a step that changed nothing on screen says that",
      "Nothing new appeared" in api.card, api.card)
check("no card starts with a blank line",
      api.card == api.card.lstrip(), repr(api.card[:40]))


print("\n--- one list of commands, in one place ---")
# Pressing "/" in Telegram opens a list only if the bot has registered one.
bot, api, sent, _ = make()
bot.publish_commands()
check("the list is published", api.published is not None, str(api.published))
check("with every command in it",
      len(api.published) == len(remote_mod.COMMANDS), str(api.published))
check("each with something to read",
      all(c["description"] for c in api.published), str(api.published))

# The help text had already fallen a version behind - three commands listed
# when there were six - which is what a second copy of a list does while
# nobody is watching it. It is now printed from the same tuple.
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "/help",
                                         "date": bot._started + 1}})
helped = api.sent[-1]
for name, _what in remote_mod.COMMANDS:
    check(f"/help mentions /{name}", f"/{name}" in helped, helped)

# Every command it publishes has to be one it answers to, or the menu offers
# things that do nothing.
answered = []
for name, _what in remote_mod.COMMANDS:
    api.sent.clear()
    bot._handle({"update_id": 9,
                 "message": {"chat": {"id": MINE}, "text": f"/{name}",
                             "date": bot._started + 1}})
    answered.append(not any("I only know" in s for s in api.sent))
check("and none of them is unknown to it", all(answered), str(answered))


print("\n--- the buttons are asked for, never volunteered ---")
# They arrived attached to every message, sat across half a phone screen, and
# reopened themselves whenever they were closed. Telegram keeps the last
# keyboard it was given until something removes it, so not resending is not
# enough - it has to be taken away.
bot, api, sent, _ = make()
markup = []
real = bot.api


def watch(token, method, params, timeout=None):
    if method == "sendMessage":
        markup.append(params.get("reply_markup"))
    return real(token, method, params, timeout)


bot.api = watch
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "/help",
                                         "date": bot._started + 1}})
check("/help brings none up", markup == [None], str(markup))

markup.clear()
bot._handle({"update_id": 2, "message": {"chat": {"id": MINE}, "text": "/keys",
                                         "date": bot._started + 1}})
check("/keys does", markup and "/status" in (markup[0] or ""), str(markup))

markup.clear()
bot._handle({"update_id": 3,
             "message": {"chat": {"id": MINE}, "text": "/keys off",
                         "date": bot._started + 1}})
check("and /keys off takes them away, rather than just not resending them",
      markup and "remove_keyboard" in (markup[0] or ""), str(markup))


print("\n--- a message from before this Relay existed is not obeyed ---")
# The belt to the confirm's braces. A /restart left pending at Telegram is
# handed to the next Relay, which restarts, which is handed it again - and it
# had already been sitting there unconfirmed before the confirm was written,
# so it kept stopping every Relay that started for a quarter of an hour.
bot, api, sent, _ = make()
obeyed = []
bot.on_restart = lambda: obeyed.append(True)
bot._handle({"update_id": 1,
             "message": {"chat": {"id": MINE}, "text": "/restart",
                         "date": bot._started - 60}})
check("an old command is ignored", obeyed == [], str(obeyed))
check("silently, because the keeper already answered for it", api.sent == [],
      str(api.sent))

bot._handle({"update_id": 2,
             "message": {"chat": {"id": MINE}, "text": "a fresh prompt",
                         "date": bot._started + 1}})
check("but anything since is taken", list(bot.pending) == ["a fresh prompt"],
      str(bot.pending))


print("\n--- restarting from the phone ---")
# For the state that has actually happened: the process alive and answering,
# and the thing it draws frozen. Nothing can fix that from the inside, so
# quitting is the repair - but only because something else is watching.
left = []
bot, api, sent, _ = make()
bot.on_restart = lambda: left.append(True)
bot._offset = 7          # as if messages up to 6 had already been read
remote_mod.RESTART_MARKER = Path(tempfile.mkdtemp()) / ".relay-restart"
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "/restart"}})
check("it goes", left == [True], str(left))
check("saying so first", any("Restarting" in s for s in api.sent), str(api.sent))
# Without it, a deliberate exit and a crash look identical from outside, and
# the keeper would announce a death that had been asked for.
check("and leaves the marker that says it was meant",
      remote_mod.RESTART_MARKER.exists())
# The loop this caused: a message is only consumed when the next request is
# made with a higher offset, and there is no next request after quitting. The
# restart came back, was handed the same instruction, and restarted again -
# four times over before it was stopped by hand.
check("and tells Telegram it is done with that message first",
      api.confirmed == 7, str(api.confirmed))

# Switching the lights off with nobody to turn them back on is worse than
# being stuck, so this refuses rather than obeying. It used to test that a way
# of quitting had been passed in, which is true whenever Relay is running at
# all - so the promise "the keeper will bring me back in a few seconds" went
# out with no keeper anywhere, three times in one evening, and the phone was
# dead until somebody walked to the laptop.
left = []
remote_mod.RESTART_MARKER = Path(tempfile.mkdtemp()) / ".relay-restart"
bot, api, sent, _ = make(watched=False)
bot.on_restart = lambda: left.append(True)
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "/restart"}})
check("with nothing watching, it stays", left == [], str(left))
check("and says why", any("Nothing is watching" in s for s in api.sent),
      str(api.sent))
check("telling you how to fix it for good",
      any("--at-logon" in s for s in api.sent), str(api.sent))
check("and it does not leave a marker for a restart that is not happening",
      not remote_mod.RESTART_MARKER.exists())

# A check that cannot be made is not a yes.
bot, api, sent, _ = make()
bot.keeper_watching = lambda: (_ for _ in ()).throw(OSError("cannot ask"))
bot.on_restart = lambda: left.append(True)
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "/restart"}})
check("a check that raises is read as nothing watching", left == [], str(left))


print("\n--- a warning has to be worth reading ---")
# The first list of words held "cannot", "could not", "not found" and "no
# such", which are ordinary English. Two of these four sentences came back
# flagged, and a card that warns on every reply is a card whose warning means
# nothing at all.
for line in ("The window cannot be read, so it will not type into it.",
             "I could not find a better way to put this, so here it is.",
             "Nothing was found in the folder you asked about.",
             "Nu se poate spune din afara daca a fost oprit."):
    check(f"prose is left alone: {line[:34]}...",
          not remote_mod._looks_wrong(line))

for line in ("ModuleNotFoundError: No module named foo",
             "Traceback (most recent call last):",
             "npm ERR! build failed",
             "Access is denied."):
    check(f"but this is caught: {line[:34]}...", remote_mod._looks_wrong(line))

# A stack trace is terse. A paragraph that happens to use the word is somebody
# explaining something, and length is the cheapest way to tell them apart.
check("and a long paragraph mentioning failure is not machine output",
      not remote_mod._looks_wrong("There was no failure here, only a long "
                                  "explanation of what happened and why, "
                                  "which runs on well past the point where a "
                                  "stack trace would have stopped, and then "
                                  "carries on for a while yet."))


print("\n--- the card is a verdict, not a transcript ---")
# Everything an agent says is new, so the diff is the whole reply. The first
# version put twelve lines of somebody's prose on a phone screen.
bot, api, sent, _ = make()
bot._prompts = ["find the cause"]
bot._result(0, ["find the cause"] + [f"paragraph number {n} " + "x" * 200
                                     for n in range(20)])
card = api.card
check("it is short enough to glance at", len(card) < 1500, str(len(card)))
check("no line runs past the budget",
      all(len(ln) <= remote_mod.LINE_CHARS for ln in card.splitlines()),
      str(max(len(ln) for ln in card.splitlines())))
check("and the prompt is not repeated in the output",
      card.count("find the cause") == 1, str(card.count("find the cause")))


print("\n--- shutting down does not leave a card saying 'working' ---")
# Three times in one evening: a chain waiting on a reply, killed by a restart,
# and the phone left showing a laptop busy on work that no longer existed.
bot, api, sent, _ = make()
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE}, "text": "something"}})
check("it says working first", remote_mod.ICON_WORKING in api.card, api.card)
bot.stop("Relay was restarted")
check("and stopped afterwards", api.card.startswith(remote_mod.ICON_STOPPED),
      api.card[:40])
check("saying what became of the queue", "lost" in api.card, api.card)
check("which is also emptied", not bot.pending, str(bot.pending))

# Nothing in the air means nothing to announce.
bot, api, sent, _ = make()
bot.stop()
check("a quiet shutdown says nothing", api.sent == [] and api.edits == [],
      f"{api.sent} {api.edits}")


print("\n--- it tells you when the agent stops to ask you something ---")
# The one interruption worth making. Nobody is in the room to notice.
bot, api, sent, _ = make()
bot._where = "Claude"
bot._progress(remote_mod.WAITING, 0, 2, None)
bot._progress(remote_mod.WAITING, 0, 2, None)
check("the card says it", "stopped to ask you something" in api.card, api.card)
check("and its own icon",
      api.card.startswith(remote_mod.ICON_NEEDS_YOU), api.card[:20])
# The queue reports its phase every second for as long as it lasts, and every
# edit counts against a rate limit at Telegram's end.
check("and saying it twice costs nothing",
      len(api.sent) + len(api.edits) == 1, f"{api.sent} {api.edits}")

print("\n--- network and DNS error resiliency ---")
class FlakyRemoteApi(Api):
    def __init__(self):
        super().__init__()
        self.say_attempts = 0
        self.edit_attempts = 0
        self.photo_attempts = 0

    def __call__(self, token, method, params, timeout=None):
        if method == "sendMessage":
            self.say_attempts += 1
            if self.say_attempts < 2:
                raise OSError("[Errno 11001] getaddrinfo failed")
        elif method == "editMessageText":
            self.edit_attempts += 1
            if self.edit_attempts < 2:
                raise OSError("WinError 10054 An existing connection was forcibly closed by the remote host")
        elif method == "sendPhoto":
            self.photo_attempts += 1
            if self.photo_attempts < 2:
                raise OSError("[Errno 11001] getaddrinfo failed")
        return super().__call__(token, method, params, timeout)

bot, api, sent, _ = make()
bot.api = FlakyRemoteApi()
msg_id = bot.say("test DNS recovery")
check("say retries on 11001 and succeeds", msg_id == 1 and bot.api.say_attempts == 2)

edited = bot.edit(msg_id, "edited text")
check("edit retries on 10054 and succeeds", edited is True and bot.api.edit_attempts == 2)

photo_id = bot.send_photo(b"fake_png_data", "caption")
check("send_photo retries on 11001 and succeeds", photo_id == 2 and bot.api.photo_attempts == 2)

class DeferredCommandsApi(Api):
    def __init__(self):
        super().__init__()
        self.offline = True
    def __call__(self, token, method, params, timeout=None):
        if method == "setMyCommands":
            if self.offline:
                raise OSError("[Errno 11001] getaddrinfo failed")
        return super().__call__(token, method, params, timeout)

bot, api, sent, _ = make()
bot.api = DeferredCommandsApi()
bot.publish_commands()
check("initial publish_commands fails quietly when offline", bot._commands_published is False and bot.api.published is None)
bot.api.offline = False
bot._offline = True
bot._offline = False
if not bot._commands_published:
    bot.publish_commands()
check("commands published once connection restored", bot._commands_published is True and bot.api.published is not None)

print("\n--- Milestone 1: Dynamic Cancel Task button and /cancel command ---")

# 1. Progress card displays '🛑 Cancel Task' inline button when active/busy
for phase in (remote_mod.SENDING, remote_mod.STARTING, remote_mod.HOLDING,
              remote_mod.COUNTING, remote_mod.WAITING):
    b, a, _, _ = make()
    b._where = "Antigravity"
    b._progress(phase, 0, 1, 5)
    last_markup = (a.edited_markups[-1] if a.edited_markups else a.sent_markups[-1])
    parsed = json.loads(last_markup) if last_markup else {}
    button_text = parsed.get("inline_keyboard", [[{}]])[0][0].get("text", "")
    button_cb = parsed.get("inline_keyboard", [[{}]])[0][0].get("callback_data", "")
    check(f"card has cancel button during {phase}",
          button_cb == "cancel_task" and "Cancel Task" in button_text,
          str(last_markup))

# 2. Progress card removes button on DONE and STOPPED
bot, api, sent, _ = make()
bot._where = "Antigravity"
bot._progress(remote_mod.HOLDING, 0, 1, None)
check("button present while holding",
      api.sent_markups and "cancel_task" in (api.sent_markups[-1] or ""))

bot._progress(remote_mod.DONE, 0, 1, None)
check("button removed on DONE",
      api.edited_markups and api.edited_markups[-1] is None,
      str(api.edited_markups))

# Re-activate and test STOPPED
bot._progress(remote_mod.HOLDING, 0, 1, None)
check("button restored when holding again",
      api.edited_markups and "cancel_task" in (api.edited_markups[-1] or ""))

bot._progress(remote_mod.STOPPED, 0, 1, None)
check("button removed on STOPPED",
      api.edited_markups and api.edited_markups[-1] is None,
      str(api.edited_markups))

# 3. Tapping inline button (callback_query) cancels task and calls canceller
cancelled_hwnds = []
def mock_canceller(h):
    cancelled_hwnds.append(h)
    return True

bot, api, sent, _ = make(canceller=mock_canceller)
bot._where = "Antigravity"
bot.pilot.start(["step 1"], HWND)
check("pilot is running", bot.pilot.running is True)

# User taps cancel button on phone
api.feed_callback(query_id="cq_test_1", data="cancel_task", chat=MINE)
bot._handle(api.updates.pop())
check("canceller was called with target hwnd", cancelled_hwnds == [HWND], str(cancelled_hwnds))
check("autopilot stop signal set", bot.pilot._stop.is_set())
deadline = time.monotonic() + 1
while bot.pilot.running and time.monotonic() < deadline:
    time.sleep(0.01)
check("autopilot thread stopped", bot.pilot.running is False)
check("pending queue is empty", len(bot.pending) == 0)
check("callback query was answered immediately",
      any(c.get("callback_query_id") == "cq_test_1" for c in api.answered_callbacks),
      str(api.answered_callbacks))
card_text = api.messages.get(bot._card, "")
check("card indicates cancelled", "cancelled" in card_text, card_text)
check("card has stopped icon", card_text.startswith(remote_mod.ICON_STOPPED), card_text[:20])
check("cancel button was removed after callback cancellation",
      api.edited_markups and api.edited_markups[-1] is None,
      str(api.edited_markups))

# 4. Sending /cancel command cancels task and calls canceller
cancelled_hwnds.clear()
bot, api, sent, _ = make(canceller=mock_canceller)
bot._where = "Antigravity"
bot.pilot.start(["step 1"], HWND)
check("pilot is running before /cancel", bot.pilot.running is True)

api.feed("/cancel", chat=MINE)
bot._handle(api.updates.pop())
check("/cancel invoked canceller with target hwnd", cancelled_hwnds == [HWND], str(cancelled_hwnds))
check("autopilot stop signal set by /cancel", bot.pilot._stop.is_set())
deadline = time.monotonic() + 1
while bot.pilot.running and time.monotonic() < deadline:
    time.sleep(0.01)
check("autopilot thread stopped by /cancel", bot.pilot.running is False)
check("chat reply confirmed cancellation", any("Task cancelled." in s for s in api.sent), str(api.sent))
card_text = api.messages.get(bot._card, "")
check("card indicates cancelled after /cancel", "cancelled" in card_text, card_text)
check("cancel button was removed after /cancel",
      api.edited_markups and api.edited_markups[-1] is None,
      str(api.edited_markups))

# 5. Unauthorized callback queries are ignored
cancelled_hwnds.clear()
bot, api, sent, _ = make(canceller=mock_canceller)
api.feed_callback(query_id="cq_unauth", data="cancel_task", chat=THEIRS)
bot._handle(api.updates.pop())
check("unauthorized callback does not invoke canceller", cancelled_hwnds == [], str(cancelled_hwnds))
check("unauthorized callback query is not answered", api.answered_callbacks == [], str(api.answered_callbacks))

# 6. Callback query with unknown data is safely acknowledged without cancelling
cancelled_hwnds.clear()
bot, api, sent, _ = make(canceller=mock_canceller)
api.feed_callback(query_id="cq_unknown", data="other_action", chat=MINE)
bot._handle(api.updates.pop())
check("unknown callback data does not invoke canceller", cancelled_hwnds == [], str(cancelled_hwnds))
check("callback query was still acknowledged",
      any(c.get("callback_query_id") == "cq_unknown" for c in api.answered_callbacks),
      str(api.answered_callbacks))

# 7. Drain starts pilot with cancel_button=True
bot, api, sent, _ = make()
bot.pending.append("do something")
bot._drain()
bot.pilot.stop()
check("drain passes cancel button to initial paint",
      api.sent_markups and "cancel_task" in (api.sent_markups[0] or ""),
      str(api.sent_markups))

print("\n--- Milestone 2: /usage command and quota monitoring ---")
# 8. usage command is published in setMyCommands
bot, api, sent, _ = make()
bot.publish_commands()
check("usage command is published to setMyCommands",
      api.published and any(cmd.get("command") == "usage" for cmd in api.published),
      str(api.published))

# 9. /usage command sends formatted usage card
api.feed("/usage", chat=MINE)
bot._handle(api.updates.pop())
check("/usage command sent a reply", len(api.sent) >= 1)
check("/usage reply contains AI Usage & Quota header",
      any("AI Usage &amp; Quota" in s or "AI Usage & Quota" in s for s in api.sent),
      str(api.sent))
check("/usage reply contains Live Quota and Activity",
      any("Live Quota" in s and "Activity" in s for s in api.sent),
      str(api.sent))

print("\n--- Milestone 3: Result text deduplication and timestamp filtering ---")
bot_dedup, api_dedup, _, _ = make()
bot_dedup._prompts = ["Good everything is functional"]
bot_dedup._where = "Antigravity"
raw_new_lines = [
    "0:45",
    "Mă bucur mult că totul funcționează impecabil acum!",
    "🎉",
    "Atât trimiterea albumelor cu mai multe fotografii simultan prin Telegram (cu lipire secvențială și confirmare unică în caseta de chat), cât și monitorizarea cotelor...",
    "Dacă mai apare orice altă nevoie sau vrei să mai optimizăm ceva, sunt aici să te ajut! Spor la lucru!",
    "Mă bucur mult că totul funcționează impecabil acum!",
    "🎉 Atât trimiterea albumelor cu mai multe fotografii simultan prin Telegram (cu lipire secvențială și confirmare unică în caseta de chat), cât și monitorizarea cotelor...",
]
bot_dedup._result(0, raw_new_lines)
card_msg = api_dedup.messages.get(bot_dedup._card, "")
check("timestamp 0:45 is filtered out from card", "0:45" not in card_msg, card_msg)
check("duplicate paragraph is deduplicated", card_msg.count("Mă bucur mult") == 1, card_msg)
check("standalone emoji is merged", "acum! 🎉" in card_msg, card_msg)

print("\n--- Milestone 4: /notes and /notes add commands ---")
bot_notes, api_notes, _, _ = make()
bot_notes._handle({"update_id": 99, "message": {"chat": {"id": MINE}, "text": "/notes add Build autonomous voice orchestrator"}})
check("/notes add replied with confirmation", any("Idea saved" in s for s in api_notes.sent), str(api_notes.sent))
api_notes.sent.clear()
bot_notes._handle({"update_id": 100, "message": {"chat": {"id": MINE}, "text": "/notes"}})
check("/notes lists saved ideas", any("Build autonomous voice" in s for s in api_notes.sent), str(api_notes.sent))

print("\n--- Milestone 5: Telegram voice notes transcription and translation ---")
import io
import wave
import numpy as np

def make_dummy_wave_bytes():
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b'\x00\x00' * 1600)
    return buf.getvalue()

dummy_wave = make_dummy_wave_bytes()

# 1. Voice note is downloaded, decoded, translated, confirmed and queued
called_audio = []
def mock_speech_translator(audio):
    called_audio.append(audio)
    return "Create a new Python script"

bot_voice, api_voice, _, _ = make(
    downloader=lambda _token, _path: dummy_wave,
    speech_translator=mock_speech_translator,
)
bot_voice.api.feed_voice(file_id="voice_123", chat=MINE)
bot_voice._handle(bot_voice.api.updates.pop())

check("speech translator was called with numpy audio array",
      len(called_audio) == 1 and isinstance(called_audio[0], np.ndarray) and len(called_audio[0]) == 1600,
      f"{len(called_audio)}")
check("voice status indicator was sent first",
      any("Listening &amp; translating" in s or "Listening & translating" in s or "Listening" in s for s in api_voice.sent),
      str(api_voice.sent))
check("voice status message was edited with translated prompt",
      any("Create a new Python script" in e and ("Transcribed" in e or "Translated" in e) for e in api_voice.edits),
      str(api_voice.edits))
check("translated prompt was appended to pending queue",
      list(bot_voice.pending) == ["Create a new Python script"],
      str(list(bot_voice.pending)))
check("translated prompt was recorded in _prompts",
      "🎙️ Create a new Python script" in bot_voice._prompts,
      str(bot_voice._prompts))

# 2. Audio message (as opposed to voice) is also handled
called_audio.clear()
bot_audio, api_audio, _, _ = make(
    downloader=lambda _token, _path: dummy_wave,
    speech_translator=mock_speech_translator,
)
bot_audio.api.feed_voice(file_id="audio_456", chat=MINE, is_audio=True)
bot_audio._handle(bot_audio.api.updates.pop())
check("audio format calls speech translator",
      len(called_audio) == 1,
      str(len(called_audio)))
check("audio prompt was queued",
      list(bot_audio.pending) == ["Create a new Python script"],
      str(list(bot_audio.pending)))

# 3. Empty or unrecognized speech informs user without queueing
bot_empty, api_empty, _, _ = make(
    downloader=lambda _token, _path: dummy_wave,
    speech_translator=lambda _a: "",
)
bot_empty.api.feed_voice(file_id="voice_silent", chat=MINE)
bot_empty._handle(bot_empty.api.updates.pop())
check("empty speech does not queue any prompt",
      len(bot_empty.pending) == 0,
      str(list(bot_empty.pending)))
check("user notified when speech not recognized",
      any("Could not recognize any speech" in e for e in api_empty.edits) or any("Could not recognize any speech" in s for s in api_empty.sent),
      f"edits: {api_empty.edits}, sent: {api_empty.sent}")

# 4. Downloader failure is handled gracefully
def raising_downloader(_token, _path):
    raise RuntimeError("Download timeout")

bot_fail, api_fail, _, _ = make(
    downloader=raising_downloader,
    speech_translator=mock_speech_translator,
)
bot_fail.api.feed_voice(file_id="voice_bad", chat=MINE)
bot_fail._handle(bot_fail.api.updates.pop())
check("download failure notifies user",
      any("could not be processed" in s for s in api_fail.sent),
      str(api_fail.sent))
check("download failure does not queue",
      len(bot_fail.pending) == 0,
      str(list(bot_fail.pending)))

# 5. Stranger sending voice note is silently ignored
called_audio.clear()
bot_stranger, api_stranger, _, _ = make(
    downloader=lambda _token, _path: dummy_wave,
    speech_translator=mock_speech_translator,
)
bot_stranger.api.feed_voice(file_id="voice_stranger", chat=THEIRS)
bot_stranger._handle(bot_stranger.api.updates.pop())
check("stranger voice is ignored in silence",
      len(api_stranger.sent) == 0 and len(called_audio) == 0,
      f"sent: {api_stranger.sent}, called: {len(called_audio)}")

sys.exit(report.finish())
