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


def _matches(rule, text, buttons):
    """Every condition in the rule has to hold, and an empty rule never does.

    Four kinds of condition, in two pairs. `text` looks for a fragment
    anywhere; `line` looks for a whole line at the end of the window. The
    second is much the stronger of the two - a window that says `Stop` under
    the box you type in is telling you something, and a window that merely
    contains the word somewhere in a conversation is not.
    """
    if not rule:
        return False
    lowered = text.lower()
    tail = None
    for key, wanted in rule.items():
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

    if _matches(profile.get("busy"), text, buttons):
        return BUSY
    if _matches(profile.get("waiting"), text, buttons):
        return WAITING
    if _matches(profile.get("idle"), text, buttons):
        return IDLE
    return UNKNOWN
