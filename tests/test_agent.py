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

Two groups of sections here name no application at all. A measured string can
only be checked against the application it came out of, and the profile that
goes wrong is the one written next - so those ask instead what has to be true
of any profile whatever it was written for: that every condition it is allowed
to use is one the reader actually enforces, that the busy window it describes
is never also read as finished, and that it says which window on screen is its
own. A fourth profile is held to all of it on the day it is added, without
anyone remembering to come back here.

One string below was not measured, and says so where it appears. Nobody has
caught Claude or opencode stopped at a permission prompt with the probe, so
neither has a `waiting` rule and neither can ring the bell on the phone. What
those sections settle is everything about that rule which does not need the
words: that it is missing, what it costs while it is, and the shape it has to
take when the measurement finally arrives.
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


print("\n--- which window a profile answers to ---")
# opencode calls a named session's window `OC | <name>` and a brand new one
# just `OpenCode`. Only the first was known at first, so a chain started
# against a freshly opened opencode was refused - and freshly opened is
# exactly when you start one.
check("a named session", agent._title_matches(oc["title_contains"],
                                              "OC | Scriere text 400 cuvinte"))
check("one with no name yet", agent._title_matches(oc["title_contains"],
                                                   "OpenCode"))
check("whatever the case", agent._title_matches(oc["title_contains"], "opencode"))
# Without the title test the process alone would match every terminal on the
# machine, including the one a probe or a test is running in.
check("not any other terminal",
      not agent._title_matches(oc["title_contains"], "Command Prompt"))
check("no fragments asked for means any title",
      agent._title_matches(None, "anything at all"))


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


print("\n--- the state none of these can report yet ---")
# The bell on the phone is agent.WAITING, and WAITING comes from exactly one
# place: a rule called `waiting`. Antigravity has one because its permission
# prompt was caught with the probe. Nobody has caught Claude or opencode at
# one, so they have none - and a rule that is not there can never match,
# whatever the window says.
carries = {name: bool(p.get("waiting")) for name, p in BY_NAME.items()}
check("Antigravity was measured at one", carries["Antigravity"])
check("Claude has not been", not carries["Claude"], "delete this line when it is")
check("opencode has not been", not carries["opencode"], "delete this line when it is")
check("and a rule nobody has written never matches",
      not agent._matches(cl.get("waiting"), window("Chat mode"), []))

# So a Claude that has stopped to ask you something falls all the way through
# to unknown, which holds the queue - safe, and useless. The phone says
# "cannot tell what it is doing" at the one moment somebody in another room
# would have wanted telling. Seen in relay.log as `step 1: cannot tell what it
# is doing; holding`, in the middle of a chain that was waiting on a dialog.
#
# What is proved here is only the half that does not need the measurement: a
# window showing neither of the two lines the profile knows has no answer left
# in it. Whether a permission prompt is really such a window is the one thing
# only the probe can settle.
check("a window with neither line under the composer has no answer left",
      state(cl, window("Chat mode")) == agent.UNKNOWN)


print("\n--- opencode has no such fall-through ---")
# Its two rules are exact opposites - the status line is on screen or it is
# not - so every read of an opencode window is busy or idle and unknown is
# unreachable. That is worth knowing before a waiting rule is written for it.
# Claude stopping to ask a question costs a notification; opencode stopping to
# ask one may cost more, because if its prompt clears `esc interrupt` the
# answer today is idle, and idle is the only state a queue will move on.
for said in ("", "esc interrupt", "9.6K (1%) ctrl+p commands", "a screen of who knows"):
    check(f"{(said or 'nothing at all')[:24]!r} is called one or the other",
          state(oc, said) in (agent.BUSY, agent.IDLE), state(oc, said))


print("\n--- the shape a waiting rule will have to have ---")
# No measured strings here, because there are none to have yet - they are
# measured or they are nothing. The shape can be settled without them, and it
# is the trap Antigravity's rule was already written around: the request stays
# in the transcript after you have answered it, so the phrase on its own would
# read as "waiting" for the rest of the session.
ASKED = "<the line the probe comes back with>"
asking = window(ASKED, "Chat mode")
answered = window(ASKED, "Chat mode", "Send")

