"""The queue, driven against a window whose state the test decides.

No real application is involved. That is the point: the states a real agent
passes through are hard to reach on demand and impossible to reach reliably -
you cannot ask Antigravity to pause for permission at second three of a
countdown - and every one of them has to be held down. The reading itself is
checked elsewhere, live, by tests/probes/agent_state.py.

Everything here is about one question: what does the queue do when the window
is not plainly finished? Almost all of these tests assert that nothing was
sent.
"""
import sys
import threading
import time

import context  # noqa: E402,F401
context.isolate_state()

from relay import agent, autopilot as auto_mod          # noqa: E402
from relay.autopilot import Autopilot                   # noqa: E402

report = context.Report()
check = report.check

HWND = 4242
FAST = dict(poll_seconds=0.02, countdown_seconds=2, countdown_tick=0.02)


class Fake:
    """A window whose state the test sets, and which records what it was sent.

    `on_send` lets a test say what the window does next - going busy the way a
    real one would, or staying idle the way a broken one would.
    """

    def __init__(self, state=agent.IDLE, on_send=None):
        self.state = state
        self.sent = []
        self.alive = True
        self.reads = 0
        self.on_send = on_send
        # Whether the window can be brought to the front. Real, and worth
        # being able to fail: a paste that goes ahead without the target in
        # front lands in whatever was.
        self.focusable = True
        # What focus_input would answer: True placed, False could not, None
        # nothing to place (a terminal).
        self.caret = None

    def read(self, _hwnd):
        self.reads += 1
        return self.state

    def send(self, text, _hwnd):
        self.sent.append(text)
        if self.on_send:
            self.on_send(self)
        return True

    def pilot(self, **kwargs):
        settings = dict(FAST)
        settings.update(kwargs)
        return Autopilot(
            send=self.send,
            read_state=self.read,
            is_window=lambda _h: self.alive,
            focus=lambda _h: self.focusable,
            place_caret=lambda _h, _p: self.caret,
            log=lambda *_: None,
            **settings,
        )


def run(pilot, steps, seconds=3.0):
    """Start a chain and wait for it to end, rather than sleeping a guess."""
    pilot.start(steps, HWND)
    deadline = time.monotonic() + seconds
    while pilot.running and time.monotonic() < deadline:
        time.sleep(0.01)
    pilot.stop("test over")
    return pilot


# Long waits are for real agents, not for a test suite.
auto_mod.STEP_TIMEOUT_SECONDS = 0.6
auto_mod.START_SECONDS = 0.4

# profile_for asks Windows about a window handle that does not exist here.
agent.profile_for = lambda hwnd, profiles=None: {"name": "fake"}


print("\n--- an idle window gets the prompt ---")
window = Fake(agent.IDLE, on_send=lambda w: setattr(w, "state", agent.BUSY))
pilot = run(window.pilot(), ["first"])
check("sent it", window.sent == ["first"], str(window.sent))
check("and finished", pilot.phase == auto_mod.DONE, pilot.phase)

print("\n--- nothing is sent to a window that is not plainly finished ---")
# The whole feature rests on these four. Each holds the queue for its own
# reason, and only the last one is a state anybody designed.
for state, why in [
    (agent.BUSY, "still working"),
    (agent.WAITING, "stopped to ask you something"),
    (agent.UNKNOWN, "cannot tell"),
]:
    window = Fake(state)
    run(window.pilot(), ["nope"], seconds=1.2)
    check(f"held while {why}", window.sent == [], str(window.sent))

print("\n--- one idle reading is not enough ---")
# The gap between an agent's own turns looks exactly like being finished.
# Two readings have to agree, so a window that flickers idle sends nothing.
class Flicker(Fake):
    def read(self, hwnd):
        self.reads += 1
        return agent.IDLE if self.reads % 2 else agent.BUSY


window = Flicker(agent.BUSY)
run(window.pilot(), ["nope"], seconds=1.2)
check("a flickering window sends nothing", window.sent == [], str(window.sent))

print("\n--- the chain does not fire all at once ---")
# The failure this guards is specific: a second after pasting, the window is
# still idle, because nothing has started yet. A queue that only watches for
# idle empties itself into the box in one go.
window = Fake(agent.IDLE)          # never goes busy, however much we send it
pilot = run(window.pilot(), ["one", "two", "three"], seconds=2.5)
check("only the first step went out", window.sent == ["one"], str(window.sent))
check("and it said why", "nothing happened" in pilot.reason, pilot.reason)

print("\n--- a whole chain, in order ---")
def works_then_finishes(w):
    """Behave like a real agent: pick the work up, then finish it."""
    w.state = agent.BUSY
    threading.Timer(0.15, lambda: setattr(w, "state", agent.IDLE)).start()


window = Fake(agent.IDLE, on_send=works_then_finishes)
pilot = run(window.pilot(), ["one", "two", "three"], seconds=6.0)
check("all three, in order", window.sent == ["one", "two", "three"], str(window.sent))
check("reported done", pilot.phase == auto_mod.DONE, pilot.phase)

print("\n--- you outrank it ---")
window = Fake(agent.IDLE, on_send=works_then_finishes)
pilot = window.pilot(countdown_seconds=20, countdown_tick=0.05)
pilot.start(["one"], HWND)
while pilot.phase != auto_mod.COUNTING:
    time.sleep(0.01)
pilot.user_typed()
deadline = time.monotonic() + 2
while pilot.running and time.monotonic() < deadline:
    time.sleep(0.01)
check("a keystroke stopped it", window.sent == [], str(window.sent))
check("saying so", "typing" in pilot.reason, pilot.reason)

