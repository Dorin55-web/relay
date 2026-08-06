"""Does a start/stop cycle leave PortAudio streams open?

An open capture stream is what holds a Bluetooth headset in hands-free mode.
Counts every InputStream built and every one closed, so a leak is a number
rather than a theory. Uses the default input only - never opens the headset.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context  # noqa: E402,F401

import sounddevice as sd

from relay import audio as audio_mod
from relay.config import load_config

fails = []


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not condition:
        fails.append(name)


built = []
closed = []

RealStream = sd.InputStream


class TrackedStream(RealStream):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        built.append(self)

    def close(self, *args, **kwargs):
        closed.append(self)
        return super().close(*args, **kwargs)


sd.InputStream = TrackedStream
audio_mod.sd.InputStream = TrackedStream

config = load_config()
config["streaming"] = True

print("\n--- a normal start/stop ---")
rec = audio_mod.AudioRecorder(config)
try:
    rec.start()
    print(f"  recording from: {rec.device_name}")
    import time
    time.sleep(0.6)
    rec.stop()
except Exception as exc:
    print(f"  could not record ({exc}); the counts below still hold")

print(f"  streams built:  {len(built)}")
print(f"  streams closed: {len(closed)}")
check("every stream opened was closed", len(built) == len(closed),
      f"{len(built)} built, {len(closed)} closed")

leaked = [s for s in built if s not in closed]
still_active = [s for s in leaked if getattr(s, "active", False)]
check("nothing left running", not still_active, f"{len(still_active)} active")

print("\n--- a start where the first candidates fail ---")
# The real case on a Bluetooth headset: several device/rate pairs open and
# then refuse to start, and only a later one works.
built.clear()
closed.clear()

original_open = audio_mod.AudioRecorder._open_candidates


def failing_candidates(self, device_index, device_info):
    real = original_open(self, device_index, device_info)
    # Three impossible rates first, then whatever actually worked before.
    return [(device_index, 7), (device_index, 11), (None, 13)] + real


audio_mod.AudioRecorder._open_candidates = failing_candidates

rec2 = audio_mod.AudioRecorder(config)
try:
    rec2.start()
    import time
    time.sleep(0.3)
    rec2.stop()
except Exception as exc:
    print(f"  start failed entirely: {str(exc)[:80]}")

print(f"  streams built:  {len(built)}")
print(f"  streams closed: {len(closed)}")
check("no stream leaks when a candidate fails", len(built) == len(closed),
      f"{len(built)} built, {len(closed)} closed")

print("\n--- what the app leaves behind on quit ---")
check("portaudio still initialised after stop", sd._initialized > 0,
      f"_initialized={sd._initialized}")

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
