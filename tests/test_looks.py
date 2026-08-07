"""Nine looks at any size, the two dials, and the wiring that picks them.

Replaces test_orb.py, which tested one look through a module that no longer
exists.
"""
import json
import math
import sys
import tempfile
import time
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from relay import config as config_mod  # noqa: E402
from relay import overlay as overlay_mod  # noqa: E402
from relay import orbs  # noqa: E402
from relay.orbs.core import js_round  # noqa: E402
from relay.overlay import (CHANGE_FRAMES, FRAME_MS, ICON_SIZES,  # noqa: E402
                           EDGE_PAD, VOICE_URGENCY, Orb, build_icon)

fails = []


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not condition:
        fails.append(name)


print("\n--- all nine are there ---")
check("nine looks", len(orbs.LOOKS) == 9, str(len(orbs.LOOKS)))
check("each has a line describing it",
      all(orbs.LOOK_BLURBS.get(look) for look in orbs.LOOKS))
check("the size runs continuously, not in steps",
      (orbs.MIN_SIZE, orbs.MAX_SIZE) == (16, 120)
      and orbs.TUNED_SIZES == (20, 64),
      f"{orbs.MIN_SIZE}..{orbs.MAX_SIZE}, tuned at {orbs.TUNED_SIZES}")

print("\n--- halves round the way the drawings expect ---")
check("js_round takes 2.5 up, where Python takes it down",
      js_round(2.5) == 3 and round(2.5) == 2)
dots, _ = orbs.frame("composing", 64, 0.0)
# 5 lanes x sqrt(0.25) = 2.5, which has to become 3 before the band
# multiplier of 3.9 turns it into twelve strands of forty-four.
check("so the sash gets its twelve strands, not eight",
      len(dots) == 12 * 44 + 38, f"{len(dots)} dots")

print("\n--- every look draws, at every size ---")
SAMPLE_SIZES = (16, 20, 27, 40, 53, 64, 88, 120)
for look in orbs.LOOKS:
    for size in SAMPLE_SIZES:
        dots, lines = orbs.frame(look, size, 1.7)
        _mode, _speed, opts = orbs.resolve(look, size)
        floor = opts.get("r_min", 0.3)
        reach = size * 0.75          # generous: the looks fill 0.76 to 0.874
        inside = all(abs(x - size / 2) <= reach and abs(y - size / 2) <= reach
                     for x, y, *_ in dots)
        # The floor, not the ink: a look is allowed to hand back ink outside
        # 0..1 - the wave pushes dots past its own radius, so their depth goes
        # over one and the ink goes under zero - and the painter clamps that.
        # A dot under the floor is a different thing: nothing downstream
        # rescues it, and it draws as a smear.
        big_enough = all(r >= floor for _x, _y, _z, r, _i, _a in dots)
        if not (dots and inside and big_enough):
            check(f"{look} at {size}px", False,
                  f"{len(dots)} dots, inside={inside}, floor={big_enough}")
check(f"all {len(orbs.LOOKS) * len(SAMPLE_SIZES)} draw dots inside the box, none under the floor",
      not [f for f in fails if " at " in f])

print("\n--- and every one of them moves ---")
for look in orbs.LOOKS:
    speed = orbs.speed_of(look, 64)
    a, _ = orbs.frame(look, 64, 0.0)
    b, _ = orbs.frame(look, 64, 1.0 * speed)
    moved = sum(1 for p, q in zip(a, b)
                if abs(p[0] - q[0]) + abs(p[1] - q[1]) > 0.3)
    if moved <= len(a) * 0.2:
        check(f"{look} moves", False, f"{moved} of {len(a)} dots")
check("a second of each look's own clock shifts most of it",
      not [f for f in fails if f.endswith(" moves")])

print("\n--- only connecting draws lines ---")
wired = {look for look in orbs.LOOKS if orbs.frame(look, 64, 1.0)[1]}
check("and it does", wired == {"connecting"}, str(sorted(wired)))

