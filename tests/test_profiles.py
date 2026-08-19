"""profiles.json, as edited by hand.

Teaching Relay a new application means writing a profile, and the file is meant
to be edited. So the interesting cases are all the same case: what a mistake in
that file is allowed to do.

The direction of the mistake is what matters. A profile that fails to recognise
a window costs a wait, which is a nuisance. A profile that reads a busy window
as finished types the next prompt over a reply in progress, into an agent with
a shell - so anything that cannot be understood has to read as "not finished".
"""
import json
import sys
import tempfile
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

from relay import agent  # noqa: E402

report = context.Report()
check = report.check

BY_NAME = {p["name"]: p for p in agent.BUILT_IN}
CLAUDE = BY_NAME["Claude"]
OPENCODE = BY_NAME["opencode"]


def state(profile, text="", buttons=()):
    return agent.state(1, profile, seen={"text": text, "buttons": list(buttons),
                                         "source": "test"})


print("\n--- a rule whose condition is misspelled matches nothing ---")
# Every condition in a rule has to hold, so a rule whose conditions were all
# misspelled has none - and used to hold for every window on screen. As the
# idle rule that reads as "it has finished" whatever is actually there.
check("not a match", agent._matches({"buttons": "Send message"}, "busy working", []) is False)
check("nor with a plausible-looking name",
      agent._matches({"contains": "esc interrupt"}, "anything", []) is False)
check("and a good condition beside a bad one does not rescue it",
      agent._matches({"text": "esc interrupt", "buttonz": "x"},
                     "esc interrupt", []) is False)

typo = {"name": "Typo", "busy": {"text": "working"}, "idle": {"buttons": "Send"}}
check("a window that has not finished is not called finished",
      state(typo, "the agent is still working") == agent.BUSY)
check("and one that cannot be read is unknown, not idle",
      state(typo, "something else entirely") == agent.UNKNOWN)


print("\n--- a condition that is not a phrase does not crash the read ---")
for value in ([1, 2], 5, None, {"a": 1}, True):
    try:
        got = agent._matches({"text": value}, "anything", [])
        failed = None
    except Exception as exc:
        got, failed = None, f"{type(exc).__name__}: {exc}"
    check(f"{type(value).__name__} is refused, not raised", failed is None and got is False,
          str(failed or got))

odd = {"name": "Odd", "busy": {"text": ["a", "b"]}, "idle": {"button": 7}}
try:
    got = state(odd, "a b", ["7"])
    failed = None
except Exception as exc:
    got, failed = None, f"{type(exc).__name__}: {exc}"
check("a whole profile of them reads as unknown",
      failed is None and got == agent.UNKNOWN, str(failed or got))


print("\n--- an entry that is not a profile at all is skipped ---")
broken = ["not a profile", None, 42, {"name": "Fine", "process": "x.exe"}]
agent.window_process = lambda hwnd: "x.exe"
agent.window_title = lambda hwnd: "a window"
try:
    got = agent.profile_for(1, broken)
    failed = None
except Exception as exc:
    got, failed = None, f"{type(exc).__name__}: {exc}"
check("the good one is still found", failed is None and got is not None,
      str(failed or got))
if got:
    check("and it is the right one", got.get("name") == "Fine", str(got))


print("\n--- the file itself ---")
tmp = Path(tempfile.mkdtemp(prefix="relay-profiles-"))
original = agent.PROFILES_PATH

agent.PROFILES_PATH = tmp / "missing.json"
check("no file means the built-ins", agent.load_profiles() == agent.BUILT_IN)

agent.PROFILES_PATH = tmp / "broken.json"
agent.PROFILES_PATH.write_text('{"profiles": [{"name": "x"},]}', encoding="utf-8")
check("a trailing comma means the built-ins", agent.load_profiles() == agent.BUILT_IN)
check("and the file is left as you typed it",
      "," in agent.PROFILES_PATH.read_text(encoding="utf-8"))

agent.PROFILES_PATH = tmp / "empty.json"
agent.PROFILES_PATH.write_text('{"profiles": []}', encoding="utf-8")
check("an empty list means the built-ins", agent.load_profiles() == agent.BUILT_IN)

agent.PROFILES_PATH = tmp / "wrong.json"
agent.PROFILES_PATH.write_text('{"profiles": "Antigravity"}', encoding="utf-8")
check("and so does one that is not a list", agent.load_profiles() == agent.BUILT_IN)

agent.PROFILES_PATH = tmp / "mine.json"
agent.PROFILES_PATH.write_text(
    json.dumps({"profiles": [{"name": "Mine", "process": "mine.exe",
                              "busy": {"text": "thinking"},
                              "idle": {"absent_text": "thinking"}}]}),
    encoding="utf-8")
mine = agent.load_profiles()
check("a good file is used as written", len(mine) == 1 and mine[0]["name"] == "Mine",
      str(mine))
check("busy", state(mine[0], "still thinking about it") == agent.BUSY)
check("idle", state(mine[0], "done") == agent.IDLE)

agent.PROFILES_PATH = tmp / "fresh.json"
check("the built-ins can be written out", agent.write_default_profiles() is True)
check("once", agent.write_default_profiles() is False)
check("and read back the same", agent.load_profiles() == agent.BUILT_IN)

agent.PROFILES_PATH = original


print("\n--- only the end of a window is read for a line rule ---")
# The transcript above it can say anything, including the words the profile
# looks for. Measured: a conversation containing the word Stop.
noise = "\n".join(f"line {i}" for i in range(200))
check("a signal buried in the transcript is not the signal",
      state(CLAUDE, noise + "\nstop\n" + "\n".join(f"tail {i}" for i in range(40)))
      == agent.UNKNOWN)
check("but the same word at the end is",
      state(CLAUDE, noise + "\nstop") == agent.BUSY)
check("the tail is the last few lines only",
      len(agent._tail(noise)) == agent.TAIL_LINES, str(len(agent._tail(noise))))
check("blank lines do not count towards it",
      agent._tail("a\n\n\n\nb")[-1] == "b", str(agent._tail("a\n\n\n\nb")))


print("\n--- buttons are whole names, not fragments ---")
# A rule that matched on part of a name would fire on "Send message to a
# friend" as readily as on "Send message".
antigravity = BY_NAME["Antigravity"]
check("the whole name matches", state(antigravity, "", ["Send message"]) == agent.IDLE)
check("a longer one does not",
      state(antigravity, "", ["Send message later"]) == agent.UNKNOWN)
check("and case does not matter", state(antigravity, "", ["SEND MESSAGE"]) == agent.IDLE)


print("\n--- what stops a queue ---")
check("busy stops it", agent.BUSY in agent.STOPS)
check("waiting stops it", agent.WAITING in agent.STOPS)
check("unknown stops it", agent.UNKNOWN in agent.STOPS)
check("only idle does not", agent.IDLE not in agent.STOPS)


print("\n--- and every profile that ships can say both ---")
# A profile with only a busy rule reads every other window as unknown for ever;
# one with only an idle rule is the dangerous half.
for profile in agent.BUILT_IN:
    check(f"{profile['name']} says when it is busy", bool(profile.get("busy")))
    check(f"{profile['name']} says when it is free", bool(profile.get("idle")))
    for rule_name in ("busy", "idle", "waiting"):
        rule = profile.get(rule_name) or {}
        unknown = [k for k in rule if k not in agent.CONDITIONS]
        check(f"{profile['name']} {rule_name} has no misspelled condition",
              not unknown, str(unknown))

sys.exit(report.finish())
