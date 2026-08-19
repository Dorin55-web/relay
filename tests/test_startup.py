"""Starting up: one copy at a time, and the CUDA libraries found first.

The single-instance guard and the keeper are two halves of one mechanism. Relay
holds a named mutex while it runs; the keeper decides whether Relay is alive by
trying to open that same name. Nothing connects them but the string, so if one
is ever edited without the other the keeper watches a name nobody holds and
reports a death every five seconds - or, worse, never reports one at all.

So this uses real mutexes and a real second process. There is no way to check
that a handle disappears when a process dies without a process that dies.
"""
import subprocess
import sys
import time
import uuid

import context  # noqa: E402,F401
context.isolate_state()

from relay import cuda_setup, keeper, single_instance, uia  # noqa: E402

report = context.Report()
check = report.check

WINDOWS = sys.platform == "win32"

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