print("\n--- retuned across the span, magnified outside it ---")
for look in orbs.LOOKS:
    counts = [len(orbs.frame(look, s, 1.0)[0])
              for s in range(orbs.TUNED_MIN, orbs.TUNED_MAX + 1)]
    if counts[0] >= counts[-1]:
        check(f"{look} count", False, f"{counts[0]} to {counts[-1]}")
    # Not monotone, and it cannot be. Rings, lanes and segments are small
    # integers, and the multipliers that set them do not all move the same
    # way with size - the sash's band multiplier falls as its lane count
    # rises. What matters is that no single step of the slider lurches.
    steps = [b - a for a, b in zip(counts, counts[1:])]
    worst_drop = min(steps)
    if worst_drop < -0.06 * counts[-1]:
        check(f"{look} count", False, f"drops {worst_drop} in one pixel")
    # Past the ends there is nothing to interpolate towards, so the count
    # holds and the drawing is scaled instead. Carrying on would be worse
    # than useless: the multipliers grow faster than the size does, and at
    # 120px the sash would resolve to nine thousand dots.
    if len(orbs.frame(look, orbs.MAX_SIZE, 1.0)[0]) != counts[-1]:
        check(f"{look} count", False, "changed past the tuned maximum")
    if len(orbs.frame(look, orbs.MIN_SIZE, 1.0)[0]) != counts[0]:
        check(f"{look} count", False, "changed below the tuned minimum")
    speeds = [orbs.speed_of(look, s) for s in (20, 40, 64)]
    if not min(speeds[0], speeds[2]) <= speeds[1] <= max(speeds[0], speeds[2]):
        check(f"{look} speed", False, str(speeds))
    if orbs.speed_of(look, orbs.MAX_SIZE) != orbs.speed_of(look, 64):
        check(f"{look} speed", False, "magnifying changed the clock")
check("counts climb with size, and no step of the slider lurches",
      not [f for f in fails if f.endswith(" count")])
check("and the clock never changes just because the mark was magnified",
      not [f for f in fails if f.endswith(" speed")])

base, _ = orbs.frame("composing", 64, 2.0)
big, _ = orbs.frame("composing", orbs.MAX_SIZE, 2.0)
k = orbs.MAX_SIZE / 64
floor = orbs.resolve("composing", 64)[2]["r_min"]
places = max(abs(x * k - bx) + abs(y * k - by)
             for (x, y, *_), (bx, by, *_) in zip(base, big))
check("every dot lands exactly where scaling would put it", places < 1e-9,
      f"{places:.1e} px off")
# Radii are proportional too, except for the dots the floor had already
# lifted at the smaller size: those were drawn larger than the drawing
# asked for, and scaling that up would carry the correction with it.
sizes_off = [(r, br) for (*_, r, _i, _a), (*_, br, _bi, _ba) in zip(base, big)
             if abs(r * k - br) > 1e-9]
check("and its radius with it, bar the ones the floor had lifted",
      all(r == floor and br < floor * k for r, br in sizes_off),
      f"{len(sizes_off)} floored dots of {len(base)}")

print("\n--- one look turns into another by moving ---")
# The nine drawings share no geometry, so this used to be two pictures laid
# over each other at opposite opacities. Every dot of the old mark is now
# paired with a place in the new one and travels to it.
WAS, NOW = "breathing", "listening"
was = orbs.frame(WAS, 64, 1.4)
now = orbs.frame(NOW, 64, 0.0)


def positions(frame):
    return sorted((round(d[0], 4), round(d[1], 4)) for d in frame[0])


start = orbs.blend(64, was, now, 0.0)
end = orbs.blend(64, was, now, 1.0)
check("at nought it is the look it is leaving",
      positions(start) == positions(was))
check("at one it is the look it is arriving at",
      positions(end) == positions(now))

middle = orbs.blend(64, was, now, 0.5)
check("and halfway it is neither",
      positions(middle) != positions(was)
      and positions(middle) != positions(now))
check("never drawing more dots than the busier of the two",
      len(middle[0]) <= max(len(was[0]), len(now[0])),
      f"{len(middle[0])} against {len(was[0])} and {len(now[0])}")

# Dots have to actually go somewhere. Pairing by angle rather than by the
# order the drawings emit them keeps that from being a random scatter.
travel = 0.0
for a, b in zip(positions(start), positions(end)):
    travel = max(travel, abs(a[0] - b[0]) + abs(a[1] - b[1]))
check("and they cover real ground getting there", travel > 5,
      f"{travel:.0f}px at the furthest")

# The gather: pulled in at the midpoint, so the mark closes and opens again
# instead of sliding along straight lines.
def reach(frame):
    return max(max(abs(d[0] - 32), abs(d[1] - 32)) for d in frame[0])


