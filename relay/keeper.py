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

    python -m relay.keeper                 watch until you close it
    python -m relay.keeper --at-logon      and every time you log in from now on
    python -m relay.keeper --not-at-logon  stop doing that

Leave it running. It costs a wake-up every few seconds and says nothing until
something happens.

The logon entry is a shortcut in your own Startup folder, not a scheduled task.
A task is a row in a list of system jobs, edited with a tool, and it outlives
any memory of having made it. This is a file, in a folder you can open, that
stops existing the moment you delete it - which for a program whose whole point
is being small enough to start and stop is the shape that matches.
"""

import argparse
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

# The keeper's own name, and deliberately not the one above. A keeper that
# claimed Relay's would answer its own liveness check for ever and never report
# a death - the one thing it exists to notice.
KEEPER_MUTEX_NAME = "Local\\RelayKeeper"
ERROR_ALREADY_EXISTS = 183

# Where the logon entry goes. Asked of Windows rather than assembled out of
# %APPDATA%, because on a redirected profile the assembled path exists and is
# writable and is still not the folder Explorer reads at logon.
CSIDL_STARTUP = 0x0007
SHORTCUT_NAME = "Relay Keeper.lnk"
SHORTCUT_NOTE = "Watches Relay and brings it back when your phone asks"

# Nothing here may flash a console, the installer included: a black rectangle
# appearing and going again reads as a fault rather than as work.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Everything Relay answers that this cannot do anything about while Relay is
# down. Written out rather than imported: the keeper imports nothing from
# `relay`, which is what keeps it simpler than the thing it watches. A second
# copy of a list drifts, so tests/test_keeper.py holds this against the real
# one - that test can see both, and this file cannot.
ANSWERED_BY_RELAY = ("/stop", "/target", "/keys", "/help", "/more", "/shot",
                     "/at")

WATCH_SECONDS = 5        # how often to look while Relay is up
POLL_SECONDS = 25        # how long a message request is held open while it is down
STARTUP_GRACE = 20       # how long to give it to claim the mutex after starting

# Offered while Relay is down, because /start is then the only thing worth
# doing, and taken away the moment it is back. Telegram keeps the last
# keyboard it was given until something replaces or removes it, so a panel
# that is merely stopped from being resent stays on screen for ever.
KEYBOARD = {
    "keyboard": [["/start", "/status"]],
    "resize_keyboard": True,
}
NO_KEYBOARD = {"remove_keyboard": True}


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


def keeper_is_watching():
    """True while a keeper holds its name. Asks; never takes it.

    Deliberately not another_keeper_running(), which claims the name when it is
    free - that is right for a keeper deciding whether to start and wrong for
    anybody else, because the asker would then hold the name and every later
    check would answer yes about itself.
    """
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, KEEPER_MUTEX_NAME)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


_claim = None


def another_keeper_running():
    """True if a keeper is already watching. Takes the name if it is free.

    Two keepers is two pollers on one Telegram bot, and Telegram gives each of
    them half the messages at random: /start would work one time in two. With a
    Startup entry as well as keeper.bat that is now one double-click away,
    which is the same trap the single-instance guard was written for.

    Asking and claiming are one call rather than two. Two keepers starting
    together would both look, both find nothing and both go on to watch;
    CreateMutexW says whether you got the name in the same breath as taking it.
    The handle is kept because the name is only held for as long as it is open.
    """
    global _claim
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        _claim = kernel32.CreateMutexW(None, False, KEEPER_MUTEX_NAME)
        return kernel32.GetLastError() == ERROR_ALREADY_EXISTS
    except Exception:
        return False    # never refuse to watch over a check that would not run


def launcher():
    """The interpreter to start with, without a console window.

    sys.executable is python.exe when this was started from a terminal, and a
    console flashing up on every restart is the sort of thing that makes an
    automatic recovery feel like a fault. The logon shortcut points at the same
    one, for the same reason.
    """
    here = Path(sys.executable)
    windowless = here.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else here)


class Keeper:
    def __init__(self, conf, api=call, alive=relay_is_running, start=None,
                 log=print, watch_seconds=WATCH_SECONDS,
                 poll_seconds=POLL_SECONDS, startup_grace=None):
        self.conf = conf
        self.api = api
        self.alive = alive
        self.start_relay = start or self._launch
        self.log = log
        self.watch_seconds = watch_seconds
        self.poll_seconds = poll_seconds
        self._startup_grace = startup_grace
        self.was_alive = None
        self.offset = 0
        self.running = True
        self.retry_wait = max(2, self.watch_seconds)
        self._offline = False

    @property
    def startup_grace(self):
        return self._startup_grace if self._startup_grace is not None else STARTUP_GRACE

    # --- talking to the phone --------------------------------------------

    def say(self, text, keys=True):
        """Send a line to the phone, and write down that it was sent.

        Logged because the keeper is silent by design: nothing it does appears
        on screen, so without this the only record of it having spoken is on a
        device that is not the one you would be looking at while working out
        whether it spoke.
        """
        params = {"chat_id": self.conf["chat_id"], "text": text}
        if keys is True:
            params["reply_markup"] = json.dumps(KEYBOARD)
        elif keys == "remove":
            params["reply_markup"] = json.dumps(NO_KEYBOARD)
        for attempt in range(3):
            try:
                self.api(self.conf["token"], "sendMessage", params, timeout=20)
                self.log(f"[keeper] told you: {text.splitlines()[0]}")
                return True
            except Exception as exc:
                if attempt < 2 and any(err in str(exc).lower() for err in ("11001", "10054", "10060", "timed out", "handshake")):
                    if self.watch_seconds > 0:
                        time.sleep(1.0 * (attempt + 1))
                    continue
                self.log(f"[keeper] could not send: {exc}")
                return False
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
        # Hand over cleanly: acknowledge the offset before starting Relay so
        # the command that started Relay is consumed before Relay's Remote
        # thread connects to Telegram.
        self.confirm()
        if not self.start_relay():
            self.say("Could not start Relay at all. Something is wrong with "
                     "the installation.")
            return False
        deadline = time.monotonic() + self.startup_grace
        while time.monotonic() < deadline:
            if self.alive():
                self.was_alive = True
                self.confirm()
                # Nothing left to press: Relay answers from here.
                self.say(f"Relay is back up. ({why})", keys="remove")
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
                self.say("Relay is running again.", keys="remove")
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

    def confirm(self):
        """Say we are done with what has been read, before going quiet."""
        try:
            self.api(self.conf["token"], "getUpdates",
                     {"offset": self.offset, "timeout": 0}, timeout=10)
        except Exception as exc:
            if "409" in str(exc):
                # Relay is already polling Telegram and has claimed the bot.
                return
            self.log(f"[keeper] could not confirm: {exc}")

    def poll(self):
        try:
            updates = self.api(self.conf["token"], "getUpdates",
                               {"offset": self.offset,
                                "timeout": self.poll_seconds}) or []
        except Exception as exc:
            exc_str = str(exc)
            if "409" in exc_str:
                # 409 Conflict: another instance called getUpdates.
                # If Relay is alive, it claimed the bot - hand over cleanly.
                if self.alive():
                    self.was_alive = True
                    return []
                # If Relay is not visible yet, wait briefly to see if it claims the mutex.
                if self.watch_seconds > 0:
                    time.sleep(self.watch_seconds)
                if self.alive():
                    self.was_alive = True
                    return []
                self.log("[keeper] telegram conflict: another instance is polling")
                return []

            if not self._offline:
                self.log(f"[keeper] not reachable ({exc})")
                self._offline = True

            sleep_time = self.retry_wait if self.watch_seconds > 0 else 0
            if sleep_time > 0:
                time.sleep(sleep_time)
            self.retry_wait = min(self.retry_wait * 2, 60)
            return []

        if self._offline:
            self.log("[keeper] connection restored")
            self._offline = False
        self.retry_wait = max(2, self.watch_seconds)
        for update in updates:
            self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
        return updates

    def handle(self, update):
        message = update.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        if not chat or int(chat) != int(self.conf["chat_id"]):
            return
        text = (message.get("text") or "").strip().lower()

        # A photo or a voice note has no command in it, and falls through to
        # the same answer as a prompt would. Returning early on it would put
        # back the silence this program exists to break.
        command = text.split()[0] if text else ""
        if command in ("/start", "/restart"):
            self.bring_back("you asked for it")
        elif command == "/status":
            self.say("Relay is not running.\n\nSend /start to bring it back.")
        elif command in ANSWERED_BY_RELAY:
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
        if not self.was_alive and self.startup_grace > 0 and self.watch_seconds > 0:
            # When started alongside Relay (such as at Windows logon), Relay
            # may still be loading Python, PySide and its models. Wait a few
            # moments before declaring it dead and fighting it for Telegram updates.
            deadline = time.monotonic() + self.startup_grace
            while time.monotonic() < deadline and self.running:
                time.sleep(min(0.5, self.watch_seconds))
                if self.alive():
                    self.was_alive = True
                    break
        if not self.was_alive:
            self.say("The keeper is up, but Relay is not running.\n\n"
                     "Send /start to bring it back.")
        while self.running:
            try:
                wait = self.tick()
            except Exception as exc:
                # The one thing this program must not do is stop. It is what
                # notices that Relay has gone, so a keeper that fell over on an
                # odd message would leave nothing watching - and no sign of it
                # anywhere, because the thing that reports trouble is the thing
                # that died.
                self.log(f"[keeper] carrying on after {exc!r}")
                wait = self.watch_seconds
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


# --- starting at logon ----------------------------------------------------


def startup_folder():
    """Your own Startup folder, as Windows itself reports it."""
    buffer = ctypes.create_unicode_buffer(260)
    result = ctypes.windll.shell32.SHGetFolderPathW(
        None, CSIDL_STARTUP, None, 0, buffer)
    if result != 0 or not buffer.value:
        raise OSError(f"Windows would not say where Startup is ({result})")
    return Path(buffer.value)


def shortcut_path(folder=None):
    """Where the logon entry lives. `folder` is for the tests and nothing else."""
    return Path(folder if folder is not None else startup_folder()) / SHORTCUT_NAME


def _literal(value):
    """`value` as a PowerShell string that can be read as nothing else.

    Single quotes rather than double: a double-quoted string interpolates, and
    a path under an account name holding a `$` - which Windows allows - would
    arrive with the rest of the word eaten as a variable name, leaving a
    shortcut that points at nothing and says nothing about why.
    """
    return "'" + str(value).replace("'", "''") + "'"


def _powershell(script):
    """Run one command. Returns what it complained about, or None."""
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, errors="replace",
            creationflags=CREATE_NO_WINDOW)
    except OSError as exc:
        return str(exc)
    if done.returncode != 0:
        return (done.stderr or done.stdout or "").strip() or "PowerShell said no"
    return None


def install_at_logon(folder=None):
    """Write the Startup shortcut, and hand back where it went.

    A .lnk is a binary format, and the two libraries that write one - pywin32
    and winshell - are dependencies this project will not take. It does not
    need them: WScript.Shell is the COM object Explorer's own "Create shortcut"
    ends up in, install.ps1 already writes Relay's entry through it, and one
    PowerShell command holds it for as long as saving a file takes.
    """
    path = shortcut_path(folder)
    icon = PROJECT_ROOT / "assets" / "relay.ico"
    script = [
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_literal(path)})",
        f"$s.TargetPath = {_literal(launcher())}",
        f"$s.Arguments = {_literal('-m relay.keeper')}",
        f"$s.WorkingDirectory = {_literal(PROJECT_ROOT)}",
        f"$s.Description = {_literal(SHORTCUT_NOTE)}",
    ]
    if icon.exists():
        script.append(f"$s.IconLocation = {_literal(icon)}")
    # Minimised as well as windowless, the pair install.ps1 uses. pythonw has
    # no console to show, and this covers the machine where it has gone missing
    # and launcher() has fallen back to python.exe.
    script += ["$s.WindowStyle = 7", "$s.Save()"]

    complaint = _powershell("; ".join(script))
    # PowerShell reports a statement that threw with an exit code of zero often
    # enough that its word is not worth taking. The file either turned up or it
    # did not.
    if not path.exists():
        raise OSError(complaint or "PowerShell wrote no shortcut and gave no reason")
    return path


def remove_from_logon(folder=None):
    """Take the shortcut out again. True if there was one to take out.

    One file deleted - which is the whole argument for a shortcut over a
    scheduled task. Doing it by hand in Explorer is the same operation, so
    nobody has to find this flag again months from now.
    """
    try:
        shortcut_path(folder).unlink()
        return True
    except FileNotFoundError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m relay.keeper",
        description="Watch Relay, and bring it back when your phone asks.",
        epilog="The logon entry is one shortcut in your own Startup folder. "
               "No service, no scheduled task, nothing in the registry - so "
               "deleting that file is the whole of undoing it.")
    logon = parser.add_mutually_exclusive_group()
    logon.add_argument(
        "--at-logon", action="store_true",
        help="put a shortcut in your Startup folder, so this starts without a "
             "window every time you log in")
    logon.add_argument(
        "--not-at-logon", action="store_true",
        help="take that shortcut out again; deleting the file yourself does "
             "exactly the same thing")

    # Before the arguments, not after: started without a console, sys.stderr is
    # None, and argparse reporting a mistyped flag would then die inside the
    # report rather than print one.
    own_output()
    args = parser.parse_args(argv)

    if args.at_logon:
        try:
            path = install_at_logon()
        except OSError as exc:
            print(f"Could not write the shortcut: {exc}")
            return 1
        print(f"Starts at logon now:\n  {path}")
        print("Nothing appears on screen when it does; it writes to "
              f"{PROJECT_ROOT / 'keeper.log'}.")
        print("Undo it with --not-at-logon, or just delete that shortcut.")
        return 0

    if args.not_at_logon:
        try:
            path = shortcut_path()
            removed = remove_from_logon()
        except OSError as exc:
            print(f"Could not find your Startup folder: {exc}")
            return 1
        print(f"Removed {path}" if removed else f"Nothing to remove at {path}")
        return 0

    if another_keeper_running():
        print("[keeper] one is already watching; not starting a second")
        return 1

    conf = settings()
    if conf is None:
        print("No bot token, or no chat paired yet. Set one up in Relay first:")
        print("  python -m relay --set-token")
        return 1
    Keeper(conf).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
