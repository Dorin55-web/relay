"""The process that watches Relay, without watching a real one.

Whether it is alive, whether Telegram answers, and whether starting it works
are all injected, so the thing under test is the reasoning: when to speak, when
to listen, and when to keep quiet.

The check that matters most is that it never listens while Relay is up. Two
programs asking Telegram for the same messages get half each, at random, and
the resulting fault would look like the phone dropping messages for no reason.
"""
import sys
import tempfile
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

from relay import keeper as keeper_mod  # noqa: E402
from relay.keeper import Keeper         # noqa: E402

report = context.Report()
check = report.check

MINE = 111


class Api:
    def __init__(self):
        self.sent = []
        self.markup = []     # what keyboard, if any, went with each message
        self.updates = []
        self.polls = 0

    def feed(self, text, chat=MINE):
        self.updates.append({"update_id": len(self.updates) + 1,
                             "message": {"chat": {"id": chat}, "text": text}})

    def __call__(self, token, method, params, timeout=None):
        if method == "getUpdates":
            self.polls += 1
            out, self.updates = self.updates, []
            return out
        if method == "sendMessage":
            self.sent.append(params["text"])
            self.markup.append(params.get("reply_markup"))
            return {"message_id": len(self.sent)}
        raise AssertionError(f"unexpected method {method}")


def make(alive=True):
    api = Api()
    state = {"up": alive, "started": 0}

    def start():
        state["started"] += 1
        state["up"] = True
        return True

    watcher = Keeper(
        conf={"token": "t", "chat_id": MINE},
        api=api, alive=lambda: state["up"], start=start,
        log=lambda *_: None, watch_seconds=0, poll_seconds=0)
    watcher.state = state
    return watcher, api, state


keeper_mod.RESTART_MARKER = Path(tempfile.mkdtemp()) / ".relay-restart"


print("\n--- while Relay is up it says nothing and asks nothing ---")
# Two pollers on one bot each get half the messages, at random. Silence here
# is not politeness, it is the thing that keeps them from fighting.
watcher, api, state = make(alive=True)
watcher.was_alive = True
api.feed("/start")
watcher.tick()
check("no message sent", api.sent == [], str(api.sent))
check("and Telegram was never asked", api.polls == 0, str(api.polls))
check("nothing was started", state["started"] == 0, str(state["started"]))


print("\n--- when it goes, you are told once ---")
watcher, api, state = make(alive=True)
watcher.was_alive = True
state["up"] = False
watcher.tick()
check("told", any("has stopped" in s for s in api.sent), str(api.sent))
check("and it starts listening", api.polls == 1, str(api.polls))

api.sent.clear()
watcher.tick()
check("but not told again", not any("has stopped" in s for s in api.sent),
      str(api.sent))


print("\n--- the button leaves with the need for it ---")
# Telegram keeps the last keyboard until something removes it, so a panel that
# is merely not resent stays on the screen for ever - which is what happened.
watcher, api, state = make(alive=True)
watcher.was_alive = True
state["up"] = False
watcher.tick()
check("offered while Relay is down",
      any("/start" in (m or "") for m in api.markup), str(api.markup))

api.markup.clear()
api.feed("/start")
watcher.tick()
check("and taken away once it is back",
      any("remove_keyboard" in (m or "") for m in api.markup), str(api.markup))


print("\n--- /start brings it back ---")
watcher, api, state = make(alive=False)
watcher.was_alive = False
api.feed("/start")
watcher.tick()
check("started", state["started"] == 1, str(state["started"]))
check("and said so", any("back up" in s for s in api.sent), str(api.sent))


print("\n--- and only for you ---")
# The bot answers one chat. A keeper that took orders from anybody would be a
# way for a stranger to start a program on somebody else's machine.
watcher, api, state = make(alive=False)
watcher.was_alive = False
api.feed("/start", chat=99999)
watcher.tick()
check("a stranger starts nothing", state["started"] == 0, str(state["started"]))
check("and hears nothing", api.sent == [], str(api.sent))


