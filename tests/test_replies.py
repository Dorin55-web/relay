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
        self.photos = []        # every sendPhoto, as (params, caption)
        self.published = None   # the command list it registered
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
            self.published = json.loads(params["commands"])
            return True
        if method == "sendPhoto":
            # A caption is parsed as HTML exactly like a message, and a
            # caption Telegram refuses does not arrive without its picture -
            # it takes the picture with it.
            self._check(params.get("caption", ""))
            self.photos.append(params)
            return {"message_id": 900 + len(self.photos)}
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


def make(chat_id=MINE, translate=None, capture=None, target=HWND,
         watched=True):
    tmp = Path(tempfile.mkdtemp(prefix="relay-replies-"))
    path = tmp / "telegram.json"
    path.write_text(json.dumps({"token": "t", "chat_id": chat_id}), encoding="utf-8")
    api = Api()
    sent = []
    bot = Remote(
        settings={"token": "t", "chat_id": chat_id, "path": path},
        send=lambda text, hwnd: (sent.append(text) or True),
        target_getter=lambda: target,
        log=lambda *_: None,
        api=api,
        is_window=lambda _h: True,
        keeper_watching=lambda: watched,
        translate=translate,
        # Standing in for the hop onto the GUI thread. What it hands back is
        # the shape the real one does: the bytes, or why there are none.
        capture=capture,
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
for message in ["/start", "/help", "/status", "/more", "/shot",
                "/target", "/target 1",
                "/target 0", "/target 99", "/keys", "/keys off", "/stop",
                "/restart", "/nonsense", "/", "salut"]:
    api.feed(text=message)
handle(bot, api)
check("each one was answered", len(api.sent) >= 16, str(len(api.sent)))
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

print("\n--- a line too long for the card is cut where a word ends ---")
# It was cut at exactly 140 characters, mid-word: the card read "nici un
# microfon Blue" and there was no way to tell that from the agent having
# written those words and stopped.
sentence = ("Suitele verifica logica: 26 din 26 trec. Nu demonstreaza citirea "
            "reala prin UI Automation a unei ferestre Antigravity, nici un "
            "microfon Bluetooth adevarat, nici reteaua Telegram.")
cut = remote_mod._shorten(sentence, 100)
check("it is shortened", len(cut) <= 100, str(len(cut)))
check("and never mid-word", sentence.startswith(cut[:-1].rstrip()), repr(cut))
check("and says so", cut.endswith("\u2026"), repr(cut[-12:]))
check("a line that fits is left exactly as it was",
      remote_mod._shorten("all done", 100) == "all done")
check("shortening twice changes nothing the second time",
      remote_mod._shorten(cut, 100) == cut, repr(cut))
check("a line with no spaces is still cut",
      len(remote_mod._shorten("x" * 300, 40)) <= 40)


print("\n--- and the card shows it ---")
bot, api, sent = make()
bot._prompts = ["do the thing"]
bot._result(0, ["do the thing", sentence + " " + sentence])
card = api.all_text[-1]
check("the ellipsis reaches the phone", "\u2026" in card, card[-80:])
check("no word is broken in half",
      all(len(ln) <= remote_mod.LINE_CHARS for ln in card.splitlines()),
      str(max(len(ln) for ln in card.splitlines())))
check("Telegram would still take it", api.rejected == [], str(api.rejected[:2]))


print("\n--- a whole ordinary sentence now fits without being cut ---")
# The reason it was being cut at all: at 140 characters an ordinary sentence
# did not fit, so nearly every line on the card ended part way through one.
plain = ("Erai in setarile Claude cand a pornit lantul, asa ca a tinut, a "
         "asteptat noua secunde pana ai inchis dialogul, si abia dupa ce "
         "caseta a redevenit Prompt a trimis pasul.")
check("this one is longer than the old limit", len(plain) > 140, str(len(plain)))
check("and is not shortened now",
      remote_mod._shorten(plain, remote_mod.LINE_CHARS) == plain, plain)


def unescaped(part):
    """One /more message with its heading off and its escaping undone.

    `&amp;` last of the three: undone first, a window that had written the
    characters `&lt;` would come back as `<` and the comparison would pass on
    text that had been mangled.
    """
    body = part.split("\n\n", 1)[-1]
    return (body.replace("&lt;", "<").replace("&gt;", ">")
                .replace("&amp;", "&"))


print("\n--- /more, before anything has finished ---")
# The same silence as everywhere else in this file: a command that answers
# nothing cannot be told from one that was never delivered.
bot, api, sent = make()
api.feed(text="/more")
handle(bot, api)
check("it answers", api.sent != [], str(api.sent))
check("and says nothing has come back yet",
      "Nothing has finished" in api.sent[0], api.sent[0][:90])


print("\n--- and when the step itself produced nothing ---")
# Different from the above, and worth different words: a step did run, and the
# window had nothing new in it afterwards.
bot, api, sent = make()
bot._result(0, [])
before = len(api.sent)
api.feed(text="/more")
handle(bot, api)
check("it says the card was all there was",
      "nothing new" in api.sent[before].lower(), api.sent[before][:90])


print("\n--- /more sends the whole of the last result ---")
# The card keeps five lines, each cut to a sentence, and drops the rest the
# moment it is drawn. Unless something holds on to it, there is nothing left
# for this command to send.
bot, api, sent = make()
bot._prompts = ["do the thing"]
long_line = "a line far longer than the card would keep: " + "detail " * 40
bot._result(0, ["first, the part the card never shows", long_line,
                "a name with a & and a <tag> in it",
                "and the verdict at the end"])
card_id = len(api.sent)
card = api.messages[card_id]
before = len(api.sent)
api.feed(text="/more")
handle(bot, api)
more = "\n".join(api.sent[before:])
check("the card had shortened that line", long_line not in card, card[-120:])
check("but /more sends it whole", long_line in more, more[:120])
check("including the line the card had no room for",
      "first, the part the card never shows" in more, more[:120])
check("the ampersand and the tag survive as themselves",
      "a name with a &amp; and a &lt;tag&gt; in it" in more, more[-160:])
check("Telegram would take it", api.rejected == [], str(api.rejected[:2]))
check("and the card itself is not touched",
      api.messages[card_id] == card, api.messages[card_id][:80])


print("\n--- the heading names the window, markup in the name and all ---")
# The heading is sent as HTML alongside text that is already escaped, so
# nothing escapes it on the way out but the heading itself.
bot, api, sent = make()
bot._where = "main.py <2> - Reports & Figures"
bot._result(0, ["something worth reading"])
before = len(api.sent)
api.feed(text="/more")
handle(bot, api)
check("the window is named", "main.py &lt;2&gt;" in api.sent[before],
      api.sent[before][:120])
check("and Telegram would take that too", api.rejected == [],
      str(api.rejected[:2]))


print("\n--- a result too long for one message goes out as several, in order ---")
# Telegram takes about 4096 characters. Six thousand in one message is not a
# truncated reply, it is no reply at all.
bot, api, sent = make()
lines = [f"line {n:02d} " + "y" * 300 for n in range(20)]
bot._result(0, lines)
before = len(api.sent)
api.feed(text="/more")
handle(bot, api)
parts = api.sent[before:]
check("it took more than one message", len(parts) > 1, str(len(parts)))
check("none of them is longer than Telegram takes",
      all(len(p) <= remote_mod.MESSAGE_CHARS for p in parts),
      str(max(len(p) for p in parts)))
check("each says which of how many it is",
      all(f"({n}/{len(parts)})" in p for n, p in enumerate(parts, start=1)),
      parts[0][:70])
check("they read in the order the window wrote them",
      "line 00" in parts[0] and "line 19" in parts[-1], parts[-1][:60])
check("and no line fell down a seam",
      all(f"line {n:02d}" in "".join(parts) for n in range(20)), str(len(parts)))
check("Telegram would take every one", api.rejected == [], str(api.rejected[:2]))


print("\n--- and a seam never lands inside an entity ---")
# Escaping and then cutting to length is the obvious order and the wrong one:
# `&amp;` is five characters, and a cut inside it leaves a bare ampersand -
# the whole message refused, in the middle of the reply you asked to see. Cut
# the plain text and escape each piece whole, and it cannot happen.
bot, api, sent = make()
dense = "<x> & " * 500          # nothing in it survives escaping unchanged
bot._result(0, [dense])
before = len(api.sent)
api.feed(text="/more")
handle(bot, api)
parts = api.sent[before:]
check("this one took several messages too", len(parts) > 1, str(len(parts)))
check("Telegram would take every one of them", api.rejected == [],
      str(api.rejected[:2]))
rebuilt = "".join(unescaped(p) for p in parts).replace("\n", "")
check("and nothing was lost or repeated where they join",
      rebuilt == dense, f"{len(rebuilt)} characters against {len(dense)}")


print("\n--- a result too big to hold is capped, and says it was ---")
# One step can put a whole build log on screen. What /more keeps is held until
# the next step replaces it, and forty messages is not an answer anybody can
# read on a phone.
bot, api, sent = make()
bot._result(0, [f"line {n:04d} " + "z" * 200 for n in range(300)])
check("only what fits is kept",
      sum(len(ln) for ln in bot._full) <= remote_mod.MORE_CHARS,
      str(sum(len(ln) for ln in bot._full)))
check("and the rest is counted rather than forgotten", bot._dropped > 0,
      str(bot._dropped))
before = len(api.sent)
api.feed(text="/more")
handle(bot, api)
parts = api.sent[before:]
check("the phone is told what it is not getting",
      "still in the window" in parts[0], parts[0][:170])
check("what it does get is the end, where the card was looking",
      "line 0299" in "".join(parts), parts[-1][-60:])
check("in a handful of messages, not forty", len(parts) <= 5, str(len(parts)))
check("Telegram would take those as well", api.rejected == [],
      str(api.rejected[:2]))


print("\n--- and it holds one step's worth, which is the most recent ---")
# Replaced every time a step finishes, so a chain of forty leaves forty times
# nothing behind - and /more is about the result you are looking at rather
# than one from an hour ago.
bot, api, sent = make()
bot._result(0, ["the first step said this"])
bot._result(1, ["the second step said something else"])
before = len(api.sent)
api.feed(text="/more")
handle(bot, api)
more = "\n".join(api.sent[before:])
check("the newer result is what comes back",
      "the second step said something else" in more, more[:120])
check("and the older one is not held on to",
      "the first step said this" not in more, more[:120])


print("\n--- and nothing offers a command that only some of the file knows ---")
# COMMANDS is what setMyCommands publishes and what /help prints. A command
# added anywhere else works and is offered by nothing - which is what happened
# to the help text before it was built from this same tuple.
check("/more is in COMMANDS",
      any(name == "more" for name, _what in remote_mod.COMMANDS),
      str([name for name, _what in remote_mod.COMMANDS]))
bot, api, sent = make()
api.feed(text="/nonsense")
handle(bot, api)
check("and the answer to one it does not know lists it too",
      "/more" in api.sent[-1], api.sent[-1])
check("along with every other one there is",
      all(f"/{name}" in api.sent[-1] for name, _what in remote_mod.COMMANDS),
      api.sent[-1])

print("\n--- a picture of the window, when you ask for one ---")
# The card after a step is a diff of what the window said, read through a
# profile. This is the window itself, which is the answer to "did that work"
# for the ones whose profiles read them poorly.
PICTURE = b"\x89PNG\r\n\x1a\n" + b"not really a window, but bytes are bytes" * 8

asked = []
bot, api, sent = make(capture=lambda hwnd: (asked.append(hwnd) or (PICTURE, None)))
remote_mod.foreground_window = lambda: HWND
api.feed(text="/shot")
handle(bot, api)
check("a photo went out", len(api.photos) == 1, str(len(api.photos)))
check("of the window the queue would write into", asked == [HWND], str(asked))
if api.photos:
    photo = api.photos[0]
    check("as a file rather than a line of text",
          isinstance(photo["photo"], tuple), str(type(photo["photo"])))
    check("carrying every byte it was given",
          photo["photo"][1] == PICTURE, str(len(photo["photo"][1])))
    check("to the paired chat and nowhere else",
          photo["chat_id"] == MINE, str(photo["chat_id"]))
    check("with a caption saying what you are looking at",
          "Some Window" in photo["caption"], photo["caption"])
check("nothing was queued to be typed", not bot.pending and sent == [],
      f"{list(bot.pending)} {sent}")
check("and Telegram would take the caption", api.rejected == [],
      str(api.rejected[:2]))


print("\n--- and a window whose name is markup is still safe under one ---")
# The same failure as a message, with more at stake: a caption Telegram
# refuses does not arrive without its picture, it takes the picture with it.
for title in ("main.py <2> - opencode", "Reports & Figures - Claude"):
    remote_mod.window_title = lambda hwnd, t=title: t
    bot, api, sent = make(capture=lambda hwnd: (PICTURE, None))
    api.feed(text="/shot")
    handle(bot, api)
    check(f"{title[:24]!r} is captioned", len(api.photos) == 1, str(api.photos))
    check("and Telegram would take it", api.rejected == [], str(api.rejected[:2]))

remote_mod.window_title = lambda hwnd: "Some Window"


print("\n--- it is never sent unless you ask ---")
# An automatic screenshot is a few hundred kilobytes through Telegram every
# step, for something usually not looked at.
bot, api, sent = make(capture=lambda hwnd: (PICTURE, None))
api.feed(text="=do a thing")
handle(bot, api)
bot._prompts = ["do a thing"]
bot._result(0, ["all three tests pass"])
bot._progress(remote_mod.DONE, 0, 1, None)
check("a whole step comes and goes with no photo", api.photos == [],
      str(len(api.photos)))
check("the card still arrived", "done" in api.all_text[-1], api.all_text[-1][:60])


print("\n--- and when there is no picture to be had, it says why ---")
# Three ways for it to come to nothing, and silence would be the worst answer
# to any of them: from another room there is no telling it from a slow upload.
for why, expect in [
        ("it is minimised, so there is nothing on screen to see", "minimised"),
        ("that window has closed", "closed"),
        ("the window thread did not answer within 8 seconds", "did not answer")]:
    bot, api, sent = make(capture=lambda hwnd, w=why: (None, w))
    api.feed(text="/shot")
    handle(bot, api)
    check(f"{expect!r} is passed on", any(expect in s for s in api.sent),
          str(api.sent))
    check("and no photo pretends otherwise", api.photos == [], str(api.photos))

bot, api, sent = make(capture=lambda hwnd: (PICTURE, None), target=None)
api.feed(text="/shot")
handle(bot, api)
check("no target at all is answered too",
      any("Nothing to photograph" in s for s in api.sent), str(api.sent))
check("with no photo", api.photos == [], str(api.photos))

# --no-ui: there is no orb, so there is no thread allowed to grab anything.
bot, api, sent = make()
api.feed(text="/shot")
handle(bot, api)
check("and so is a Relay with no window thread",
      any("without the window thread" in s for s in api.sent), str(api.sent))


print("\n--- a window that was behind something says so ---")
# The whole desktop is what gets grabbed - a GPU-composited window hands back
# a black device context - so anything on top of it is in the picture. Without
# a word about that, the photo reads as the wrong window.
remote_mod.foreground_window = lambda: 777
bot, api, sent = make(capture=lambda hwnd: (PICTURE, None))
api.feed(text="/shot")
handle(bot, api)
check("the caption warns you",
      api.photos and "not the window in front" in api.photos[0]["caption"],
      str(api.photos[:1])[:120])

remote_mod.foreground_window = lambda: HWND
bot, api, sent = make(capture=lambda hwnd: (PICTURE, None))
api.feed(text="/shot")
handle(bot, api)
check("and does not when it was in front",
      api.photos and "not the window in front" not in api.photos[0]["caption"],
      str(api.photos[:1])[:120])


print("\n--- one list of commands, and /shot is on it ---")
# Two copies of this list had already drifted - the help text was offering
# three commands when there were six - so a new one has to reach the menu
# Telegram shows, the help text, and the answer to a command that is not there.
bot, api, sent = make()
bot.publish_commands()
check("the menu Telegram publishes has it",
      any(c["command"] == "shot" for c in api.published or []),
      str(api.published))
api.feed(text="/help")
api.feed(text="/nonsense")
handle(bot, api)
check("the help text has it", any("/shot" in s for s in api.sent), str(api.sent))
check("and so does the answer to one it does not know",
      any("only know" in s and "/shot" in s for s in api.sent), str(api.sent))


print("\n--- the bytes leave as a form with a file in it ---")
# sendPhoto is not sendMessage: Telegram takes a picture only as
# multipart/form-data, which is the one shape urlencode cannot make. Anything
# else must still go the way it always did.
type_a, body_a = remote_mod._multipart(
    {"chat_id": 5, "caption": "sesiune terminata", "photo": ("window.png", PICTURE)})
check("the type names a boundary", "boundary=" in type_a, type_a)
boundary = type_a.split("boundary=")[1]
check("with one part in it for each field given",
      body_a.count(f"--{boundary}\r\n".encode()) == 3,
      str(body_a.count(f"--{boundary}\r\n".encode())))
check("the picture is in there whole", PICTURE in body_a, str(len(body_a)))
check("under a name Telegram can read",
      b'name="photo"; filename="window.png"' in body_a, str(body_a[:200]))
check("and the plain fields with it",
      b"sesiune terminata" in body_a and b"chat_id" in body_a, str(body_a[:200]))
check("it ends the way a form must", body_a.endswith(f"--{boundary}--\r\n".encode()),
      str(body_a[-40:]))

# A fixed marker eventually turns up inside a screenshot, and a body whose
# boundary appears in its own payload is a body that ends early.
type_b, _ = remote_mod._multipart({"photo": ("window.png", PICTURE)})
check("and no two calls share a boundary", type_a != type_b, f"{type_a} {type_b}")

diacritics = remote_mod._multipart({"caption": "ferestra a raspuns și a stat"})[1]
check("a caption is sent as UTF-8, not mangled",
      "ș".encode("utf-8") in diacritics, str(diacritics))


print("\n--- and the same one call still sends everything else ---")
# The photo had to fit the api callable already there, injected as
# (token, method, params, timeout). A second callable beside it would be a
# second thing every caller and every test has to know about.
requests = []


class Answered:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps({"ok": True, "result": {"message_id": 1}}).encode()


def fake_urlopen(request, timeout=None):
    requests.append(request)
    return Answered()


import urllib.request as urllib_request  # noqa: E402

real_urlopen = urllib_request.urlopen
urllib_request.urlopen = fake_urlopen
try:
    remote_mod.call_api("tok", "sendMessage", {"chat_id": 1, "text": "hello"})
    remote_mod.call_api("tok", "sendPhoto",
                        {"chat_id": 1, "photo": ("window.png", PICTURE)})
finally:
    urllib_request.urlopen = real_urlopen

check("both calls went out", len(requests) == 2, str(len(requests)))
if len(requests) == 2:
    plain, photo = requests
    check("a message is still a plain form",
          (plain.get_header("Content-type") or "").find("multipart") == -1,
          str(plain.get_header("Content-type")))
    check("with its text urlencoded as before", b"text=hello" in plain.data,
          str(plain.data))
    check("a photo is multipart",
          "multipart/form-data" in (photo.get_header("Content-type") or ""),
          str(photo.get_header("Content-type")))
    check("carrying the picture itself", PICTURE in photo.data, str(len(photo.data)))
    check("and each to its own Telegram method",
          plain.full_url.endswith("/sendMessage")
          and photo.full_url.endswith("/sendPhoto"),
          f"{plain.full_url[-20:]} {photo.full_url[-20:]}")

print("\n--- keeping a prompt for an hour that has not come ---")
# The case: the agent has run out of its allowance and will not take another
# word until the small hours, and you are not going to be awake for that.
from datetime import datetime, timedelta  # noqa: E402

from relay import later as later_mod      # noqa: E402

later_mod.save([])
bot, api, sent = make()
api.feed(text="/at 05:00 read the log and tell me why")
handle(bot, api)
check("it says it was kept", any("Kept for" in s for s in api.sent), str(api.sent))
check("naming the hour", any("05:00" in s for s in api.sent), str(api.sent))
check("nothing was queued now", len(bot.pending) == 0, str(list(bot.pending)))
check("nothing was typed", sent == [], str(sent))
check("and it is on disk", len(later_mod.load()) == 1, str(later_mod.load()))
check("Telegram would take that", api.rejected == [], str(api.rejected[:2]))


print("\n--- and /at on its own says what is waiting ---")
api.feed(text="/at")
handle(bot, api)
check("it lists it", any("read the log" in s for s in api.sent), str(api.sent[-1:]))
check("with how far off it is",
      any("tomorrow at 05:00" in s or "today at 05:00" in s for s in api.sent),
      str(api.sent[-1:]))


print("\n--- a time it cannot read is refused, not guessed at ---")
later_mod.save([])
bot, api, sent = make()
for bad in ["/at 25:00 do the thing", "/at half-five do the thing",
            "/at banana do the thing"]:
    api.feed(text=bad)
handle(bot, api)
check("each one answered", len(api.sent) == 3, str(len(api.sent)))
check("saying what it takes",
      all("05:00" in s for s in api.sent), str(api.sent[:1]))
check("and nothing was kept", later_mod.load() == [], str(later_mod.load()))


print("\n--- an hour with no prompt after it ---")
later_mod.save([])
bot, api, sent = make()
api.feed(text="/at 05:00")
handle(bot, api)
check("it says the prompt is missing",
      any("no prompt" in s for s in api.sent), str(api.sent))
check("and keeps nothing", later_mod.load() == [], str(later_mod.load()))


print("\n--- what is kept is the English, translated when you wrote it ---")
# So there is a whole night in which to read it back and drop it, rather than
# finding out at five what the model made of your sentence.
later_mod.save([])
bot, api, sent = make(translate=lambda text: "the English of it")
api.feed(text="ceva in romana")
api.feed(text="/at 05:00 alta chestie in romana")
handle(bot, api)
check("kept in English",
      later_mod.load()[0]["prompt"] == "the English of it", str(later_mod.load()))
check("and shown to you now", any("the English of it" in s for s in api.sent),
      str(api.sent[-1:]))


print("\n--- dropping one, and dropping the lot ---")
later_mod.save([])
bot, api, sent = make()
for hour in ("05:00", "07:00", "09:00"):
    api.feed(text=f"/at {hour} prompt for {hour}")
handle(bot, api)
check("three waiting", len(later_mod.load()) == 3, str(len(later_mod.load())))
api.feed(text="/at cancel 2")
handle(bot, api)
check("the second is gone", len(later_mod.load()) == 2, str(later_mod.load()))
check("and the other two are not",
      all("07:00" not in e["prompt"] for e in later_mod.load()),
      str([e["prompt"] for e in later_mod.load()]))
api.feed(text="/at cancel")
handle(bot, api)
check("then none", later_mod.load() == [], str(later_mod.load()))
check("Telegram would take all of it", api.rejected == [], str(api.rejected[:2]))


print("\n--- and when the hour comes, it goes ---")
later_mod.save([])
bot, api, sent = make()
gone_by = datetime.now() - timedelta(minutes=1)
later_mod.add(gone_by, "the prompt you left", None)
bot._release_due()
check("it is queued", list(bot.pending) == ["the prompt you left"],
      str(list(bot.pending)))
check("the card says so", any("waiting for a free window" in t for t in api.all_text),
      str(api.all_text[-1:]))
check("and it is off the list", later_mod.load() == [], str(later_mod.load()))


print("\n--- once, not on every poll ---")
# The list is written back before anything is queued. The other order sends the
# prompt and then, if the write fails or the process goes, sends it again on
# the next poll, and again - typed into an agent over and over with nobody at
# the desk to stop it.
bot.pending.clear()
bot._release_due()
bot._release_due()
check("nothing came round a second time", len(bot.pending) == 0,
      str(list(bot.pending)))


print("\n--- one left too late is reported rather than sent ---")
later_mod.save([])
bot, api, sent = make()
ancient = datetime.now() - timedelta(hours=later_mod.LATE_HOURS + 2)
later_mod.add(ancient, "this one has gone stale", None)
bot._release_due()
check("not queued", len(bot.pending) == 0, str(list(bot.pending)))
check("but you are told",
      any("have not sent it" in s for s in api.sent), str(api.sent))
check("and it does not sit there for ever", later_mod.load() == [],
      str(later_mod.load()))
later_mod.save([])

print("\n--- when the window could not be read before the step ---")
# The card is a verdict, and there are three of them, not two: what it said,
# nothing, and no way to tell. Reported as the first, the phone was handed the
# whole window - minutes of older work, with the word "failed" somewhere in it.
bot, api, sent = make()
bot._prompts = ["do the thing"]
bot._result(0, None)
card = api.all_text[-1]
check("the card says so", "could not be read" in card, card[:120])
check("it does not claim the window said nothing",
      "Nothing new appeared" not in card, card[:120])
check("Telegram would take it", api.rejected == [], str(api.rejected[:2]))

api.feed(text="/more")
handle(bot, api)
check("and /more says the same rather than 'nothing yet'",
      any("no way to tell" in s for s in api.sent), str(api.sent[-1:]))

# And the next step, read properly, is reported properly again.
bot._result(1, ["what it actually said this time"])
check("the one after it is a result again",
      any("what it actually said this time" in t for t in api.all_text),
      str(api.all_text[-1:])[:140])
check("and the card does not still say it could not be read",
      "could not be read" not in api.all_text[-1], api.all_text[-1][:160])
api.feed(text="/more")
handle(bot, api)
check("and /more has it", any("what it actually said this time" in s
                             for s in api.sent), str(api.sent[-1:])[:140])

print("\n--- when nothing is watching, you are told before you need it ---")
# The failure this closes is silent by nature. Relay comes back at logon by
# itself because it has a Startup entry; the keeper has none, so it does not.
# From the phone the two look identical until the evening you send /restart and
# nothing answers - which happened three times in one evening before this.
bot, api, sent = make(watched=False)
bot._warn_if_unwatched()
check("nothing at first, in case a keeper is still starting", api.sent == [],
      str(api.sent))

bot._started = time.time() - remote_mod.UNWATCHED_GRACE - 1
bot._warn_if_unwatched()
check("then it says so", any("Nothing is watching" in s for s in api.sent),
      str(api.sent))
check("and how to make it stick",
      any("--at-logon" in s for s in api.sent), str(api.sent))

before = len(api.sent)
for _ in range(5):
    bot._warn_if_unwatched()
check("once, not once a poll", len(api.sent) == before, str(len(api.sent)))


print("\n--- and when something is, it says nothing at all ---")
bot, api, sent = make(watched=True)
bot._started = time.time() - remote_mod.UNWATCHED_GRACE - 1
for _ in range(5):
    bot._warn_if_unwatched()
check("silence", api.sent == [], str(api.sent))


print("\n--- a check it cannot make counts as nothing watching ---")
# The one direction that is safe to be wrong in: a warning you did not need
# costs a line, and a restart into an empty room costs the evening.
bot, api, sent = make()
bot.keeper_watching = lambda: (_ for _ in ()).throw(OSError("cannot ask"))
bot._started = time.time() - remote_mod.UNWATCHED_GRACE - 1
bot._warn_if_unwatched()
check("it warns", any("Nothing is watching" in s for s in api.sent), str(api.sent))

sys.exit(report.finish())
