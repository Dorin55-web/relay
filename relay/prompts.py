"""The prompt library behind the orb's right-click menu.

Ten scaffolds you reach for constantly, kept one click away so the ones you use
every day never have to be typed or dictated again. Each is a skeleton with
`<angle brackets>` where your specifics go - pasting one leaves the blanks in
place for you to fill, which is why inserting a prompt never submits it.

They live in `prompts.json` next to the config so you can rewrite them without
touching any code. The defaults below are the fallback: if that file is missing,
malformed, or you delete an entry you regret, the menu still comes up.
"""

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_PATH = PROJECT_ROOT / "prompts.json"

MAX_PROMPTS = 10          # more than fits comfortably in a right-click menu

# Ordered in pairs, two per kind of work, roughly in the order a task moves:
# find the cause, read the code, plan, build, check.
DEFAULTS = [
    {
        "section": "Diagnose",
        "label": "Pinpoint the cause",
        "text": "I need you to review this in complete depth and pinpoint the "
                "exact cause of <problem>. Do not fix anything yet.",
    },
    {
        "section": "Diagnose",
        "label": "Deep dive, several agents",
        "text": "I need you to launch <3> deep dive agents and review what is "
                "causing <problem>. Review this in complete depth and pinpoint "
                "the exact cause.",
    },
    {
        "section": "Review",
        "label": "Complete review of an area",
        "text": "I need you to do a complete review of <area> and identify "
                "<what you are looking for>, and then create a structured plan "
                "for improvements respectively.",
    },
    {
        "section": "Review",
        "label": "Find dead code and duplication",
        "text": "I need you to do an in-depth review and identify any dead "
                "code, duplication and leftover scaffolding, and output all of "
                "your findings in a readme file.",
    },
    {
        "section": "Plan",
        "label": "Structured plan, no code",
        "text": "Would you review this in complete depth and then create a "
                "structured plan to <goal>. Do not write any code yet.",
    },
    {
        "section": "Plan",
        "label": "Options and trade-offs",
        "text": "Would you think through how we could approach <problem>, and "
                "give me the trade-offs of each option before we pick one.",
    },
    {
        "section": "Build",
        "label": "Implement a feature",
        "text": "I need you to build <feature> and <design requirement> "
                "respectively, following the conventions already in this "
                "codebase.",
    },
    {
        "section": "Build",
        "label": "Fix and update accordingly",
        "text": "Would you review this and update the code accordingly to fix "
                "<problem>, and then check that nothing else depended on the "
                "old behaviour.",
    },
    {
        "section": "Check",
        "label": "Test and verify",
        "text": "I need you to test <what> and verify that <condition> holds, "
                "and then report what you actually ran and what it returned.",
    },
    {
        "section": "Check",
        "label": "Recap for a hand-off",
        "text": "Would you recap what was done in this conversation, and output "
                "your findings in a readme file so another session can pick it "
                "up.",
    },
]


def _clean(entries):
    """Keep only usable entries, so one bad edit cannot empty the menu."""
    out = []
    for i, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            print(f"[prompts] entry {i} is not an object; skipped")
            continue
        text = str(entry.get("text", "")).strip()
        if not text:
            print(f"[prompts] entry {i} has no text; skipped")
            continue
        out.append({
            "section": str(entry.get("section", "") or "").strip(),
            # Falling back to the text itself means a hand-written entry with no
            # label still shows something you can recognise.
            "label": str(entry.get("label", "") or text).strip(),
            "text": text,
        })
    return out[:MAX_PROMPTS]


def load(path=None):
    """The prompt list, from prompts.json when it is readable, else the defaults."""
    prompts_path = Path(path) if path else PROMPTS_PATH
    if not prompts_path.is_file():
        return _clean(DEFAULTS)

    try:
        data = json.loads(prompts_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[prompts] {prompts_path.name} could not be read ({exc}); using defaults")
        return _clean(DEFAULTS)

    entries = data.get("prompts") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        print(f"[prompts] {prompts_path.name} has no 'prompts' list; using defaults")
        return _clean(DEFAULTS)

    cleaned = _clean(entries)
    if not cleaned:
        print(f"[prompts] {prompts_path.name} is empty; using defaults")
        return _clean(DEFAULTS)
    return cleaned


def ensure_file(path=None):
    """Write prompts.json on first run, so there is something to edit."""
    prompts_path = Path(path) if path else PROMPTS_PATH
    if prompts_path.exists():
        return prompts_path
    try:
        prompts_path.write_text(
            json.dumps({"prompts": DEFAULTS}, indent=2) + "\n", encoding="utf-8"
        )
        print(f"[prompts] wrote {prompts_path.name}")
    except OSError as exc:
        print(f"[prompts] could not write {prompts_path.name}: {exc}")
    return prompts_path


def open_for_editing(path=None):
    """Open prompts.json in whatever the system uses for .json files."""
    prompts_path = ensure_file(path)
    try:
        if sys.platform == "win32":
            os.startfile(prompts_path)  # noqa: S606 - a file we just wrote ourselves
        else:
            os.system(f'xdg-open "{prompts_path}"')
        return True
    except Exception as exc:
        print(f"[prompts] could not open {prompts_path}: {exc}")
        return False
