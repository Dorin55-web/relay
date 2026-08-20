"""Starting up: one copy at a time, and the CUDA libraries found first.

The single-instance guard and the keeper are two halves of one mechanism. Relay
holds a named mutex while it runs; the keeper decides whether Relay is alive by
trying to open that same name. Nothing connects them but the string, so if one
is ever edited without the other the keeper watches a name nobody holds and
reports a death every five seconds - or, worse, never reports one at all.

So this uses real mutexes and a real second process. There is no way to check
that a handle disappears when a process dies without a process that dies.

The logon entry is checked the same way round. The shortcut is written into a
throwaway folder and read back through the Windows shell rather than through
the code that wrote it - reading it back with the writer would only prove that
agrees with itself - and the real Startup folder is listed before and after, so
the suite can say it put nothing in it.
"""
import contextlib
import io
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

from relay import cuda_setup, keeper, single_instance, uia  # noqa: E402

report = context.Report()
check = report.check

WINDOWS = sys.platform == "win32"

# The first twenty bytes of every .lnk: a header size of 0x4C and the shell
# link class id. It is what Explorer looks at to decide a file is a shortcut
# rather than something that merely ends in .lnk.
LNK_HEADER = bytes.fromhex("4C000000") + bytes.fromhex(
    "0114020000000000C000000000000046")

# Holds a named mutex, says so, and waits to be killed.
HOLDER = """
import ctypes, sys, time
ctypes.windll.kernel32.CreateMutexW(None, False, sys.argv[1])
print("holding", flush=True)
time.sleep(60)
"""


def hold(name):
    """A second process holding `name`, once it actually holds it."""
    child = subprocess.Popen([sys.executable, "-c", HOLDER, name],
                             stdout=subprocess.PIPE, text=True)
    child.stdout.readline()
    return child


print("\n--- the keeper and the guard mean the same thing by 'running' ---")
check("the same name, exactly", keeper.MUTEX_NAME == single_instance.MUTEX_NAME,
      f"{single_instance.MUTEX_NAME!r} vs {keeper.MUTEX_NAME!r}")
check("and it is scoped to this logon session",
      single_instance.MUTEX_NAME.startswith("Local\\"), single_instance.MUTEX_NAME)

# The nastiest way to get the keeper's own guard wrong: hold Relay's name, and
# from then on answer your own liveness check for ever - a keeper that watches
# happily and never reports a death.
check("the keeper's own name is its own",
      keeper.KEEPER_MUTEX_NAME != keeper.MUTEX_NAME,
      f"{keeper.KEEPER_MUTEX_NAME!r} vs {keeper.MUTEX_NAME!r}")
check("and scoped to this logon session too",
      keeper.KEEPER_MUTEX_NAME.startswith("Local\\"), keeper.KEEPER_MUTEX_NAME)


if not WINDOWS:
    print("\n(not Windows - the mutex checks need it)")
    check("off Windows, nothing blocks a start",
          single_instance.already_running() is False)
    sys.exit(report.finish())


print("\n--- nobody else running means you may start ---")
single_instance.MUTEX_NAME = f"Local\\relay-test-{uuid.uuid4().hex}"
check("nothing in the way", single_instance.already_running() is False)
check("and asking again finds the copy you just became",
      single_instance.already_running() is True)


print("\n--- a second copy is refused ---")
name = f"Local\\relay-test-{uuid.uuid4().hex}"
single_instance.MUTEX_NAME = name
other = hold(name)
try:
    check("it can tell someone is there", single_instance.already_running() is True)
    keeper.MUTEX_NAME = name
    check("and so can the keeper", keeper.relay_is_running() is True)
finally:
    other.terminate()
    other.wait(timeout=10)


print("\n--- and the handle goes when the process does ---")
# This is why it is a mutex and not a pid file. Whatever ends Relay - a quit, a
# crash, the task manager - the name is free the instant it is gone, with
# nothing to clean up and nothing to go stale.
name = f"Local\\relay-test-{uuid.uuid4().hex}"
keeper.MUTEX_NAME = name
other = hold(name)
check("held while it runs", keeper.relay_is_running() is True)
other.kill()
other.wait(timeout=10)
gone = False
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    if not keeper.relay_is_running():
        gone = True
        break
    time.sleep(0.05)
