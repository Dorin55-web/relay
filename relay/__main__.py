"""Entry point: hotkey or orb click -> record -> translate -> paste."""

# The CUDA DLL directories must be registered before anything imports
# ctranslate2, and stdout must exist before anything prints (under pythonw.exe
# it is None). Keep both first.
from .cuda_setup import enable_cuda_dlls  # isort: skip
from .logsetup import setup_output  # isort: skip

setup_output()
enable_cuda_dlls()

import argparse  # noqa: E402
import contextlib  # noqa: E402
import ctypes  # noqa: E402
import queue  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from pynput import keyboard  # noqa: E402

from . import audio as audio_mod  # noqa: E402
from .config import load_config, write_default_config  # noqa: E402
from .feedback import Feedback  # noqa: E402
from .injector import paste_text, restore_clipboard, save_clipboard  # noqa: E402
from . import prompts as prompts_mod  # noqa: E402
from .watchdog import Watchdog  # noqa: E402

IDLE, RECORDING, PROCESSING = "idle", "recording", "processing"

# Windows groups taskbar buttons by the process's application id. Without one of
# our own, everything here is filed under the python interpreter that happens to
# be hosting it, and the taskbar shows Python's icon whatever the window icon
# says. Must be set before the first window exists.
APP_ID = "Relay.VoicePrompt"


def set_app_id():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception as exc:
        print(f"[app] could not set the taskbar identity: {exc}")


def parse_hotkey(name):
    """Turn a config string like "f9" into a predicate over pynput key events."""
    name = name.strip().lower()
    special = getattr(keyboard.Key, name, None)
    if special is not None:
        return lambda key: key == special, name.upper()
    if len(name) == 1:
        code = keyboard.KeyCode.from_char(name)
        return lambda key: key == code, name.upper()
    raise ValueError(f"unrecognised hotkey {name!r} (try 'f9', 'f4', 'pause', 'insert')")


