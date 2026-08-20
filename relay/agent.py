"""Is the agent in that window still working, or has it finished?

The autopilot cannot send the next prompt until it knows the last one is done,
and no two of these applications answer that the same way. Antigravity puts a
`Cancel (Ctrl+D)` button on screen while it works and a `Send message` button
when it stops. opencode writes `esc interrupt` along the bottom of the terminal
and takes it away. Cursor, Windsurf and whatever comes next will do a third
thing.

So none of it lives in code. A profile says which window it recognises and what
it looks for, `profiles.json` holds them, and teaching Relay a new application
is editing that file rather than this one.

Two rules per profile, not one. Asking only "is it busy" makes every window
that fails to match look finished - including one that was read halfway through
a repaint, and including an application with no profile at all. Requiring the
idle rule to match as well means the answer is UNKNOWN unless the window
actually said so, and UNKNOWN never advances a queue.

That is what makes the dangerous case safe for free. An agent stopped at a
permission prompt has finished nothing, but it has stopped moving, and to
anything watching for stillness it looks done - which is how a queue ends up
typing its next prompt over a dialog. Measured on Antigravity: while it waits,
`Cancel (Ctrl+D)` is gone and `Send message` has not come back, so both rules
fail and the answer is UNKNOWN. The optional third rule only puts a name to it,
so the reason can be shown rather than guessed at.
"""

import json
from pathlib import Path

from . import uia
from .target import window_process, window_title

PROFILES_PATH = Path(__file__).resolve().parent.parent / "profiles.json"

# How much of the end of a window's text a `line` rule is allowed to see.
#
# An app with a transcript publishes the whole conversation, and the
# conversation is the least trustworthy thing on screen: it contains whatever
# was said, including - measured, in the session that produced the Claude
# profile - the very words the profile looks for. The controls that report
# state sit at the end of the tree, below the transcript, so a rule that only
# reads the last few lines cannot be fooled by something that was said.
#
# Twenty-five holds the composer and the footer under it with room to spare,
# and is far short of the two hundred-odd lines a transcript runs to.
TAIL_LINES = 25

BUSY = "busy"
IDLE = "idle"
WAITING = "waiting"      # stopped, but for you - a permission prompt, a question
UNKNOWN = "unknown"

# Neither of these advances a queue. The difference is only what can be said
# about why it did not.
STOPS = (BUSY, WAITING, UNKNOWN)

# Every profile Relay ships with was measured, not guessed - see
# tests/probes/target_text.py, which is what these strings came out of.
BUILT_IN = [
    {
        "name": "Antigravity",
        "process": "Antigravity.exe",
        # The accessible name of the box you type in, so a step can be put
        # there rather than wherever focus happens to be. See focus_input.
        "input": "Message input",
        "busy": {"button": "Cancel (Ctrl+D)"},
        "idle": {"button": "Send message"},
        # The text alone would be wrong: the request stays in the transcript
        # after you have answered it. Pairing it with the input box still being
        # gone is what makes it mean "right now" instead of "at some point".
        "waiting": {"text": "Requesting permission",
                    "absent_button": "Send message"},
    },
    {
        "name": "Claude",
        "process": "claude.exe",
        "input": "Prompt",
        # The two states of the one button under the box you type in. Whole
        # lines, and only from the end of the window: this app publishes the
        # entire conversation as text, and a conversation can say anything -
        # including, in the session these were measured in, "Claude is
        # working" and the word "Stop". Neither is trustworthy as a fragment.
        # As the line directly under the composer, both are.
        "busy": {"line": "stop"},
        "idle": {"line": "send", "absent_line": "stop"},
    },
    {
        "name": "opencode",
        # Any terminal is WindowsTerminal.exe, so the process alone would match
        # the window this is being run from. opencode names its own window.
        "process": "WindowsTerminal.exe",
        "title_contains": ["OC |", "OpenCode"],
        # No "input": a terminal has no text box to put a cursor in. Bringing
        # the window forward is all there is, and it is enough.
        "busy": {"text": "esc interrupt"},
        "idle": {"absent_text": "esc interrupt"},
    },
]


