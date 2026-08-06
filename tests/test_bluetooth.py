"""Does Relay now leave the headset's stereo profile alone?

The point is not only that it picks another mic - it is that it still records
when the headset is the only microphone there is. A fix that goes quiet on a
machine with no built-in array would be worse than the bug.
"""
import sys

import context  # noqa: E402,F401
context.isolate_state()

import sounddevice as sd

from relay import audio as audio_mod
from relay.audio import AudioRecorder, is_handsfree
from relay.config import load_config

fails = []


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not condition:
        fails.append(name)


print("\n--- telling a headset mic from a real one ---")
cases = [
    ("Headset (2- Nothing Ear)", True),
    ("Headset (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0", True),
    ("Headset Microphone (JBL TUNE660NC)", True),
    ("Microphone Array (Intel Smart Sound)", False),
    ("Microphone (USB Audio Device)", False),
    ("Nothing ear white (2- Nothing Ear)", False),
    ("Line In (Realtek)", False),
    ("", False),
]
for name, expected in cases:
    got = is_handsfree(name)
    check(f"{name[:44]!r:<48} -> {'headset' if expected else 'real mic'}",
          got == expected)

config = load_config()
default_index = sd.default.device[0]
default_name = sd.query_devices(default_index, "input")["name"]
print(f"\n  Windows default input: {default_name!r}")
print(f"  is that a headset mic: {is_handsfree(default_name)}")

print("\n--- the candidate order ---")
rec = AudioRecorder(config)
info = sd.query_devices(default_index, "input")
pairs = rec._open_candidates(default_index, info)
named = []
for device, rate in pairs[:6]:
    label = "system mapper" if device is None else sd.query_devices(device)["name"][:38]
    named.append(f"    [{device}] {label} @ {rate}")
print("  first six tried, in order:")
print("\n".join(named))

if is_handsfree(default_name):
    first_device = pairs[0][0]
    first_name = sd.query_devices(first_device)["name"] if first_device is not None else ""
    check("a real mic is tried before the headset", not is_handsfree(first_name),
          repr(first_name[:40]))
    # The trap: a mapper passes the name check but routes to the default,
    # which is the very headset being stepped around.
    from relay.audio import is_mapper
    check("and it is real hardware, not a routing entry",
          not is_mapper(first_name), repr(first_name[:40]))
    mapper_first = [
        sd.query_devices(d)["name"] for d, _ in pairs[:4]
        if d is not None and is_mapper(sd.query_devices(d)["name"])
    ]
    check("no mapper anywhere near the front", not mapper_first, str(mapper_first))
    check("the headset is still in the list, not banned",
          any(d is not None and is_handsfree(sd.query_devices(d)["name"])
              for d, _ in pairs),
          "it must still work when it is the only mic")
else:
    check("default is not a headset, so order is unchanged",
          pairs[0][0] == default_index)

print("\n--- with the option off, nothing changes ---")
config_off = load_config()
config_off["avoid_bluetooth_mic"] = False
rec_off = AudioRecorder(config_off)
pairs_off = rec_off._open_candidates(default_index, info)
check("the Windows default is tried first", pairs_off[0][0] == default_index,
      f"got {pairs_off[0][0]}, default is {default_index}")

print("\n--- it still actually records ---")
import time

rec2 = AudioRecorder(config)
try:
    rec2.start()
    print(f"    recording from: {rec2.device_name}")
    time.sleep(0.8)
    rec2.stop()
    check("a recording opened and closed cleanly", True)
    check("and not through the headset", not is_handsfree(rec2.device_name),
          repr(rec2.device_name))
except Exception as exc:
    check("a recording opened and closed cleanly", False, str(exc)[:70])

print("\n--- headset-only machine: does it still work? ---")
# Pretend every other input vanished, which is what a desktop with no built-in
# mic looks like. The headset must still be chosen.
real_query = sd.query_devices


def only_headset(*args, **kwargs):
    result = real_query(*args, **kwargs)
    if args or kwargs:
        return result
    return [d for d in result
            if d["max_input_channels"] < 1 or is_handsfree(d["name"])]


audio_mod.sd.query_devices = only_headset
try:
    rec3 = AudioRecorder(config)
    pairs3 = rec3._open_candidates(default_index, info)
    usable = [d for d, _ in pairs3 if d is not None or True]
    check("candidates are not empty", len(pairs3) > 0, f"{len(pairs3)} pairs")
    check("the headset is still offered",
          any(d == default_index for d, _ in pairs3))
finally:
    audio_mod.sd.query_devices = real_query

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
sys.exit(1 if fails else 0)
