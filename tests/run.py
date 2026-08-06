"""Run every suite in tests/ and say whether the lot passed.

    python tests/run.py                 all of them
    python tests/run.py looks tooltip   just those two

Each suite runs in its own process. They are not unit tests against mocks -
every one builds a real QApplication, and several build real windows - so
sharing an interpreter between them would mean sharing Qt's global state, and
a suite that quits the event loop would take the next one with it.

The probes in tests/probes/ are not run from here. They report numbers rather
than pass or fail, and several need a human to read the answer.
"""

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Generous on purpose. The whole run is about a minute and the slowest suite
# is the editor at half of that, but a suite that hangs should be reported as
# hung rather than sat on - and a first run of test_compose has a translation
# model to download before it can start.
TIMEOUT_SECONDS = 240


def suites(wanted):
    found = sorted(HERE.glob("test_*.py"))
    if not wanted:
        return found
    picked = []
    for name in wanted:
        matches = [f for f in found if name in f.stem]
        if not matches:
            print(f"no suite matching {name!r}")
            return []
        picked.extend(m for m in matches if m not in picked)
    return picked


def run(path):
    started = time.perf_counter()
    try:
        done = subprocess.run(
            [sys.executable, str(path)], capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS, cwd=str(HERE),
        )
        return done.returncode, done.stdout + done.stderr, time.perf_counter() - started
    except subprocess.TimeoutExpired as expired:
        out = (expired.stdout or b"")
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return None, out, time.perf_counter() - started


def main(argv):
    chosen = suites(argv)
    if not chosen:
        return 1

    failures = []
    for path in chosen:
        print(f"{path.stem:<24} ", end="", flush=True)
        code, output, seconds = run(path)
        if code == 0:
            print(f"PASS   {seconds:5.1f}s")
        elif code is None:
            print(f"TIMED OUT after {seconds:.0f}s")
            failures.append((path.stem, output))
        else:
            print(f"FAIL   {seconds:5.1f}s")
            failures.append((path.stem, output))

    print()
    if not failures:
        print(f"all {len(chosen)} suites passed")
        return 0

    for name, output in failures:
        print(f"--- {name} " + "-" * (60 - len(name)))
        # The lines that say what went wrong, then the tail for context.
        bad = [l for l in output.splitlines()
               if l.strip().startswith("FAIL") or "Error" in l or "Traceback" in l]
        for line in bad[:12]:
            print("   " + line.strip())
        if not bad:
            for line in output.splitlines()[-12:]:
                print("   " + line)
    print(f"\n{len(failures)} of {len(chosen)} suites failed")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
