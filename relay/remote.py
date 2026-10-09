"""Send a prompt from your phone, and have it typed into the window you left.

A Telegram bot is the whole transport. You message it, this reads the message
and puts the text through exactly the same queue the chain window uses - the
one that waits for the agent to finish, puts the cursor in the box, and refuses
to type into a window it cannot read.

Telegram rather than a page served on the laptop, chosen deliberately: nothing
listens here, so no port is opened and no tunnel is needed; it works from
anywhere rather than only on the home network; and a message sent while the
laptop is asleep waits on their servers until it wakes, which is the one thing
a web page cannot do.

The cost is worth saying out loud: the text of every prompt passes through
Telegram's servers. Everything else in this program stays on the machine - the
speech never leaves, the translation never leaves - and this one piece does
not. That was a decision, not an oversight.

No dependency is added for it. Long polling is a request that blocks for thirty
seconds, which urllib does as well as anything.
"""

import json
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections import deque
from pathlib import Path

from . import agent
from . import keeper as keeper_mod
from . import later
from .autopilot import (Autopilot, COUNTING, DONE, HOLDING, SENDING,
                        STARTING, STOPPED, WAITING)
from .injector import cancel_task_in_window
from .target import foreground_window, window_title

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Its own file, never config.json: that one is in the repository, and this
# holds a credential that must not be.
SETTINGS_PATH = PROJECT_ROOT / "telegram.json"

# Left behind on a deliberate exit so the keeper can tell a restart you
# asked for from a process that fell over. relay/keeper.py reads it.
RESTART_MARKER = PROJECT_ROOT / ".relay-restart"

# Buttons rather than remembered spelling.
#
# Sent with a reply, not with every message. Telegram keeps the last
# keyboard it was given until something replaces it, so attaching it to
# each card was both pointless and worse than pointless: the panel sat
# open over half a phone screen and reopened itself whenever it was
# closed. Without is_persistent it can be folded away like any other.
KEYBOARD = {
    "keyboard": [["/status", "/target"], ["/stop", "/restart"]],
    "resize_keyboard": True,
}
NO_KEYBOARD = {"remove_keyboard": True}

CANCEL_KEYBOARD = {
    "inline_keyboard": [
        [{"text": "🛑 Cancel Task", "callback_data": "cancel_task"}]
    ]
}

# Every command, in one place.
#
# Registered with Telegram so that typing "/" opens the list by itself, and
# printed by /help from the same tuple - two copies of a list like this drift
# apart, and the one that drifts is always the one somebody is reading.
#
# /start is here although Relay never acts on it: when Relay is down the keeper
# answers, and that is the moment you most need to be told the command exists.
COMMANDS = (
    ("status", "What it can see right now"),
    ("usage", "Quota and token activity"),
    ("more", "The whole of the last result, not just the card"),
    ("shot", "A picture of the window"),
    ("target", "Choose which window to write into"),
    ("at", "Send a prompt later - /at 05:00 read the log"),
    ("cancel", "Cancel active task (Ctrl+D)"),
    ("stop", "Cancel the queue"),
    ("restart", "Quit and come straight back"),
    ("start", "Start Relay when it is not running"),
    ("keys", "Show the buttons, /keys off to hide them"),
    ("help", "This list"),
)


def _known():
    """The commands as a sentence, for the reply to one that is not there.

    The third place this list was written out by hand, and the two before it
    had both fallen behind. A command missing from here is answered with "I
    only know" a list that does not contain it, which reads as the bot being
    broken rather than as this line being stale.
    """
    names = [f"/{name}" for name, _ in COMMANDS]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


API = "https://api.telegram.org/bot{token}/{method}"

# Telegram holds the request open until something arrives or this many seconds
# pass, so an idle bot costs one connection rather than a request a second.
POLL_SECONDS = 30
HTTP_TIMEOUT = POLL_SECONDS + 15

# A picture is an upload rather than a line of text, and Telegram does not
# answer until the last byte of it is in. Half a megabyte on a laptop's uplink
# is not twenty seconds' work, and a timeout here loses a photograph that was
# most of the way there.
PHOTO_TIMEOUT = 90
DEFAULT_PHOTO_PROMPT = "Please analyze and explain the code/error in this image."


# After a failure, wait before trying again, and wait longer each time. A
# laptop that closes its lid on a train should not fill the log with one line
# a second until it lands.
# How long to give a keeper that started alongside this one to claim its
# name before saying nothing is watching.
UNWATCHED_GRACE = 25

RETRY_START = 2
RETRY_MAX = 60

# A bot token runs to about 46 characters. The bounds are loose enough to
# survive Telegram changing the shape and tight enough to catch the two ways
# a paste goes wrong: half of one, or the same one several times over.
MIN_TOKEN = 30
MAX_TOKEN = 80

# How much of a finished step to send back.
#
# Small on purpose. Everything an agent says is new, so the diff against
# the window before the step is the whole reply - and the first version
# put twelve lines of somebody's prose on a phone screen. The card is a
# verdict, not a transcript: the detail is in the window it came from, and
# anyone who wants it will go and look.
RESULT_LINES = 5
RESULT_CHARS = 700
# A whole sentence, near enough. At 140 an ordinary sentence did not fit, so
# almost every line on the card ended part way through one.
LINE_CHARS = 220
BAD_LINES = 4
BAD_CHARS = 180

# And how much of it /more will hand over when the verdict is not enough.
#
# One step's worth, replaced every time a step finishes: an evening of forty
# steps costs this once rather than forty times, and nothing here has to
# decide when to let an old chain go. The cap is for the step that puts a
# whole build log on screen - four messages is already a lot to scroll on a
# phone, and past that the window is the better place to read it.
MORE_CHARS = 12000
# Kept back inside each message for the line that names the window and says
# which part of how many this is.
MORE_HEAD_CHARS = 300

# How many of a batch's prompts to list on the card before summarising the
# rest. More than a few and the state line is pushed off a phone screen.
CARD_PROMPTS = 4
CARD_PROMPT_CHARS = 160

# The icon is the part read first and from furthest away. One per state, and
# none of them reused, so a glance at the notification is already an answer.
ICON_WORKING = "⏳"
ICON_SENDING = "✍️"
ICON_NEEDS_YOU = "🔔"
ICON_DONE = "✅"
ICON_TROUBLE = "⚠️"
ICON_STOPPED = "⛔"
# Not a state: what /more sends is not a card, and should not arrive looking
# like one that has changed its mind about how the step went.
ICON_MORE = "📄"


def _shorten(text, limit):
    """Cut to `limit`, at a space rather than mid-word, and show it was cut.

    Two separate things, and the second matters more. A line that stops in the
    middle of a word reads as the window having gone quiet there - the card
    said "nici un microfon Blue" and there was no way to tell that from the
    agent having actually written that. The ellipsis is the whole difference
    between "that is all it said" and "there is more where this came from".

    Never longer than `limit`, so passing an already-shortened line through
    again changes nothing.
    """
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        # Only if the last space is somewhere near the end. A line with no
        # spaces at all - a path, a hash - is better cut short than left whole.
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "\u2026"


