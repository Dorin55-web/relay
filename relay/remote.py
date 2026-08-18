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
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

from . import agent
from .autopilot import DONE, STOPPED, WAITING, Autopilot
from .target import window_title

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Its own file, never config.json: that one is in the repository, and this
# holds a credential that must not be.
SETTINGS_PATH = PROJECT_ROOT / "telegram.json"

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
    return {"token": token, "chat_id": int(chat) if chat else None, "path": path}


def save_chat_id(settings, chat_id):
    """Write down which chat claimed the bot, so it survives a restart."""
    path = Path(settings["path"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = dict(TEMPLATE)
        data["token"] = settings["token"]
    data["chat_id"] = chat_id
    try:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        print(f"[remote] could not remember the chat id: {exc}")
        return False


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

    def __init__(self, settings, send, target_getter, log=print, api=call_api):
        self.settings = settings
        self.target_getter = target_getter
        self.log = log
        self.api = api
        self.pending = deque()
        self.pilot = Autopilot(send=send, on_progress=self._progress, log=log)
        self._stop = threading.Event()
        self._thread = None
        self._offset = 0
        self._said_waiting = False

    # --- outside world ---------------------------------------------------

    def start(self):
        self._thread = threading.Thread(
            target=self._run, name="remote", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    @property
    def chat_id(self):
        return self.settings.get("chat_id")

    def say(self, text):
        """Send a line back to the phone. Never fatal: this is commentary."""
        if not self.chat_id:
            return False
        try:
            self.api(self.settings["token"], "sendMessage",
                     {"chat_id": self.chat_id, "text": text[:3900]},
                     timeout=20)
            return True
        except Exception as exc:
            self.log(f"[remote] could not reply: {exc}")
            return False

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

    def _handle(self, update):
        message = update.get("message") or update.get("edited_message") or {}
        chat = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()
        if not chat or not text:
            return

        if self.chat_id is None:
            self.settings["chat_id"] = int(chat)
            save_chat_id(self.settings, int(chat))
            self.log(f"[remote] paired with chat {chat}")
            self.say("Paired. Only this chat can drive the laptop now.\n\n"
                     "Send any text and it goes into the window you were last "
                     "working in. /status says what it can see, /stop cancels.")
            return

        if int(chat) != int(self.chat_id):
            # Somebody else found the bot. Say nothing to them at all.
            self.log(f"[remote] ignored a message from chat {chat}")
            return

        if text.startswith("/"):
            self._command(text.split()[0].lower())
            return

        self.pending.append(text)
        self.say(f"Queued. {len(self.pending)} waiting.")

    def _command(self, command):
        if command in ("/status", "/state"):
            self.say(self._describe())
        elif command == "/stop":
            self.pending.clear()
            if self.pilot.running:
                self.pilot.stop("you stopped it from your phone")
                self.say("Stopped, and the queue is empty.")
            else:
                self.say("Nothing was running. The queue is empty.")
        elif command in ("/start", "/help"):
            self.say("Send text and it is typed into the window you were last "
                     "working in, once whatever is in there has finished.\n\n"
                     "/status  what it can see right now\n"
                     "/stop    cancel the queue")
        else:
            self.say("I only know /status, /stop and /help.")

    def _describe(self):
        """What it can see, in the words a phone needs rather than the log's."""
        hwnd = self._target()
        if not hwnd:
            return "No target. Click into the window you want to drive."
        title = window_title(hwnd)
        profile = agent.profile_for(hwnd)
        if profile is None:
            return (f"{title}\n\nRelay does not know how to read this one, so "
                    f"it will not type into it.")
        state = {
            agent.BUSY: "working",
            agent.IDLE: "free",
            agent.WAITING: "stopped, waiting for you to answer something",
            agent.UNKNOWN: "cannot tell",
        }.get(agent.state(hwnd, profile), "cannot tell")
        queued = len(self.pending) + (1 if self.pilot.running else 0)
        return (f"{title}\nread as {profile['name']} - {state}\n"
                f"{queued} in the queue")

    def _target(self):
        try:
            return self.target_getter()
        except Exception:
            return None

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
            self.say("Could not start.\n\n" + self._describe())
            return
        self.pending.clear()
        self._said_waiting = False
        self.say(f"Sending {len(steps)} to {window_title(hwnd)}.")

    def _progress(self, phase, index, total, seconds_left):
        """Called from the queue's thread. Only the ends are worth a message.

        Except one middle. An agent that has stopped to ask permission will sit
        there until somebody answers, and the entire point of this is that you
        are not in the room to notice.
        """
        if phase == WAITING and not self._said_waiting:
            self._said_waiting = True
            self.say(f"Step {index + 1} of {total}: it has stopped to ask you "
                     f"something. Nothing more goes out until you answer.")
        elif phase == DONE:
            self.say(f"All {total} done.")
        elif phase == STOPPED:
            self.say(f"Stopped at step {index + 1} of {total}"
                     + (f": {self.pilot.reason}" if self.pilot.reason else "."))
