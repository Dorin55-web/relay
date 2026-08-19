"""The prompt library: the other file a person edits by hand.

The rule the whole module is built around is that the right-click menu must
come up. A file with one bad entry, an empty list, a stray comma or no file at
all all have to end with something in the menu, because the alternative is a
menu that opens empty and a user with no way to tell why.

The save path has the opposite rule: never turn a good file into a worse one.
"""
import json
import sys
import tempfile
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

from relay import prompts as prompts_mod  # noqa: E402

report = context.Report()
check = report.check

HERE = Path(tempfile.mkdtemp(prefix="relay-prompts-"))


def write(name, text):
    path = HERE / name
    path.write_text(text, encoding="utf-8")
    return path


def entries(n, text="something to say"):
    return [{"section": "S", "label": f"L{i}", "text": f"{text} {i}"} for i in range(n)]


print("\n--- with no file, the built-in prompts ---")
loaded = prompts_mod.load(HERE / "nothing.json")
check("there are prompts", len(loaded) == 10, str(len(loaded)))
check("each has text", all(e["text"].strip() for e in loaded))
check("each has a label", all(e["label"].strip() for e in loaded))


print("\n--- a file you wrote is used as written ---")
path = write("mine.json", json.dumps({"prompts": entries(3)}))
loaded = prompts_mod.load(path)
check("all three", len(loaded) == 3, str(len(loaded)))
check("in order", [e["label"] for e in loaded] == ["L0", "L1", "L2"],
      str([e["label"] for e in loaded]))


print("\n--- a bare list is accepted too ---")
path = write("bare.json", json.dumps(entries(2)))
check("read as prompts", len(prompts_mod.load(path)) == 2)


print("\n--- one bad entry costs you that entry, not the menu ---")
path = write("partly.json", json.dumps({"prompts": [
    {"label": "good", "text": "keep me"},
    "not an object",
    {"label": "no text at all", "text": "   "},
    {"text": "no label, so the text stands in for one"},
]}))
loaded = prompts_mod.load(path)
check("the good ones are there", len(loaded) == 2, str(len(loaded)))
check("and the one with no label shows its text",
      loaded[1]["label"].startswith("no label"), loaded[1]["label"])


print("\n--- and a file that is nonsense falls back ---")
for name, body in [("comma.json", '{"prompts": [{"text": "x"},]}'),
                   ("empty.json", '{"prompts": []}'),
                   ("wrong.json", '{"prompts": {"a": 1}}'),
                   ("junk.json", 'hello'),
                   ("null.json", 'null')]:
    loaded = prompts_mod.load(write(name, body))
    check(f"{name} still gives a menu", len(loaded) == 10, str(len(loaded)))


print("\n--- more than the menu holds are cut, and you are told ---")
path = write("many.json", json.dumps({"prompts": entries(14)}))
loaded = prompts_mod.load(path)
check("cut to ten", len(loaded) == prompts_mod.MAX_PROMPTS, str(len(loaded)))


print("\n--- saving ---")
path = HERE / "save.json"
check("an empty list is refused", prompts_mod.save([], path) is False)
check("and nothing was written", not path.exists())

check("a real list saves", prompts_mod.save(entries(4), path) is True)
check("and reads back the same", [e["label"] for e in prompts_mod.load(path)]
      == ["L0", "L1", "L2", "L3"], str(prompts_mod.load(path)))
check("with no temp file left behind",
      not (HERE / "save.json.tmp").exists() and
      not any(p.suffix == ".tmp" for p in HERE.iterdir()),
      str([p.name for p in HERE.iterdir() if p.suffix == ".tmp"]))


print("\n--- diacritics come back as diacritics ---")
# ensure_ascii=False, and the file read back as utf-8. A prompt written in
# Romanian that reloads as \u0103 escapes is unreadable in the editor.
romanian = [{"section": "Ș", "label": "Întrebare",
             "text": "Aș vrea să verifici această funcție și să îmi spui ce face."}]
prompts_mod.save(romanian, path)
back = prompts_mod.load(path)
check("the text is intact", back[0]["text"] == romanian[0]["text"], back[0]["text"])
check("and so is the section", back[0]["section"] == "Ș", back[0]["section"])
check("the file is not escaped", "ș" in path.read_text(encoding="utf-8"))


print("\n--- a save that cannot land leaves the old file alone ---")
path = HERE / "precious.json"
prompts_mod.save(entries(3, "original"), path)
before = path.read_text(encoding="utf-8")
missing = HERE / "no-such-dir" / "prompts.json"
check("saving into a missing directory fails cleanly",
      prompts_mod.save(entries(2), missing) is False)
check("and the good file is untouched", path.read_text(encoding="utf-8") == before)


print("\n--- ensure_file writes once and never again ---")
path = HERE / "once.json"
prompts_mod.ensure_file(path)
check("it appears", path.exists())
path.write_text(json.dumps({"prompts": entries(1, "mine")}), encoding="utf-8")
prompts_mod.ensure_file(path)
check("and a second call does not overwrite what you put in it",
      "mine" in path.read_text(encoding="utf-8"),
      path.read_text(encoding="utf-8")[:60])


print("\n--- the prompts.json this project ships loads clean ---")
real = prompts_mod.load(prompts_mod.PROJECT_ROOT / "prompts.json")
check("it has prompts", 1 <= len(real) <= prompts_mod.MAX_PROMPTS, str(len(real)))
check("every one is usable",
      all(e["text"].strip() and e["label"].strip() for e in real))

sys.exit(report.finish())