class VoicePrompt:
    def __init__(self, config):
        self.config = config
        self.feedback = Feedback(config)
        self.recorder = audio_mod.AudioRecorder(config)
        self.engine = None
        self.orb = None
        self.state = IDLE
        self._lock = threading.Lock()
        self._key_held = False
        self._hotkey_matches = lambda key: False
        self._commands = queue.Queue()
        self._jobs = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._listener = None
        self.streaming = bool(config.streaming)
        self._session_clipboard = None
        self._pasted_any = False
        self.tracker = None
        # Built the first time the write window is opened; most sessions
        # only ever dictate and never need the text model in memory.
        self.text_translator = None
        # Pinned when a dictation starts, so phrases keep going to the window
        # you were writing in even if focus wanders mid-sentence.
        self._target_hwnd = None
        # Watches for the stretches where nothing Python can run. Costs a
        # wake-up every 50ms and says nothing unless something goes wrong.
        self.watchdog = Watchdog()

    # --- hotkey handling -------------------------------------------------

    def on_press(self, key):
        """Runs inside the Windows keyboard hook - must return immediately.

        A low-level hook that overruns LowLevelHooksTimeout (300ms by default)
        gets silently removed by Windows and the hotkey dies with no error.
        Opening an audio stream can easily take that long, so all we do here is
        queue the request and let the control thread do the work.
        """
        if not self._hotkey_matches(key):
            return
        # Windows repeats key-down events while a key is held; without this the
        # toggle would fire dozens of times per press.
        if self._key_held:
            return
        self._key_held = True
        self._optimistic_ui()
        self._commands.put("toggle")

    def on_release(self, key):
        if self._hotkey_matches(key):
            self._key_held = False

    def _optimistic_ui(self):
        """Show the next state before the audio device is ready.

        Opening an input stream costs ~300ms. Waiting for it before repainting
        makes the click feel broken, so the orb moves now and the control thread
        catches up; if the device fails to open, toggle() puts it back.
        """
        if self.orb is None or not self._ready.is_set():
            return
        with self._lock:
            state = self.state
        if state == IDLE:
            self.orb.set_state(RECORDING)
        elif state == RECORDING:
            self.orb.set_state(PROCESSING)

    def request_toggle(self):
        """Called by the orb; same queue as the hotkey so they can't interleave."""
        self._optimistic_ui()
        self._commands.put("toggle")

    def _control_loop(self):
        """Serialises start/stop so the hook thread never blocks on audio I/O."""
        while not self._stop.is_set():
            try:
                self._commands.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self.toggle()
            except Exception as exc:
                self.feedback.error(f"toggle failed: {exc}")
                self._set_state(IDLE)

    def _set_state(self, state):
        with self._lock:
            self.state = state
        if self.orb is not None:
            self.orb.set_state(state)

    def toggle(self):
        if not self._ready.is_set():
            print("[..] still loading the model")
            return

        with self._lock:
            state = self.state

        if state == IDLE:
            # Pin for the whole dictation: the window you last clicked into.
            # Pinning rather than re-reading per phrase keeps one session's
            # phrases together even if you click around while talking.
            if self.tracker is not None:
                self._target_hwnd = self.tracker.current()
                if self._target_hwnd:
                    print(f"[target] writing into: {self.tracker.title}")
                    if self.config.restore_caret:
                        # Bring the window forward and put the cursor back in
                        # the box now, while you are still drawing breath, so
                        # the first phrase has somewhere to land.
                        self.tracker.restore()
                        self.tracker.restore_caret()
            if self.streaming:
                # Snapshot once for the whole session; phrases paste repeatedly.
                self._session_clipboard = save_clipboard(self.config)
                self._pasted_any = False
            try:
                self.recorder.start()
            except Exception as exc:
                self.feedback.error(f"could not start recording: {exc}")
                if self.orb:
                    self.orb.set_state(IDLE)   # undo the optimistic repaint
                    self.orb.flash("error")
                return
            self._set_state(RECORDING)
            self.feedback.recording_started(self.recorder.device_name)

        elif state == RECORDING:
            self._set_state(PROCESSING)
            self.feedback.recording_stopped()
            try:
                clip = self.recorder.stop()
            except Exception as exc:
                self.feedback.error(f"could not stop recording: {exc}")
                self._set_state(IDLE)
                return
            if self.streaming:
                # stop() already queued the final phrase and the end marker;
                # the streaming worker finishes up and returns us to idle.
                return
            # Hand off to the worker: transcription takes about a second and must
            # not run on the control thread, which needs to stay responsive.
            self._jobs.put(clip)

        else:
            print("[..] still processing the previous dictation")

    # --- worker ----------------------------------------------------------

    def _worker(self):
        while not self._stop.is_set():
            try:
                clip = self._jobs.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._handle_clip(clip)
            except Exception as exc:
                self.feedback.error(f"processing failed: {exc}")
                if self.orb:
                    self.orb.flash("error")
            finally:
                self._set_state(IDLE)
                self._jobs.task_done()

    def _streaming_worker(self):
        """Translate and paste each phrase as you finish saying it.

        One thread, one queue: phrases reach the prompt box in the order you
        spoke them, and each paste only ever appends.
        """
        while not self._stop.is_set():
            try:
                phrase = self.recorder.get_phrase(timeout=0.25)
            except queue.Empty:
                continue

            if phrase is audio_mod.END_OF_SESSION:
                restore_clipboard(self._session_clipboard, self.config)
                self._session_clipboard = None
                if self._pasted_any:
                    if self.orb:
                        self.orb.flash("ok")
                else:
                    self.feedback.nothing_heard()
                    if self.orb:
                        self.orb.flash("error")
                self._set_state(IDLE)
                continue

            try:
                text = self.engine.translate(phrase)
                if not text:
                    continue
                # A leading space keeps phrases apart without a trailing one
                # dangling when you stop.
                chunk = text if not self._pasted_any else " " + text
                if paste_text(chunk, self.config, manage_clipboard=False,
                              target_hwnd=self._target_hwnd):
                    self._pasted_any = True
                    self.feedback.success(text)
            except Exception as exc:
                self.feedback.error(f"phrase failed: {exc}")

    def _handle_clip(self, clip):
        if clip is None:
            self.feedback.nothing_heard()
            if self.orb:
                self.orb.flash("error")
            return
        text = self.engine.translate(clip)
        if not text:
            self.feedback.nothing_heard()
            if self.orb:
                self.orb.flash("error")
            return
        if paste_text(text, self.config, target_hwnd=self._target_hwnd):
            self.feedback.success(text)
            if self.orb:
                self.orb.flash("ok")
        else:
            self.feedback.error(f"could not paste, text was: {text}")
            if self.orb:
                self.orb.flash("error")

    # --- prompt templates -------------------------------------------------

    def insert_prompt(self, text):
        """Paste a template from the orb's menu into the box you last typed in.

        Dispatched to its own thread because paste_text sleeps through the
        clipboard round-trip, and the caller is the Qt menu - the same thread
        that paints the orb.
        """
        threading.Thread(
            target=self._insert_prompt, args=(text,), daemon=True
        ).start()

    def _insert_prompt(self, text):
        # A menu is a real window and takes the foreground when it opens. Let Qt
        # finish tearing the popup down first: hand focus back too early and the
        # closing menu takes it straight off the window we just restored.
        time.sleep(0.12)

        target = None
        if self.tracker is not None:
            target = self.tracker.current()
            if target:
                print(f"[prompt] writing into: {self.tracker.title}")
                self.tracker.restore()
                if self.config.restore_caret:
                    self.tracker.restore_caret()

        try:
            # submit=False whatever auto_enter says: a template still has its
            # <blanks> in it, and Enter would send it half-written.
            if paste_text(text, self.config, target_hwnd=target, submit=False):
                self.feedback.success(text)
            else:
                self.feedback.error(f"could not insert prompt: {text}")
        except Exception as exc:
            self.feedback.error(f"could not insert prompt: {exc}")

    @contextlib.contextmanager
    def _hooks_down(self, what):
        """Uninstall the input hooks while `what` runs on the GUI thread.

        Both hooks are low-level Windows hooks whose callbacks are Python, so
        both need the GIL to return. Building a window for the first time holds
        it for around half a second - Qt loading fonts, parsing the stylesheet
        and creating the native window - and during that:

          - the mouse hook cannot return, so Windows stops delivering mouse
            input to every application, which is the cursor freezing
          - the keyboard hook overruns LowLevelHooksTimeout, 300ms by default,
            and Windows quietly unhooks it, which would take F9 with it

        Uninstalled, there is nothing to block and nothing to time out. The
        cost is that a click or an F9 during those milliseconds is missed,
        which is a fair trade against the mouse stopping system-wide.
        """
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()
        if self.tracker is not None:
            self.tracker.pause()
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            if elapsed > 100:
                print(f"[hooks] down {elapsed:.0f}ms while {what} was built")
            if self.tracker is not None:
                self.tracker.resume()
            self._listener = keyboard.Listener(
                on_press=self.on_press, on_release=self.on_release
            )
            self._listener.start()

    def _request_prebuild(self):
        """Ask the GUI thread to build the write window while nobody is waiting.

        Called from the model-loading thread, so it cannot touch widgets
        itself. singleShot with the orb as receiver queues the call onto the
        thread that owns it, which is the one allowed to build windows.
        """
        if self.orb is None:
            return
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self.orb, self._prebuild_compose)

    def _prebuild_compose(self):
        from .compose import prebuild

        if self.text_translator is None:
            from .translator import TextTranslator

            self.text_translator = TextTranslator(self.config)
        try:
            started = time.perf_counter()
            with self._hooks_down("the write window"):
                prebuild(
                    self.text_translator,
                    on_paste=self.insert_prompt,
                    target_getter=lambda: (
                        self.tracker.title if self.tracker is not None else None
                    ),
                )
            print(f"[compose] built ahead of time in "
                  f"{(time.perf_counter() - started) * 1000:.0f}ms")
        except Exception as exc:
            # Not fatal: the window is simply built on the click instead.
            print(f"[compose] could not prebuild: {exc}")

    def open_compose(self):
        """The typed-text window. Called from the menu, so on the GUI thread."""
        from .compose import open_compose

        if self.text_translator is None:
            from .translator import TextTranslator

            self.text_translator = TextTranslator(self.config)

        try:
            with self._hooks_down("the write window"):
                open_compose(
                    self.text_translator,
                    # The same route a template insert takes.
                    on_paste=self.insert_prompt,
                    # Read each time, not once: the target follows your clicks
                    # and can change while the window sits open.
                    target_getter=lambda: (
                        self.tracker.title if self.tracker is not None else None
                    ),
                )
        except Exception as exc:
            self.feedback.error(f"could not open the write window: {exc}")

    def edit_prompts(self):
        """Open the editor window. Called from the menu, so already on the GUI thread."""
        from .prompt_editor import open_editor

        try:
            with self._hooks_down("the prompt editor"):
                open_editor()
        except Exception as exc:
            # Rather than leave the menu item doing nothing, fall back to the
            # file itself - editable, just less forgiving about commas.
            self.feedback.error(f"could not open the prompt editor: {exc}")
            prompts_mod.open_for_editing()

    # --- startup ---------------------------------------------------------

    def _load_engine(self):
        """Load Whisper off the main thread so the orb appears immediately."""
        from .transcriber import WhisperEngine

        try:
            engine = WhisperEngine(self.config)
            engine.load()
            self.engine = engine
            self._ready.set()
            self._set_state(IDLE)
            print("[ready] press the hotkey or click the orb")
            self._request_prebuild()
        except Exception as exc:
            self.feedback.error(f"could not load Whisper: {exc}")
            if self.orb:
                self.orb.flash("error")

    def _start_threads(self):
        self.watchdog.start()
        if self.config.remember_target:
            from .target import TargetTracker

            self.tracker = TargetTracker().start()
        threading.Thread(target=self._control_loop, daemon=True).start()
        worker = self._streaming_worker if self.streaming else self._worker
        threading.Thread(target=worker, daemon=True).start()
        self._listener = keyboard.Listener(
            on_press=self.on_press, on_release=self.on_release
        )
        self._listener.start()

    def shutdown(self):
        self._stop.set()
        if self.tracker is not None:
            self.tracker.stop()
        if self._listener is not None:
            self._listener.stop()
        if self.recorder.is_recording:
            try:
                self.recorder.stop()
            except Exception:
                pass
        # Hand the audio stack back explicitly rather than leaving it to the
        # interpreter's exit hooks. A Bluetooth headset only returns to stereo
        # once everything holding its hands-free profile has let go, and a
        # half-torn-down PortAudio is exactly what stops that happening.
        try:
            audio_mod.sd._terminate()
        except Exception as exc:
            print(f"[audio] could not release the audio stack: {exc}")
        self.watchdog.stop()
        print(self.watchdog.summary())
        print("Stopped.\n")

    def run(self, show_ui=True):
        self._hotkey_matches, hotkey_label = parse_hotkey(self.config.hotkey)

        target = "English" if self.config.task == "translate" else "as spoken"
        print("\n" + "=" * 62)
        print(f"  Press {hotkey_label} to start, {hotkey_label} again to stop.")
        if show_ui:
            print("  Or click the orb. Drag to move it.")
            print("  Right-click it for the prompt templates, and to quit.")
        print(f"  Speak Romanian -> {target} text is pasted where your cursor is.")
        if self.streaming:
            print("  Live: each phrase is pasted when you pause, as you speak.")
        if not self.config.auto_enter:
            print("  Nothing is submitted for you; press Enter yourself.")
        print("=" * 62 + "\n")

        if not show_ui:
            self._start_threads()
            self._load_engine()
            try:
                self._listener.join()
            except KeyboardInterrupt:
                pass
            finally:
                self.shutdown()
            return

        from .overlay import Orb

        prompts_mod.ensure_file()
        self.orb = Orb(
            on_toggle=self.request_toggle,
            on_quit=self.shutdown,
            tooltip=hotkey_label,
            # Passed as a callable, not a list: the menu re-reads prompts.json
            # each time it opens, so an edit lands without a restart.
            prompts_getter=prompts_mod.load,
            on_prompt=self.insert_prompt,
            on_edit_prompts=self.edit_prompts,
            on_compose=self.open_compose,
        )
        self.orb.level_getter = lambda: self.recorder.level
        self.orb.set_state(PROCESSING)  # spinner until the model is loaded

        self._start_threads()
        threading.Thread(target=self._load_engine, daemon=True).start()

        try:
            self.orb.run()
        except KeyboardInterrupt:
            self.shutdown()


