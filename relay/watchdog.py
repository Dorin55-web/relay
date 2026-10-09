"""Catch a stall in the act and write down what caused it.

Three attempts at this bug were made by reproducing it here, and all three
reproductions came back clean while the fault carried on happening. When the
laboratory disagrees with the machine it happens on, instrument the machine.

A thread wakes every TICK_MS and notes how late it was. Being late means it did
not get the GIL, and a thread that cannot get the GIL is exactly what stops a
low-level Windows hook returning - which is what freezes the mouse for every
application, not just this one. On a long gap it writes out what every thread
was executing, so the next freeze names itself instead of needing a guess.
"""

import sys
import threading
import time
import traceback

TICK_MS = 50
# Below this a gap is scheduling noise. Above it, a low-level hook would have
# missed LowLevelHooksTimeout and Windows would have been sitting on input.
REPORT_MS = 250
# Above this a gap is a machine suspension, sleep or hibernation, not a Python
# GIL stall. Low-level hooks are not starved and the mouse is not frozen while
# the operating system itself is asleep.
SUSPEND_THRESHOLD_MS = 10000
# Never write two reports for the same stall, or one freeze fills the log.
QUIET_SECONDS = 2.0

# How often to say nothing is wrong.
#
# An idle session writes nothing at all, so a log that simply stops tells you
# only that the last thing happened before whatever came next - which, when the
# application vanished without a trace, left the moment of death bounded by
# hours. One line every few minutes bounds it by minutes, and carries the worst
# stall since the last one, so a session's responsiveness can be read back
# afterwards rather than only watched live.
HEARTBEAT_SECONDS = 300


class Watchdog:
    def __init__(self, on_report=print, heartbeat_seconds=HEARTBEAT_SECONDS,
                 suspend_threshold_ms=SUSPEND_THRESHOLD_MS,
                 clock=time.perf_counter):
        self.on_report = on_report
        self.heartbeat_seconds = heartbeat_seconds
        self.suspend_threshold_ms = suspend_threshold_ms
        self.clock = clock
        self.worst_ms = 0.0
        self.stalls = 0
        self._stop = threading.Event()
        self._thread = None
        self._last_report = 0.0
        self._paused = 0
        self._lock = threading.Lock()
        self._just_resumed = False

    def pause(self):
        """Temporarily pause stall monitoring while hooks are safely down."""
        with self._lock:
            self._paused += 1

    def resume(self):
        """Resume stall monitoring once hooks are back up."""
        with self._lock:
            if self._paused > 0:
                self._paused -= 1
            if self._paused == 0:
                self._just_resumed = True

    def paused(self):
        """Context manager for pausing stall monitoring."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            self.pause()
            try:
                yield
            finally:
                self.resume()
        return _ctx()

    def start(self):
        if self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._run, name="watchdog", daemon=True
        )
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        last = self.clock()
        beat_at = last
        worst_since_beat = 0.0
        while not self._stop.is_set():
            time.sleep(TICK_MS / 1000.0)
            now = self.clock()
            gap = (now - last) * 1000.0
            last = now

            with self._lock:
                if self._paused > 0:
                    worst_since_beat = 0.0
                    continue
                if self._just_resumed:
                    self._just_resumed = False
                    worst_since_beat = 0.0
                    continue

            if gap >= self.suspend_threshold_ms:
                duration = f"{gap / 1000:.1f}s" if gap < 60000 else f"{gap / 60000:.1f}m"
                try:
                    self.on_report(
                        f"[watchdog] system resumed after {duration} sleep/standby"
                    )
                except Exception:
                    pass
                continue
            worst_since_beat = max(worst_since_beat, gap)

            if now - beat_at >= self.heartbeat_seconds:
                beat_at = now
                try:
                    self.on_report(
                        f"[alive] worst gap in the last "
                        f"{self.heartbeat_seconds // 60} minutes: "
                        f"{worst_since_beat:.0f}ms")
                except Exception:
                    pass
                worst_since_beat = 0.0

            if gap <= REPORT_MS:
                continue
            self.stalls += 1
            self.worst_ms = max(self.worst_ms, gap)
            if now - self._last_report < QUIET_SECONDS:
                continue
            self._last_report = now
            try:
                self.on_report(self._describe(gap))
            except Exception:
                pass

    def _describe(self, gap):
        """Every thread's innermost few frames, at the moment of the stall."""
        lines = [
            f"[stall] no GIL for {gap:.0f}ms - a low-level hook would have "
            f"been stuck this long, and the system's mouse with it",
        ]
        names = {t.ident: t.name for t in threading.enumerate()}
        for ident, frame in sys._current_frames().items():
            if ident == threading.get_ident():
                continue          # the watchdog itself is not interesting
            stack = traceback.extract_stack(frame)[-4:]
            lines.append(f"  thread {names.get(ident, ident)!r}:")
            for entry in stack:
                where = entry.filename.replace("\\", "/").rsplit("/", 1)[-1]
                lines.append(f"    {where}:{entry.lineno} in {entry.name}"
                             f"   {(entry.line or '').strip()[:70]}")
        return "\n".join(lines)

    def summary(self):
        if not self.stalls:
            return "[stall] none over the whole session"
        return (f"[stall] {self.stalls} stalls, worst {self.worst_ms:.0f}ms")
