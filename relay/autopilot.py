"""Send the next prompt when the last one has finished, and not before.

A chain of prompts is only useful if the thing driving it is more careful than
you would be. Typing over an agent that is halfway through an answer loses the
answer and the prompt; doing it while it waits for permission loses the dialog
too. So every rule here is written to fail towards waiting.

Four of them carry the weight:

**Only IDLE advances.** `agent.state` reports BUSY, WAITING, IDLE or UNKNOWN,
and three of those hold the queue. A window whose profile does not match, or
that was read halfway through a repaint, is never mistaken for a finished one.

**Idle has to hold still.** One reading is not enough - agents go quiet between
their own turns, and a single glance at that gap looks exactly like being
finished. Two readings a second apart have to agree.

**It has to start before it can finish again.** The moment after a prompt is
pasted the window is still idle, because nothing has begun yet. Watching for
idle at that point sends the whole chain in one go. So after each send the
queue waits to see the agent actually pick the work up, and only then starts
watching for the end.

**You outrank it.** A keystroke during the countdown cancels, because a
keystroke means you are at the desk and the machine should get out of the way.

The countdown itself is the last line of defence rather than the first: by the
time it runs, all of the above has already agreed.
"""

import ctypes
import threading
import time

from . import agent
from .target import focus_window, window_title

POLL_SECONDS = 1.0

# One idle reading is a glance; two a second apart is a state. Agents pause
# between their own turns and a single look into that gap reads as finished.
SETTLE_POLLS = 2

COUNTDOWN_SECONDS = 3

# How long to wait after sending for the agent to visibly pick the work up.
# If it never does, the paste did not land, and continuing would talk into a
# window that is not listening.
START_SECONDS = 45

# A step that never ends. Long, because "review the whole codebase" is a real
# request, but not unbounded - a queue that waits forever waits silently.
STEP_TIMEOUT_SECONDS = 900

HOLDING = "holding"        # waiting for the agent to be free
# Holding, but for a nameable reason: the agent has stopped and is asking
# you something. Worth its own phase rather than folding into HOLDING,
# because it is the one hold that will never end on its own - and the one
# worth telling a phone about, since nobody is in the room to see it.
WAITING = "waiting"
COUNTING = "counting"      # it is free; counting down before sending
SENDING = "sending"
STARTING = "starting"      # sent; waiting to see it picked up
DONE = "done"
STOPPED = "stopped"

# Returned by the countdown when the agent started talking again part way
# through it. Not True and not False: nothing went wrong, and nothing was sent.
RESTART = object()