check("it gathers inward on the way", reach(middle) < max(reach(was), reach(now)),
      f"{reach(middle):.1f}px against {max(reach(was), reach(now)):.1f}px")

for f in (0.0, 0.15, 0.35, 0.5, 0.65, 0.85, 1.0):
    frame = orbs.blend(64, was, now, f)
    inside = all(abs(d[0] - 32) <= 48 and abs(d[1] - 32) <= 48 for d in frame[0])
    alphas = all(0.0 <= d[5] <= 1.0 for d in frame[0])
    if not (inside and alphas):
        check(f"blend at {f} stays in bounds", False,
              f"inside={inside} alphas={alphas}")
check("nothing leaves the box or runs out of range at any point",
      not [f for f in fails if f.startswith("blend at")])

# Only one look draws lines, and lines between dots that are each on their way
# somewhere else would be a scribble - they fade with their own look instead.
wired = orbs.blend(64, orbs.frame("connecting", 64, 1.0), now, 0.5)
check("the constellation's wires fade rather than stretch",
      wired[1] and all(0.0 < w[5] < 1.0 for w in wired[1]),
      f"{len(wired[1])} lines")

# An empty look would be a division by nothing in the pairing.
check("a look with no dots does not break it",
      orbs.blend(64, ([], []), now, 0.5)[0] == now[0])

print("\n--- the size dial ---")
check("clamps below", orbs.clamp_size(2) == orbs.MIN_SIZE)
check("clamps above", orbs.clamp_size(999) == orbs.MAX_SIZE)
check("rounds to whole pixels", orbs.clamp_size(47.6) == 48)
check("and no two sizes in the span resolve the same way",
      len({len(orbs.frame("searching", s, 1.0)[0])
           for s in range(orbs.TUNED_MIN, orbs.TUNED_MAX + 1)}) > 3)

print("\n--- the speed dial ---")
check("clamps below", orbs.clamp_speed(0.001) == orbs.MIN_SPEED)
check("clamps above", orbs.clamp_speed(99) == orbs.MAX_SPEED)
check("the range asked for", (orbs.MIN_SPEED, orbs.MAX_SPEED) == (0.1, 3.0))
check("and leaves what is in range alone", orbs.clamp_speed(1.1) == 1.1)

print("\n--- the config section ---")
default = config_mod.normalise_orb(None)
check("a missing section still gives three slots",
      all(default[s]["look"] for s in config_mod.ORB_SLOTS))
half = config_mod.normalise_orb({"recording": {"look": "weaving"}})
check("naming one slot does not blank the other two",
      half["recording"]["look"] == "weaving"
      and half["idle"]["look"] == config_mod.DEFAULTS["orb"]["idle"]["look"])
check("a look that does not exist falls back",
      config_mod.normalise_orb({"idle": {"look": "sparkles"}})["idle"]["look"]
      == config_mod.DEFAULTS["orb"]["idle"]["look"])
check("an odd size is kept, not snapped to a step",
      config_mod.normalise_orb({"size": 33})["size"] == 33)
check("but one out of range is pulled back in",
      config_mod.normalise_orb({"size": 400})["size"] == orbs.MAX_SIZE)
check("and one that is not a number at all falls back",
      config_mod.normalise_orb({"size": "huge"})["size"]
      == config_mod.DEFAULTS["orb"]["size"])