def load_profiles():
    """Profiles from disk, falling back to the built-in ones.

    A broken file must not take the feature down silently, and must not be
    rewritten either - someone editing it by hand deserves to keep what they
    typed while they fix it.
    """
    try:
        data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        profiles = data.get("profiles")
        if isinstance(profiles, list) and profiles:
            return profiles
        print(f"[agent] {PROFILES_PATH.name} has no profiles; using built-ins")
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[agent] could not read {PROFILES_PATH.name} ({exc}); using built-ins")
    return BUILT_IN


def write_default_profiles():
    """Put the built-ins on disk so there is something to edit."""
    if PROFILES_PATH.exists():
        return False
    PROFILES_PATH.write_text(
        json.dumps({"profiles": BUILT_IN}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    return True


def profile_for(hwnd, profiles=None):
    """The profile that recognises this window, or None."""
    process = window_process(hwnd)
    title = window_title(hwnd)
    for profile in profiles if profiles is not None else load_profiles():
        if not isinstance(profile, dict):
            _complain(f"a profile that is not an object ({profile!r:.30}); skipped")
            continue
        wanted = profile.get("process")
        if wanted and wanted.lower() != process.lower():
            continue
        if not _title_matches(profile.get("title_contains"), title):
            continue
        return profile
    return None


def _title_matches(wanted, title):
    """True when the title carries any of the fragments the profile asks for.

    A list, not one string, because an application does not always call its
    window the same thing. opencode titles a named session `OC | <name>` and
    an unnamed one just `OpenCode`, and a profile that only knew the first
    stopped recognising the second - which is the state it is in when you have
    only just opened it, and so the state you would most often start a chain
    from.
    """
    if not wanted:
        return True
    fragments = [wanted] if isinstance(wanted, str) else list(wanted)
    lowered = title.lower()
    return any(str(f).lower() in lowered for f in fragments)


def _tail(text):
    """The last few lines, which is where a window keeps its controls."""
    lines = [ln.strip().lower() for ln in text.splitlines() if ln.strip()]
    return lines[-TAIL_LINES:]


# What a rule may ask for. Anything else in a rule is a mistake in the file,
# and mistakes here are not symmetrical - see _matches.
CONDITIONS = ("text", "absent_text", "line", "absent_line",
              "button", "absent_button")

# So a profile that is wrong says so once rather than once a second.
_complained = set()


def _complain(about):
    if about in _complained:
        return
    _complained.add(about)
    print(f"[agent] {about}")


def _matches(rule, text, buttons):
    """Every condition in the rule has to hold, and an empty rule never does.

    Four kinds of condition, in two pairs. `text` looks for a fragment
    anywhere; `line` looks for a whole line at the end of the window. The
    second is much the stronger of the two - a window that says `Stop` under
    the box you type in is telling you something, and a window that merely
    contains the word somewhere in a conversation is not.

    A condition this does not recognise fails the whole rule rather than being
    passed over. "Every condition holds" is trivially true of a rule with no
    conditions left in it, so a misspelled key used to make its rule match
    every window there is - and on the idle rule that reads as "it has
    finished" whatever is on screen, which is the one direction of being wrong
    that types over a reply in progress.
    """
    if not rule:
        return False
    lowered = text.lower()
    tail = None
    for key, wanted in rule.items():
        if key not in CONDITIONS:
            _complain(f"no rule condition called {key!r}; that rule can never match")
            return False
        if not isinstance(wanted, str):
            _complain(f"{key!r} should be a phrase, not "
                      f"{type(wanted).__name__}; that rule can never match")
            return False
        wanted_low = wanted.lower()
        if key in ("line", "absent_line") and tail is None:
            tail = _tail(text)
        if key == "text" and wanted_low not in lowered:
            return False
        if key == "absent_text" and wanted_low in lowered:
            return False
        if key == "line" and wanted_low not in tail:
            return False
        if key == "absent_line" and wanted_low in tail:
            return False
        if key == "button" and not any(wanted_low == b.lower() for b in buttons):
            return False
        if key == "absent_button" and any(wanted_low == b.lower() for b in buttons):
            return False
    return True


def recognised_windows():
    """Every window on screen that some profile knows how to read.

    Needed because the target is normally the window you last clicked into,
    and choosing one that way means being at the keyboard. From a phone in
    another room there is nothing to click, so the list has to come to you.

    Returns [(hwnd, title, profile), ...] in the order Windows hands them
    over, which is roughly front to back.
    """
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    found = []
    profiles = load_profiles()

    def visit(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if not user32.GetWindowTextLengthW(hwnd):
                return True
            profile = profile_for(hwnd, profiles)
            if profile is not None:
                found.append((hwnd, window_title(hwnd), profile))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(callback(visit), 0)
    except Exception as exc:
        print(f"[agent] could not list the windows: {exc}")
    return found


def focus_input(hwnd, profile):
    """Put the cursor where this window is typed into, before anything types.

    Bringing a window to the front is not the same as the keyboard reaching
    its text box. In a Chromium application focus stays on whatever element
    had it last - the transcript, a button, anything - and a paste sent then
    goes nowhere while the Enter after it presses whatever is focused.
    Measured: a step pasted into Claude with focus on its Stop button pressed
    Stop, which reads in the transcript as the user interrupting the reply.

    Three answers, because there are three situations:

      True  - the caret is in the box
      False - this window has a box, and it could not be reached
      None  - it has no such element. A terminal is typed into as a whole,
              and there is nothing to put a cursor in.
    """
    name = (profile or {}).get("input")
    if not name:
        return None
    if uia.focus_named_input(hwnd, name):
        return True
    # The accessible name can change with a new version of the application,
    # and refusing to send for that alone would be its own failure. If the
    # caret is already somewhere that takes typing, that is enough.
    from .target import window_rect

    return uia.focused_input(window_rect(hwnd)) is not None


def read(hwnd):
    """One look at the window: its text and its buttons, cleaned.

    Both come from the same instant. Reading them a second apart would let a
    window change underneath and produce a state that was never really there.
    """
    text, source = uia.window_text(hwnd)
    return {
        "text": "\n".join(uia.clean(ln) for ln in text.splitlines()),
        "buttons": uia.window_buttons(hwnd),
        "source": source,
    }


def state(hwnd, profile=None, seen=None):
    """BUSY, WAITING, IDLE or UNKNOWN for the agent in `hwnd`.

    The order is the safety. Anything that stops a queue is checked before the
    one thing that lets it through, so a window that somehow satisfies two
    rules at once - a repaint caught mid-way, a profile written too loosely -
    is read as not finished. Being wrong that way costs a wait; being wrong the
    other way types over whatever is on screen.
    """
    profile = profile or profile_for(hwnd)
    if profile is None:
        return UNKNOWN
    seen = seen if seen is not None else read(hwnd)
    text, buttons = seen["text"], seen["buttons"]

    # A read that came back with nothing is not a state. An `absent_text` or
    # `absent_button` condition is perfectly satisfied by an empty window, so a
    # profile whose idle rule is the exact absence of its busy one - opencode
    # has one - called a window that had published nothing "finished", and idle
    # is the single state that lets a queue send. Chromium builds its
    # accessibility tree lazily and the first read of a window really can come
    # back empty, so this is a window that exists, not a hypothetical one.
    if not text.strip() and not buttons:
        return UNKNOWN

    if _matches(profile.get("busy"), text, buttons):
        return BUSY
    if _matches(profile.get("waiting"), text, buttons):
        return WAITING
    if _matches(profile.get("idle"), text, buttons):
        return IDLE
    return UNKNOWN
