"""One dictation, from the key press to the text landing in the box.

The state machine is three states and one queue, and almost every failure it
has ever had was the same shape: something threw, and the orb was left spinning
in `processing` for ever with the hotkey doing nothing. So most of what is
checked here is not the happy path - it is that every way a step can fail still
ends at `idle`.

Escape throws one away, and gets the same treatment: the interesting half of
it is not that nothing is pasted, it is that the clipboard comes back and the
next dictation still works.

Nothing real is recorded, translated or pasted. The recorder, the engine and
the clipboard are all stood in for, which is what lets a device failure be
tested at all.
"""
import queue
import sys
import threading
import time

import context  # noqa: E402,F401
context.isolate_state()

from relay import __main__ as app  # noqa: E402
from relay import feedback as feedback_mod  # noqa: E402
from relay.config import load_config  # noqa: E402

report = context.Report()
check = report.check


class Recorder:
    """Stands in for AudioRecorder, and can fail on demand."""

    def __init__(self):
        self.started = self.stopped = 0
        self.fail_start = self.fail_stop = False
        self.clip = "some audio"
        self.phrases = queue.Queue()
        self.is_recording = False
        self.device_name = "a microphone"
        self.level = 0.0

    def start(self):
        self.started += 1
        if self.fail_start:
            raise OSError("the device is in use")
        self.is_recording = True

    def stop(self):
        self.stopped += 1
        self.is_recording = False
        if self.fail_stop:
            raise OSError("the device went away")
        return self.clip

    def get_phrase(self, timeout=0.25):
        return self.phrases.get(timeout=timeout)


class Engine:
    def __init__(self, text="hello there"):
        self.text, self.calls, self.fail = text, 0, False

    def translate(self, clip):
        self.calls += 1
        if self.fail:
            raise RuntimeError("the model fell over")
        return self.text


class Clipboard:
    """Records what the injector was asked to do, and what it was given back."""

    def __init__(self):
        self.pasted = []
        self.saved = 0
        self.restored = []
        self.refuse = False

    def paste(self, text, config, manage_clipboard=True, target_hwnd=None, submit=None):
        self.pasted.append(text)
        return not self.refuse

    def save(self, config):
        self.saved += 1
        return "what you were carrying"

    def restore(self, original, config):
        self.restored.append(original)


def build(**overrides):
    cfg = load_config("no-such-file.json")
    # Off, or the suite plays the failure buzz through whatever you are wearing
    # every time it exercises an error path.
    cfg.update(beep_feedback=False)
    cfg.update(overrides)
    voice = app.VoicePrompt(cfg)
    voice.recorder = Recorder()
    voice.engine = Engine()
    voice._ready.set()
    board = Clipboard()
    app.paste_text = board.paste
    app.save_clipboard = board.save
    app.restore_clipboard = board.restore
    return voice, board


print("\n--- nothing happens until the model is loaded ---")
# Pressing the hotkey during the twenty seconds Whisper takes to load used to
# open a stream nothing would ever read from.
voice, board = build(streaming=False)
voice._ready.clear()
voice.toggle()
check("no recording started", voice.recorder.started == 0, str(voice.recorder.started))
check("and the state is unchanged", voice.state == app.IDLE, voice.state)


print("\n--- press, speak, press ---")
voice, board = build(streaming=False)
voice.toggle()
check("recording", voice.state == app.RECORDING, voice.state)
check("the device was opened", voice.recorder.started == 1)
voice.toggle()
check("processing", voice.state == app.PROCESSING, voice.state)
check("the device was closed", voice.recorder.stopped == 1)
check("and the clip went to the worker, not the control thread",
      voice._jobs.qsize() == 1, str(voice._jobs.qsize()))


print("\n--- and a third press while it is thinking is ignored ---")
voice.toggle()
check("still processing", voice.state == app.PROCESSING, voice.state)
check("nothing reopened", voice.recorder.started == 1, str(voice.recorder.started))


print("\n--- a microphone that will not open leaves you idle, not stuck ---")
voice, board = build(streaming=False)
voice.recorder.fail_start = True
voice.toggle()
check("back to idle", voice.state == app.IDLE, voice.state)
check("and you can try again", voice.recorder.started == 1)
voice.recorder.fail_start = False
voice.toggle()
check("the next attempt works", voice.state == app.RECORDING, voice.state)


