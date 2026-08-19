"""Watches Relay, tells you when it is gone, and starts it when you ask.

The bot inside Relay cannot answer for a Relay that is not running - it is a
thread in that process, and a dead process has no threads. Whatever brings the
application back has to be something else, already running, and small enough
that it is not going to fall over the same way.

So this imports nothing from `relay`. Not tidiness: the keeper's whole purpose
is to be simpler than the thing it watches, and a keeper that dragged in the
window system, the audio stack and the accessibility layer would inherit every
failure it exists to recover from. Four modules from the standard library, and
one file read for the token.

It never competes for messages. While Relay is up, Relay answers Telegram and
this only counts seconds; the moment Relay is gone, this starts answering.
Two pollers on one bot would each get half the messages at random, which is
the sort of fault you would spend an evening on.

    python -m relay.keeper

Leave it running. It costs a wake-up every few seconds and says nothing until
something happens.
"""

import ctypes
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_ROOT / "telegram.json"

# Written by Relay on its way out when you asked it to restart, so this can
# tell "he asked for this" from "it fell over" and act accordingly.
RESTART_MARKER = PROJECT_ROOT / ".relay-restart"

API = "https://api.telegram.org/bot{token}/{method}"

# The same name Relay's single-instance guard creates. Opening it is the whole
# liveness check: no process list to walk, no pid file to go stale, and it
# disappears by itself when the process holding it ends, however it ends.
MUTEX_NAME = "Local\\RelaySingleInstance"
SYNCHRONIZE = 0x00100000

WATCH_SECONDS = 5        # how often to look while Relay is up
POLL_SECONDS = 25        # how long a message request is held open while it is down
STARTUP_GRACE = 20       # how long to give it to claim the mutex after starting

KEYBOARD = {
    "keyboard": [["/start", "/status"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def settings():
    """The token and chat from the file Relay already uses. None if not set up."""
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = str(data.get("token") or "").strip()
    chat = data.get("chat_id")
    if not token or not chat:
        return None
    return {"token": token, "chat_id": int(chat)}


def call(token, method, params, timeout=40):
    url = API.format(token=token, method=method)
    body = urllib.parse.urlencode(params).encode("utf-8")
    with urllib.request.urlopen(
            urllib.request.Request(url, data=body), timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "telegram said no"))
    return payload.get("result")


def relay_is_running():
    """True while something holds Relay's single-instance mutex."""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, MUTEX_NAME)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def launcher():
    """The interpreter to start Relay with, without a console window.

    sys.executable is python.exe when this was started from a terminal, and a
    console flashing up on every restart is the sort of thing that makes an
    automatic recovery feel like a fault.
    """
    here = Path(sys.executable)
    windowless = here.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else here)


class Keeper:
    def __init__(self, conf, api=call, alive=relay_is_running, start=None,
                 log=print, watch_seconds=WATCH_SECONDS,
                 poll_seconds=POLL_SECONDS):
        self.conf = conf
        self.api = api
        self.alive = alive
        self.start_relay = start or self._launch
        self.log = log
        self.watch_seconds = watch_seconds
        self.poll_seconds = poll_seconds
        self.was_alive = None
        self.offset = 0
        self.running = True

    # --- talking to the phone --------------------------------------------

    def say(self, text, keys=True):
        params = {"chat_id": self.conf["chat_id"], "text": text}
        if keys:
            params["reply_markup"] = json.dumps(KEYBOARD)
        try:
            self.api(self.conf["token"], "sendMessage", params, timeout=20)
            return True
        except Exception as exc:
            self.log(f"[keeper] could not send: {exc}")
            return False

    # --- starting it ------------------------------------------------------

    def _launch(self):
        try:
            subprocess.Popen([launcher(), "-m", "relay"],
                             cwd=str(PROJECT_ROOT), close_fds=True)
            return True
        except Exception as exc:
            self.log(f"[keeper] could not start Relay: {exc}")
            return False

    def bring_back(self, why):
        """Start Relay and wait to see it actually claim the mutex.

        Reporting success the moment Popen returns would report that a process
        was created, which is not the same as an application that came up - and
        the difference is exactly the case worth knowing about.
        """
        self.log(f"[keeper] starting Relay ({why})")
        if not self.start_relay():
            self.say("Could not start Relay at all. Something is wrong with "
                     "the installation.")
            return False
        deadline = time.monotonic() + STARTUP_GRACE
        while time.monotonic() < deadline:
            if self.alive():
                self.was_alive = True
                self.say(f"Relay is back up. ({why})")
                return True
            time.sleep(1)
        self.say("Relay was started but has not come up. It may be loading the "
                 "speech model, or it may have failed - send /status in a "
                 "minute.")
        return False

    # --- the loop ---------------------------------------------------------

    def tick(self):
        """One pass. Returns how long to wait before the next one."""
        alive = self.alive()

        if alive:
            if self.was_alive is False:
                self.say("Relay is running again.")
            self.was_alive = True
            return self.watch_seconds

        if self.was_alive:
            # It was up a moment ago and is not now.
            if RESTART_MARKER.exists():
                # It went on purpose, and asked to be brought straight back.
                try:
                    RESTART_MARKER.unlink()
                except OSError:
                    pass
                self.was_alive = False
                self.bring_back("you asked it to restart")
                return self.watch_seconds
            self.say("Relay has stopped.\n\nSend /start to bring it back.")
        self.was_alive = False

        # Only now, with Relay down, does this listen - so the two are never
        # both asking Telegram for the same messages.
        for update in self.poll():
            self.handle(update)
        return 0

    def poll(self):
        try:
            updates = self.api(self.conf["token"], "getUpdates",
                               {"offset": self.offset,
                                "timeout": self.poll_seconds}) or []
        except Exception as exc:
            self.log(f"[keeper] not reachable ({exc})")
            time.sleep(self.watch_seconds)
            return []
        for update in updates:
            self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
        return updates

    def handle(self, update):
        message = update.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip().lower()
        if not chat or int(chat) != int(self.conf["chat_id"]) or not text:
            return

        command = text.split()[0]
        if command in ("/start", "/restart"):
            self.bring_back("you asked for it")
        elif command == "/status":
            self.say("Relay is not running.\n\nSend /start to bring it back.")
        elif command in ("/stop", "/target"):
            self.say(f"Relay is not running, so {command} has nothing to do.\n\n"
                     f"Send /start first.")
        else:
            # A prompt sent while nothing was there to receive it. Saying so is
            # the whole point: silence would look exactly like it having been
            # queued.
            self.say("Relay is not running, so that was not queued.\n\n"
                     "Send /start, then send it again.")

    def run(self):
        self.log("[keeper] watching")
        self.was_alive = self.alive()
        if not self.was_alive:
            self.say("The keeper is up, but Relay is not running.\n\n"
                     "Send /start to bring it back.")
        while self.running:
            wait = self.tick()
            if wait:
                time.sleep(wait)


def own_output():
    """Somewhere to print to when there is no console.

    Started without a window - which is how it will be started, since a
    console flashing up at logon is not wanted - sys.stdout is None and the
    first print() raises AttributeError. The keeper would then die on its own
    first line, which is a particularly poor showing for a program whose job
    is not dying.
    """
    if sys.stdout is not None:
        return None
    path = PROJECT_ROOT / "keeper.log"
    try:
        handle = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    sys.stdout = sys.stderr = handle
    print(f"\n=== keeper started {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    return path


def main():
    own_output()
    conf = settings()
    if conf is None:
        print("No bot token, or no chat paired yet. Set one up in Relay first:")
        print("  python -m relay --set-token")
        return 1
    Keeper(conf).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
