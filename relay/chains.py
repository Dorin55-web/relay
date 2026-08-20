"""The chains you have kept, over chains.json.

Building a chain is work. It is not a list of templates - it is templates with
their `<angle brackets>` filled in for the job in front of you, in an order you
worked out. All of that used to end with the window: close it and the list was
gone, so a sequence you run every morning was rebuilt every morning.

Same shape as `prompts.py`, for the same reasons: a JSON file next to the
config so you can read and edit it yourself, a fallback rather than a crash
when it will not parse, and a write that goes through a temp file so an
interrupted save cannot leave you holding half a chain.

Where it differs is what nothing means. There are no built-in chains and there
should not be - a chain is filled in for one job in one application, and
nothing shipped could know yours. So a missing file answers with an empty list
rather than with defaults, and the window shows nothing kept, which is the
truth.

`chain.py` is the window that builds one; this is the shelf it puts them on.
Nothing here imports Qt, so the shelf can be read from a thread that has no
business touching widgets - a message from the phone asking for a saved chain
by name is meant to be the second caller.
"""

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHAINS_PATH = PROJECT_ROOT / "chains.json"


def _fold(name):
    """How two names are compared: not by case, not by surrounding space.

    'Morning triage' and 'morning triage' are one chain to the person who
    saved them, and a name typed on a phone keyboard does not arrive with the
    capitals it was saved under.
    """
    return str(name or "").strip().casefold()


def _clean(entries):
    """Keep only chains that could actually be run, and only one per name."""
    out, seen = [], set()
    for i, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            print(f"[chains] entry {i} is not an object; skipped")
            continue
        name = str(entry.get("name", "") or "").strip()
        raw = entry.get("steps")
        steps = ([str(s).strip() for s in raw if str(s).strip()]
                 if isinstance(raw, list) else [])
        if not name or not steps:
            print(f"[chains] entry {i} has no name or no steps; skipped")
            continue
        if _fold(name) in seen:
            # Two chains under one name is a name that cannot be asked for.
            # From the phone it would be a command with two answers, and the
            # window would show you which one it picked only after it ran.
            print(f"[chains] a second chain called {name!r}; skipped")
            continue
        seen.add(_fold(name))
        out.append({"name": name, "steps": steps})
    return out


def _read(path):
    """The chains in the file, and whether the file itself made sense.

    Two callers want different halves of this. Loading wants the chains and
    does not care why there are none - an empty shelf and an unreadable one
    both mean there is nothing to offer. Saving cares very much: writing over a
    file it could not read is how one stray comma costs you every chain in it.
    """
    if not path.is_file():
        return [], True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[chains] {path.name} could not be read ({exc}); nothing kept")
        return [], False
    entries = data.get("chains") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        print(f"[chains] {path.name} has no 'chains' list; nothing kept")
        return [], False
    return _clean(entries), True


def load(path=None):
    """Every saved chain, in the order they were saved.

    An empty list when there is no file, when it will not parse, or when you
    have simply not kept one yet.
    """
    return _read(Path(path) if path else CHAINS_PATH)[0]


def names(path=None):
    """Just the names, in the order the window lists them.

    What a caller offers when it has one line to spend - a phone reply, say -
    rather than the steps of every chain.
    """
    return [chain["name"] for chain in load(path)]


def steps_for(name, path=None):
    """The steps of one chain, or None when nothing is called that."""
    wanted = _fold(name)
    if not wanted:
        return None
    for chain in load(path):
        if _fold(chain["name"]) == wanted:
            # A copy. The caller is about to hand this to a queue or edit the
            # blanks in it, and neither should reach back into the file.
            return list(chain["steps"])
    return None


def save(chains, path=None):
    """Write the whole shelf back. True when it actually landed.

    Through a neighbouring temp file, so a failure part way through leaves the
    previous chains intact rather than a truncated file that loads as none.

    An empty list is refused. Nothing here deletes a chain, so the only way to
    arrive with nothing is a caller that read the file, got the empty fallback
    because it would not parse, and is about to write that back - which is the
    stray comma costing you the file.
    """
    chains_path = Path(path) if path else CHAINS_PATH
    cleaned = _clean(chains)
    if not cleaned:
        print("[chains] refusing to save an empty list")
        return False

    temp_path = chains_path.with_suffix(".json.tmp")
    try:
        temp_path.write_text(
            json.dumps({"chains": cleaned}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, chains_path)
        print(f"[chains] saved {len(cleaned)} chain(s)")
        return True
    except OSError as exc:
        print(f"[chains] could not save {chains_path.name}: {exc}")
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def save_chain(name, steps, path=None):
    """Keep one chain under a name. True when the file was written.

    A name already taken replaces that chain where it stands, rather than
    adding a second one under it or moving it to the end. Replacing is what
    re-saving a chain you have just improved means; appending would give the
    name two answers, and moving it would send the entry you use most
    travelling down the list every time you touched it.
    """
    chains_path = Path(path) if path else CHAINS_PATH
    name = str(name or "").strip()
    steps = [str(s).strip() for s in (steps or []) if str(s).strip()]
    if not name or not steps:
        print("[chains] a chain needs a name and at least one step")
        return False

    kept, readable = _read(chains_path)
    if not readable:
        # The fallback for an unreadable file is an empty list, and saving on
        # top of that would replace everything in it with this one chain.
        print(f"[chains] {chains_path.name} could not be read; not saving over it")
        return False

    entry = {"name": name, "steps": steps}
    for i, chain in enumerate(kept):
        if _fold(chain["name"]) == _fold(name):
            kept[i] = entry
            break
    else:
        kept.append(entry)
    return save(kept, chains_path)
