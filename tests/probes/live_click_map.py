"""How much of the orb that is actually on screen can be clicked?

Asks the window manager, not the app: WindowFromPoint answers the same
question the mouse does, including the per-pixel alpha test Windows applies to
layered windows. The running orb is genuinely visible and on top, so nothing
here depends on a test window winning a z-order fight.
"""
import ctypes
import ctypes.wintypes as w

user32 = ctypes.windll.user32
user32.WindowFromPoint.restype = w.HWND
user32.WindowFromPoint.argtypes = [w.POINT]

CB = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)
found = []


def collect(hwnd, _):
    n = user32.GetWindowTextLengthW(hwnd)
    if n:
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value == "Relay" and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
    return True


user32.EnumWindows(CB(collect), 0)
if not found:
    raise SystemExit("no visible Relay orb - is it running?")

hwnd = found[0]
rect = w.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rect))
width = rect.right - rect.left
height = rect.bottom - rect.top
print(f"orb window {hwnd} at {rect.left},{rect.top}  {width}x{height}")

hits = 0
total = 0
rows = []
for row in range(height):
    line = ""
    for col in range(width):
        point = w.POINT(rect.left + col, rect.top + row)
        got = user32.WindowFromPoint(point) == hwnd
        line += "#" if got else "."
        hits += got
        total += 1
    rows.append(line)

print(f"\nclickable: {hits} of {total} pixels ({hits / total * 100:.1f}%)\n")
# Every other row and column, so the map fits a terminal.
for line in rows[::2]:
    print("  " + line[::2])

names = {}
for row in range(0, height, 4):
    for col in range(0, width, 4):
        other = user32.WindowFromPoint(w.POINT(rect.left + col, rect.top + row))
        if other != hwnd:
            buf = ctypes.create_unicode_buffer(80)
            user32.GetClassNameW(other, buf, 80)
            names[buf.value] = names.get(buf.value, 0) + 1
if names:
    print("\nwhat a click lands on instead:")
    for name, count in sorted(names.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>4}  {name}")