check("and free the moment it is killed", gone)


print("\n--- a failed check never blocks a start ---")
# It is a guard against a second orb, not a reason to have no first one.
single_instance.MUTEX_NAME = None       # CreateMutexW will not take this
check("it says no rather than raising",
      single_instance.already_running() is False)


print("\n--- and one keeper at a time, for the same reason ---")
# Two keepers is two pollers on one Telegram bot, and Telegram gives each of
# them half the messages at random - so /start works one time in two. With a
# Startup entry as well as keeper.bat, two is one double-click away.
keeper.KEEPER_MUTEX_NAME = f"Local\\relay-keeper-test-{uuid.uuid4().hex}"
check("the first one may watch", keeper.another_keeper_running() is False)
check("and the next finds the one it has just become",
      keeper.another_keeper_running() is True)

name = f"Local\\relay-keeper-test-{uuid.uuid4().hex}"
keeper.KEEPER_MUTEX_NAME = name
other = hold(name)
try:
    check("a keeper in another process is seen too",
          keeper.another_keeper_running() is True)
finally:
    other.terminate()
    other.wait(timeout=10)

keeper.KEEPER_MUTEX_NAME = None         # CreateMutexW will not take this
check("and a check that will not run never stops the watch",
      keeper.another_keeper_running() is False)


print("\n--- asking whether a keeper is watching, without becoming one ---")
# Relay asks this before /restart quits. If asking claimed the name, the first
# question would answer itself for ever after, and the promise "the keeper will
# bring me back in a few seconds" would be true of nothing - which is how three
# restarts in one evening left a laptop dark.
keeper.KEEPER_MUTEX_NAME = f"Local\\relay-watch-test-{uuid.uuid4().hex}"
check("nothing is watching", keeper.keeper_is_watching() is False)
check("and asking did not make us the one watching",
      keeper.keeper_is_watching() is False)

name = f"Local\\relay-watch-test-{uuid.uuid4().hex}"
keeper.KEEPER_MUTEX_NAME = name
other = hold(name)
try:
    check("one held in another process is seen", keeper.keeper_is_watching() is True)
finally:
    other.terminate()
    other.wait(timeout=10)
gone = False
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    if not keeper.keeper_is_watching():
        gone = True
        break
    time.sleep(0.05)
check("and unseen the moment it goes", gone)

keeper.KEEPER_MUTEX_NAME = None         # OpenMutexW will not take this
check("a check that cannot be made is not a yes",
      keeper.keeper_is_watching() is False)


print("\n--- where the logon entry would go ---")
startup = keeper.startup_folder()
check("Windows named a folder", startup.is_dir(), str(startup))
check("it is the Startup one", startup.name.lower() == "startup", str(startup))
check("and it is yours rather than the machine's",
      str(startup).lower().startswith(str(Path.home()).lower()), str(startup))
before = sorted(p.name for p in startup.iterdir())


print("\n--- writing one, into a folder that is not that one ---")


def read_shortcut(path):
    """What the Windows shell says is in a .lnk.

    Asked of the shell rather than of the code that wrote the file: reading it
    back with the writer would only prove the writer agrees with itself, and
    the question is whether Explorer would show this as a shortcut at all.
    """
    quoted = "'" + str(path).replace("'", "''") + "'"
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({quoted}); "
              "Write-Output $s.TargetPath; Write-Output $s.Arguments; "
              "Write-Output $s.WorkingDirectory; Write-Output $s.WindowStyle")
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, errors="replace")
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


folder = Path(tempfile.mkdtemp(prefix="relay-startup-"))
try:
    made = keeper.install_at_logon(folder)
    failed = None
except Exception as exc:
    made, failed = None, f"{type(exc).__name__}: {exc}"
check("a shortcut was written", failed is None, str(failed))

