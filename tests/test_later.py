"""Prompts held back until an hour you named.

The whole point of this is that nobody is watching when it happens. You write
the prompt at midnight because the agent has run out of its allowance, and you
find out in the morning whether it went. So the failures that matter are the
quiet ones: a schedule that evaporated when Relay restarted, a prompt sent
twice because the list was written back after sending rather than before, and
an unreadable file that reads as "nothing is waiting" and then gets saved over.

The clock is passed in everywhere, so a test about five in the morning does not
have to be run at five in the morning.
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

from relay import later  # noqa: E402

report = context.Report()
check = report.check

HERE = Path(tempfile.mkdtemp(prefix="relay-later-"))
NOW = datetime(2026, 8, 20, 23, 30)      # a Thursday night, which is the case


def fresh(name):
    return HERE / f"{name}.json"


print("\n--- the hour you name is the next one of those ---")
# Typed at half past eleven at night, 05:00 means the morning that is coming,
# not the one that has gone.
for text, expected in [("5", "2026-08-21 05:00"),
                       ("05", "2026-08-21 05:00"),
                       ("05:00", "2026-08-21 05:00"),
                       ("5:00", "2026-08-21 05:00"),
                       ("5.30", "2026-08-21 05:30"),
                       ("17:45", "2026-08-21 17:45"),
                       ("23:31", "2026-08-20 23:31"),
                       ("00:00", "2026-08-21 00:00")]:
    got = later.parse_time(text, NOW)
    check(f"{text!r}", got is not None and f"{got:%Y-%m-%d %H:%M}" == expected,
          f"{got}")

check("the hour that has just gone is tomorrow",
      later.parse_time("23:29", NOW).day == 21, str(later.parse_time("23:29", NOW)))
check("and this exact minute is tomorrow too, not now",
      later.parse_time("23:30", NOW).day == 21, str(later.parse_time("23:30", NOW)))

for text in ["25:00", "5:99", "-1", "half five", "", "   ", "5:00:00", "abc"]:
    check(f"{text!r} is not a time", later.parse_time(text, NOW) is None,
          str(later.parse_time(text, NOW)))


print("\n--- said the way a person would say it ---")
check("tonight", "today at 23:45" in later.in_words(
    datetime(2026, 8, 20, 23, 45), NOW), later.in_words(datetime(2026, 8, 20, 23, 45), NOW))
check("with how long that is", "15m" in later.in_words(
    datetime(2026, 8, 20, 23, 45), NOW), later.in_words(datetime(2026, 8, 20, 23, 45), NOW))
check("tomorrow morning", "tomorrow at 05:00" in later.in_words(
    datetime(2026, 8, 21, 5, 0), NOW), later.in_words(datetime(2026, 8, 21, 5, 0), NOW))
check("and how many hours away", "5h 30m" in later.in_words(
    datetime(2026, 8, 21, 5, 0), NOW), later.in_words(datetime(2026, 8, 21, 5, 0), NOW))


print("\n--- keeping one ---")
path = fresh("keep")
check("nothing is waiting to start with", later.load(path) == [])
waiting = later.add(datetime(2026, 8, 21, 5, 0), "read the log and tell me why", path)
check("it is kept", len(waiting) == 1, str(waiting))
check("with the prompt as written",
      waiting[0]["prompt"] == "read the log and tell me why", str(waiting[0]))
check("and the hour it is for", waiting[0]["at"].endswith("05:00"), waiting[0]["at"])
check("it is on disk, not just in memory", path.exists())


print("\n--- and it survives everything this program does to itself ---")
# Relay is restarted to pick up a change, the laptop reboots, the keeper brings
# it back. A schedule that lived in memory would be gone every time, and you
# would find out by the work not being done.
again = later.load(path)
check("read back from a cold start", len(again) == 1, str(again))
check("the same prompt", again[0] == waiting[0], str(again))


print("\n--- several, soonest first ---")
path = fresh("order")
later.add(datetime(2026, 8, 21, 9, 0), "third", path)
later.add(datetime(2026, 8, 21, 5, 0), "first", path)
later.add(datetime(2026, 8, 21, 7, 0), "second", path)
check("in the order they will go out",
      [e["prompt"] for e in later.load(path)] == ["first", "second", "third"],
      str([e["prompt"] for e in later.load(path)]))


print("\n--- what is due, what is late, and what is still to come ---")
path = fresh("due")
later.add(datetime(2026, 8, 21, 5, 0), "due now", path)
later.add(datetime(2026, 8, 21, 9, 0), "not yet", path)
send, missed, waiting = later.due(datetime(2026, 8, 21, 5, 0), path)
check("the one whose hour has come", [e["prompt"] for e in send] == ["due now"],
      str(send))
check("the one that has not", [e["prompt"] for e in waiting] == ["not yet"],
      str(waiting))
check("and nothing was missed", missed == [], str(missed))

send, missed, waiting = later.due(datetime(2026, 8, 21, 4, 59), path)
check("a minute early is not yet", send == [], str(send))

# The laptop slept through it. Two hours late is still the prompt you wanted.
send, missed, waiting = later.due(datetime(2026, 8, 21, 7, 0), path)
check("late but not hopeless still goes", [e["prompt"] for e in send] == ["due now"],
      str(send))

# Days late is not. Typing it into whatever window is open now is the kind of
# surprise this project exists to avoid.
late = datetime(2026, 8, 21, 5, 0) + timedelta(hours=later.LATE_HOURS + 1)
send, missed, waiting = later.due(late, path)
check("but days late is reported, not sent",
      [e["prompt"] for e in missed] == ["due now"], str(missed))
check("while the later one, only nine hours over, still goes",
      [e["prompt"] for e in send] == ["not yet"], str(send))


print("\n--- dropping ---")
path = fresh("drop")
for hour, what in [(5, "one"), (7, "two"), (9, "three")]:
    later.add(datetime(2026, 8, 21, hour, 0), what, path)
check("the second one", later.drop(2, path) == 1)
check("and the other two are still there",
      [e["prompt"] for e in later.load(path)] == ["one", "three"],
      str([e["prompt"] for e in later.load(path)]))
check("a number that is not there drops nothing", later.drop(9, path) == 0)
check("and neither does one below the first", later.drop(0, path) == 0)
check("all of them", later.drop(None, path) == 2)
check("leaves nothing waiting", later.load(path) == [])


print("\n--- a file you cannot read is never written over ---")
# It holds a list of things somebody is expecting to happen. Reading it as
# nothing and then saving over it would throw that away silently, which is the
# one failure nobody would notice until the morning.
path = fresh("broken")
path.write_text('{"waiting": [{"at": "2026-08-21T05:00",}]}', encoding="utf-8")
before = path.read_text(encoding="utf-8")
check("it reads as nothing, so the phone keeps working", later.load(path) == [])
check("saving is refused", later.save([{"at": "2026-08-21T05:00", "prompt": "x"}],
                                      path) is False)
check("and the file is exactly as you left it",
      path.read_text(encoding="utf-8") == before)
check("adding is refused too",
      later.add(datetime(2026, 8, 21, 5, 0), "x", path) is None)


print("\n--- and a save that cannot land leaves the good one alone ---")
path = fresh("safe")
later.add(datetime(2026, 8, 21, 5, 0), "the good one", path)
before = path.read_text(encoding="utf-8")
missing = HERE / "no-such-dir" / "later.json"
check("saving into a missing directory fails cleanly",
      later.save([{"at": "2026-08-21T05:00", "prompt": "x"}], missing) is False)
check("the good file is untouched", path.read_text(encoding="utf-8") == before)
check("and no temp file is left behind",
      not any(p.suffix == ".tmp" for p in HERE.iterdir()),
      str([p.name for p in HERE.iterdir() if p.suffix == ".tmp"]))


print("\n--- rubbish in the file costs that line, not the schedule ---")
path = fresh("partly")
path.write_text(json.dumps({"waiting": [
    {"at": "2026-08-21T05:00", "prompt": "keep me"},
    {"at": "not a time", "prompt": "no hour"},
    {"at": "2026-08-21T06:00", "prompt": "   "},
    "not an entry",
    {"prompt": "no time at all"},
]}), encoding="utf-8")
check("the good one survives",
      [e["prompt"] for e in later.load(path)] == ["keep me"],
      str(later.load(path)))


print("\n--- it does not grow without end ---")
path = fresh("many")
for minute in range(later.MAX_WAITING):
    later.add(datetime(2026, 8, 21, 5, 0) + timedelta(minutes=minute), f"p{minute}", path)
check("full", len(later.load(path)) == later.MAX_WAITING, str(len(later.load(path))))
check("and the next one is refused rather than dropped quietly",
      later.add(datetime(2026, 8, 21, 23, 0), "one too many", path) is None)


print("\n--- diacritics come back as diacritics ---")
path = fresh("romanian")
later.add(datetime(2026, 8, 21, 5, 0), "Verifică log-ul și spune-mi ce s-a întâmplat", path)
check("as written", later.load(path)[0]["prompt"].startswith("Verifică"),
      later.load(path)[0]["prompt"])
check("and the file is not escaped", "ă" in path.read_text(encoding="utf-8"))

sys.exit(report.finish())
