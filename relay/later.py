"""Prompts held back until a time you name.

The case this exists for: the agent has run out of its allowance, says so, and
will not take another word until the small hours. The work is ready and you are
not going to be awake. So you write it now, say when, and go to bed.

Everything here is on disk rather than in memory, because the gap between
writing a prompt and sending it is measured in hours and Relay does not last
that long untouched - it gets restarted to pick up a change, the laptop reboots
overnight, the keeper brings it back. A schedule that evaporated on any of
those would be worse than no schedule at all: you would find out it had gone by
the work not being done.

Local time throughout. One machine, one clock, and a phone in the same room.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LATER_PATH = PROJECT_ROOT / "later.json"

# More than this many at once is a queue you have lost track of rather than a
# plan, and every one of them types into the same window when it comes round.
MAX_WAITING = 20

# How late a prompt may be and still be worth sending. The laptop sleeps, Relay
# is restarted, the keeper takes a minute to notice - a prompt due at five and
# sent at seven is still the prompt you wanted. One due last Tuesday is not,
# and typing it into whatever window is open now is the sort of surprise this
# whole project is written to avoid.
LATE_HOURS = 12


def parse_time(text, now=None):
    """The next time of day `text` names, or None if it names none.

    Takes 5, 05, 5:00, 05:00, 5.30, 17:45. Always the next one to come round:
    a time that has already passed today means tomorrow, which is what anybody
    typing 05:00 at midnight means by it.
    """
    now = now or datetime.now()
    cleaned = (text or "").strip().replace(".", ":").replace(",", ":")
    if not cleaned:
        return None

    parts = cleaned.split(":")
    if len(parts) > 2 or not all(p.isdigit() for p in parts if p != ""):
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)
    return when


def in_words(when, now=None):
    """How far off it is, said the way a person would say it."""
    now = now or datetime.now()
    minutes = int((when - now).total_seconds() // 60)
    if minutes < 0:
        return "already past"
    hours, minutes = divmod(minutes, 60)
    gap = f"{hours}h {minutes}m" if hours else f"{minutes}m"
    day = "today" if when.date() == now.date() else "tomorrow"
    return f"{day} at {when:%H:%M}, in {gap}"


def _clean(entries):
    """Keep only the entries that still make sense, oldest due first."""
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        prompt = str(entry.get("prompt", "")).strip()
        try:
            when = datetime.fromisoformat(str(entry.get("at", "")))
        except ValueError:
            continue
        if not prompt:
            continue
        out.append({"at": when.isoformat(timespec="minutes"), "prompt": prompt})
    out.sort(key=lambda e: e["at"])
    return out[:MAX_WAITING]


def load(path=None):
    """Everything waiting, soonest first. An unreadable file reads as nothing.

    Nothing rather than an error, because this is read on every poll and a bad
    file must not stop the phone working. Saving checks for itself - see save.
    """
    path = Path(path) if path else LATER_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[later] {path.name} could not be read ({exc}); nothing is waiting")
        return []
    entries = data.get("waiting") if isinstance(data, dict) else data
    return _clean(entries) if isinstance(entries, list) else []


def readable(path=None):
    """False when the file is there but cannot be parsed.

    Saving over one of those would throw away whatever is in it, and what is in
    it is a list of things somebody is expecting to happen.
    """
    path = Path(path) if path else LATER_PATH
    if not path.exists():
        return True
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (json.JSONDecodeError, OSError):
        return False


def save(entries, path=None):
    """Write the list back. True when it landed.

    Through a neighbouring temp file, so a failure part way through leaves
    yesterday's schedule intact rather than a truncated file that reads as an
    empty one - and an empty one is silent, which is the failure that would not
    be noticed until the morning.
    """
    path = Path(path) if path else LATER_PATH
    if not readable(path):
        print(f"[later] {path.name} cannot be read, so it will not be written over")
        return False

    cleaned = _clean(entries)
    temp = path.with_suffix(".json.tmp")
    try:
        temp.write_text(
            json.dumps({"waiting": cleaned}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        os.replace(temp, path)
        return True
    except OSError as exc:
        print(f"[later] could not save {path.name}: {exc}")
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def add(when, prompt, path=None):
    """Hold one prompt until `when`. Returns the whole list, or None if full."""
    waiting = load(path)
    if len(waiting) >= MAX_WAITING:
        return None
    waiting.append({"at": when.isoformat(timespec="minutes"),
                    "prompt": str(prompt).strip()})
    if not save(waiting, path):
        return None
    return load(path)


def drop(which=None, path=None):
    """Forget one by its position in the list, or all of them. How many went."""
    waiting = load(path)
    if which is None:
        gone = len(waiting)
        return gone if save([], path) else 0
    if not 1 <= which <= len(waiting):
        return 0
    del waiting[which - 1]
    return 1 if save(waiting, path) else 0


def due(now=None, path=None):
    """What should go now, what was left too late, and what is still waiting.

    Returns (send, missed, waiting). The three are separate because they need
    three different things said about them: one is being sent, one never will
    be and you should hear so, and one is still to come.
    """
    now = now or datetime.now()
    send, missed, waiting = [], [], []
    for entry in load(path):
        when = datetime.fromisoformat(entry["at"])
        if when > now:
            waiting.append(entry)
        elif now - when > timedelta(hours=LATE_HOURS):
            missed.append(entry)
        else:
            send.append(entry)
    return send, missed, waiting