class Autopilot:
    """Runs a list of prompts into one window, one at a time.

    Polling happens on its own thread. Reading a Chromium window costs about
    110ms, and the orb repaints every 33ms, so doing this on the GUI thread
    would drop three frames a second for as long as a chain ran.
    """

    def __init__(self, send, read_state=None, on_progress=None,
                 is_window=None, focus=None, place_caret=None, log=print,
                 read_text=None, on_result=None,
                 poll_seconds=POLL_SECONDS,
                 countdown_seconds=COUNTDOWN_SECONDS, countdown_tick=1.0):
        self.send = send                       # (step, hwnd) -> bool
        self.read_state = read_state or agent.state
        # What the window says, so a step's answer can be read back. Only
        # called when somebody is listening for the result.
        self.read_text = read_text or (lambda hwnd: agent.read(hwnd)["text"])
        self.on_result = on_result             # (index, new_lines) -> None
        self.focus = focus or focus_window
        self.place_caret = place_caret or agent.focus_input
        self.is_window = is_window or (lambda hwnd: bool(
            ctypes.windll.user32.IsWindow(hwnd)))
        self.on_progress = on_progress or (lambda *a, **k: None)
        self.log = log
        self.poll_seconds = poll_seconds
        self.countdown_seconds = countdown_seconds
        # Only ever anything but a second in the tests, which would otherwise
        # spend five real seconds on every countdown they exercise.
        self.countdown_tick = countdown_tick

        self.steps = []
        self.hwnd = None
        self.profile = None
        self.title = ""
        self.index = 0
        self.phase = STOPPED
        self.reason = ""

        self._thread = None
        self._stop = threading.Event()
        self._typed = threading.Event()
        # (step index, what the window said before that step was sent). The
        # answer to a step is whatever is there afterwards and was not there
        # before; nothing else distinguishes it from the rest of a transcript.
        self._before = None

    # --- outside world ---------------------------------------------------

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, steps, hwnd):
        """Begin a chain. False if one is already running or there is nothing to do."""
        if self.running:
            self.log("[auto] a chain is already running")
            return False
        steps = [s for s in steps if (s.strip() if isinstance(s, str) else bool(s))]
        if not steps or not hwnd:
            self.log("[auto] nothing to send")
            return False

        profile = agent.profile_for(hwnd)
        if profile is None:
            # Without a profile every reading is UNKNOWN, so the chain would
            # sit at the first step forever. Better to say so now.
            self.log(f"[auto] no profile recognises {window_title(hwnd)!r}; "
                     "not starting")
            return False

        self.steps, self.hwnd = steps, hwnd
        self.profile = profile
        self.title = window_title(hwnd)
        self.index, self.reason = 0, ""
        # A chain that was stopped part way through leaves its snapshot behind,
        # and the next chain would then diff against it - reporting the
        # previous chain's leftovers as the answer to a step it had not sent
        # yet.
        self._before = None
        self._stop.clear()
        self._typed.clear()
        self.log(f"[auto] {len(steps)} step(s) into {self.title!r} "
                 f"({profile['name']})")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self, why="stopped"):
        if not self.running:
            return
        self.reason = why
        self._stop.set()

    def user_typed(self):
        """Called from the keyboard listener. A keystroke outranks the queue."""
        self._typed.set()

    # --- the loop --------------------------------------------------------

    def _announce(self, phase, seconds_left=None):
        self.phase = phase
        self.on_progress(phase, self.index, len(self.steps), seconds_left)

    def _finish(self, phase, why):
        self.reason = why
        self._announce(phase)
        self.log(f"[auto] {why}")

    def _alive(self):
        """False once anything has asked this chain to end."""
        if self._stop.is_set():
            return False
        if not self.is_window(self.hwnd):
            self.reason = "the window was closed"
            return False
        return True

    def _run(self):
        try:
            for self.index, step in enumerate(self.steps):
                # A countdown interrupted by the agent starting to talk again
                # goes back to waiting, so these two belong in a loop together.
                while True:
                    if not self._wait_until_free():
                        return self._finish(STOPPED, self.reason or "stopped")
                    counted = self._count_down()
                    if counted is RESTART:
                        continue
                    if not counted:
                        return self._finish(STOPPED, self.reason or "cancelled")
                    break
                if not self._send_step(step):
                    return self._finish(STOPPED, self.reason or "could not send")
                if not self._wait_until_started():
                    return self._finish(STOPPED, self.reason or "never started")

            # The last step has been picked up but not finished. Reporting
            # "done" here would be reporting that the work was handed over,
            # which is not what the word means - and the answer to the last
            # step is the one most worth having, especially when the whole
            # chain was one message from a phone.
            #
            # And if that wait does not come back, the chain did not finish:
            # the window was closed, or the step ran past the timeout, or you
            # stopped it. The answer this reported was "done" regardless, which
            # is the one thing a verdict must never be wrong about.
            if self._before is not None and not self._wait_until_free():
                return self._finish(STOPPED, self.reason or "stopped")
            self._finish(DONE, f"all {len(self.steps)} step(s) done")
        except Exception as exc:
            self._finish(STOPPED, f"stopped on an error: {exc}")

    def _report_result(self):
        """Hand back what the window said that it had not said before.

        Diffing whole lines against the snapshot taken before the step is the
        only way to tell one answer from the transcript around it. A terminal
        redraws a fixed screen and a chat window keeps everything, and neither
        marks where a reply begins.
        """
        if self._before is None:
            return
        index, before = self._before
        self._before = None
        if self.on_result is None:
            return
        try:
            was = {ln.strip() for ln in before.splitlines() if ln.strip()}
            now = [ln.strip() for ln in self.read_text(self.hwnd).splitlines()
                   if ln.strip()]
            if not was and now:
                # The window could not be read before the step - a cold
                # accessibility tree, a repaint caught halfway - and against
                # nothing every line on screen counts as new. What went to the
                # phone then was the whole window: minutes of unrelated work
                # from earlier in the session, reported as the answer to the
                # prompt just sent, with any line in it containing the word
                # "failed" lifted out as a problem. None says so instead.
                self.log("[auto] the window could not be read before the step; "
                         "there is no telling what is new")
                self.on_result(index, None)
                return
            self.on_result(index, [ln for ln in now if ln not in was])
        except Exception as exc:
            self.log(f"[auto] could not read the result: {exc}")

    def _wait_until_free(self):
        """Hold until the agent has been idle for SETTLE_POLLS readings."""
        settled = 0
        said = None
        deadline = time.monotonic() + STEP_TIMEOUT_SECONDS
        while self._alive():
            state = self.read_state(self.hwnd)
            if state == agent.IDLE:
                settled += 1
                if settled >= SETTLE_POLLS:
                    # Free again means whatever was sent before has finished,
                    # so this is the moment its answer can be read.
                    self._report_result()
                    return True
            else:
                settled = 0
                if state != said:
                    # Only on change: a chain can hold for many minutes and a
                    # line a second would bury everything else in the log.
                    said = state
                    self.log(f"[auto] step {self.index + 1}: {self._why(state)}")
            self._announce(WAITING if state == agent.WAITING else HOLDING)
            if time.monotonic() > deadline:
                self.reason = (f"step {self.index + 1} was still not finished "
                               f"after {STEP_TIMEOUT_SECONDS // 60} minutes")
                return False
            if self._stop.wait(self.poll_seconds):
                return False
        return False

    def _why(self, state):
        if state == agent.WAITING:
            return "it stopped to ask you something; holding"
        if state == agent.BUSY:
            return "still working"
        return "cannot tell what it is doing; holding"

    def _count_down(self):
        """Give you time to stop it, and keep checking while you have it.

        The state is re-read every second here too. An agent that starts
        talking again mid-countdown sends us back to holding rather than
        pasting into the middle of what it just began.
        """
        self._typed.clear()
        for left in range(self.countdown_seconds, 0, -1):
            self._announce(COUNTING, left)
            if self._stop.wait(self.countdown_tick) or not self._alive():
                return False
            if self._typed.is_set():
                self.reason = "you started typing"
                return False
            if self.read_state(self.hwnd) != agent.IDLE:
                self.log("[auto] it started again; back to waiting")
                return RESTART
        return True

    def _send_step(self, text):
        self._announce(SENDING)
        # Bring the window forward first. The paste is a synthetic Ctrl+V and
        # lands wherever focus is, so this is not cosmetic.
        if not self.focus(self.hwnd):
            self.reason = f"could not bring {self.title!r} to the front"
            return False
        time.sleep(0.12)      # let the app settle before asking for the caret

        # Then the box inside it. A window in front is not a cursor in a text
        # field: what follows is Ctrl+V and Enter, and sent blind they go to
        # whatever element holds focus - which has, measured, meant pressing
        # the target's own Stop button and cutting off the reply. Better to
        # send nothing and say so.
        if self.place_caret(self.hwnd, self.profile) is False:
            self.reason = (f"could not put the cursor in the box to type in, "
                           f"so nothing was sent to {self.title!r}")
            return False

        # Taken before the paste, so the answer can be told apart from
        # everything already on screen. Only when somebody wants it: reading a
        # Chromium window costs about 110ms.
        if self.on_result is not None:
            try:
                self._before = (self.index, self.read_text(self.hwnd))
            except Exception as exc:
                self.log(f"[auto] could not read the window first: {exc}")
                self._before = None

        if isinstance(text, str):
            preview = text
        elif isinstance(text, dict):
            num_imgs = len(text.get("images", [])) if text.get("images") else (1 if text.get("image_bytes") else 0)
            prefix = f"[{num_imgs} imgs] " if num_imgs > 1 else ""
            caption = text.get("caption") or text.get("type") or str({k: v for k, v in text.items() if k not in ("image_bytes", "images")})
            preview = f"{prefix}{caption}"
        else:
            preview = str(text)
        self.log(f"[auto] step {self.index + 1}/{len(self.steps)} -> {preview[:60]!r}")
        if not self.send(text, self.hwnd):
            self.reason = "the paste failed"
            return False
        return True

    def _wait_until_started(self):
        """Wait to see the agent pick the work up.

        Without this the next step goes out immediately: the window is still
        idle a second after pasting, because nothing has begun yet. WAITING
        counts as started - an agent that asks for permission straight away has
        certainly read the prompt.
        """
        deadline = time.monotonic() + START_SECONDS
        while self._alive():
            self._announce(STARTING)
            if self.read_state(self.hwnd) in (agent.BUSY, agent.WAITING):
                return True
            if time.monotonic() > deadline:
                self.reason = (f"step {self.index + 1} was sent but nothing "
                               f"happened in {START_SECONDS}s")
                return False
            if self._stop.wait(self.poll_seconds):
                return False
        return False