check("and a wild speed is clamped",
      config_mod.normalise_orb({"idle": {"speed": 80}})["idle"]["speed"] == 3.0)

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "config.json"
    path.write_text(json.dumps({"hotkey": "f8", "beam_size": 5}), encoding="utf-8")
    config_mod.save_orb({"size": 20, "idle": {"look": "shaping", "speed": 0.4}},
                        path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    check("saving the orb leaves the rest of the file alone",
          raw["hotkey"] == "f8" and raw["beam_size"] == 5)
    check("and what comes back is what went in",
          raw["orb"]["size"] == 20
          and raw["orb"]["idle"] == {"look": "shaping", "speed": 0.4})
    back = config_mod.load_config(path)
    check("it survives a round trip through the loader",
          back["orb"]["idle"]["look"] == "shaping")

print("\n--- the widget wears them ---")
settings = {
    "size": 64,
    "idle": {"look": "breathing", "speed": 1.0},
    "recording": {"look": "listening", "speed": 2.0},
    "processing": {"look": "working", "speed": 0.5},
}
orb = Orb(on_toggle=lambda: None, on_quit=lambda: None, tooltip="F9",
          prompts_getter=lambda: [], on_pick_look=lambda: None,
          orb_settings=settings)
orb.move(-3000, -3000)

check("it starts on the resting look", orb._look == "breathing")
check("the window is the mark plus a hair of margin",
      orb.width() == orb.height() == 64 + 2 * EDGE_PAD, f"{orb.width()}px")

orb.set_state("recording")
check("a state change switches the look", orb._look == "listening")
check("and the old one is still on its way out",
      orb._leaving is not None and orb._leaving[0] == "breathing")
orb.grab()
check("the change draws without complaint", True)
for _ in range(CHANGE_FRAMES + 1):
    orb._tick()
check("and it finishes", orb._leaving is None)

print("\n--- the clock obeys the dial and the voice ---")
orb.state = "recording"
orb.level_getter = lambda: 0.0
orb._level = 0.0
orb._clock = 0.0
orb._tick()
quiet = orb._clock
expected = orbs.speed_of("listening", 64) * 2.0 * FRAME_MS / 1000.0
check("its own tuning times the slot's speed", abs(quiet - expected) < 1e-9,
      f"{quiet:.4f} vs {expected:.4f}")

orb._level = 1.0
orb._clock = 0.0
orb.level_getter = lambda: 1.0
orb._tick()
loud = orb._clock
check("and speaking hurries it along",
      abs(loud / quiet - (1 + VOICE_URGENCY)) < 0.02,
      f"{loud / quiet:.2f}x")

print("\n--- the picker changes it live ---")
orb.apply_settings({"size": 37, "idle": {"look": "shaping", "speed": 0.3},
                    "recording": {"look": "connecting", "speed": 1.0},
                    "processing": {"look": "solving", "speed": 1.0}})
check("an in-between size takes effect",
      orb.orb_size == 37 and orb.width() == 37 + 2 * EDGE_PAD)
widths = []
for size in (orbs.MIN_SIZE, 23, 64, 91, orbs.MAX_SIZE):
    orb.apply_settings(dict(settings, size=size))
    widths.append((size, orb.width()))
    orb.grab()
check("every size from end to end draws and sizes its window",
      all(w == s + 2 * EDGE_PAD for s, w in widths), str(widths))
orb.apply_settings({"size": 37, "idle": {"look": "shaping", "speed": 0.3},
                    "recording": {"look": "connecting", "speed": 1.0},
                    "processing": {"look": "solving", "speed": 1.0}})
check("so does the look for the state it is in", orb._look == "connecting")
orb.set_state("idle")
check("and for the next one", orb._look == "shaping")
orb.grab()
check("it still draws afterwards", True)

print("\n--- the icon is untouched ---")
icon = build_icon()
check("every size is still present",
      sorted(s.width() for s in icon.availableSizes()) == sorted(ICON_SIZES))
tiny = icon.pixmap(16, 16).toImage()
lit = sum(1 for y in range(16) for x in range(16)
          if tiny.pixelColor(x, y).red() > 110)
check("and the 16px one still has dots", lit >= 8, f"{lit} lit pixels")

print("\n--- and all of it can be clicked ---")
# Windows hit-tests a layered window against its per-pixel alpha, so a click
# on a fully transparent pixel goes through to whatever is behind. Once the
# disc under the mark was removed, only the dots themselves could be clicked -
# measured at 18% of the running orb, and 5% for an outline like shaping. What
# that felt like was a dot that ignored two clicks out of three.


def rendered(orb, background=None):
    image = QImage(orb.width(), orb.height(), QImage.Format_ARGB32)
    image.fill(Qt.transparent if background is None else QColor(background))
    painter = QPainter(image)
    orb.render(painter, QPoint(), renderFlags=Orb.RenderFlag.DrawChildren)
    painter.end()
    return image


def inside_the_mark(orb):
    centre = orb.width() / 2
    reach = orb.orb_size / 2 + EDGE_PAD - 1.5
    return [(x, y)
            for y in range(orb.height()) for x in range(orb.width())
            if (x + 0.5 - centre) ** 2 + (y + 0.5 - centre) ** 2 <= reach ** 2]


solid = True
for look in orbs.LOOKS:
    orb.apply_settings(dict(settings, idle={"look": look, "speed": 1.0}))
    orb.set_state("idle")
    orb._leaving = None
    image = rendered(orb)
    holes = [1 for x, y in inside_the_mark(orb)
             if image.pixelColor(x, y).alpha() == 0]
    if holes:
        solid = False
        check(f"{look} has no holes", False, f"{len(holes)} transparent pixels")
check("every look is opaque enough to hit, right across the mark", solid)

areas = []
for size in (orbs.MIN_SIZE, 32, 64, orbs.MAX_SIZE):
    orb.apply_settings(dict(settings, size=size))
    image = rendered(orb)
    hit = sum(1 for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x, y).alpha() > 0)
    # The disc is the mark plus a fixed margin, so its area is that circle's.
    want = math.pi * (size / 2 + EDGE_PAD) ** 2
    areas.append((size, hit, want))
