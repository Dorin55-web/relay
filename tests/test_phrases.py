"""Where a spoken phrase begins and ends.

Streaming dictation pastes each phrase as you finish saying it, so everything
here happens in the PortAudio callback: it decides on arithmetic alone whether
the last fiftieth of a second was speech, whether you have paused long enough
to have finished, and whether what it has is worth sending to Whisper at all.

There is no microphone in any of this. Blocks of samples are handed to the
callback directly, which is what PortAudio does, and it lets a pause be exactly
0.7 seconds rather than roughly.
"""
import queue
import sys

import numpy as np

import context  # noqa: E402,F401
context.isolate_state()

from relay.audio import END_OF_SESSION, AudioRecorder, _resample  # noqa: E402
from relay.config import load_config  # noqa: E402

report = context.Report()
check = report.check

RATE = 16000
BLOCK = 800                      # 0.05s, the size the stream is opened with
BLOCK_SECONDS = BLOCK / RATE

SPEECH = 0.05
ROOM = 0.00002                   # a quiet room, well under vad_min_rms


def settings(**overrides):
    cfg = load_config("no-such-file.json")
    cfg.update(overrides)
    return cfg


def recorder(**overrides):
    overrides.setdefault("streaming", True)
    cfg = settings(**overrides)
    rec = AudioRecorder(cfg)
    rec._stream_rate = RATE
    return rec


def block(rms):
    """One callback of samples at roughly this loudness."""
    if rms <= 0:
        return np.zeros((BLOCK, 1), dtype=np.float32)
    return np.full((BLOCK, 1), rms, dtype=np.float32)


def feed(rec, rms, seconds):
    for _ in range(max(1, int(round(seconds / BLOCK_SECONDS)))):
        data = block(rms)
        rec._callback(data, len(data), None, None)


def drain(rec):
    out = []
    while True:
        try:
            out.append(rec.phrases.get_nowait())
        except queue.Empty:
            return out


class Stream:
    """Stands in for the open PortAudio stream, and can refuse to close."""

    def __init__(self, fail=False):
        self.fail, self.stopped, self.closed = fail, False, False

    def stop(self):
        self.stopped = True
        if self.fail:
            raise OSError("the device went away")

    def close(self):
        self.closed = True


print("\n--- a sentence, then a pause, is one phrase ---")
rec = recorder()
feed(rec, SPEECH, 2.0)
feed(rec, ROOM, 0.6)             # under phrase_silence_seconds
check("nothing while you are only drawing breath", rec.phrases.qsize() == 0,
      str(rec.phrases.qsize()))
feed(rec, ROOM, 0.3)             # now over it
got = drain(rec)
check("one phrase once you have stopped", len(got) == 1, str(len(got)))
if got:
    audio, rate = got[0]
    check("it holds the speech", len(audio) / rate >= 1.9, f"{len(audio) / rate:.2f}s")
    check("at the rate it was captured", rate == RATE, str(rate))
    check("as flat float32", audio.ndim == 1 and audio.dtype == np.float32,
          f"{audio.ndim}d {audio.dtype}")


print("\n--- two sentences with a pause between them are two phrases ---")
rec = recorder()
feed(rec, SPEECH, 1.0)
feed(rec, ROOM, 0.9)
feed(rec, SPEECH, 1.0)
feed(rec, ROOM, 0.9)
check("two", len(drain(rec)) == 2)


print("\n--- a cough is not a phrase ---")
# Measured against the speech in it, not the length of the buffer: a tenth of
# a second of noise inside a second of silence would otherwise pass a duration
# check and come back from Whisper as an invented sentence.
rec = recorder()
feed(rec, SPEECH, 0.15)          # under min_phrase_seconds
feed(rec, ROOM, 0.8)
check("dropped", drain(rec) == [], "something was emitted")


print("\n--- talking without pausing still gets sent ---")
rec = recorder(max_phrase_seconds=2.0)
feed(rec, SPEECH, 2.2)
check("flushed at the cap", len(drain(rec)) == 1)
feed(rec, SPEECH, 2.2)
check("and the next one too", len(drain(rec)) == 1)


print("\n--- an idle microphone does not grow ---")
rec = recorder()
feed(rec, ROOM, 30.0)
check("nothing emitted", rec.phrases.qsize() == 0, str(rec.phrases.qsize()))
check("and the buffer stayed at a run-up", rec._phrase_seconds <= 1.1,
      f"{rec._phrase_seconds:.2f}s")


print("\n--- and the run-up before you speak is kept ---")
# Whisper needs the attack of the first word; trimming to nothing clips it.
rec = recorder()
feed(rec, ROOM, 5.0)
feed(rec, SPEECH, 1.0)
feed(rec, ROOM, 0.8)
got = drain(rec)
check("one phrase", len(got) == 1, str(len(got)))
if got:
    seconds = len(got[0][0]) / RATE
    check("short enough to be a phrase, not the whole wait",
          seconds < 3.5, f"{seconds:.2f}s")
    check("but with room before the first word", seconds > 1.2, f"{seconds:.2f}s")


