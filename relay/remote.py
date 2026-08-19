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
from collections import deque
from pathlib import Path

from . import agent
from .autopilot import (Autopilot, COUNTING, DONE, HOLDING, SENDING,
                        STARTING, STOPPED, WAITING)
from .target import window_title

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

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram holds the request open until something arrives or this many seconds
# pass, so an idle bot costs one connection rather than a request a second.
POLL_SECONDS = 30
HTTP_TIMEOUT = POLL_SECONDS + 15

# After a failure, wait before trying again, and wait longer each time. A
# laptop that closes its lid on a train should not fill the log with one line
# a second until it lands.
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
RESULT_CHARS = 500
LINE_CHARS = 140
BAD_LINES = 4

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


def _esc(text):
    """Telegram parses the card as HTML, so the text inside it cannot be."""
    return (str(text).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))

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
        line = line[:LINE_CHARS]
        if out and len(line) > budget:
            break
        out.append(line)
        budget -= len(line)
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


def call_api(token, method, params, timeout=HTTP_TIMEOUT):
    """One Telegram API call. Returns the result field, or raises."""
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "telegram said no"))
    return payload.get("result")


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
                 on_restart=None):
        self.settings = settings
        self.target_getter = target_getter
        # Injected for the same reason the queue injects it: whether a
        # window still exists is a question for Windows, and a test cannot
        # conjure a real one to ask about.
        self.is_window = is_window or (lambda hwnd: bool(
            __import__('ctypes').windll.user32.IsWindow(hwnd)))
        self.log = log
        self.api = api
        self.pending = deque()
        # A window picked from the phone with /target. None means follow
        # whatever you last clicked into, which is what everything else does.
        self.chosen = None
        # What /target chose, by name rather than by handle, so it can be
        # taken back up after a restart. See _find_remembered.
        self.remembered = settings.get("target") or None
        self.translate = translate
        # How to leave, when asked to. None means nobody is watching for
        # the gap, so /restart refuses rather than switching the lights off.
        self.on_restart = on_restart
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
        self.pilot = Autopilot(send=send, on_progress=self._progress,
                               on_result=self._result, log=log)
        self._stop = threading.Event()
        self._thread = None
        self._offset = 0
        # Anything older than this was addressed to a Relay that no longer
        # exists. See _handle.
        self._started = time.time()
        self._said_waiting = False

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
            try:
                self.edit(self._card, self._render(), timeout=6)
            except Exception:
                pass

    @property
    def chat_id(self):
        return self.settings.get("chat_id")

    def say(self, text, keys=False):
        """Send a message. Returns its id, so it can be edited later."""
        if not self.chat_id:
            return None
        params = {"chat_id": self.chat_id, "text": text[:3900],
                  "parse_mode": "HTML"}
        if keys:
            params["reply_markup"] = json.dumps(KEYBOARD)
        try:
            result = self.api(
                self.settings["token"], "sendMessage", params, timeout=20)
            return (result or {}).get("message_id")
        except Exception as exc:
            self.log(f"[remote] could not reply: {exc}")
            return None

    def edit(self, message_id, text, timeout=20):
        """Rewrite a message already sent. False if it could not be done."""
        if not self.chat_id or not message_id:
            return False
        try:
            self.api(
                self.settings["token"], "editMessageText",
                {"chat_id": self.chat_id, "message_id": message_id,
                 "text": text[:3900], "parse_mode": "HTML"}, timeout=timeout)
            return True
        except Exception as exc:
            self.log(f"[remote] could not edit: {exc}")
            return False

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
                          + "\n".join(_esc(p[:CARD_PROMPT_CHARS]) for p in shown))

        if self._bad:
            word = "problem" if len(self._bad) == 1 else "problems"
            blocks.append(f"⚠️ <b>{len(self._bad)} {word}</b>\n"
                          + "\n".join(_esc(ln[:180]) for ln in self._bad))

        if self._tail:
            blocks.append("<b>What it said</b>\n"
                          + "\n".join(_esc(ln[:180]) for ln in self._tail))

        if self._note:
            blocks.append(_esc(self._note))

        return "\n\n".join(b for b in blocks if b)

    def _paint(self, icon=None, head=None, bad=None, tail=None, note=None):
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

        text = self._render()
        if text == self._painted:
            return
        self._painted = text
        if self._card is None:
            self._card = self.say(text)
        elif not self.edit(self._card, text):
            # The message may have been deleted from the phone. Start another
            # rather than going quiet for the rest of the chain.
            self._card = self.say(text)

    def _new_card(self):
        self._card = None
        self._painted = None
        self._icon = ICON_WORKING
        self._head = ""
        self._prompts = []
        self._bad = []
        self._tail = []
        self._note = ""

    # --- the loop --------------------------------------------------------

    def _run(self):
        self.log("[remote] listening"
                 + ("" if self.chat_id else " - the first message will claim the bot"))
        wait = RETRY_START
        while not self._stop.is_set():
            try:
                for update in self._poll():
                    self._handle(update)
                wait = RETRY_START
            except Exception as exc:
                # Offline, asleep, or Telegram having a moment. None of those
                # deserve a line a second.
                self.log(f"[remote] not reachable ({exc}); retrying in {wait}s")
                if self._stop.wait(wait):
                    return
                wait = min(wait * 2, RETRY_MAX)
                continue
            self._drain()

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

    def _handle(self, update):
        message = update.get("message") or update.get("edited_message") or {}
        chat = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()
        if not chat or not text:
            return

        # Anything sent before this process existed was meant for a Relay that
        # was not there, and acting on it now is acting on the past. It matters
        # for one command in particular: a /restart left unconfirmed is handed
        # to the next Relay, which restarts, which is handed it again. That ran
        # four times in a row before it was stopped by hand, and would have run
        # until the machine was turned off.
        when = message.get("date")
        if when and float(when) < self._started:
            self.log(f"[remote] ignoring a message from before I started: "
                     f"{text[:40]!r}")
            return

        if self.chat_id is None:
            self.settings["chat_id"] = int(chat)
            save_chat_id(self.settings, int(chat))
            self.log(f"[remote] paired with chat {chat}")
            self.say("Paired. Only this chat can drive the laptop now.\n\n"
                     "Write in Romanian and it goes into the window you were "
                     "last working in, in English. /target chooses the window, "
                     "/status says what it can see, /stop cancels.", keys=True)
            return

        if int(chat) != int(self.chat_id):
            # Somebody else found the bot. Say nothing to them at all.
            self.log(f"[remote] ignored a message from chat {chat}")
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
        elif command == "/target":
            self._target_command(parts[1] if len(parts) > 1 else None)
        elif command == "/stop":
            self.pending.clear()
            if self.pilot.running:
                self.pilot.stop("you stopped it from your phone")
                self.say("Stopped, and the queue is empty.")
            else:
                self.say("Nothing was running. The queue is empty.")
        elif command == "/restart":
            self._restart()
        elif command in ("/start", "/help"):
            self.say("Write in Romanian. It is translated to English and typed "
                     "into the window you were last working in, once whatever "
                     "is in there has finished.\n\n"
                     "/status  what it can see right now\n"
                     "/target  choose which window to write into\n"
                     "/stop    cancel the queue\n\n"
                     "Start a line with = to send it exactly as typed, without "
                     "translating.", keys=True)
        else:
            self.say("I only know /status, /target, /stop, /restart and "
                     "/help.")

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
            self.say("There is no keeper running, so nothing would start me "
                     "again. Leaving myself off is worse than being stuck.")
            return
        self.say("Restarting. The keeper will bring me back in a few seconds.")
        try:
            RESTART_MARKER.write_text("asked from the phone\n", encoding="utf-8")
        except OSError as exc:
            self.log(f"[remote] could not write the restart marker: {exc}")
        # Before leaving, not after: there is no after. See _confirm.
        self._confirm()
        self.on_restart()

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
                        note=self._describe())
            return
        self.pending.clear()
        self._said_waiting = False
        self._where = window_title(hwnd)
        self._paint(icon=ICON_SENDING, head=f"{self._where} - sending")

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
        asked = {p.strip() for p in self._prompts}
        lines = [ln for ln in new_lines
                 if len(ln) > 1 and not _is_chrome(ln) and ln.strip() not in asked]
        if not lines:
            self._paint(note="Nothing new appeared in the window - it may have "
                             "answered somewhere this cannot see.")
            return
        self._paint(bad=[ln for ln in lines if _looks_wrong(ln)][:BAD_LINES],
                    tail=_last_of(lines))

    def _progress(self, phase, index, total, seconds_left):
        """Called from the queue's thread, once a second. Rewrites the card.

        _paint does nothing when the words have not changed, which is most of
        the time: the queue reports the same phase every poll for as long as it
        lasts, and every edit counts against a rate limit at Telegram's end.
        """
        where = self._step_of(index, total) + (self._where or "")
        if phase == WAITING:
            self._said_waiting = True
            self._paint(icon=ICON_NEEDS_YOU,
                        head=f"{where} needs you",
                        note="It has stopped to ask you something. Nothing "
                             "more goes out until you answer it.")
        elif phase == COUNTING:
            self._paint(icon=ICON_SENDING, head=f"{where} - sending in "
                                                f"{seconds_left}s")
        elif phase in (SENDING, STARTING):
            self._paint(icon=ICON_SENDING, head=f"{where} - sending")
        elif phase == HOLDING:
            self._paint(icon=ICON_WORKING, head=f"{where} - working")
        elif phase == DONE:
            self.pending_own_card = False
            # The icon carries the verdict, so a notification answers the
            # question before the message is even opened.
            self._paint(icon=ICON_TROUBLE if self._bad else ICON_DONE,
                        head=f"{self._where or 'Done'} - "
                             + ("done, with something to look at"
                                if self._bad else "done"))
        elif phase == STOPPED:
            self.pending_own_card = False
            self._paint(icon=ICON_STOPPED, head=f"{where} - stopped",
                        note=self.pilot.reason or "")
