"""The typed-text path: the translator, and the window around it."""
import sys
import time

import context  # noqa: E402,F401
context.isolate_state()

from relay.cuda_setup import enable_cuda_dlls

enable_cuda_dlls()

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from relay.translator import TextTranslator, split_lines

fails = []


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not condition:
        fails.append(name)


print("\n--- splitting, before any model is involved ---")
cases = [
    ("O propozitie.", [["O propozitie."]]),
    ("Una. Doua.", [["Una.", "Doua."]]),
    ("Prima linie\nA doua linie", [["Prima linie"], ["A doua linie"]]),
    ("Sus\n\nJos", [["Sus"], [], ["Jos"]]),
    ("", [[]]),
    ("   ", [[]]),
]
for text, expected in cases:
    got = split_lines(text)
    check(f"{text!r:<34} -> {expected}", got == expected, f"got {got}")

print("\n--- the translator ---")
tr = TextTranslator()
check("not loaded until asked", not tr.ready)

t0 = time.time()
english = tr.translate("Verifica fiecare fisier si spune-mi ce ai gasit.")
print(f"  first call took {time.time() - t0:.1f}s (includes loading)")
check("loaded on demand", tr.ready, f"device {tr.device}")
check("produced English", "check" in english.lower(), repr(english))
check("no repetition loop", len(english) < 200, f"{len(english)} chars")

print("\n--- structure survives ---")
multi = "Prima fraza. A doua fraza.\n\nAlt paragraf aici."
out = tr.translate(multi)
print(f"  in : {multi!r}")
print(f"  out: {out!r}")
check("same number of lines", len(out.split("\n")) == len(multi.split("\n")),
      f"{len(multi.split(chr(10)))} in, {len(out.split(chr(10)))} out")
check("the blank line is still blank", out.split("\n")[1].strip() == "")
check("two sentences stayed on one line", out.split("\n")[0].count(".") >= 2,
      repr(out.split("\n")[0]))

print("\n--- empty input is not an error ---")
check("empty string", tr.translate("") == "")
check("only whitespace", tr.translate("   \n  ") == "")

print("\n--- the window ---")
app = QApplication.instance() or QApplication(sys.argv)

from relay.compose import DEBOUNCE_MS, Compose  # noqa: E402

sent = []
target = ["Claude"]
win = Compose(tr, on_paste=sent.append, target_getter=lambda: target[0])
win.show()

check("buttons start disabled", not win.copy_btn.isEnabled()
      and not win.paste_btn.isEnabled())
check("the target is named on the button", "Claude" in win.paste_btn.text(),
      win.paste_btn.text())

win.source.setPlainText("Nu scrie cod inca, doar fa-mi un plan structurat.")
check("typing starts the debounce", win._debounce.isActive())
check("and says so", win.status.text() == "typing...", win.status.text())

# Let the debounce fire and the worker finish.
deadline = time.time() + 20
while time.time() < deadline and not win.output.toPlainText().strip():
    app.processEvents()
    time.sleep(0.05)

result = win.output.toPlainText()
print(f"  translated to: {result!r}")
check("the English arrived", bool(result.strip()))
check("buttons became usable", win.copy_btn.isEnabled() and win.paste_btn.isEnabled())
check("status names the device", "translated on" in win.status.text(),
      win.status.text())

print("\n--- clearing empties everything ---")
win.source.setPlainText("")
app.processEvents()
check("output cleared", win.output.toPlainText() == "")
check("buttons disabled again", not win.paste_btn.isEnabled())
check("debounce stopped", not win._debounce.isActive())

print("\n--- stale results are dropped ---")
win._pending = 99
before = win.output.toPlainText()
win._work(1, "Aplicatia se blocheaza.")     # an old request id
app.processEvents()
check("an out-of-date translation is ignored",
      win.output.toPlainText() == before)

print("\n--- sending ---")
win.output.setPlainText("Edited by hand before sending.")
win._set_ready(True)
win._send()
app.processEvents()
check("the window closed itself first", not win.isVisible())
check("and sent what was in the box, not the raw translation",
      sent == ["Edited by hand before sending."], str(sent))

print("\n--- the Send label follows the target ---")
from relay.compose import MAX_TARGET_CHARS, short_target  # noqa: E402

# The real thing that broke: a browser window title is the whole page title,
# query string and all, and it stretched the button across the window.
OPERA = ('how to get help in windows 11 - Search?q=how+to+get+help+in+windows'
         '+11&filters=guid:"711b6492-0435-0038-8706-7c6b0feb200a_win11-en-dia"'
         ' - Opera')
check("a huge title is cut down", len(short_target(OPERA)) <= MAX_TARGET_CHARS,
      f"{len(short_target(OPERA))} chars: {short_target(OPERA)!r}")
check("short titles are left alone", short_target("Notepad") == "Notepad")
check("newlines in a title collapse", short_target("a\nb") == "a b")
check("no title gives no name", short_target("") == "" and short_target(None) == "")

win2 = Compose(tr, on_paste=lambda t: None, target_getter=lambda: OPERA)
win2.show()
app.processEvents()
label = win2.paste_btn.text()
check("the button stays a button", len(label) < 40, f"{len(label)} chars: {label!r}")
check("the full title is in the tooltip", OPERA in win2.paste_btn.toolTip())
hint = win2.paste_btn.sizeHint().width()
check("and cannot blow out the row", hint <= win2.paste_btn.maximumWidth(),
      f"{hint}px against a {win2.paste_btn.maximumWidth()}px cap")

moving = ["Notepad"]
win3 = Compose(tr, on_paste=lambda t: None, target_getter=lambda: moving[0])
win3.show()
app.processEvents()
check("names the target it starts on", "Notepad" in win3.paste_btn.text(),
      win3.paste_btn.text())
moving[0] = "Claude"
win3._refresh_target()
check("follows when the target moves", "Claude" in win3.paste_btn.text(),
      win3.paste_btn.text())
check("the poll timer is running", win3._target_timer.isActive())
win3.close()
app.processEvents()
check("and stops when the window closes", not win3._target_timer.isActive())
win2.close()
app.processEvents()

print("\n--- one window only ---")
from relay.compose import open_compose  # noqa: E402

a = open_compose(tr, sent.append, lambda: "Claude")
b = open_compose(tr, sent.append, lambda: "Claude")
check("same window returned", a is b)
a.close()
app.processEvents()

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