def _esc(text):
    """Telegram parses the card as HTML, so the text inside it cannot be."""
    return (str(text).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


# Telegram takes 4096 characters; this leaves room for the escaping to grow.
MESSAGE_CHARS = 3900

# The line under a picture is held to a quarter of that by Telegram - 1024 -
# and a caption it refuses takes the picture down with it.
CAPTION_CHARS = 900


def _fit(text, limit=MESSAGE_CHARS):
    """Escaped, cut to what Telegram will take, and never cut mid-entity.

    Cutting inside an `&amp;` leaves a bare ampersand at the end, which is the
    same rejection this exists to avoid.
    """
    out = _esc(text)[:limit]
    last = out.rfind("&")
    if last != -1 and ";" not in out[last:]:
        out = out[:last]
    return out


def _pieces(line, limit):
    """One line as lengths that fit once escaped, cut on the plain text.

    For the line that is a message all by itself: a pasted stack trace on one
    line, or a terminal that wrapped nothing. Escaping can turn one character
    into five, so where to cut is measured on the escaped form and applied to
    the plain one.
    """
    while len(_esc(line)) > limit:
        take = limit
        while len(_esc(line[:take])) > limit:
            # Shrink in proportion to the overrun rather than a character at a
            # time: a line of nothing but ampersands is five times its own
            # length escaped, and stepping down to it one at a time is
            # thousands of passes over the same string.
            take = max(1, take * limit // len(_esc(line[:take])))
        yield line[:take]
        line = line[take:]
    yield line


def _split(text, limit=MESSAGE_CHARS):
    """The text as messages Telegram will take, cut first and escaped after.

    That order is the whole of it. Escaping first and cutting the result puts
    the knife through an `&amp;` sooner or later, and half an entity is the
    same message Telegram refuses that everything here is escaped to avoid -
    which arrives as silence, in the middle of a reply you asked to see. Cut
    the plain text and every piece is escaped whole.

    On line ends where there are any, because what is being sent is output.
    """
    parts, part = [], ""
    for line in text.split("\n"):
        joined = f"{part}\n{line}" if part else line
        if len(_esc(joined)) <= limit:
            part = joined
            continue
        if part:
            parts.append(_esc(part))
        # The line will not fit in a message even on its own, so it goes out
        # in lengths of itself - and those are not put back together with a
        # newline between them, which would be a line break the window never
        # wrote.
        pieces = list(_pieces(line, limit))
        parts.extend(_esc(piece) for piece in pieces[:-1])
        part = pieces[-1]
    if part:
        parts.append(_esc(part))
    return parts


# What Telegram sends when a message is not text. Named, so the reply can say
# what it was rather than only that it was not readable.
ATTACHMENTS = (
    ("photo", "a photo"),
    ("voice", "a voice message"),
    ("audio", "an audio file"),
    ("video", "a video"),
    ("video_note", "a video message"),
    ("animation", "a GIF"),
    ("sticker", "a sticker"),
    ("document", "a file"),
    ("location", "a location"),
    ("contact", "a contact"),
    ("poll", "a poll"),
)


def _kind_of(message):
    """What arrived, when what arrived has no text in it."""
    for key, name in ATTACHMENTS:
        if message.get(key):
            return name
    return "something that is not text"

# Words that mean a step did not do what it was asked.
#
# Shorter than it was, and measured rather than guessed. The first list held
# "cannot", "could not", "not found" and "no such", which are ordinary English:
# five sentences of normal prose were tried against it and two came back
# flagged. A card that shows a warning on every reply is a card whose warning
# means nothing, which is the opposite of what it is for.
TROUBLE = (
    "error", "exception", "traceback", "failed", "failure", "fatal",
    "denied", "refused", "eroare", "esuat",
)

# And only on a line short enough to be machine output. A stack trace is
# terse; a paragraph that happens to use the word "failure" is somebody
# explaining something.
TROUBLE_MAX_CHARS = 160

# Buttons, labels and chrome that come back with the text of any window and say
# nothing about what happened.
CHROME = (
    "copy message", "copy code", "read aloud", "show message actions",
    "pin as chapter", "run in terminal", "good response", "bad response",
    "send message", "type / for commands", "bypass permissions",
    "dictation settings", "press and hold to record", "notifications",
    "more options for", "collapse sidebar", "show more", "just now",
)


def _looks_wrong(line):
    if len(line) > TROUBLE_MAX_CHARS:
        return False
    low = line.lower()
    return any(word in low for word in TROUBLE)


def _is_chrome(line):
    low = line.strip().lower()
    return any(low == word or low.startswith(word) for word in CHROME)


def _last_of(lines):
    """The end of what was said, within a budget a phone can hold.

    Counted in characters as well as lines, because five lines of an agent
    explaining itself is a screenful and five lines of a terminal is
    nothing.
    """
    out, budget = [], RESULT_CHARS
    for line in reversed(lines[-RESULT_LINES:]):
        line = _shorten(line, LINE_CHARS)
        if out and len(line) > budget:
            break
        out.append(line)
        budget -= len(line)
    return list(reversed(out))


def _within(lines, budget):
    """As much of the end of `lines` as `budget` characters allow.

    The end, like the card, so /more carries on outwards from the same place
    rather than showing a different part of the same answer and leaving the
    join to be worked out. Whole lines, except when the first one kept is on
    its own longer than the budget - one line and no output at all is a worse
    answer than the front of that line.
    """
    out, left = [], budget
    for line in reversed(lines):
        if len(line) + 1 > left:
            if not out:
                out.append(_shorten(line, budget))
            break
        out.append(line)
        left -= len(line) + 1
    return list(reversed(out))

# What gets written on first run. The instructions are one line, and they say
# where to put the token rather than showing an example of one: the first
# version spelled out a sample token inside the file, and the sample read as
# somewhere to type - which is exactly what happened the first time anyone
# filled it in.
TEMPLATE = {
    "PUT THE TOKEN FROM @BotFather ON THE token LINE BELOW, BETWEEN THE QUOTES":
        "leave chat_id null - the first message the bot gets claims it",
    "token": "",
    "chat_id": None,
}


def write_template(path=None):
    """Put an empty settings file where it can be filled in. True if written."""
    path = Path(path) if path else SETTINGS_PATH
    if path.exists():
        return False
    path.write_text(json.dumps(TEMPLATE, indent=2) + "\n", encoding="utf-8")
    return True


def load_settings(path=None):
    """The token and the paired chat, or None when there is nothing to run."""
    path = Path(path) if path else SETTINGS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[remote] {path.name} could not be read ({exc})")
        return None
    token = str(data.get("token") or "").strip()
    if not token:
        return None
    chat = data.get("chat_id")
    return {"token": token, "chat_id": int(chat) if chat else None,
            "target": data.get("target") or None, "path": path}


def save_setting(settings, key, value):
    """Write one field back to the file, leaving everything else as it is."""
    path = Path(settings["path"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = dict(TEMPLATE)
        data["token"] = settings["token"]
    data[key] = value
    try:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        print(f"[remote] could not write {key}: {exc}")
        return False


def save_chat_id(settings, chat_id):
    """Write down which chat claimed the bot, so it survives a restart."""
    return save_setting(settings, "chat_id", chat_id)


def set_token(path=None, ask=None):
    """Ask for the token and write it, so nobody has to edit JSON by hand.

    Editing the file in Notepad means landing a cursor between two quote marks
    that are touching. That is a fiddly thing to ask of anybody, and it is the
    part that actually went wrong the first time this was set up.

    Read without echo where the terminal allows it. A token pasted into a
    visible console stays in the scrollback, and from there ends up in the next
    screenshot - which is exactly how the first one came to need revoking.

    Setting a token clears the paired chat. A new token is a new bot, and the
    chat that claimed the old one has no business driving this one.
    """
    import getpass

    path = Path(path) if path else SETTINGS_PATH
    if ask is None:
        def ask(prompt):
            try:
                return getpass.getpass(prompt)
            except Exception:
                return input(prompt)

    print("Paste the token @BotFather gave you, then press Enter.")
    print("It will not appear as you paste - that is deliberate.")
    print("")
    token = (ask("token: ") or "").strip()

    if not token:
        print("")
        print("Nothing pasted; the file is unchanged.")
        return False
    # 123456789:AAE... - enough of a shape to catch a half-copied paste, not so
    # strict that a change at Telegram's end locks anybody out.
    if ":" not in token or not token.split(":")[0].isdigit():
        print("")
        print(f"That does not look like a bot token ({len(token)} characters, "
              f"no digits before a colon). The file is unchanged.")
        return False

    # Nothing is echoed while it is pasted, so a paste that seems not to have
    # worked gets tried again, and again. Measured on the first real setup:
    # 171 characters, which is the same token nearly four times over, and it
    # was accepted without a word. A token is about 46.
    if not MIN_TOKEN <= len(token) <= MAX_TOKEN:
        print("")
        print(f"That is {len(token)} characters, and a bot token is about 46.")
        if len(token) > MAX_TOKEN:
            print("It looks pasted more than once - which is easy to do when "
                  "nothing appears on screen. Run this again and paste once.")
        print("The file is unchanged.")
        return False

    data = dict(TEMPLATE)
    data["token"] = token
    data["chat_id"] = None
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("")
    print(f"Written to {path.name}. The paired chat was cleared, so the first "
          f"message the bot receives claims it.")
    print("Restart Relay, then message your bot.")
    return True


def _multipart(params):
    """The same call, as a form with a file in it. Returns (type, body).

    Telegram takes a photograph only as multipart/form-data, which is the one
    shape urlencode cannot make. The boundary is random because it must not
    occur anywhere in the body, and the body here is a screenshot - arbitrary
    bytes, in which any fixed marker eventually appears.
    """
    boundary = "relay" + uuid.uuid4().hex
    body = bytearray()
    for key, value in params.items():
        body += f"--{boundary}\r\n".encode("utf-8")
        if isinstance(value, tuple):
            filename, blob = value
            body += (f'Content-Disposition: form-data; name="{key}"; '
                     f'filename="{filename}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n"
                     ).encode("utf-8")
            body += blob
        else:
            body += (f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                     ).encode("utf-8")
            body += str(value).encode("utf-8")
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    return f"multipart/form-data; boundary={boundary}", bytes(body)


def call_api(token, method, params, timeout=HTTP_TIMEOUT):
    """One Telegram API call. Returns the result field, or raises.

    A parameter whose value is a (filename, bytes) pair sends the call as
    multipart instead of as an ordinary form, which is how a photograph goes
    out. Keeping it inside this one function rather than adding a second
    callable next to it means every caller - and every test standing in for
    this - still has one thing of one shape to deal with.
    """
    url = API.format(token=token, method=method)
    if any(isinstance(value, tuple) for value in params.values()):
        content_type, data = _multipart(params)
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": content_type})
    else:
        data = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "telegram said no"))
    return payload.get("result")


def download_file(token, file_path, timeout=PHOTO_TIMEOUT):
    """Download binary file content from Telegram Bot API."""
    clean_path = urllib.parse.quote(str(file_path).strip(), safe="/")
    url = f"https://api.telegram.org/file/bot{token}/{clean_path}"
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class Remote:
    """Reads messages from one chat and turns them into prompts.

    A message is not typed straight into the window. It becomes a step in the
    same Autopilot the chain window drives, which is what makes every rule
    already written apply here too - and they matter more from a phone than
    from the desk, because the last of them, a keystroke cancelling the
    countdown, is not available to somebody in another room.
    """

    def __init__(self, settings, send, target_getter, log=print,
                 api=call_api, is_window=None, translate=None,
                 on_restart=None, capture=None, keeper_watching=None,
                 downloader=None, canceller=None):
        self.settings = settings
        self.target_getter = target_getter
        self.canceller = canceller or cancel_task_in_window
        # Injected for the same reason the queue injects it: whether a
        # window still exists is a question for Windows, and a test cannot
        # conjure a real one to ask about.
        self.is_window = is_window or (lambda hwnd: bool(
            __import__('ctypes').windll.user32.IsWindow(hwnd)))
        self.log = log
        self.api = api
        self.downloader = downloader
        self.pending = deque()
        # A window picked from the phone with /target. None means follow
        # whatever you last clicked into, which is what everything else does.
        self.chosen = None
        # What /target chose, by name rather than by handle, so it can be
        # taken back up after a restart. See _find_remembered.
        self.remembered = settings.get("target") or None
        self.translate = translate
        # How to leave, when asked to.
        self.on_restart = on_restart
        # And whether anything would bring us back. Asked of Windows, not
        # assumed: this used to test whether on_restart had been passed, which
        # is true whenever Relay is running at all, so /restart promised "the
        # keeper will bring me back in a few seconds" with no keeper anywhere
        # and left the laptop dark. Measured three times in one evening.
        self.keeper_watching = keeper_watching or keeper_mod.keeper_is_watching
        # Said once, and only when there is nothing watching. See _run.
        self._warned_unwatched = False
        # How to photograph a window. Handed in for the same reason leaving is:
        # a grab is a Qt call and Qt takes one only from the thread that owns
        # the windows, which is not this one. None means nobody here can take a
        # picture at all - Relay started with --no-ui - and /shot says so
        # rather than going quiet.
        self.capture = capture
        # The one message a batch of prompts lives in. Set up in one place
        # rather than two: the first version listed these fields here as well
        # as in _new_card, and adding one to a card layout left the other
        # behind.
        self._new_card()
        # Whether the card on screen belongs to a batch still being built
        # or run. Once a chain has finished, the next prompt starts a new
        # card rather than reopening a closed one.
        self.pending_own_card = False
        self._where = ""
        # What the last finished step said, in full, for /more. The card keeps
        # five shortened lines of it and drops the rest the moment it is drawn,
        # so this is the only copy - and None here means no step has finished
        # yet, which is a different answer from a step that said nothing.
        self._full = None
        # How many lines of that were more than MORE_CHARS would hold.
        self._dropped = 0
        # Set when a step finished but the window could not be read before it.
        # A third answer, and it has to be: "all of the window" and "none of
        # it" are both untrue about a step whose before and after cannot be
        # told apart.
        self._unreadable = False
        self.pilot = Autopilot(send=send, on_progress=self._progress,
                               on_result=self._result, log=log)
        # The card is drawn from two threads: this one, when a message
        # arrives, and the queue's own, once a second while a chain runs. Both
        # finding it empty at the same moment is two cards on the phone, and
        # from then on the one being edited is not the one you are looking at.
        self._drawing = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._offset = 0
        # Anything older than this was addressed to a Relay that no longer
        # exists. See _handle.
        self._started = time.time()
        self._said_waiting = False
        self._commands_published = False
        self._offline = False
        self._retry_delay = 1.0

    # --- outside world ---------------------------------------------------

    def start(self):
        self._thread = threading.Thread(
            target=self._run, name="remote", daemon=True)
        self._thread.start()
        return self

    def stop(self, why="Relay was stopped"):
        """Say goodbye before going, if anything was left in the air.

        A card frozen on "working" is worse than no card: it says the laptop
        is busy on your behalf when the process that was doing it no longer
        exists. Measured three times over one evening, every one of them a
        restart to deploy the next change while a chain sat waiting.

        Short timeout, and failure ignored. This runs on the way out and must
        not hold shutdown up on a network that is not there.
        """
        self._stop.set()
        if self.pilot.running or self.pending:
            self.pilot.stop(why)
            self.pending.clear()
            self._icon, self._head = ICON_STOPPED, why
            self._note = ("Whatever was waiting is lost. Send it again once "
                          "Relay is back.")
            self._cancel_button = False
            try:
                self.edit(self._card, self._render(), timeout=6, reply_markup=None)
            except Exception:
                pass

    @property
    def chat_id(self):
        return self.settings.get("chat_id")

    def say(self, text, keys=False, html=False, reply_markup=None):
        """Send a message. Returns its id, so it can be edited later.

        Escaped on the way out unless the caller has already done it. Nearly
        everything sent from here carries a window title or the text of an
        exception, and one angle bracket in a title is a message Telegram
        refuses outright - which arrives as silence, at the moment you were
        asking what was going on.
        """
        if not self.chat_id:
            return None
        params = {"chat_id": self.chat_id,
                  "text": text[:MESSAGE_CHARS] if html else _fit(text),
                  "parse_mode": "HTML"}
        if reply_markup is not None:
            params["reply_markup"] = (json.dumps(reply_markup)
                                      if isinstance(reply_markup, (dict, list))
                                      else reply_markup)
        elif keys is True:
            params["reply_markup"] = json.dumps(KEYBOARD)
        elif keys == "remove":
            params["reply_markup"] = json.dumps(NO_KEYBOARD)
        for attempt in range(3):
            try:
                result = self.api(
                    self.settings["token"], "sendMessage", params, timeout=20)
                return (result or {}).get("message_id")
            except Exception as exc:
                if attempt < 2 and any(err in str(exc).lower() for err in ("11001", "10054", "10060", "timed out", "handshake")):
                    if self._retry_delay > 0 and self._stop.wait(self._retry_delay * (attempt + 1)):
                        return None
                    continue
                self.log(f"[remote] could not reply: {exc}")
                return None
        return None

    def send_photo(self, blob, caption=""):
        """Send a picture with a line under it. Returns its id, or None.

        The caption is escaped and cut exactly as a message is, and for the
        same reason with more at stake: it carries a window title, titles carry
        angle brackets and ampersands, and a caption Telegram refuses does not
        arrive without its photograph - it takes the photograph with it.
        """
        if not self.chat_id:
            return None
        params = {"chat_id": self.chat_id,
                  # A (name, bytes) pair is what turns this into a multipart
                  # call. See call_api.
                  "photo": ("window.png", blob),
                  "caption": _fit(caption, CAPTION_CHARS),
                  "parse_mode": "HTML"}
        for attempt in range(3):
            try:
                result = self.api(self.settings["token"], "sendPhoto", params,
                                  timeout=PHOTO_TIMEOUT)
                return (result or {}).get("message_id")
            except Exception as exc:
                if attempt < 2 and any(err in str(exc).lower() for err in ("11001", "10054", "10060", "timed out", "handshake")):
                    if self._retry_delay > 0 and self._stop.wait(self._retry_delay * (attempt + 1)):
                        return None
                    continue
                self.log(f"[remote] could not send the picture: {exc}")
                self.say(f"The picture was taken but would not send ({exc}).")
                return None
        return None

    def edit(self, message_id, text, timeout=20, reply_markup=None):
        """Rewrite a message already sent. False if it could not be done."""
        if not self.chat_id or not message_id:
            return False
        params = {"chat_id": self.chat_id, "message_id": message_id,
                  "text": text[:MESSAGE_CHARS], "parse_mode": "HTML"}
        if reply_markup is not None:
            params["reply_markup"] = (json.dumps(reply_markup)
                                      if isinstance(reply_markup, (dict, list))
                                      else reply_markup)
        for attempt in range(3):
            try:
                self.api(
                    self.settings["token"], "editMessageText",
                    params,
                    timeout=timeout)
                return True
            except Exception as exc:
                if attempt < 2 and any(err in str(exc).lower() for err in ("11001", "10054", "10060", "timed out", "handshake")):
                    if self._retry_delay > 0 and self._stop.wait(self._retry_delay * (attempt + 1)):
                        return False
                    continue
                self.log(f"[remote] could not edit: {exc}")
                return False
        return False

    def _download(self, token, file_path):
        """Fetch binary image bytes via injected downloader or default download_file."""
        if self.downloader is not None:
            try:
                return self.downloader(token, file_path)
            except TypeError:
                try:
                    return self.downloader(file_path)
                except TypeError:
                    clean_path = urllib.parse.quote(str(file_path).strip(), safe="/")
                    url = f"https://api.telegram.org/file/bot{token}/{clean_path}"
                    return self.downloader(url)
        return download_file(token, file_path)

    # --- one message per batch, rewritten as it goes ----------------------

    def _render(self):
        """The card as it should currently read.

        One message that changes rather than four that arrive, and always the
        same shape, so the eye lands in the same place every time:

            an icon and one line saying how it went
            what was asked
            what went wrong, if anything
            what it said

        The verdict goes first because it is the question being asked - did
        that work - and a phone is read at a glance. Anything that looks like a
        failure comes above the output rather than inside it: an error twenty
        lines up is the answer even when the last line reads calmly.
        """
        blocks = [f"{self._icon} <b>{_esc(self._head)}</b>" if self._head else ""]

        shown = list(self._prompts[:CARD_PROMPTS])
        if len(self._prompts) > CARD_PROMPTS:
            shown.append(f"...and {len(self._prompts) - CARD_PROMPTS} more")
        if shown:
            blocks.append("<b>Asked</b>\n"
                          + "\n".join(_esc(_shorten(p, CARD_PROMPT_CHARS))
                                        for p in shown))

        if self._bad:
            word = "problem" if len(self._bad) == 1 else "problems"
            blocks.append(f"⚠️ <b>{len(self._bad)} {word}</b>\n"
                          + "\n".join(_esc(_shorten(ln, BAD_CHARS))
                                        for ln in self._bad))

        if self._tail:
            blocks.append("<b>What it said</b>\n"
                          + "\n".join(_esc(_shorten(ln, LINE_CHARS))
                                        for ln in self._tail))

        if self._note:
            blocks.append(_esc(self._note))

        return "\n\n".join(b for b in blocks if b)

    def _paint(self, icon=None, head=None, bad=None, tail=None, note=None, cancel_button=None):
        """Show the card, creating it the first time and editing it after.

        Only when what it would say has changed. The queue reports its phase
        every second, and Telegram counts every edit against a rate limit.
        """
        if icon is not None:
            self._icon = icon
        if head is not None:
            self._head = head
        if bad is not None:
            self._bad = bad
        if tail is not None:
            self._tail = tail
        if note is not None:
            self._note = note
        if cancel_button is not None:
            self._cancel_button = bool(cancel_button)

        with self._drawing:
            text = self._render()
            markup = json.dumps(CANCEL_KEYBOARD) if self._cancel_button else None
            state_key = (text, markup)
            if state_key == self._painted_state:
                return
            self._painted = text
            self._painted_state = state_key
            if self._card is None:
                self._card = self.say(text, html=True, reply_markup=markup)
            elif not self.edit(self._card, text, reply_markup=markup):
                # The message may have been deleted from the phone. Start
                # another rather than going quiet for the rest of the chain.
                self._card = self.say(text, html=True, reply_markup=markup)

    def _new_card(self):
        self._card = None
        self._unreadable = False
        self._painted = None
        self._painted_state = None
        self._cancel_button = False
        self._icon = ICON_WORKING
        self._head = ""
        self._prompts = []
        self._bad = []
        self._tail = []
        self._note = ""

    # --- the loop --------------------------------------------------------

    def publish_commands(self):
        """Tell Telegram what this bot answers to.

        This is what makes pressing "/" in the chat open a list rather than an
        empty box. Done once at start-up, and quietly: a menu a version behind
        is a small annoyance, and a bot that refuses to start because a menu
        could not be published is not.
        """
        listing = [{"command": name, "description": what}
                   for name, what in COMMANDS]
        try:
            self.api(self.settings["token"], "setMyCommands",
                     {"commands": json.dumps(listing)}, timeout=20)
            self._commands_published = True
            self.log(f"[remote] published {len(listing)} commands")
        except Exception as exc:
            self.log(f"[remote] could not publish the command list: {exc}")

    def _run(self):
        self.publish_commands()
        self.log("[remote] listening"
                 + ("" if self.chat_id else " - the first message will claim the bot"))
        wait = RETRY_START
        while not self._stop.is_set():
            try:
                updates = self._poll()
            except Exception as exc:
                # Offline, asleep, or Telegram having a moment. None of those
                # deserve a line a second.
                if not self._offline:
                    self.log(f"[remote] not reachable ({exc}); retrying in {wait}s")
                    self._offline = True
                if self._stop.wait(wait):
                    return
                wait = min(wait * 2, RETRY_MAX)
                continue

            if self._offline:
                self.log("[remote] connection restored")
                self._offline = False
            wait = RETRY_START

            if not self._commands_published:
                self.publish_commands()

            for update in updates:
                try:
                    self._handle(update)
                except Exception as exc:
                    # One message nothing could be done with is not a network
                    # problem. Counting it as one used to back the poll off to
                    # a minute between reads, throw away the rest of the batch,
                    # and say "not reachable" about a laptop that was sitting
                    # right there. Telegram has already been told these were
                    # read, so there is no second attempt at them either.
                    self.log(f"[remote] could not deal with a message: {exc}")
                    self.say("Something went wrong dealing with that message. "
                             "It has not been queued.")

            self._warn_if_unwatched()

            try:
                self._release_due()
            except Exception as exc:
                self.log(f"[later] could not release what was waiting: {exc}")

            try:
                self._drain()
            except Exception as exc:
                # This used to sit outside the guard entirely, so anything it
                # raised ended the thread - and a phone link that has stopped
                # listening looks exactly like one with nothing to say.
                self.log(f"[remote] could not start the queue: {exc}")
                self.pending.clear()
                self._paint(icon=ICON_STOPPED, head="could not start",
                            note=str(exc))

    def _poll(self):
        updates = self.api(
            self.settings["token"], "getUpdates",
            {"offset": self._offset, "timeout": POLL_SECONDS}) or []
        for update in updates:
            self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
        return updates

    def _confirm(self):
        """Tell Telegram we are done with everything read so far.

        A message is only consumed when the next request is made with a higher
        offset. Ordinarily the next poll does it, a second or two later, and
        nobody has to think about it - but a command that ends the process
        never makes that call, so the message stays pending and the next
        Relay to start is handed it again.

        Which is exactly what happened with /restart: it restarted, came back,
        was given the same instruction, and restarted again, four times over
        before anyone stopped it.
        """
        try:
            self.api(self.settings["token"], "getUpdates",
                     {"offset": self._offset, "timeout": 0}, timeout=10)
        except Exception as exc:
            self.log(f"[remote] could not confirm the last message: {exc}")

    def _handle_callback_query(self, query):
        chat_id = (query.get("message") or {}).get("chat", {}).get("id") or (query.get("from") or {}).get("id")
        if not chat_id or not self.chat_id or int(chat_id) != int(self.chat_id):
            self.log(f"[remote] ignored callback query from unknown sender: {chat_id}")
            return

        query_id = query.get("id")
        if query_id:
            try:
                self.api(self.settings["token"], "answerCallbackQuery",
                         {"callback_query_id": query_id, "text": "Task cancelled"})
            except Exception as exc:
                self.log(f"[remote] could not answer callback query: {exc}")

        data = query.get("data")
        if data == "cancel_task":
            self._cancel_task(from_command=False)

    def _cancel_task(self, from_command=False):
        """Halt autopilot, clear queue, send Ctrl+D to target window, and update card."""
        hwnd = (self.pilot.hwnd if self.pilot.running else None) or self._target()
        if self.pilot.running:
            self.pilot.stop("cancelled from phone")
        self.pending.clear()
        self.pending_own_card = False
        try:
            self.canceller(hwnd)
        except Exception as exc:
            self.log(f"[remote] canceller failed: {exc}")
        self._paint(icon=ICON_STOPPED,
                    head=f"{self._where or 'Task'} - cancelled",
                    note="Cancelled from your phone.",
                    cancel_button=False)
        if from_command:
            self.say("Task cancelled.")

    def _handle(self, update):
        if "callback_query" in update:
            self._handle_callback_query(update["callback_query"])
            return

        message = update.get("message") or update.get("edited_message") or {}
        chat = (message.get("chat") or {}).get("id")
        if not chat:
            return
        text = (message.get("text") or "").strip()

        # Anything sent before this process existed was meant for a Relay that
        # was not there, and acting on it now is acting on the past. It matters
        # for one command in particular: a /restart left unconfirmed is handed
        # to the next Relay, which restarts, which is handed it again. That ran
        # four times in a row before it was stopped by hand, and would have run
        # until the machine was turned off.
        when = message.get("date")
        if when and float(when) < self._started:
            self.log(f"[remote] ignoring a message from before I started: "
                     f"{(text or _kind_of(message))[:40]!r}")
            return

        if self.chat_id is None:
            if not text:
                return
            self.settings["chat_id"] = int(chat)
            save_chat_id(self.settings, int(chat))
            self.log(f"[remote] paired with chat {chat}")
            self.say("Paired. Only this chat can drive the laptop now.\n\n"
                     "Write in Romanian and it goes into the window you were "
                     "last working in, in English. /target chooses the window, "
                     "/status says what it can see, /stop cancels.")
            return

        if int(chat) != int(self.chat_id):
            # Somebody else found the bot. Say nothing to them at all.
            self.log(f"[remote] ignored a message from chat {chat}")
            return

        photos = message.get("photo")
        if photos and isinstance(photos, list):
            try:
                best_photo = max(
                    photos,
                    key=lambda p: (
                        int(p.get("width") or 0) * int(p.get("height") or 0),
                        int(p.get("file_size") or 0),
                    ),
                )
                file_id = best_photo.get("file_id")
                if not file_id:
                    raise RuntimeError("photo has no file_id")

                file_info = self.api(
                    self.settings["token"], "getFile", {"file_id": file_id}
                )
                file_path = (file_info or {}).get("file_path") if isinstance(file_info, dict) else None
                if not file_path:
                    raise RuntimeError(f"getFile returned no file_path for {file_id}")

                image_bytes = self._download(self.settings["token"], file_path)

                caption_raw = (message.get("caption") or "").strip()
                if caption_raw:
                    caption_en, _ = self._to_english(caption_raw)
                    caption_en = caption_en or DEFAULT_PHOTO_PROMPT
                else:
                    caption_en = DEFAULT_PHOTO_PROMPT

                step = {
                    "type": "photo",
                    "image_bytes": image_bytes,
                    "caption": caption_en,
                }
                self.pending.append(step)
                if self._card is not None and not self.pending_own_card:
                    self._new_card()
                self.pending_own_card = True
                self._prompts.append(f"📷 {caption_en}")
                self._paint(icon=ICON_WORKING, head="waiting for a free window")
                return
            except Exception as exc:
                self.log(f"[remote] could not download photo: {exc}")
                self.say(f"That came through as a photo, but could not be downloaded: {exc}")
                return

        if not text:
            # A photo, a voice note, a file. There is nothing here that could
            # be typed into a window, and answering costs one line - where
            # saying nothing is indistinguishable from having queued it, and
            # leaves you waiting for a result that was never coming. The
            # caption is not used either: a sentence about a picture, sent on
            # its own to something that cannot see the picture, is worse than
            # nothing.
            kind = _kind_of(message)
            self.log(f"[remote] {kind} from the phone; nothing to type")
            self.say(f"That came through as {kind}, and I can only read text. "
                     f"Send what you want typed as a message.")
            return

        if text.startswith("/"):
            self._command(text)
            return

        prompt, _english = self._to_english(text)
        self.pending.append(prompt)
        if self._card is not None and not self.pending_own_card:
            # A card that has already reported a finished chain is closed;
            # this prompt starts a new one.
            self._new_card()
        self.pending_own_card = True
        # The prompt shown is the English, because that is what will be typed.
        # Seeing it is the only chance to /stop a sentence the model mangled
        # before it lands in an agent.
        self._prompts.append(prompt)
        self._paint(icon=ICON_WORKING, head="waiting for a free window")

    def _to_english(self, text):
        """Romanian in, English out - the same model the write window uses.

        Returns (what to send, the translation or None). A leading `=` sends
        the line exactly as typed: the model translates Romanian, and a prompt
        that is already English, or is a file path, or a command, is better off
        untouched.

        A translation that fails hands back the Romanian rather than nothing.
        Losing the prompt would be a worse outcome than sending it in the wrong
        language, and the reply says which happened.
        """
        if text.startswith("="):
            return text[1:].strip(), None
        if self.translate is None:
            return text, None
        try:
            english = (self.translate(text) or "").strip()
        except Exception as exc:
            self.log(f"[remote] could not translate: {exc}")
            self.say(f"Could not translate that ({exc}); queueing the Romanian "
                     f"as it is.")
            return text, None
        return (english, english) if english else (text, None)

    def _command(self, text):
        parts = text.split()
        command = parts[0].lower()
        if command in ("/status", "/state"):
            self.say(self._describe())
        elif command == "/usage":
            self._usage()
        elif command == "/more":
            self._more()
        elif command == "/shot":
            self._shot()
        elif command == "/target":
            self._target_command(parts[1] if len(parts) > 1 else None)
        elif command == "/at":
            self._at(text.split(None, 1)[1] if len(parts) > 1 else "")
        elif command == "/cancel":
            self._cancel_task(from_command=True)
        elif command == "/stop":
            self.pending.clear()
            if self.pilot.running:
                self.pilot.stop("you stopped it from your phone")
                self.say("Stopped, and the queue is empty.")
            else:
                self.say("Nothing was running. The queue is empty.")
        elif command == "/keys":
            # Asked for, never volunteered. A panel across half the screen
            # that you did not put there is one you end up fighting.
            if len(parts) > 1 and parts[1].lower() in ("off", "hide", "no"):
                self.say("Buttons hidden. /keys brings them back.",
                         keys="remove")
            else:
                self.say("Here they are. /keys off hides them again.",
                         keys=True)
        elif command == "/restart":
            self._restart()
        elif command in ("/start", "/help"):
            # Built from COMMANDS, not written out again. This block had
            # already fallen a version behind - it was still offering three
            # commands when there were six - which is what a second copy of a
            # list does while nobody is looking at it.
            width = max(len(name) for name, _ in COMMANDS) + 3
            listing = "\n".join(f"/{name}{' ' * (width - len(name))}{what}"
                                for name, what in COMMANDS)
            self.say("Write in Romanian. It is translated to English and typed "
                     "into the window you were last working in, once whatever "
                     "is in there has finished.\n\n"
                     + listing +
                     "\n\nStart a line with = to send it exactly as typed, "
                     "without translating.")
        else:
            self.say(f"I only know {_known()}.")

    def _usage(self):
        from . import usage
        self.say(usage.format_usage_card(), html=True)

    def _more(self):
        """The whole of the last result, in as many messages as that takes.

        The card is deliberately five shortened lines - a verdict read at a
        glance - and the answer to "did that work" is usually all anybody
        wants. This is the other times: the verdict says something went wrong
        and the reason is four lines above the ones shown, and the window it
        is all sitting in is on a laptop in another room.

        Separate messages rather than a longer card. The card has a shape that
        was argued over, it is edited in place while the chain runs, and a
        transcript pasted into it would be rewritten out from under you at the
        next poll.
        """
        if getattr(self, "_unreadable", False):
            self.say("The window could not be read before that step, so there "
                     "is no way to tell what it added. Look at the window, or "
                     "send the prompt again now that it is up.")
            return
        if self._full is None:
            self.say("Nothing has finished yet, so there is nothing more to "
                     "show. Send a prompt, and /more gives you the whole of "
                     "what comes back.")
            return
        if not self._full:
            self.say("The last step put nothing new in the window, so the card "
                     "is all there was. It may have answered somewhere this "
                     "cannot see.")
            return

        # Escaped inside _split, one whole piece at a time, so nothing here
        # can hand Telegram half an entity. See _split.
        parts = _split("\n".join(self._full), MESSAGE_CHARS - MORE_HEAD_CHARS)
        where = self._where or "the last step"
        for number, part in enumerate(parts, start=1):
            which = f" ({number}/{len(parts)})" if len(parts) > 1 else ""
            head = f"{ICON_MORE} <b>{_fit(where, 120)}{which}</b>"
            if number == 1 and self._dropped:
                # At the top, because what was dropped came off the top.
                head += (f"\nThe first {self._dropped} lines are not here; "
                         f"they are still in the window.")
            self.say(f"{head}\n\n{part}", html=True)

    def _shot(self):
        """A picture of the window, because you asked for one.

        The card a finished step sends back is built by diffing what the window
        said before against what it says after, through whichever profile
        recognises it - a reconstruction, and the thing you trust least at the
        moment you are asking whether something worked. This is the window
        itself, and windows whose profiles read them poorly photograph exactly
        as well as the ones that read cleanly.

        On request and never otherwise. A picture after every step is a few
        hundred kilobytes through Telegram, each time, for something usually
        not looked at.
        """
        if self.capture is None:
            self.say("Nothing here can take a picture - Relay is running "
                     "without the window thread that grabs one.")
            return
        hwnd = self._target()
        if not hwnd:
            self.say("Nothing to photograph. Send /target to choose a window, "
                     "or click into one on the laptop.")
            return

        title = window_title(hwnd)
        # Read before the grab rather than after, so it describes the picture
        # that was taken rather than the screen a second later.
        in_front = foreground_window() == hwnd
        blob, why = self.capture(hwnd)
        if not blob:
            self.say(f"No picture of {title}: {why}.")
            return

        self.log(f"[remote] photographed {title!r}, {len(blob) // 1024}kB")
        caption = self._describe()
        if not in_front:
            # Otherwise the picture is of something else entirely and reads as
            # the wrong window having been photographed. The whole desktop is
            # what gets grabbed - see shot.py - so whatever is on top is in it.
            caption += ("\n\nIt was not the window in front, so anything over "
                        "it is in the picture too.")
        self.send_photo(blob, caption)

    def _at(self, rest):
        """Hold a prompt back until a time you name.

        For the hours when the agent has run out of its allowance and says so.
        The work is ready, the window will not take it until the small hours,
        and you are not going to be awake for that - so it waits on disk and
        goes out by itself.

        The same shapes as /target: nothing after it lists what is waiting, and
        an argument acts.
        """
        rest = (rest or "").strip()
        if not rest:
            self.say(self._waiting_list())
            return

        first, _, tail = rest.partition(" ")
        if first.lower() == "cancel":
            which = tail.strip()
            if which and not which.isdigit():
                self.say("Send /at cancel to drop them all, or /at cancel 2 "
                         "to drop the second.")
                return
            gone = later.drop(int(which) if which else None)
            if not gone:
                self.say("Nothing was dropped.\n\n" + self._waiting_list())
                return
            word = "prompt" if gone == 1 else "prompts"
            self.say(f"Dropped {gone} {word}.\n\n" + self._waiting_list())
            return

        when = later.parse_time(first)
        if when is None:
            self.say("I did not understand that time. /at 05:00 your prompt, "
                     "or /at 5. It is always the next time it comes round, so "
                     "05:00 written at midnight means this morning.")
            return
        if not tail.strip():
            self.say(f"That is {later.in_words(when)}, but there is no prompt "
                     f"after it. /at {when:%H:%M} and then what to send.")
            return

        # Translated now rather than at the hour, so what you see is what will
        # be typed and there is a whole night in which to /at cancel it.
        prompt, _english = self._to_english(tail.strip())
        waiting = later.add(when, prompt)
        if waiting is None:
            self.say(f"Nothing was kept - there are already "
                     f"{later.MAX_WAITING} waiting, which is as many as this "
                     f"holds.\n\n" + self._waiting_list())
            return
        self.say(f"Kept for {later.in_words(when)}.\n\n{prompt}\n\n"
                 f"/at lists them, /at cancel drops them.")

    def _waiting_list(self):
        """What is held back, in the order it will go out."""
        waiting = later.load()
        if not waiting:
            return ("Nothing is waiting. Send /at 05:00 and then a prompt, and "
                    "it goes out at five - useful when the agent has run out "
                    "of its allowance and will not take anything until then.")
        from datetime import datetime

        lines = ["Waiting to go out:", ""]
        for number, entry in enumerate(waiting, start=1):
            when = datetime.fromisoformat(entry["at"])
            lines.append(f"{number}. {later.in_words(when)}")
            lines.append(f"    {entry['prompt'][:120]}")
        lines.append("")
        lines.append("/at cancel 1 drops one, /at cancel drops the lot.")
        return "\n".join(lines)

    def _release_due(self):
        """Queue anything whose hour has come. Called once a poll.

        Written back to disk before anything is queued rather than after: the
        other order sends a prompt and then, if the write fails or the process
        goes, sends it again on the next poll, and again - a prompt typed into
        an agent over and over while nobody is at the desk.
        """
        send, missed, waiting = later.due()
        if not send and not missed:
            return
        if not later.save(waiting):
            self.log("[later] could not write the schedule back; nothing released")
            return

        for entry in missed:
            self.say(f"This was due at {entry['at'][11:16]} and is more than "
                     f"{later.LATE_HOURS} hours late, so I have not sent "
                     f"it:\n\n{entry['prompt'][:300]}")

        if not send:
            return
        self.log(f"[later] releasing {len(send)} prompt(s)")
        if self._card is not None and not self.pending_own_card:
            self._new_card()
        self.pending_own_card = True
        for entry in send:
            self.pending.append(entry["prompt"])
            self._prompts.append(entry["prompt"])
        due_at = send[0]["at"][11:16]
        self._paint(icon=ICON_WORKING,
                    head=f"the {due_at} prompt - waiting for a free window")

    def _restart(self):
        """Leave, having asked the keeper to bring us straight back.

        For the state that has actually happened: the process alive and
        answering, and the thing it draws frozen. Nothing here can fix that
        from the inside, and quitting is a repair when something else is
        watching for the gap.

        The marker is what tells the keeper this was meant. Without it a
        deliberate exit and a crash look identical, and the keeper would
        announce a death you had asked for.
        """
        if not self.on_restart:
            self.say("There is no way to quit from here, so /restart would "
                     "leave you with nothing.")
            return
        if not self._watched():
            self.say("Nothing is watching, so if I go now nothing brings me "
                     "back and the phone goes dead until you are at the "
                     "laptop.\n\nStart the keeper first, or put it in Startup "
                     "so this cannot happen again:\n"
                     "python -m relay.keeper --at-logon")
            return
        self.say("Restarting. The keeper will bring me back in a few seconds.")
        try:
            RESTART_MARKER.write_text("asked from the phone\n", encoding="utf-8")
        except OSError as exc:
            self.log(f"[remote] could not write the restart marker: {exc}")
        # Before leaving, not after: there is no after. See _confirm.
        self._confirm()
        self.on_restart()

    def _watched(self):
        """Whether a keeper is out there. Never raises: this gates leaving."""
        try:
            return bool(self.keeper_watching())
        except Exception as exc:
            self.log(f"[remote] could not tell whether a keeper is watching: {exc}")
            return False

    def _warn_if_unwatched(self):
        """Say once, early, when nothing is watching.

        The failure this exists for is silent by nature: Relay comes back by
        itself at logon because it has a Startup entry, the keeper does not,
        and the two look identical from the phone until the evening you send
        /restart and nothing answers. One line at the start of a session costs
        nothing on the sessions where it is fine.
        """
        if self._warned_unwatched or self._stop.is_set():
            return
        if time.time() - self._started < UNWATCHED_GRACE:
            # A keeper started alongside this from the same Startup folder has
            # not necessarily claimed its name yet.
            return
        self._warned_unwatched = True
        if self._watched():
            return
        self.log("[remote] no keeper is watching")
        self.say("Nothing is watching this. Relay is up, but if it stops, "
                 "nothing brings it back and /restart will refuse.\n\n"
                 "python -m relay.keeper --at-logon")

    def _target_command(self, which):
        """List the windows worth writing into, or pin one of them.

        The point of the whole feature is not being at the laptop, and the
        target is otherwise the window you last clicked into - which is a thing
        you can only change by being there. So the list comes to the phone.
        """
        windows = agent.recognised_windows()
        if not windows:
            self.say("Nothing on screen that Relay knows how to read. Open "
                     "Claude, Antigravity or opencode.")
            return

        if which is None:
            lines = ["Which window should I write into?", ""]
            for number, (hwnd, title, profile) in enumerate(windows, start=1):
                mark = " (now)" if hwnd == self.chosen else ""
                lines.append(f"{number}. {title[:48]}{mark}")
                lines.append(f"    {profile['name']} - "
                             f"{self._plain(agent.state(hwnd, profile))}")
            lines.append("")
            lines.append("Send /target 1 to pick the first, and /target 0 to go "
                         "back to following whatever you last clicked into.")
            self.say("\n".join(lines))
            return

        if which == "0":
            self.chosen = None
            self._forget()
            self.say("Back to following the window you last clicked into.\n\n"
                     + self._describe())
            return

        if not which.isdigit() or not 1 <= int(which) <= len(windows):
            self.say(f"Pick a number between 1 and {len(windows)}, or 0 to "
                     f"follow your clicks again.")
            return

        hwnd, title, profile = windows[int(which) - 1]
        self.chosen = hwnd
        # Written down, so a restart does not quietly hand the next prompt to
        # whatever window happened to be clicked last.
        self._remember(title, profile.get("name"))
        self.say(f"Writing into {title}.\n\n" + self._describe())

    @staticmethod
    def _plain(state):
        return {
            agent.BUSY: "working",
            agent.IDLE: "free",
            agent.WAITING: "stopped, waiting for you to answer something",
            agent.UNKNOWN: "cannot tell",
        }.get(state, "cannot tell")

    def _describe(self):
        """What it can see, in the words a phone needs rather than the log's."""
        hwnd = self._target()
        if not hwnd:
            return ("No target. Send /target to choose a window, or click into "
                    "one on the laptop.")
        title = window_title(hwnd)
        profile = agent.profile_for(hwnd)
        if profile is None:
            return (f"{title}\n\nRelay does not know how to read this one, so "
                    f"it will not type into it. Send /target to choose one it "
                    f"does.")
        how = "pinned" if self.chosen else "following your clicks"
        queued = len(self.pending) + (1 if self.pilot.running else 0)
        return (f"{title}\nread as {profile['name']} - "
                f"{self._plain(agent.state(hwnd, profile))}\n"
                f"{queued} in the queue, {how}")

    def _target(self):
        """The pinned window if there is one, otherwise the one you last used.

        A pinned window that has since been closed is worse than none: every
        message would go to a handle that no longer exists. So it is dropped,
        once, with a word about it.
        """
        if self.chosen is None and self.remembered:
            self._find_remembered()
        if self.chosen is not None:
            if self.is_window(self.chosen):
                return self.chosen
            self.chosen = None
            self.say("The window I was pinned to has closed. Back to following "
                     "the one you last clicked into.")
        try:
            return self.target_getter()
        except Exception:
            return None

    def _find_remembered(self):
        """Take the pin back up after a restart, if the window is still there.

        A handle only means anything inside the process that asked for it, so
        what is written down is what the window was called. Measured: a target
        chosen from the phone survived four minutes and one restart, after
        which prompts went silently back to whatever had last been clicked -
        and the first anyone knew of it was a refusal naming a window nobody
        had chosen.

        Matched on the title first, and on the profile when only one window
        wears it: an application that puts the current document in its title
        is not called the same thing twice.
        """
        title, profile = self.remembered.get("title"), self.remembered.get("profile")
        windows = agent.recognised_windows()
        for hwnd, name, found in windows:
            if name == title:
                self.chosen = hwnd
                return
        same = [w for w in windows if w[2].get("name") == profile]
        if len(same) == 1:
            self.chosen = same[0][0]
            self.remembered["title"] = same[0][1]

    def _remember(self, title, profile):
        self.remembered = {"title": title, "profile": profile}
        save_setting(self.settings, "target", self.remembered)

    def _forget(self):
        self.remembered = None
        save_setting(self.settings, "target", None)

    def _drain(self):
        """Start a chain with everything waiting, once nothing else is running.

        Several messages sent in a row become one chain in the order they were
        sent, which is what makes a phone useful for more than a single line.
        """
        if self.pilot.running or not self.pending:
            return
        hwnd = self._target()
        steps = list(self.pending)
        if not self.pilot.start(steps, hwnd):
            self.pending.clear()
            self.pending_own_card = False
            self._paint(icon=ICON_STOPPED, head="could not start",
                        note=self._describe(), cancel_button=False)
            return
        self.pending.clear()
        self._said_waiting = False
        self._where = window_title(hwnd)
        self._paint(icon=ICON_SENDING, head=f"{self._where} - sending", cancel_button=True)

    def _step_of(self, index, total):
        return "" if total == 1 else f"step {index + 1} of {total} - "

    def _result(self, index, new_lines):
        """Send back what the window said, once a step has finished.

        Reading the whole of it would be useless on a phone - the transcript
        runs to two hundred lines - so this is the tail of what is new, with
        anything that looks like a failure lifted out of it first. The question
        being answered is "did that work", and an error twenty lines up is the
        answer even when the last line looks calm.
        """
        if new_lines is None:
            # The queue could not read the window before the step, so it cannot
            # say what came after. Saying nothing appeared would be a different
            # untruth from saying all of it did.
            self._full, self._dropped, self._unreadable = None, 0, True
            self._paint(note="The window could not be read before this step, so "
                             "there is no telling what in it is new. Whatever it "
                             "said is in the window itself.")
            return

        self._unreadable = False
        asked = {p.strip() for p in self._prompts}
        lines = [ln for ln in new_lines
                 if len(ln) > 1 and not _is_chrome(ln) and ln.strip() not in asked]
        # Kept whole, before the card shortens anything, because this is the
        # only moment the whole of it exists. Rebound rather than added to:
        # /more reads it from the poll thread while this runs on the queue's,
        # and a list being appended to is a list that can be read half built.
        self._full = _within(lines, MORE_CHARS)
        self._dropped = len(lines) - len(self._full)
        if not lines:
            self._paint(note="Nothing new appeared in the window - it may have "
                             "answered somewhere this cannot see.")
            return
        # The note is cleared with it. A step that could not be read leaves one
        # behind saying so, and left standing under a result that did read
        # cleanly the card says both things at once.
        self._paint(bad=[ln for ln in lines if _looks_wrong(ln)][:BAD_LINES],
                    tail=_last_of(lines), note="")

    def _progress(self, phase, index, total, seconds_left):
        """Called from the queue's thread, once a second. Rewrites the card.

        _paint does nothing when the words have not changed, which is most of
        the time: the queue reports the same phase every poll for as long as it
        lasts, and every edit counts against a rate limit at Telegram's end.
        """
        where = self._step_of(index, total) + (self._where or "")
        show_cancel = phase in (COUNTING, SENDING, STARTING, HOLDING, WAITING)
        if phase == WAITING:
            self._said_waiting = True
            self._paint(icon=ICON_NEEDS_YOU,
                        head=f"{where} needs you",
                        note="It has stopped to ask you something. Nothing "
                             "more goes out until you answer it.",
                        cancel_button=show_cancel)
        elif phase == COUNTING:
            self._paint(icon=ICON_SENDING, head=f"{where} - sending in "
                                                f"{seconds_left}s",
                        cancel_button=show_cancel)
        elif phase in (SENDING, STARTING):
            self._paint(icon=ICON_SENDING, head=f"{where} - sending",
                        cancel_button=show_cancel)
        elif phase == HOLDING:
            self._paint(icon=ICON_WORKING, head=f"{where} - working",
                        cancel_button=show_cancel)
        elif phase == DONE:
            self.pending_own_card = False
            # The icon carries the verdict, so a notification answers the
            # question before the message is even opened.
            self._paint(icon=ICON_TROUBLE if self._bad else ICON_DONE,
                        head=f"{self._where or 'Done'} - "
                             + ("done, with something to look at"
                                if self._bad else "done"),
                        cancel_button=False)
        elif phase == STOPPED:
            self.pending_own_card = False
            self._paint(icon=ICON_STOPPED, head=f"{where} - stopped",
                        note=self.pilot.reason or "",
                        cancel_button=False)