check("the phrase alone matches while it is asking",
      agent._matches({"line": ASKED}, asking, []))
check("and would still match an hour after you answered",
      agent._matches({"line": ASKED}, answered, []))
check("paired with the composer being gone it matches while asking",
      agent._matches({"line": ASKED, "absent_line": "send"}, asking, []))
check("and falls away the moment the composer is back",
      not agent._matches({"line": ASKED, "absent_line": "send"}, answered, []))

# And it has to be `line` rather than `text`, for the same reason busy and idle
# already are: this app publishes the whole conversation, so the session that
# writes the rule puts the rule's own trigger on screen. A fragment rule would
# ring the bell at somebody quoting it.
quoted = f"someone in the conversation quoting {ASKED}\n" + window("Chat mode")
check("a fragment rule fires on the conversation quoting it",
      agent._matches({"text": ASKED, "absent_line": "send"}, quoted, []))
check("a whole-line rule does not",
      not agent._matches({"line": ASKED, "absent_line": "send"}, quoted, []))

# Dropped into the profile, that shape gives the three answers the phone needs.
# The last is the ordering: state() asks busy before waiting, so a window that
# is working again is never read as still stopped for you.
supposed = dict(cl, waiting={"line": ASKED, "absent_line": "send"})
check("asking rings the bell", state(supposed, asking) == agent.WAITING)
check("answering it puts the queue back to work",
      state(supposed, answered) == agent.IDLE)
check("and one that has started working again is busy, not asking",
      state(supposed, window(ASKED, "Chat mode", "Stop")) == agent.BUSY)


print("\n--- where a step gets typed ---")
# focus_input answers None only when the profile names no box, which is how a
# terminal is told apart from an application that has one. Getting this wrong
# either types blind into a chat window or refuses to type into a terminal.
check("a terminal has no box to find", agent.focus_input(0, oc) is None)
check("nor has a window with no profile", agent.focus_input(0, None) is None)
check("Claude names its box", cl.get("input") == "Prompt")
check("Antigravity names its box", ag.get("input"))

print("\n--- nothing recognises nothing ---")
check("no profile at all", agent.state(0, None) == agent.UNKNOWN)
check("an empty rule never matches", not agent._matches({}, "anything", []))
check("nor a missing one", not agent._matches(None, "anything", []))

print("\n--- only idle lets a queue through ---")
check("the other three all stop it",
      set(agent.STOPS) == {agent.BUSY, agent.WAITING, agent.UNKNOWN})

print("\n--- every condition the tuple lists is one the reader enforces ---")
# CONDITIONS is what stops a misspelled key: _matches refuses any rule holding
# a name that is not in it. That only helps while the tuple and the reader
# agree. A name added to the tuple and never given a branch below is passed
# over rather than refused, and a condition that is passed over is a condition
# that always holds - so a rule made of one reads every window on screen as a
# match. On an idle rule that is "it has finished" whatever is actually there,
# which is the one direction of being wrong that types over a reply.
#
# So each name is asked to do both halves of its job: hold where it should,
# and refuse where it should not. A name with no branch fails the second half.
MARK = "zzmarker"
PRESENT = (f"a line of something else\n{MARK}", [MARK])
ABSENT = ("a line of something else\nand another", [])

for condition in agent.CONDITIONS:
    # An `absent_` condition wants the opposite window from the rest.
    holds, refuses = ((ABSENT, PRESENT) if condition.startswith("absent_")
                      else (PRESENT, ABSENT))
    check(f"{condition} holds where it should",
          agent._matches({condition: MARK}, *holds) is True)
    check(f"{condition} refuses where it should",
          agent._matches({condition: MARK}, *refuses) is False)