print("\n--- speaking the instant you start counts as speech ---")
# The noise floor starts at the configured minimum, not at whatever arrives
# first. Seeded from the first block, your own voice becomes the floor and
# nothing you say is ever loud enough again.
rec = recorder()
feed(rec, SPEECH, 1.0)
check("heard", rec._had_speech is True)
feed(rec, ROOM, 0.8)
check("and emitted", len(drain(rec)) == 1)


print("\n--- a long sentence does not mute itself ---")
# The floor follows the room only while the room is quiet. If speech could
# raise it, the second half of a long sentence would fall under the bar.
rec = recorder()
feed(rec, SPEECH, 8.0)
check("still speech at the end", rec._is_speech(SPEECH) is True,
      f"floor={rec._noise_floor}")


print("\n--- stop() flushes what you were saying ---")
rec = recorder()
rec._stream = Stream()
feed(rec, SPEECH, 1.0)
check("nothing emitted yet", rec.phrases.qsize() == 0)
rec.stop()
got = drain(rec)
check("the half-finished sentence comes out", len(got) == 2, str(len(got)))
check("followed by the end of the session", got and got[-1] is END_OF_SESSION,
      str(got[-1:]))
check("and the recorder is idle", rec.is_recording is False)


print("\n--- and still ends the session when the device is yanked ---")
# Unplug a USB mic, or walk out of range with a headset, and stop() raises.
# Without the end marker the streaming worker waits for ever - and the clipboard
# it is holding on your behalf is never handed back.
rec = recorder()
rec._stream = Stream(fail=True)
feed(rec, SPEECH, 1.0)
try:
    rec.stop()
except Exception:
    pass
got = drain(rec)
check("the session is ended either way", END_OF_SESSION in got, str(got))
check("and the recorder is idle", rec.is_recording is False)


print("\n--- not streaming: one clip at the end ---")
rec = recorder(streaming=False)
rec._stream = Stream()
feed(rec, SPEECH, 2.0)
clip = rec.stop()
check("a clip came back", clip is not None and len(clip) > 0)
check("about as long as you spoke",
      clip is not None and abs(len(clip) / RATE - 2.0) < 0.2,
      f"{len(clip) / RATE:.2f}s" if clip is not None else "-")

rec = recorder(streaming=False)
rec._stream = Stream()
feed(rec, SPEECH, 0.1)
check("a double-tap gives nothing", rec.stop() is None)

rec = recorder(streaming=False, max_recording_seconds=1)
rec._stream = Stream()
feed(rec, SPEECH, 3.0)
clip = rec.stop()
check("a forgotten toggle is capped, not endless",
      clip is not None and len(clip) / RATE <= 1.2,
      f"{len(clip) / RATE:.2f}s" if clip is not None else "-")

rec = recorder(streaming=False)
check("stopping when not recording is harmless", rec.stop() is None)

rec = recorder(streaming=False)
rec._stream = Stream(fail=True)
feed(rec, SPEECH, 2.0)
try:
    rec.stop()
except Exception:
    pass
check("a failed close still leaves it stoppable again", rec.is_recording is False)


print("\n--- rate conversion ---")
audio = np.sin(np.linspace(0, 40 * np.pi, 48000)).astype(np.float32)
out = _resample(audio, 48000, 16000)
check("a third of the samples", abs(len(out) - 16000) <= 2, str(len(out)))
check("still float32", out.dtype == np.float32, str(out.dtype))
check("and still audible", float(np.sqrt(np.mean(out ** 2))) > 0.5,
      f"{float(np.sqrt(np.mean(out ** 2))):.3f}")
check("same rate in, same array out", _resample(audio, 16000, 16000) is audio)


print("\n--- get_phrase hands the worker Whisper's rate ---")
rec = recorder()
rec._stream_rate = 48000
rec.phrases.put((np.zeros(48000, dtype=np.float32), 48000))
out = rec.get_phrase(timeout=0.1)
check("resampled on the way out", abs(len(out) - 16000) <= 2, str(len(out)))
rec.phrases.put(END_OF_SESSION)
check("and the marker passes straight through",
      rec.get_phrase(timeout=0.1) is END_OF_SESSION)


print("\n--- the level the orb animates from ---")
rec = recorder()
check("silent to start", rec.level == 0.0, str(rec.level))
feed(rec, SPEECH, 1.0)
loud = rec.level
check("rises while you talk", loud > 0.2, f"{loud:.3f}")
feed(rec, ROOM, 2.0)
check("and falls when you stop", rec.level < loud / 4, f"{rec.level:.3f}")
check("never above one", loud <= 1.0, f"{loud:.3f}")

sys.exit(report.finish())
