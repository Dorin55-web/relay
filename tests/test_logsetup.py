"""The log has to survive being read months later, and say when things happened.

This exists because of an evening the application vanished off the screen. The
log held plenty of detail about what it had been doing and not one clue as to
when it stopped, nor whether it had quit or been killed - and those were the
only two questions worth asking.
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

from relay import logsetup  # noqa: E402

report = context.Report()
check = report.check


class Console:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass


class File(Console):
    def text(self):
        return "".join(self.written)


print("\n--- every line in the file carries a time ---")
handle, console = File(), Console()
tee = logsetup._Tee(handle, console)
tee.write("first line\n")
tee.write("second line\n")
lines = handle.text().splitlines()
check("both lines are there", len(lines) == 2, str(lines))
stamped = [ln for ln in lines if len(ln) > 9 and ln[2] == ":" and ln[5] == ":"]
check("both are stamped", len(stamped) == 2, str(lines))
check("the text survives intact",
      all(ln.endswith(("first line", "second line")) for ln in lines), str(lines))

print("\n--- but the console is left alone ---")
# You are watching the console as it happens; the clock is on the wall.
check("no stamps there", console.written == ["first line\n", "second line\n"],
      str(console.written))

print("\n--- a line written in pieces is stamped once ---")
# print() arrives as the text and then the newline, so a writer that stamped
# every call would put a time in the middle of every line it wrote.
handle, tee = File(), None
tee = logsetup._Tee(handle, None)
tee.write("[target] now writing into: ")
tee.write("Some Window")
tee.write("\n")
written = handle.text()
check("one line out", written.count("\n") == 1, repr(written))
check("one stamp on it", written.count(":") >= 2 and "Some Window" in written,
      repr(written))
check("the stamp is at the front", written.strip().endswith("Some Window"),
      repr(written))

print("\n--- rotation keeps the old log instead of deleting it ---")
tmp = Path(tempfile.mkdtemp(prefix="relay-log-"))
logsetup.LOG_PATH = tmp / "relay.log"
logsetup.PREVIOUS_LOG_PATH = tmp / "relay.previous.log"

logsetup.LOG_PATH.write_text("small", encoding="utf-8")
logsetup._rotate()
check("a small log is left where it is", logsetup.LOG_PATH.read_text() == "small")
check("and nothing is rotated out", not logsetup.PREVIOUS_LOG_PATH.exists())

logsetup.LOG_PATH.write_text("x" * (logsetup.MAX_LOG_BYTES + 1), encoding="utf-8")
logsetup._rotate()
check("a full one moves aside", not logsetup.LOG_PATH.exists())
check("and is still readable", logsetup.PREVIOUS_LOG_PATH.exists()
      and len(logsetup.PREVIOUS_LOG_PATH.read_text()) > logsetup.MAX_LOG_BYTES)

print("\n--- quitting says so, being killed does not ---")
# The whole point. atexit runs for every ordinary end and for none of the
# sudden ones, so the presence of that line is what separates the two - and
# nothing in the log could tell them apart before.
work = Path(tempfile.mkdtemp(prefix="relay-exit-"))
project = str(Path(__file__).resolve().parent.parent)
script = work / "run.py"
script.write_text(
    "import sys, time\n"
    f"sys.path.insert(0, r'{project}')\n"
    "from relay import logsetup\n"
    f"logsetup.LOG_PATH = __import__('pathlib').Path(r'{work}') / 'relay.log'\n"
    f"logsetup.PREVIOUS_LOG_PATH = __import__('pathlib').Path(r'{work}') / 'prev.log'\n"
    "logsetup.setup_output()\n"
    "print('[test] up')\n"
    "if 'hang' in sys.argv:\n"
    "    time.sleep(60)\n",
    encoding="utf-8")

subprocess.run([sys.executable, str(script)], timeout=60,
               capture_output=True)
quit_log = (work / "relay.log").read_text(encoding="utf-8")
check("a normal exit is written down", "[exit]" in quit_log, repr(quit_log[-80:]))

(work / "relay.log").unlink()
killed = subprocess.Popen([sys.executable, str(script), "hang"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(100):
    time.sleep(0.05)
    if (work / "relay.log").exists() and "[test] up" in \
            (work / "relay.log").read_text(encoding="utf-8"):
        break
killed.kill()
killed.wait(timeout=20)
kill_log = (work / "relay.log").read_text(encoding="utf-8")
check("it had started", "[test] up" in kill_log, repr(kill_log))
check("a kill leaves no such line", "[exit]" not in kill_log, repr(kill_log))

sys.exit(report.finish())
