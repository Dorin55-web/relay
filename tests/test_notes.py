"""Drive the Notes & Ideas system and window without a human."""
import json
import sys
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

from PySide6.QtWidgets import QApplication

from relay import notes as notes_mod
from relay.notes import NotesWindow, open_notes, filter_notes, add_note, save, load

report = context.Report()
check = report.check

app = QApplication.instance() or QApplication(sys.argv)
dialogs = context.silence_dialogs(notes_mod)

print("\n--- 1. Storage & CRUD ---")
notes_mod.ensure_file()
check("notes.json exists", notes_mod.NOTES_PATH.exists())

initial = load()
check("initial notes loaded", len(initial) >= 1)
check("initial note has title and text", bool(initial[0].get("title")) and bool(initial[0].get("text")))

added = add_note(title="New Test Idea", text="Exploring deep multi-agent workflow", tag="Idea")
check("add_note returns created note", added["title"] == "New Test Idea")
check("add_note prepended to list", load()[0]["title"] == "New Test Idea")

# Filter tests
filtered = filter_notes("multi-agent")
check("filter_notes finds note by text", len(filtered) == 1 and filtered[0]["id"] == added["id"])

filtered_tag = filter_notes("Idea")
check("filter_notes finds note by tag", len(filtered_tag) >= 1)

filtered_none = filter_notes("nonexistent_random_phrase_xyz")
check("filter_notes returns empty on mismatch", len(filtered_none) == 0)

print("\n--- 2. NotesWindow UI & Interaction ---")
pasted_result = []

def fake_paste(text):
    pasted_result.append(text)

win = NotesWindow(on_paste=fake_paste, target_getter=lambda: "Test Editor Window")
check("window title correct", win.windowTitle() == "Relay - Notes & Ideas")
check("list count matches notes count", win.list.count() == len(win.notes))

# Selection
win._select_by_id(added["id"])
check("active_id is selected", win.active_id == added["id"])
check("title input matches note", win.title_input.text() == "New Test Idea")
check("text editor matches note", "Exploring deep multi-agent" in win.text_editor.toPlainText())

# Editing
win.title_input.setText("Updated Idea Title")
win.text_editor.setPlainText("Updated note content with extra details")
win._explicit_save()

reloaded = load()
found = next((n for n in reloaded if n["id"] == added["id"]), None)
check("explicit save persisted title", found is not None and found["title"] == "Updated Idea Title")
check("explicit save persisted content", found is not None and "extra details" in found["text"])

# Target button text
win._update_target_label()
check("paste button displays target", "Test Editor Window" in win.paste_btn.text())

# Paste action
win._paste_active()
check("paste action invoked callback", len(pasted_result) == 1 and "extra details" in pasted_result[0])

# Move action
win2 = NotesWindow(on_paste=fake_paste)
count_before = len(win2.notes)
if count_before >= 2:
    id0 = win2.notes[0]["id"]
    id1 = win2.notes[1]["id"]
    win2._select_by_id(id0)
    win2._move(1)
    check("move down swaps items", win2.notes[0]["id"] == id1 and win2.notes[1]["id"] == id0)

# Add new from UI
win2._add_new()
check("add_new increments count", len(win2.notes) == count_before + 1)
check("newly added note is selected", win2.active_id == win2.notes[0]["id"])

# Delete action
deleted_id = win2.active_id
win2._delete_active()
check("delete removes active note", not any(n["id"] == deleted_id for n in win2.notes))

# Window singleton & opener
w_opened = open_notes(on_paste=fake_paste)
check("open_notes returns NotesWindow instance", isinstance(w_opened, NotesWindow))
w_opened.close()

sys.exit(report.finish())
