"""The rules that decide whether the agent in another window has finished.

Every string here was measured, not invented - tests/probes/target_text.py
against Antigravity, opencode and Claude, once while each was working and once
after it had stopped. What is reconstructed below is the shape of what came
back, so that a profile edited later cannot quietly stop recognising the thing
it was written for.

The case that matters most is the last one in each group: a window that says
the right words for the wrong reason. Two of these three applications publish
their whole conversation as text, and a conversation can contain anything -
including a discussion of these very rules. That is not hypothetical. The
session that produced the Claude profile had the words "Claude is working" and
"Stop" in its transcript, put there by writing the profile.
"""
import sys

import context  # noqa: E402,F401
context.isolate_state()

from relay import agent  # noqa: E402

report = context.Report()
check = report.check

BY_NAME = {p["name"]: p for p in agent.BUILT_IN}


def state(profile, text="", buttons=()):
    return agent.state(0, profile, {"text": text, "buttons": list(buttons)})


print("\n--- Antigravity: buttons ---")
ag = BY_NAME["Antigravity"]
check("working", state(ag, buttons=["Copy", "Cancel (Ctrl+D)"]) == agent.BUSY)
check("finished", state(ag, buttons=["Send message", "Good response"]) == agent.IDLE)
check("asking to run a command",
      state(ag, "Requesting permission to run ipconfig",
            ["Copy", "Submit"]) == agent.WAITING)
# The request stays in the transcript after you answer it, so the text alone
# would mean "waiting" forever. It is paired with the composer being gone.
check("after you answered it",
      state(ag, "Requesting permission to run ipconfig",
            ["Send message"]) == agent.IDLE)
check("while it then runs it",
      state(ag, "Requesting permission to run ipconfig",
            ["Cancel (Ctrl+D)"]) == agent.BUSY)
check("caught mid-repaint", state(ag, buttons=["Copy"]) == agent.UNKNOWN)


print("\n--- opencode: the status line ---")
oc = BY_NAME["opencode"]
check("working", state(oc, "x esc interrupt tab agents ctrl+p") == agent.BUSY)
check("finished", state(oc, "9.6K (1%) ctrl+p commands") == agent.IDLE)


print("\n--- Claude: whole lines, from the end only ---")
cl = BY_NAME["Claude"]
FOOTER = ["Bypass permissions", "Add", "Press and hold to record",
          "Dictation settings", "Opus 5", "Effort:", "High",
          "Usage: context 26%", "Fast mode off", "Notifications"]
TRANSCRIPT = ["a line of the conversation"] * 200


def window(*composer):
    """A window's text: a long transcript, then the composer, then the footer."""
    return "\n".join(TRANSCRIPT + list(composer) + FOOTER)


check("working", state(cl, window(
    "thinking...", "Create PR",
    "Claude is working - wait for the turn to finish",
    "Chat mode", "Type / for commands", "Stop")) == agent.BUSY)

check("finished", state(cl, window(
    "just now", "Create PR", "More PR options", "Chat mode",
    "Send")) == agent.IDLE)

# The one that would have shipped broken. A conversation about the profile
# puts its own trigger words on screen; only their position saves it.
poisoned = window(
    "I will look for the line Stop under the composer,",
    "and for the text Claude is working - wait for the turn to finish",
    "Copy message", "Read aloud", "Chat mode", "Send")
check("a conversation quoting the signals is still idle",
      state(cl, poisoned) == agent.IDLE)
check("and a fragment rule would have been fooled by it",
      agent._matches({"text": "claude is working"}, poisoned, []))

# The sidebar lists every other session with its own status. Matching those
# would make any conversation running anywhere look like this one.
check("another session running elsewhere is not this one",
      state(cl, window("Running Some other conversation", "Chat mode",
                       "Send")) == agent.IDLE)

check("a cold accessibility tree is unknown, not idle",
      state(cl, "") == agent.UNKNOWN)


print("\n--- nothing recognises nothing ---")
check("no profile at all", agent.state(0, None) == agent.UNKNOWN)
check("an empty rule never matches", not agent._matches({}, "anything", []))
check("nor a missing one", not agent._matches(None, "anything", []))

print("\n--- only idle lets a queue through ---")
check("the other three all stop it",
      set(agent.STOPS) == {agent.BUSY, agent.WAITING, agent.UNKNOWN})

print("\n--- the file on disk says what the code says ---")
# profiles.json is what you edit; BUILT_IN is the fallback. They start life
# identical, and a change to one that forgets the other is a profile that
# works until the file is deleted.
on_disk = {p["name"]: p for p in agent.load_profiles()}
check("same profiles", set(on_disk) == set(BY_NAME), str(sorted(on_disk)))
for name, profile in BY_NAME.items():
    check(f"{name} matches", on_disk.get(name) == profile)

sys.exit(report.finish())
