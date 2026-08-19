"""What the bot sends back, and whether Telegram would take it.

Every message goes out with parse_mode=HTML, which means Telegram parses it
before showing it and rejects the whole message if the markup does not add up.
A window called `main.py <2>` or a project called `Reports & Figures` is enough
to do that - and the failure is silence on the phone, at the exact moment you
asked what was going on.

So this suite reads every message on its way out and refuses anything Telegram
would have rejected. The rest of it is the other kind of silence: a message the
bot has nothing to say about must still be answered, because saying nothing
looks exactly like having queued it.
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

# What Telegram allows in HTML mode, near enough for this purpose.
TAGS = ("b", "strong", "i", "em", "u", "s", "code", "pre")
ENTITIES = ("&amp;", "&lt;", "&gt;", "&quot;")


def rejected_by_telegram(text):
    """Why Telegram would refuse this message, or None if it would take it."""
    stripped = text
    for tag in TAGS:
        stripped = stripped.replace(f"<{tag}>", "").replace(f"</{tag}>", "")
    if "<" in stripped or ">" in stripped:
        where = stripped[max(0, stripped.find("<") - 20):stripped.find("<") + 30]
        return f"unparsable tag near {where!r}"
    rest = stripped
    for entity in ENTITIES:
        rest = rest.replace(entity, "")
    if "&" in rest:
        where = rest[max(0, rest.find("&") - 20):rest.find("&") + 30]
        return f"bare ampersand near {where!r}"
    return None


class Api:
    def __init__(self):
        self.updates = []
        self.sent = []
        self.edits = []
        self.messages = {}
        self.rejected = []      # everything Telegram would have thrown out

    @property
    def all_text(self):
        return self.sent + self.edits

    def feed(self, chat=MINE, **message):
        message.setdefault("chat", {"id": chat})
        self.updates.append({"update_id": len(self.updates) + 1,
                             "message": message})

    def _check(self, text):
        why = rejected_by_telegram(text)
        if why:
            self.rejected.append((why, text[:120]))

    def __call__(self, token, method, params, timeout=None):
        if method == "getUpdates":
            if params.get("timeout") == 0:
                return []
            out, self.updates = self.updates, []
            return out
        if method == "setMyCommands":
            return True
        if method == "sendMessage":
            self._check(params["text"])
            self.sent.append(params["text"])
            self.messages[len(self.sent)] = params["text"]
            return {"message_id": len(self.sent)}
        if method == "editMessageText":
            self._check(params["text"])
            self.edits.append(params["text"])
            self.messages[params["message_id"]] = params["text"]
            return {}
        raise AssertionError(f"unexpected method {method}")


def make(chat_id=MINE, translate=None):
    tmp = Path(tempfile.mkdtemp(prefix="relay-replies-"))
    path = tmp / "telegram.json"
    path.write_text(json.dumps({"token": "t", "chat_id": chat_id}), encoding="utf-8")
    api = Api()
    sent = []
    bot = Remote(
        settings={"token": "t", "chat_id": chat_id, "path": path},
        send=lambda text, hwnd: (sent.append(text) or True),
        target_getter=lambda: HWND,
        log=lambda *_: None,
        api=api,
        is_window=lambda _h: True,
        translate=translate,
    )
    bot.pilot.read_state = lambda _h: agent.IDLE
    bot.pilot.is_window = lambda _h: True
    bot.pilot.focus = lambda _h: True
    bot.pilot.place_caret = lambda _h, _p: True
    bot.pilot.poll_seconds = 0.02
    return bot, api, sent


agent.profile_for = lambda hwnd, profiles=None: ({"name": "fake"} if hwnd else None)
agent.state = lambda hwnd, profile=None, seen=None: agent.IDLE
remote_mod.window_title = lambda hwnd: "Some Window"


def handle(bot, api):
    """Deliver everything fed to the api, the way the poll loop would."""
    for update in api.updates[:]:
        bot._handle(update)
    api.updates = []


print("\n--- a photo is answered, not swallowed ---")
# Sent one from the phone and nothing came back at all - no reply, and not a
# line in the log. Silence is the one answer that cannot be told apart from
# having been queued, so you wait for a result that was never coming.
bot, api, sent = make()
api.feed(photo=[{"file_id": "abc", "width": 90, "height": 90}])
handle(bot, api)
check("it says something", api.sent != [], str(api.sent))
check("and it names what arrived",
      any("photo" in s.lower() for s in api.sent), str(api.sent))
check("nothing was queued", len(bot.pending) == 0, str(list(bot.pending)))


print("\n--- and so is everything else that is not text ---")
for field, value, word in [
        ("voice", {"file_id": "v", "duration": 3}, "voice"),
        ("document", {"file_id": "d", "file_name": "notes.txt"}, "file"),
        ("sticker", {"file_id": "s"}, "sticker"),
        ("video", {"file_id": "vid"}, "video"),
        ("audio", {"file_id": "a"}, "audio"),
        ("location", {"latitude": 1.0, "longitude": 2.0}, "location")]:
    bot, api, sent = make()
    api.feed(**{field: value})
    handle(bot, api)
    check(f"a {field} is answered", api.sent != [], field)
    check(f"and called a {word}",
          any(word in s.lower() for s in api.sent), str(api.sent))


print("\n--- a photo with a caption is not run as a prompt ---")
# The caption is not what you meant to send, and sending it alone would put a
# sentence about a picture into an agent that cannot see the picture.
bot, api, sent = make()
api.feed(photo=[{"file_id": "abc"}], caption="uite eroarea asta")
handle(bot, api)
check("nothing typed", sent == [], str(sent))
check("nothing queued", len(bot.pending) == 0, str(list(bot.pending)))
check("but you are told", api.sent != [], str(api.sent))


print("\n--- a stranger sending a photo hears nothing ---")
bot, api, sent = make()
api.feed(chat=THEIRS, photo=[{"file_id": "abc"}])
handle(bot, api)
check("silence", api.sent == [], str(api.sent))


print("\n--- and a photo cannot claim an unpaired bot ---")
# Pairing hands a chat the keyboard. It takes a written message.
bot, api, sent = make(chat_id=None)
api.feed(chat=THEIRS, photo=[{"file_id": "abc"}])
handle(bot, api)
check("not paired", bot.chat_id is None, str(bot.chat_id))
check("and nothing said", api.sent == [], str(api.sent))


print("\n--- a window whose name is markup ---")
# Two titles that turn up in real life: a shell showing a placeholder in angle
# brackets, and any project with an ampersand in its name.
for title in ("main.py <2> - opencode", "Reports & Figures - Claude",
              "<untitled>", "a & b <c> d"):
    remote_mod.window_title = lambda hwnd, t=title: t
    agent.recognised_windows = lambda t=title: [(HWND, t, {"name": "fake"})]
    bot, api, sent = make()
    api.feed(text="/status")
    api.feed(text="/target")
    api.feed(text="/target 1")
    handle(bot, api)
    check(f"{title[:28]!r} is reported", len(api.sent) == 3, str(len(api.sent)))
    check("and Telegram would take every word of it",
          api.rejected == [], str(api.rejected[:2]))

remote_mod.window_title = lambda hwnd: "Some Window"


print("\n--- a prompt full of angle brackets ---")
# Every built-in template has <blanks> in it, and they reach the card.
bot, api, sent = make()
api.feed(text="=Review <area> and report <what you find> & why")
handle(bot, api)
check("the card went out", api.sent != [], str(api.sent))
check("Telegram would take it", api.rejected == [], str(api.rejected[:2]))
check("and the brackets survive as brackets",
      "&lt;area&gt;" in "".join(api.all_text), "".join(api.all_text)[:200])


print("\n--- an error message that contains markup ---")
# The worst moment to lose a message is the one where something already went
# wrong, and exception text is full of <class ...> and paths.
def explode(text):
    raise RuntimeError("no handler for <class 'dict'> & no fallback")


bot, api, sent = make(translate=explode)
api.feed(text="ceva de tradus")
handle(bot, api)
check("you are told it failed", any("ranslat" in s for s in api.sent), str(api.sent))
check("and Telegram would take that too", api.rejected == [], str(api.rejected[:2]))
check("the Romanian is queued rather than lost",
      list(bot.pending) == ["ceva de tradus"], str(list(bot.pending)))


print("\n--- what a window said, when what it said was markup ---")
bot, api, sent = make()
api.feed(text="=do a thing")
handle(bot, api)
bot._prompts = ["do a thing"]
bot._result(0, ["Traceback (most recent call last):",
                "  File <stdin>, line 1, in <module>",
                "TypeError: a & b"])
check("the failure is lifted out",
      any("Traceback" in t for t in api.all_text), str(api.all_text[-1:])[:160])
check("and Telegram would take the card", api.rejected == [], str(api.rejected[:2]))


print("\n--- every reply the bot can give ---")
# The whole command surface in one pass, checked as it goes out.
agent.recognised_windows = lambda: [(HWND, "Some Window", {"name": "fake"})]
bot, api, sent = make()
for message in ["/start", "/help", "/status", "/target", "/target 1", "/target 0",
                "/target 99", "/keys", "/keys off", "/stop", "/restart",
                "/nonsense", "/", "salut"]:
    api.feed(text=message)
handle(bot, api)
check("each one was answered", len(api.sent) >= 13, str(len(api.sent)))
check("and Telegram would take all of them", api.rejected == [],
      str(api.rejected[:3]))


print("\n--- the help text lists what there is, not what there was ---")
listed = [s for s in api.sent if "Write in Romanian" in s]
check("help came back", listed != [], str(api.sent[:1]))
if listed:
    for name, _what in remote_mod.COMMANDS:
        check(f"/{name} is in it", f"/{name}" in listed[0], listed[0][:80])


print("\n--- an empty message is not a prompt ---")
bot, api, sent = make()
api.feed(text="   ")
api.feed(text="")
handle(bot, api)
check("nothing queued", len(bot.pending) == 0, str(list(bot.pending)))


print("\n--- a message with no chat at all is ignored ---")
bot, api, sent = make()
bot._handle({"update_id": 1, "message": {"text": "hello"}})
bot._handle({"update_id": 2})
check("no crash, nothing queued", len(bot.pending) == 0, str(list(bot.pending)))
check("and nothing said", api.sent == [], str(api.sent))


print("\n--- an edited message is treated as a message ---")
bot, api, sent = make()
bot._handle({"update_id": 1,
             "edited_message": {"chat": {"id": MINE}, "text": "=fixed typo"}})
check("queued", list(bot.pending) == ["fixed typo"], str(list(bot.pending)))


print("\n--- and one sent before Relay started is not acted on ---")
bot, api, sent = make()
bot._started = time.time()
bot._handle({"update_id": 1, "message": {"chat": {"id": MINE},
                                         "text": "/restart",
                                         "date": time.time() - 600}})
check("ignored", api.sent == [], str(api.sent))

print("\n--- one message it cannot deal with does not deafen the link ---")
# It used to. Anything raised while handling a message was caught by the same
# guard as a network failure, which reported "not reachable" about a laptop
# sitting right there and then doubled the wait between polls, to a minute.
bot, api, sent = make()
polls = {"n": 0}
real_poll = bot._poll


def counted_poll():
    polls["n"] += 1
    if polls["n"] >= 4:
        bot._stop.set()
    return real_poll()


bot._poll = counted_poll
real_handle = bot._handle


def sometimes_explodes(update):
    text = ((update.get("message") or {}).get("text") or "")
    if text == "bad":
        raise RuntimeError("something unforeseen")
    return real_handle(update)


bot._handle = sometimes_explodes
api.feed(text="bad")
api.feed(text="=good one")
bot._run()
check("it kept polling", polls["n"] >= 4, str(polls["n"]))
check("the rest of the batch was still dealt with",
      any("good one" in t for t in api.all_text), str(api.all_text))
check("and you were told something went wrong",
      any("went wrong" in s for s in api.sent), str(api.sent))


print("\n--- and a queue that will not start does not end the thread ---")
# _drain sat outside the guard entirely, so anything it raised ended the poll
# loop - and a phone link that has stopped listening looks exactly like one
# with nothing to say.
bot, api, sent = make()
polls = {"n": 0}
real_poll = bot._poll


def counted_poll_2():
    polls["n"] += 1
    if polls["n"] >= 4:
        bot._stop.set()
    return real_poll()


bot._poll = counted_poll_2
bot.pilot.start = lambda steps, hwnd: (_ for _ in ()).throw(
    RuntimeError("the window went away"))
api.feed(text="=do something")
bot._run()
check("it kept polling", polls["n"] >= 4, str(polls["n"]))
check("the queue was emptied rather than retried for ever",
      len(bot.pending) == 0, str(list(bot.pending)))
check("and the card says so",
      any("could not start" in t for t in api.all_text), str(api.all_text[-1:])[:150])
check("Telegram would take it", api.rejected == [], str(api.rejected[:2]))

print("\n--- one card, however many threads are writing it ---")
# The queue reports its progress from its own thread while messages arrive on
# the poll thread, and both draw the same card. Two of them finding it empty at
# the same moment is two cards on the phone - and from then on the one being
# edited is not the one you are looking at.
import threading as _threading  # noqa: E402
import time as _time            # noqa: E402

bot, api, sent = make()
slow = {"n": 0}
real_call = api.__call__


def slow_api(token, method, params, timeout=None):
    if method == "sendMessage":
        slow["n"] += 1
        _time.sleep(0.05)      # the network, near enough
    return real_call(token, method, params, timeout)


bot.api = slow_api
bot._new_card()
painters = [_threading.Thread(target=bot._paint,
                              kwargs={"icon": "⏳", "head": f"thread {i}"})
            for i in range(4)]
for painter in painters:
    painter.start()
for painter in painters:
    painter.join(timeout=5)
check("one message was created, not four", slow["n"] == 1, str(slow["n"]))
check("and the rest were edits", len(api.edits) >= 1, str(len(api.edits)))
check("all of them to the same message", bot._card == 1, str(bot._card))

sys.exit(report.finish())