print("\n--- and no profile reads its own busy window as finished ---")
# Everything above is one application's measured text. This is about the shape
# of a profile whatever it was written for, so a fourth one added later is held
# to it without anyone remembering to come back here.
#
# A busy rule is a description of the busy window. Build that window from the
# rule and the profile has to still call it busy - and the idle rule must not
# match it at all. Two rules that can both be true of one window is a profile
# that hands the queue a window still working.


def window_the_rule_describes(rule):
    """The window a rule says it is looking at: what it asks to be present.

    Nothing is added for an `absent_` condition, which is the point - what a
    rule wants gone is simply never put there.
    """
    lines, buttons = [], []
    for condition, wanted in (rule or {}).items():
        if condition == "text":
            # Buried in a longer line on purpose: `text` is a fragment rule and
            # must not be quietly satisfied by the whole-line `line` test.
            lines.append(f"before {wanted} after")
        elif condition == "line":
            lines.append(wanted)
        elif condition == "button":
            buttons.append(wanted)
    return "\n".join(lines), buttons


for profile in agent.BUILT_IN:
    name = profile.get("name")

    text, buttons = window_the_rule_describes(profile.get("busy"))
    got = state(profile, text, buttons)
    check(f"{name} still calls its busy window busy", got == agent.BUSY, got)
    check(f"and {name} does not also call that one finished",
          not agent._matches(profile.get("idle"), text, buttons))

    text, buttons = window_the_rule_describes(profile.get("idle"))
    got = state(profile, text, buttons)
    check(f"{name} still calls its idle window idle", got == agent.IDLE, got)

    if profile.get("waiting"):
        text, buttons = window_the_rule_describes(profile["waiting"])
        got = state(profile, text, buttons)
        check(f"{name} still calls its waiting window waiting",
              got == agent.WAITING, got)


print("\n--- and every profile can say which window is its own ---")
# profile_for skips the process test when a profile names no process, and
# _title_matches answers True when it asks for no fragments. A profile naming
# neither therefore matches the first window Windows hands over - and then
# recognised_windows offers every window on screen as an agent, and a step is
# read with rules written for something else entirely. An empty title fragment
# is the same mistake spelled differently: "" is in every title there is.
READ_KEYS = {"name", "process", "title_contains", "input",
             "busy", "idle", "waiting"}
names = []
for profile in agent.BUILT_IN:
    label = profile.get("name") or profile.get("process") or repr(profile)[:20]
    names.append(profile.get("name"))

    check(f"{label} says what it is called",
          isinstance(profile.get("name"), str) and bool(profile["name"].strip()),
          repr(profile.get("name")))
    check(f"{label} says which window to look at",
          bool(profile.get("process")) or bool(profile.get("title_contains")))

    wanted = profile.get("title_contains") or []
    fragments = [wanted] if isinstance(wanted, str) else list(wanted)
    check(f"{label} asks for no fragment that is in every title",
          all(str(f).strip() for f in fragments), str(fragments))

    # The near misses are what this is for. `imput` leaves focus_input
    # answering None, and None is how a terminal is told apart from a window
    # with a box - so the step is typed blind into a chat window instead.
    stray = sorted(set(profile) - READ_KEYS)
    check(f"{label} carries nothing the reader never looks at", not stray,
          str(stray))

    box = profile.get("input")
    check(f"{label} names a box or no box at all",
          box is None or (isinstance(box, str) and bool(box.strip())), repr(box))

check("and no two of them answer to the same name",
      len(set(names)) == len(names), str(names))


print("\n--- the file on disk says what the code says ---")
# profiles.json is what you edit; BUILT_IN is the fallback. They start life
# identical, and a change to one that forgets the other is a profile that
# works until the file is deleted.
on_disk = {p["name"]: p for p in agent.load_profiles()}
check("same profiles", set(on_disk) == set(BY_NAME), str(sorted(on_disk)))
for name, profile in BY_NAME.items():
    check(f"{name} matches", on_disk.get(name) == profile)

sys.exit(report.finish())