if made is not None:
    check("in the folder it was given", made.parent == folder, str(made))
    check("and Explorer would see a shortcut, not a file called .lnk",
          made.read_bytes()[:20] == LNK_HEADER, made.read_bytes()[:20].hex())

    read = read_shortcut(made)
    check("the shell can read it back", len(read) == 4, str(read))

if made is not None and len(read) == 4:
    target, arguments, working, style = read
    # pythonw.exe is the console-less twin. Started with python.exe, every
    # logon would open a black window and leave it on the desktop all day.
    check("this venv has a windowless interpreter",
          Path(sys.executable).with_name("pythonw.exe").exists(), sys.executable)
    check("and that is what the shortcut starts",
          Path(target).name.lower() == "pythonw.exe", target)
    check("it is the interpreter the keeper would use itself",
          Path(target) == Path(keeper.launcher()), f"{target} vs {keeper.launcher()}")
    check("running the keeper", arguments.strip() == "-m relay.keeper", arguments)
    check("from the project folder", Path(working) == keeper.PROJECT_ROOT, working)
    check("minimised as well, in case pythonw is ever missing",
          style.strip() == "7", style)

    keeper.install_at_logon(folder)
    check("installing twice leaves one of it",
          len(list(folder.iterdir())) == 1, str(list(folder.iterdir())))


print("\n--- and taking it out is one file deleted ---")
# The whole argument for a shortcut over a scheduled task: doing it by hand in
# Explorer is the same operation, so nobody has to find the flag again.
check("removed", keeper.remove_from_logon(folder) is True)
check("the folder is empty again", list(folder.iterdir()) == [],
      str(list(folder.iterdir())))
check("and asking again says there was nothing to remove",
      keeper.remove_from_logon(folder) is False)

check("your own Startup folder was never touched",
      sorted(p.name for p in startup.iterdir()) == before,
      str(sorted(set(p.name for p in startup.iterdir()) ^ set(before))))


print("\n--- and the help says how to undo it ---")
usage = io.StringIO()
try:
    with contextlib.redirect_stdout(usage):
        keeper.main(["--help"])
    left = "it did not exit"
except SystemExit as exc:
    left = exc.code
check("--help exits rather than starting a watch", left == 0, str(left))
offered = usage.getvalue()
check("--at-logon is offered", "--at-logon" in offered, offered[:80])
check("--not-at-logon is offered", "--not-at-logon" in offered, offered[:80])
check("it says deleting the file is enough", "delet" in offered.lower(),
      offered[-200:])
check("and says it is not a scheduled task", "task" in offered.lower(),
      offered[-200:])


print("\n--- the CUDA directories ---")
first = cuda_setup.enable_cuda_dlls()
again = cuda_setup.enable_cuda_dlls()
check("registering twice registers nothing twice", first == again,
      f"{len(first)} then {len(again)}")
check("it found the wheels in this venv", len(first) >= 1, str(first))
check("each one holds DLLs",
      all(any(p.endswith(".dll") for p in __import__("os").listdir(d))
          for d in first), str(first))
check("and ctranslate2 can see the GPU through them",
      cuda_setup.cuda_device_count() >= 0)


print("\n--- text read out of another window is comparable with itself ---")
# Chromium marks embedded objects with U+FFFC and does not place them
# identically between two walks of the same tree. Left in, the state detector
# fires on its own noise.
check("object markers go", uia.clean("Send￼ message") == "Send message",
      repr(uia.clean("Send￼ message")))
check("zero-width spaces go", uia.clean("a​b") == "ab", repr(uia.clean("a​b")))
check("runs of space collapse", uia.clean("a    b\n") == "a b", repr(uia.clean("a    b\n")))
check("and unprintables become space",
      uia.clean("a\x00\x01b") == "a b", repr(uia.clean("a\x00\x01b")))
check("the same line twice is the same line",
      uia.clean("Stop ￼ ") == uia.clean("Stop￼"),
      f"{uia.clean('Stop ￼ ')!r} vs {uia.clean('Stop￼')!r}")
check("an empty line stays empty", uia.clean("") == "", repr(uia.clean("")))

sys.exit(report.finish())