print("\n--- and neither does one that will not close ---")
voice, board = build(streaming=False)
voice.toggle()
voice.recorder.fail_stop = True
voice.toggle()
check("idle", voice.state == app.IDLE, voice.state)
check("nothing was queued from a failed stop", voice._jobs.qsize() == 0,
      str(voice._jobs.qsize()))


print("\n--- the worker pastes what came back ---")
voice, board = build(streaming=False)
voice._handle_clip("some audio")
check("translated once", voice.engine.calls == 1, str(voice.engine.calls))
check("and pasted", board.pasted == ["hello there"], str(board.pasted))

voice, board = build(streaming=False)
voice._handle_clip(None)
check("silence is not pasted", board.pasted == [], str(board.pasted))
check("and not translated either", voice.engine.calls == 0, str(voice.engine.calls))

voice, board = build(streaming=False)
voice.engine.text = ""
voice._handle_clip("some audio")
check("nor is an empty translation", board.pasted == [], str(board.pasted))


print("\n--- a worker that throws goes back to idle and keeps working ---")
# The state is set in a finally: an exception that skipped it would leave the
# orb spinning and the hotkey dead for the rest of the session.
voice, board = build(streaming=False)
voice.engine.fail = True
voice._set_state(app.PROCESSING)
thread = threading.Thread(target=voice._worker, daemon=True)
thread.start()
voice._jobs.put("some audio")
deadline = time.monotonic() + 2
while voice.state != app.IDLE and time.monotonic() < deadline:
    time.sleep(0.02)
check("idle after a failure", voice.state == app.IDLE, voice.state)
voice.engine.fail = False
voice._jobs.put("more audio")
deadline = time.monotonic() + 2
while not board.pasted and time.monotonic() < deadline:
    time.sleep(0.02)
check("and the next dictation still works", board.pasted == ["hello there"],
      str(board.pasted))
voice._stop.set()
thread.join(timeout=2)


print("\n--- streaming: the clipboard is taken once and given back once ---")
voice, board = build(streaming=True)
voice.toggle()
check("saved when you start talking", board.saved == 1, str(board.saved))
voice.toggle()
check("still only once", board.saved == 1, str(board.saved))
check("and nothing was queued to the clip worker", voice._jobs.qsize() == 0)

worker = threading.Thread(target=voice._streaming_worker, daemon=True)
worker.start()
voice.recorder.phrases.put("first phrase")
voice.recorder.phrases.put("second phrase")
deadline = time.monotonic() + 2
while len(board.pasted) < 2 and time.monotonic() < deadline:
    time.sleep(0.02)
check("each phrase pasted as it finished", len(board.pasted) == 2, str(board.pasted))
check("the first has no leading space", board.pasted[0] == "hello there",
      repr(board.pasted[0]))
check("and the ones after it do", board.pasted[1] == " hello there",
      repr(board.pasted[1]))

voice.recorder.phrases.put(app.audio_mod.END_OF_SESSION)
deadline = time.monotonic() + 2
while not board.restored and time.monotonic() < deadline:
    time.sleep(0.02)
check("the clipboard came back at the end",
      board.restored == ["what you were carrying"], str(board.restored))
check("and you are idle again", voice.state == app.IDLE, voice.state)
voice._stop.set()
worker.join(timeout=2)


print("\n--- a phrase that fails does not end the session ---")
voice, board = build(streaming=True)
voice._session_clipboard = "what you were carrying"
voice.engine.fail = True
worker = threading.Thread(target=voice._streaming_worker, daemon=True)
worker.start()
voice.recorder.phrases.put("a phrase")
time.sleep(0.3)
voice.engine.fail = False
voice.recorder.phrases.put("another phrase")
deadline = time.monotonic() + 2
while not board.pasted and time.monotonic() < deadline:
    time.sleep(0.02)
check("the one after it still lands", board.pasted == ["hello there"], str(board.pasted))
voice.recorder.phrases.put(app.audio_mod.END_OF_SESSION)
deadline = time.monotonic() + 2
while not board.restored and time.monotonic() < deadline:
    time.sleep(0.02)
check("and the clipboard still comes back", board.restored == ["what you were carrying"],
      str(board.restored))