print("\n--- a prompt sent while it was down is not silently swallowed ---")
# Silence would look exactly like it having been queued, and the answer would
# never come.
watcher, api, state = make(alive=False)
watcher.was_alive = False
api.feed("find the cause of the freeze")
watcher.tick()
check("it says so", any("not queued" in s for s in api.sent), str(api.sent))
check("and starts nothing on its own", state["started"] == 0,
      str(state["started"]))


print("\n--- a restart you asked for is not announced as a death ---")
# Relay leaves a marker on its way out when the phone asked it to go. Without
# it, a deliberate exit and a crash are the same event from out here.
watcher, api, state = make(alive=True)
watcher.was_alive = True
state["up"] = False
keeper_mod.RESTART_MARKER.write_text("asked", encoding="utf-8")
watcher.tick()
check("no death notice", not any("has stopped" in s for s in api.sent),
      str(api.sent))
check("it just comes back", state["started"] == 1, str(state["started"]))
check("and the marker is cleared", not keeper_mod.RESTART_MARKER.exists())


print("\n--- a start that does not take is reported, not assumed ---")
# Popen returning means a process was created, which is not the same as an
# application that came up.
api = Api()
stubborn = Keeper(conf={"token": "t", "chat_id": MINE}, api=api,
                  alive=lambda: False, start=lambda: True,
                  log=lambda *_: None, watch_seconds=0, poll_seconds=0)
keeper_mod.STARTUP_GRACE = 0
check("it does not claim success", stubborn.bring_back("test") is False)
check("and says what it saw", any("has not come up" in s for s in api.sent),
      str(api.sent))

print("\n--- a photo sent while Relay is down is answered too ---")
# The keeper exists to break silence. Ignoring a message because it happens not
# to be text puts back exactly the silence it was written to remove.
watcher, api, state = make(alive=False)
watcher.was_alive = False
api.updates.append({"update_id": 1, "message": {"chat": {"id": MINE},
                                                "photo": [{"file_id": "x"}]}})
watcher.tick()
check("it says Relay is not running",
      any("not running" in s for s in api.sent), str(api.sent))
check("and starts nothing on its own", state["started"] == 0, str(state["started"]))


print("\n--- a tick that throws does not end the watch ---")
# The one program that must not stop. If it falls over there is nothing left
# watching, and no sign of it anywhere - the thing that reports trouble is the
# thing that died.
api = Api()
calls = {"n": 0}
stubborn = Keeper(conf={"token": "t", "chat_id": MINE}, api=api,
                  alive=lambda: True, start=lambda: True,
                  log=lambda *_: None, watch_seconds=0, poll_seconds=0)


def sometimes_explodes():
    calls["n"] += 1
    if calls["n"] == 1:
        raise RuntimeError("something unforeseen")
    if calls["n"] >= 3:
        stubborn.running = False
    return 0


stubborn.tick = sometimes_explodes
stubborn.run()
check("it carried on", calls["n"] >= 3, str(calls["n"]))


print("\n--- and neither does Telegram being unreachable ---")
class Unreachable(Api):
    def __call__(self, token, method, params, timeout=None):
        if method == "getUpdates" and params.get("timeout") != 0:
            raise OSError("the network is not there")
        return super().__call__(token, method, params, timeout)


api = Unreachable()
watcher = Keeper(conf={"token": "t", "chat_id": MINE}, api=api,
                 alive=lambda: False, start=lambda: True,
                 log=lambda *_: None, watch_seconds=0, poll_seconds=0)
watcher.was_alive = False
try:
    watcher.tick()
    failed = None
except Exception as exc:
    failed = f"{type(exc).__name__}: {exc}"
check("the tick came back", failed is None, str(failed))


print("\n--- the settings it reads ---")
import json as _json
folder = Path(tempfile.mkdtemp(prefix="relay-keeper-settings-"))
keeper_mod.SETTINGS_PATH = folder / "telegram.json"
check("no file at all", keeper_mod.settings() is None)

keeper_mod.SETTINGS_PATH.write_text("{not json", encoding="utf-8")
check("a broken file", keeper_mod.settings() is None)