def run_selftest(config):
    """Record a fixed clip and print the result without touching the clipboard."""
    from .transcriber import WhisperEngine

    engine = WhisperEngine(config)
    engine.load()

    seconds = 5
    print(f"\nSpeak Romanian for {seconds} seconds, starting now...")
    clip = audio_mod.record_fixed(config, seconds)
    print("Done recording, translating...\n")

    text = engine.translate(clip)
    print("-" * 62)
    print(text if text else "(nothing recognised)")
    print("-" * 62)
    return 0 if text else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="relay",
        description="Dictate in Romanian, get an English prompt where you're typing.",
    )
    parser.add_argument("--config", help="path to a config.json")
    parser.add_argument("--list-devices", action="store_true", help="show audio inputs and exit")
    parser.add_argument(
        "--mic-test", action="store_true", help="measure signal level on every input"
    )
    parser.add_argument(
        "--selftest", action="store_true", help="record 5s, print the translation, don't paste"
    )
    parser.add_argument(
        "--write-config", action="store_true", help="write a config.json with the defaults"
    )
    parser.add_argument("--check-cuda", action="store_true", help="report GPU availability and exit")
    parser.add_argument("--no-ui", action="store_true", help="hotkey only, no floating orb")
    args = parser.parse_args(argv)

    if args.list_devices:
        audio_mod.list_devices()
        return 0

    if args.mic_test:
        return audio_mod.mic_test(load_config(args.config))

    if args.write_config:
        path = write_default_config()
        print(f"wrote {path}")
        return 0

    if args.check_cuda:
        from .cuda_setup import cuda_device_count

        added = enable_cuda_dlls(verbose=True)
        count = cuda_device_count()
        print(f"  DLL directories registered: {len(added)}")
        print(f"  CUDA devices visible to ctranslate2: {count}")
        print("  -> GPU ready" if count else "  -> no GPU; will run on CPU")
        return 0 if count else 1

    config = load_config(args.config)

    if args.selftest:
        return run_selftest(config)

    from .single_instance import already_running

    if already_running():
        print("[!!] relay is already running; not starting a second copy")
        return 1

    print(f"\n=== relay started {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    set_app_id()
    VoicePrompt(config).run(show_ui=not args.no_ui)
    return 0


if __name__ == "__main__":
    sys.exit(main())
