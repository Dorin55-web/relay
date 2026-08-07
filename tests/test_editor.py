"""Drive the prompt editor without a human: click its buttons, check the file."""
import json
import sys

import context  # noqa: E402,F401
context.isolate_state()

from PySide6.QtWidgets import QApplication

from relay import prompts as prompts_mod

fails = []


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not condition:
        fails.append(name)


# context.isolate_state() has already pointed PROMPTS_PATH at a temp
# directory; this only has to put the ten defaults in it.
prompts_mod.ensure_file()
tmp = prompts_mod.PROMPTS_PATH.parent

app = QApplication.instance() or QApplication(sys.argv)

import relay.prompt_editor as editor_mod  # noqa: E402
from relay.prompt_editor import PromptEditor, open_editor  # noqa: E402

# The editor refuses a bad save by putting up a modal warning, and a modal
# waits for a click this file cannot give. See context.silence_dialogs.
dialogs = context.silence_dialogs(editor_mod)

print("\n--- save round-trip ---")
ed = PromptEditor()
check("loads ten entries", len(ed.entries) == 10)
check("first row selected", ed.index == 0)
check("fields filled from the model", ed.label.text() == ed.entries[0]["label"])

ed.label.setText("Renamed by the test")
ed.text.setPlainText("Find the cause of <thing>, and do not fix it yet.")
ed.group.setText("Regrouped")
check("typing reaches the model", ed.entries[0]["label"] == "Renamed by the test")
check("list caption follows", "Renamed by the test" in ed.list.item(0).text())

check("save reports success", ed._save() is True)
on_disk = json.loads(prompts_mod.PROMPTS_PATH.read_text(encoding="utf-8"))
check("file has ten prompts", len(on_disk["prompts"]) == 10)
check("edit persisted", on_disk["prompts"][0]["label"] == "Renamed by the test")
check("text persisted", "<thing>" in on_disk["prompts"][0]["text"])
check("reload sees it", prompts_mod.load()[0]["label"] == "Renamed by the test")

print("\n--- list edits ---")
ed = PromptEditor()
first, second = ed.entries[0]["label"], ed.entries[1]["label"]
ed.list.setCurrentRow(0)
ed._move(1)
check("move down swaps", ed.entries[0]["label"] == second and ed.entries[1]["label"] == first)
check("selection follows the move", ed.index == 1)
ed._move(-1)
check("move up restores", ed.entries[0]["label"] == first)

ed.list.setCurrentRow(0)
check("up disabled at the top", not ed.up_btn.isEnabled())
ed.list.setCurrentRow(9)
check("down disabled at the bottom", not ed.down_btn.isEnabled())

check("add refused when full", (ed._add() is None) and len(ed.entries) == 10)
check("add button disabled when full", not ed.add_btn.isEnabled())

ed.list.setCurrentRow(3)
removed = ed.entries[3]["label"]
ed._delete()
check("delete removes one", len(ed.entries) == 9)
check("deleted the right one", all(e["label"] != removed for e in ed.entries))
check("numbers renumbered", ed.list.item(3).text().startswith("4."))
check("add allowed again", ed.add_btn.isEnabled())

ed._add()
check("add appends", len(ed.entries) == 10)
check("new entry selected", ed.index == 9)

print("\n--- refuses bad input ---")
ed.entries[9]["text"] = ""
dialogs.clear()
check("blank text blocks the save", ed._save() is False)
# Refusing silently would be worse than saving nine prompts: the numbering is
# the point of the menu, and losing one without a word is how you find out
# later. It has to say so, not just return False.
check("and says why", [d for d in dialogs if d[0] == "warning"],
      str([d[1] for d in dialogs]))
check("naming the prompt that is empty",
      any("10" in d[2] for d in dialogs), str([d[2][:40] for d in dialogs]))
check("file untouched by the blocked save",
      len(json.loads(prompts_mod.PROMPTS_PATH.read_text(encoding='utf-8'))["prompts"]) == 10)

ed.list.setCurrentRow(9)
ed.text.setPlainText("Now it has text <x>.")
check("save works once fixed", ed._save() is True)

print("\n--- last one cannot be deleted ---")
ed = PromptEditor()
while len(ed.entries) > 1:
    ed.list.setCurrentRow(0)
    ed._delete()
check("one left", len(ed.entries) == 1)
ed._delete()
check("delete refused at one", len(ed.entries) == 1)
check("delete button disabled", not ed.del_btn.isEnabled())
ed.close()

print("\n--- single window ---")
a = open_editor()
b = open_editor()
check("same window returned", a is b)
a.close()
c = open_editor()
check("new window after close", c is not a)
c.close()

print("\n--- defaults survive a corrupt file ---")
prompts_mod.PROMPTS_PATH.write_text("{ broken", encoding="utf-8")
ed = PromptEditor()
check("editor still opens on ten defaults", len(ed.entries) == 10)
ed._save()
check("saving repairs the file",
      len(json.loads(prompts_mod.PROMPTS_PATH.read_text(encoding='utf-8'))["prompts"]) == 10)

print("\n--- no temp file left behind ---")
leftovers = list(tmp.glob("*.tmp"))
check("no .tmp remains", not leftovers, str(leftovers))

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