print("\n--- it starts talking again mid-countdown ---")
# Not a cancellation: nothing went wrong and nothing was sent, so the queue
# goes back to waiting rather than giving up on the step.
window = Fake(agent.IDLE, on_send=works_then_finishes)
pilot = window.pilot(countdown_seconds=6, countdown_tick=0.05)
pilot.start(["one"], HWND)
while pilot.phase != auto_mod.COUNTING:
    time.sleep(0.01)
window.state = agent.BUSY
time.sleep(0.2)
check("nothing sent while it was busy again", window.sent == [], str(window.sent))
window.state = agent.IDLE
deadline = time.monotonic() + 3
while pilot.running and time.monotonic() < deadline:
    time.sleep(0.01)
check("sent once it was free again", window.sent == ["one"], str(window.sent))

print("\n--- the window goes away ---")
window = Fake(agent.BUSY)
pilot = window.pilot()
pilot.start(["one"], HWND)
time.sleep(0.1)
window.alive = False
deadline = time.monotonic() + 2
while pilot.running and time.monotonic() < deadline:
    time.sleep(0.01)
check("stopped when the window closed", not pilot.running)
check("sent nothing", window.sent == [], str(window.sent))

print("\n--- will not paste into a window it could not bring forward ---")
# Ctrl+V goes to whatever has focus. If the target would not come forward,
# sending anyway puts the prompt in someone else's window.
window = Fake(agent.IDLE, on_send=works_then_finishes)
window.focusable = False
pilot = run(window.pilot(), ["one"], seconds=1.5)
check("sent nothing", window.sent == [], str(window.sent))
check("and said why", "front" in pilot.reason, pilot.reason)

print("\n--- will not type into a window whose box it could not reach ---")
# The one that shipped broken. Bringing a window forward does not put the
# caret in its text box, and Ctrl+V followed by Enter sent blind goes to
# whatever element has focus. Measured against Claude: it pressed that
# window's own Stop button and cut off the reply that was being written.
window = Fake(agent.IDLE, on_send=works_then_finishes)
window.caret = False
pilot = run(window.pilot(), ["one"], seconds=1.5)
check("sent nothing", window.sent == [], str(window.sent))
check("and said why", "cursor" in pilot.reason, pilot.reason)

window = Fake(agent.IDLE, on_send=works_then_finishes)
window.caret = True
pilot = run(window.pilot(), ["one"], seconds=2.0)
check("sends once the caret is in the box", window.sent == ["one"], str(window.sent))

# A terminal has no such element, and typing reaches it anyway. Refusing
# there would have broken opencode to fix Claude.
window = Fake(agent.IDLE, on_send=works_then_finishes)
window.caret = None
pilot = run(window.pilot(), ["one"], seconds=2.0)
check("sends where there is no box to find", window.sent == ["one"], str(window.sent))

print("\n--- what the window said, once a step has finished ---")
# The answer to a step is whatever the window says afterwards and did not say
# before. Nothing else tells one reply apart from the transcript around it.
window = Fake(agent.IDLE, on_send=works_then_finishes)
window.caret = True
said = {"text": "line one\nline two\n"}
results = []


def read_text(_hwnd):
    return said["text"]


def grew(w):
    """Behave like an agent: pick the work up, answer, go quiet."""
    w.state = agent.BUSY
    said["text"] += "the answer\nERROR: it went wrong\n"
    threading.Timer(0.15, lambda: setattr(w, "state", agent.IDLE)).start()


window.on_send = grew
pilot = Autopilot(
    send=window.send, read_state=window.read, is_window=lambda _h: True,
    focus=lambda _h: True, place_caret=lambda _h, _p: True,
    read_text=read_text, on_result=lambda i, lines: results.append((i, lines)),
    log=lambda *_: None, **FAST)
run(pilot, ["do a thing"], seconds=4.0)

check("a result came back", len(results) == 1, str(results))
check("for the step that produced it", results and results[0][0] == 0, str(results))
check("only the new lines", results and results[0][1] == ["the answer",
      "ERROR: it went wrong"], str(results))
# Reporting done when the last step has merely been picked up would report
# that the work was handed over, not that it finished - and the last step's
# answer is the one most worth having.
check("and done means done", pilot.phase == auto_mod.DONE, pilot.phase)

print("\n--- and nothing is read when nobody is listening ---")
# Reading a Chromium window costs about 110ms. A chain nobody is watching
# should not pay it twice a step.
reads = []
window = Fake(agent.IDLE, on_send=works_then_finishes)
window.caret = True
pilot = Autopilot(
    send=window.send, read_state=window.read, is_window=lambda _h: True,
    focus=lambda _h: True, place_caret=lambda _h, _p: True,
    read_text=lambda _h: reads.append(1) or "", on_result=None,
    log=lambda *_: None, **FAST)
run(pilot, ["do a thing"], seconds=3.0)
check("the window was never read", reads == [], str(reads))

print("\n--- refuses to start on nothing ---")
window = Fake(agent.IDLE)
pilot = window.pilot()
check("no steps", pilot.start([], HWND) is False)
check("blank steps", pilot.start(["", "   "], HWND) is False)
check("no window", pilot.start(["one"], None) is False)

print("\n--- refuses a window it does not recognise ---")
# Without a profile every reading is UNKNOWN, so the chain would sit at step
# one until it timed out. Saying so at the start is the difference between a
# refusal and a mystery.
was, agent.profile_for = agent.profile_for, lambda hwnd, profiles=None: None
window = Fake(agent.IDLE)
check("would not start", window.pilot().start(["one"], HWND) is False)
agent.profile_for = was

print("\n--- one chain at a time ---")
window = Fake(agent.BUSY)
pilot = window.pilot()
pilot.start(["one"], HWND)
check("a second start is refused", pilot.start(["two"], HWND) is False)
pilot.stop("test over")

sys.exit(report.finish())