voice._stop.set()
worker.join(timeout=2)


print("\n--- a session where nothing was heard says so ---")
voice, board = build(streaming=True)
voice._session_clipboard = "what you were carrying"
heard = []
voice.feedback.nothing_heard = lambda: heard.append(True)
worker = threading.Thread(target=voice._streaming_worker, daemon=True)
worker.start()
voice.recorder.phrases.put(app.audio_mod.END_OF_SESSION)
deadline = time.monotonic() + 2
while not board.restored and time.monotonic() < deadline:
    time.sleep(0.02)
check("you are told", heard == [True], str(heard))
voice._stop.set()
worker.join(timeout=2)


print("\n--- escape throws the dictation away instead of pasting it ---")
# There used to be no way to change your mind. Fluff a sentence, or have
# somebody walk in on you half way through one, and the text went into
# whatever window you were writing in regardless.
voice, board = build(streaming=False)
thrown = []
voice.feedback.discarded = lambda already_pasted=False: thrown.append(already_pasted)
voice.toggle()
check("recording", voice.state == app.RECORDING, voice.state)
voice.discard()
check("the device was closed", voice.recorder.stopped == 1, str(voice.recorder.stopped))
check("nothing went to the worker", voice._jobs.qsize() == 0, str(voice._jobs.qsize()))
check("nothing was translated", voice.engine.calls == 0, str(voice.engine.calls))
check("nothing was pasted", board.pasted == [], str(board.pasted))
check("you are told it went in the bin", thrown == [False], str(thrown))
check("and you are back at idle", voice.state == app.IDLE, voice.state)

voice.toggle()
check("the next dictation starts", voice.state == app.RECORDING, voice.state)
voice.toggle()
check("and this one does reach the worker", voice._jobs.qsize() == 1,
      str(voice._jobs.qsize()))


print("\n--- escape does nothing at all when nothing is being said ---")
# It is the key people press most: to close a menu, to leave a field, to stop
# a page loading. Every one of those has to be free, silent and invisible.
voice, board = build(streaming=False)
voice._hotkey_matches = lambda key: key == "f9"
voice.on_press(app.keyboard.Key.esc)
check("nothing was queued", voice._commands.qsize() == 0, str(voice._commands.qsize()))
check("the microphone was not touched", voice.recorder.stopped == 0,
      str(voice.recorder.stopped))
check("and the state did not move", voice.state == app.IDLE, voice.state)
voice.discard()
check("nor does the command itself, should one ever arrive",
      voice.state == app.IDLE and voice.recorder.stopped == 0, voice.state)

voice.toggle()
voice.on_press(app.keyboard.Key.esc)
check("but while you are talking, the hook queues one",
      list(voice._commands.queue) == ["discard"], str(list(voice._commands.queue)))


print("\n--- the control thread tells a discard from a toggle ---")
# One queue carries both, and a discard taken for a toggle would paste the
# very thing you asked it to throw away.
voice, board = build(streaming=False)
thread = threading.Thread(target=voice._control_loop, daemon=True)
thread.start()
voice._commands.put("toggle")
deadline = time.monotonic() + 2
while voice.state != app.RECORDING and time.monotonic() < deadline:
    time.sleep(0.02)
check("a toggle starts recording", voice.state == app.RECORDING, voice.state)
voice._commands.put("discard")
deadline = time.monotonic() + 2
while voice.state != app.IDLE and time.monotonic() < deadline:
    time.sleep(0.02)
check("a discard ends it", voice.state == app.IDLE, voice.state)
check("with no clip behind it", voice._jobs.qsize() == 0, str(voice._jobs.qsize()))
check("and the thread is still listening", thread.is_alive())
voice._stop.set()
thread.join(timeout=2)


print("\n--- a microphone that throws on the way out still leaves you idle ---")
voice, board = build(streaming=False)
voice.toggle()
voice.recorder.fail_stop = True
voice.discard()
check("idle", voice.state == app.IDLE, voice.state)
check("nothing was pasted", board.pasted == [], str(board.pasted))
check("nothing was queued either", voice._jobs.qsize() == 0, str(voice._jobs.qsize()))
voice.recorder.fail_stop = False
voice.toggle()
check("and you can dictate again", voice.state == app.RECORDING, voice.state)