check("the target grows with the size slider rather than staying put",
      all(abs(hit - want) < 0.03 * want for _s, hit, want in areas),
      ", ".join(f"{s}px->{hit}" for s, hit, _w in areas))
check("and a small orb gets proportionally the most help",
      areas[0][1] / (orbs.MIN_SIZE ** 2) > areas[-1][1] / (orbs.MAX_SIZE ** 2))
orb.apply_settings(settings)

overlay_mod.HIT_ALPHA = 0
bare = rendered(orb, "#ffffff")
overlay_mod.HIT_ALPHA = 1
backed = rendered(orb, "#ffffff")
shift = max(
    max(abs(a.red() - b.red()), abs(a.green() - b.green()),
        abs(a.blue() - b.blue()))
    for a, b in ((backed.pixelColor(x, y), bare.pixelColor(x, y))
                 for x, y in inside_the_mark(orb))
)
check("and the backing that does it is invisible", shift <= 1,
      f"{shift}/255 at worst, over white")

print("\n--- cheap enough to run all day ---")
orb.apply_settings(settings)
worst = 0.0
for look in orbs.LOOKS:
    orb._look = look
    orb._clock = 1.0
    for _ in range(3):
        orb.grab()
    N = 20
    t0 = time.perf_counter()
    for _ in range(N):
        orb._clock += 0.05
        orb.grab()
    ms = (time.perf_counter() - t0) / N * 1000
    worst = max(worst, ms)
    print(f"    {look:<12} {ms:>5.2f} ms")
check("the dearest look still fits the frame", worst < FRAME_MS * 0.5,
      f"{worst:.2f} ms of {FRAME_MS} ms")

print("\n--- and the picker itself ---")
from relay.look_picker import LookPicker  # noqa: E402

picker = LookPicker(settings, lambda s: None)
picker.move(-3000, -3000)
check("one tile per look", len(picker.tiles) == 9)
picker._choose_slot("processing")
check("selecting a slot checks its tab",
      picker.slot_tabs["processing"].isChecked())
check("and shows that slot's speed",
      abs(picker.speed.value() / 100 - 0.5) < 1e-6, str(picker.speed.value()))
picker._choose_look("weaving")
check("clicking a look assigns it to that slot",
      picker.settings["processing"]["look"] == "weaving")
check("and only that slot",
      picker.settings["idle"]["look"] == "breathing")
picker.size.setValue(23)
check("the size slider sets the size", picker.settings["size"] == 23)
check("the tiles follow it", all(t.size_px == 23 for t in picker.tiles.values()))
picker.size.setValue(orbs.TUNED_MIN - 2)
check("below the tuned minimum it says the drawing is shrunk",
      "shrunk" in picker.hint.text(), picker.hint.text())
picker.size.setValue(100)
check("past the tuned maximum the tiles stop growing",
      all(t.size_px == orbs.TUNED_MAX for t in picker.tiles.values()))
check("and it says so", "magnified" in picker.hint.text(), picker.hint.text())
picker.size.setValue(50)
check("in between it says neither",
      "magnified" not in picker.hint.text()
      and "shrunk" not in picker.hint.text(), picker.hint.text())

reverted = []
picker.on_change = reverted.append
picker.close()
check("closing without saving puts back what was there",
      reverted and reverted[-1]["processing"]["look"] == "working",
      reverted[-1]["processing"]["look"] if reverted else "nothing applied")

print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
QTimer.singleShot(0, app.quit)
sys.exit(1 if fails else 0)
