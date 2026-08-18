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

# How much of a finished step to send back. A transcript runs to a couple of
# hundred lines and a phone is not the place to read one.
RESULT_LINES = 12
BAD_LINES = 6

# Words that mean a step did not do what it was asked. Kept deliberately short:
# every addition is another way for an ordinary sentence to be flagged, and a
# summary that cries wolf gets skimmed and then ignored.
TROUBLE = (
    "error", "exception", "traceback", "failed", "failure", "cannot",
    "could not", "denied", "not found", "no such", "fatal", "refused",
    "eroare", "nu a reusit", "nu s-a putut",
)

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
    low = line.lower()
    return any(word in low for word in TROUBLE)


def _is_chrome(line):
    low = line.strip().lower()
    return any(low == word or low.startswith(word) for word in CHROME)

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

    def __init__(self, settings, send, target_getter, log=print,
                 api=call_api, is_window=None, translate=None):
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
        self.translate = translate
        self.pilot = Autopilot(send=send, on_progress=self._progress,
                               on_result=self._result, log=log)
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
                     "Write in Romanian and it goes into the window you were "
                     "last working in, in English. /target chooses the window, "
                     "/status says what it can see, /stop cancels.")
            return

        if int(chat) != int(self.chat_id):
            # Somebody else found the bot. Say nothing to them at all.
            self.log(f"[remote] ignored a message from chat {chat}")
            return

        if text.startswith("/"):
            self._command(text)
            return

        prompt, english = self._to_english(text)
        self.pending.append(prompt)
        if english and english != text:
            # The translation goes back to the phone, not just into the queue.
            # It is what will actually be typed, and seeing it is the only
            # chance to /stop a sentence the model got wrong before it lands.
            self.say(f"Queued, {len(self.pending)} waiting:\n\n{english}")
        else:
            self.say(f"Queued. {len(self.pending)} waiting.")

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
        elif command in ("/start", "/help"):
            self.say("Write in Romanian. It is translated to English and typed "
                     "into the window you were last working in, once whatever "
                     "is in there has finished.\n\n"
                     "/status  what it can see right now\n"
                     "/target  choose which window to write into\n"
                     "/stop    cancel the queue\n\n"
                     "Start a line with = to send it exactly as typed, without "
                     "translating.")
        else:
            self.say("I only know /status, /target, /stop and /help.")

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
            self.say("Back to following the window you last clicked into.\n\n"
                     + self._describe())
            return

        if not which.isdigit() or not 1 <= int(which) <= len(windows):
            self.say(f"Pick a number between 1 and {len(windows)}, or 0 to "
                     f"follow your clicks again.")
            return

        hwnd, title, profile = windows[int(which) - 1]
        self.chosen = hwnd
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

    def _result(self, index, new_lines):
        """Send back what the window said, once a step has finished.

        Reading the whole of it would be useless on a phone - the transcript
        runs to two hundred lines - so this is the tail of what is new, with
        anything that looks like a failure lifted out of it first. The question
        being answered is "did that work", and an error twenty lines up is the
        answer even when the last line looks calm.
        """
        lines = [ln for ln in new_lines if len(ln) > 1 and not _is_chrome(ln)]
        if not lines:
            self.say(f"Step {index + 1} finished. Nothing new appeared in the "
                     f"window - which may mean it answered somewhere this "
                     f"cannot see.")
            return

        bad = [ln for ln in lines if _looks_wrong(ln)][:BAD_LINES]
        tail = lines[-RESULT_LINES:]

        out = [f"Step {index + 1} finished."]
        if bad:
            out.append("")
            out.append(f"Something to look at ({len(bad)} of them):")
            out.extend(f"  {ln[:180]}" for ln in bad)
        out.append("")
        out.append("It ended with:")
        out.extend(f"  {ln[:180]}" for ln in tail)
        self.say("\n".join(out))

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