keeper_mod.SETTINGS_PATH.write_text(_json.dumps({"token": "", "chat_id": 5}),
                                    encoding="utf-8")
check("no token", keeper_mod.settings() is None)

keeper_mod.SETTINGS_PATH.write_text(_json.dumps({"token": "t", "chat_id": None}),
                                    encoding="utf-8")
check("no chat paired yet", keeper_mod.settings() is None)

keeper_mod.SETTINGS_PATH.write_text(_json.dumps({"token": "t", "chat_id": "111"}),
                                    encoding="utf-8")
conf = keeper_mod.settings()
check("a filled-in file", conf is not None and conf["chat_id"] == 111, str(conf))

print("\n--- the keeper has an answer for everything Relay answers ---")
# The keeper imports nothing from `relay`, so its list of commands is a second
# copy of one - and a second copy is a thing that drifts. This test can see
# both, which is the only reason writing it out over there is safe.
from relay.remote import COMMANDS  # noqa: E402

# What the keeper deals with itself, rather than saying "not running".
ITS_OWN = ("/start", "/restart", "/status")
for name, _what in COMMANDS:
    command = f"/{name}"
    check(f"{command}",
          command in ITS_OWN or command in keeper_mod.ANSWERED_BY_RELAY)

watcher, api, state = make(alive=False)
watcher.was_alive = False
for name, _what in COMMANDS:
    api.feed(f"/{name}")
watcher.tick()
check("every one of them was answered", len(api.sent) == len(COMMANDS),
      f"{len(api.sent)} of {len(COMMANDS)}")
check("and none was mistaken for a prompt",
      not any("not queued" in s for s in api.sent),
      str([s[:40] for s in api.sent if "not queued" in s]))

print("\n--- Telegram 409 Conflict during poll yields when Relay is alive ---")
class ConflictApi(Api):
    def __call__(self, token, method, params, timeout=None):
        if method == "getUpdates":
            raise RuntimeError("HTTP Error 409: Conflict")
        return super().__call__(token, method, params, timeout)

api = ConflictApi()
state = {"up": True}
logs = []
watcher = Keeper(conf={"token": "t", "chat_id": MINE}, api=api,
                 alive=lambda: state["up"], start=lambda: True,
                 log=logs.append, watch_seconds=0, poll_seconds=0)
watcher.was_alive = False
updates = watcher.poll()
check("poll returns empty list on 409", updates == [], str(updates))
check("recognized Relay is up", watcher.was_alive is True)
check("did not log unreachable error", not any("not reachable" in l for l in logs), str(logs))

print("\n--- Telegram 409 Conflict during confirm is handled cleanly ---")
class ConfirmConflictApi(Api):
    def __call__(self, token, method, params, timeout=None):
        if method == "getUpdates" and params.get("timeout") == 0:
            raise RuntimeError("HTTP Error 409: Conflict")
        return super().__call__(token, method, params, timeout)

api = ConfirmConflictApi()
logs = []
watcher = Keeper(conf={"token": "t", "chat_id": MINE}, api=api,
                 alive=lambda: True, start=lambda: True,
                 log=logs.append, watch_seconds=0, poll_seconds=0)
watcher.confirm()
check("confirm swallowed 409 without logging error", logs == [], str(logs))

print("\n--- startup grace period avoids 409 race and false alarm ---")
api = Api()
# Simulates Relay starting 100ms after Keeper
startup_state = {"attempts": 0}
def booting_alive():
    startup_state["attempts"] += 1
    return startup_state["attempts"] >= 3

watcher = Keeper(conf={"token": "t", "chat_id": MINE}, api=api,
                 alive=booting_alive, start=lambda: True,
                 log=lambda *_: None, watch_seconds=0.01, poll_seconds=0,
                 startup_grace=5)
# Run just the startup check and stop
def stop_after_startup():
    watcher.running = False
    return 0

watcher.tick = stop_after_startup
watcher.run()
check("no false death notice sent on startup", api.sent == [], str(api.sent))
check("Relay recognized as up", watcher.was_alive is True)

sys.exit(report.finish())