print("\n--- live: escape stops the rest, and owns up to what it cannot stop ---")
# Phrases go into the window as you finish saying them, so by the time you
# press Escape some of the text is already there and no paste can be taken
# back. What can still be stopped is everything not yet pasted.
voice, board = build(streaming=True)
thrown = []
voice.feedback.discarded = lambda already_pasted=False: thrown.append(already_pasted)
worker = threading.Thread(target=voice._streaming_worker, daemon=True)
worker.start()
voice.toggle()
voice.recorder.phrases.put("a phrase you meant")
deadline = time.monotonic() + 2
while not board.pasted and time.monotonic() < deadline:
    time.sleep(0.02)
check("the phrase you meant landed", board.pasted == ["hello there"], str(board.pasted))

voice.discard()
# What the real recorder does on its way out: flush the sentence you were half
# way through, then mark the end of the session.
voice.recorder.phrases.put("the half sentence you fluffed")
voice.recorder.phrases.put(app.audio_mod.END_OF_SESSION)
deadline = time.monotonic() + 2
while not board.restored and time.monotonic() < deadline:
    time.sleep(0.02)
check("the fluffed one was never translated", voice.engine.calls == 1,
      str(voice.engine.calls))
check("nor pasted", board.pasted == ["hello there"], str(board.pasted))
check("the clipboard came back", board.restored == ["what you were carrying"],
      str(board.restored))
check("you are told the text already there is staying", thrown == [True], str(thrown))
check("and you are idle", voice.state == app.IDLE, voice.state)

# The flag that stops phrases is cleared at both ends of a session. Left
# standing, it would throw away every phrase from here on and say nothing
# about why nothing was appearing.
voice.toggle()
check("a dictation started after it records", voice.state == app.RECORDING, voice.state)
voice.recorder.phrases.put("a fresh phrase")
deadline = time.monotonic() + 2
while len(board.pasted) < 2 and time.monotonic() < deadline:
    time.sleep(0.02)
check("and its phrases are pasted again", len(board.pasted) == 2, str(board.pasted))
check("as the first of a new session, with no leading space",
      board.pasted[1] == "hello there", repr(board.pasted[1]))
voice._stop.set()
worker.join(timeout=2)


print("\n--- live, with nothing pasted yet, and the whole thing goes ---")
voice, board = build(streaming=True)
thrown, heard = [], []
voice.feedback.discarded = lambda already_pasted=False: thrown.append(already_pasted)
voice.feedback.nothing_heard = lambda: heard.append(True)
worker = threading.Thread(target=voice._streaming_worker, daemon=True)
worker.start()
voice.toggle()
voice.discard()
voice.recorder.phrases.put("the one sentence you fluffed")
voice.recorder.phrases.put(app.audio_mod.END_OF_SESSION)
deadline = time.monotonic() + 2
while not board.restored and time.monotonic() < deadline:
    time.sleep(0.02)
check("nothing was pasted", board.pasted == [], str(board.pasted))
check("and it is called discarded, not nothing recognised",
      thrown == [False] and heard == [], f"{thrown} {heard}")
check("the clipboard came back", board.restored == ["what you were carrying"],
      str(board.restored))
check("and you are idle", voice.state == app.IDLE, voice.state)
voice._stop.set()
worker.join(timeout=2)


print("\n--- a phrase inside the model when you press escape is dropped ---")
# The likeliest moment of all to press it: you pause, the phrase goes off to
# be translated, and that is when you hear yourself.
voice, board = build(streaming=True)
inside, release = threading.Event(), threading.Event()


def slow_translate(clip):
    inside.set()
    release.wait(2)
    return "hello there"


voice.engine.translate = slow_translate
worker = threading.Thread(target=voice._streaming_worker, daemon=True)
worker.start()
voice.toggle()
voice.recorder.phrases.put("a phrase already on its way")
check("the model has it", inside.wait(2))
voice.discard()
release.set()
voice.recorder.phrases.put(app.audio_mod.END_OF_SESSION)
deadline = time.monotonic() + 2
while not board.restored and time.monotonic() < deadline:
    time.sleep(0.02)
check("what came back out of it was not pasted", board.pasted == [], str(board.pasted))
check("the clipboard still came back", board.restored == ["what you were carrying"],
      str(board.restored))
check("and you are idle", voice.state == app.IDLE, voice.state)
voice._stop.set()
worker.join(timeout=2)


print("\n--- and a live stop that throws still ends the session ---")
# stop() is what puts the end marker on the phrase queue, and it raises before
# it gets there. Without a marker the worker waits for ever, holding the
# clipboard it took on your behalf and never coming back to idle.
voice, board = build(streaming=True)
worker = threading.Thread(target=voice._streaming_worker, daemon=True)
worker.start()
voice.toggle()
voice.recorder.fail_stop = True
voice.discard()
deadline = time.monotonic() + 2
while not board.restored and time.monotonic() < deadline:
    time.sleep(0.02)
check("the clipboard came back anyway", board.restored == ["what you were carrying"],
      str(board.restored))
check("and you are idle", voice.state == app.IDLE, voice.state)
voice._stop.set()
worker.join(timeout=2)


print("\n--- a discarded dictation does not sound like a finished one ---")
# There is no window, so the beeps are the whole vocabulary. Six outcomes into
# the same speaker, and a discard that borrowed the sound of a paste would be
# telling you the opposite of what happened.
played = []
feedback_mod._beep_async = played.append
loud = load_config("no-such-file.json")
loud.update(beep_feedback=True)
speaker = feedback_mod.Feedback(loud)
speaker.recording_started("a microphone")
speaker.recording_stopped()
speaker.success("hello there")
speaker.nothing_heard()
speaker.discarded()
speaker.error("something went wrong")
check("a discard is audible at all", len(played) == 6, str(len(played)))
check("and every outcome has a sound of its own",
      len({tuple(tones) for tones in played}) == len(played),
      str([tuple(tones) for tones in played]))


print("\n--- the hotkey does not fire twice while you hold it ---")
# Windows repeats key-down while a key is held.
voice, board = build(streaming=False)
voice._hotkey_matches = lambda key: key == "f9"
for _ in range(20):
    voice.on_press("f9")
check("one request", voice._commands.qsize() == 1, str(voice._commands.qsize()))
voice.on_release("f9")
voice.on_press("f9")
check("and another after you let go", voice._commands.qsize() == 2,
      str(voice._commands.qsize()))

voice, board = build(streaming=False)
voice._hotkey_matches = lambda key: key == "f9"
voice.on_press("a")
check("any other key is not a toggle", voice._commands.qsize() == 0,
      str(voice._commands.qsize()))


print("\n--- the control thread survives a toggle that throws ---")
voice, board = build(streaming=False)


def explode():
    raise RuntimeError("something unforeseen")


voice.toggle = explode
voice._set_state(app.RECORDING)
thread = threading.Thread(target=voice._control_loop, daemon=True)
thread.start()
voice._commands.put("toggle")
deadline = time.monotonic() + 2
while voice.state != app.IDLE and time.monotonic() < deadline:
    time.sleep(0.02)
check("it puts you back to idle", voice.state == app.IDLE, voice.state)
check("and is still listening", thread.is_alive())
voice._stop.set()
thread.join(timeout=2)


print("\n--- hotkey names ---")
matches, label = app.parse_hotkey("f9")
check("a function key", label == "F9", label)
matches, label = app.parse_hotkey(" F4 ")
check("spaces and case do not matter", label == "F4", label)
matches, label = app.parse_hotkey("insert")
check("a named key", label == "INSERT", label)
matches, label = app.parse_hotkey("k")
check("a letter", label == "K", label)
try:
    app.parse_hotkey("ctrl+alt+x")
    said = None
except ValueError as exc:
    said = str(exc)
check("and a combination is refused clearly", said and "unrecognised" in said, str(said))


print("\n--- shutting down while recording releases the microphone ---")
voice, board = build(streaming=False)
voice.toggle()
check("recording", voice.recorder.is_recording is True)
voice.shutdown()
check("the device was released", voice.recorder.stopped == 1, str(voice.recorder.stopped))
check("and the threads were told to stop", voice._stop.is_set())

voice, board = build(streaming=False)
voice.toggle()
voice.recorder.fail_stop = True
voice.shutdown()
check("even when the device is already gone", voice._stop.is_set())

sys.exit(report.finish())
