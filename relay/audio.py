"""Microphone capture that follows whatever input device is currently active."""

import queue
import threading

import numpy as np
import sounddevice as sd

# Put on the phrase queue to mark the end of a dictation session.
END_OF_SESSION = object()


def refresh_devices():
    """Re-enumerate audio devices so we see the *current* Windows default.

    PortAudio snapshots the device list when it initialises. Without this,
    a tool started while using the laptop mic keeps recording from the laptop
    mic even after you connect a Bluetooth headset. Tearing PortAudio down and
    bringing it back up is the supported way to pick up device changes.
    """
    try:
        sd._terminate()
        sd._initialize()
    except Exception as exc:  # never let a refresh failure kill a recording
        print(f"[audio] device refresh failed ({exc}); using cached device list")


def current_input_device():
    """(index, info) for the input device we should record from right now."""
    index = sd.default.device[0]
    info = sd.query_devices(index, "input")
    return index, info


def list_devices():
    """Print all input devices, marking the current default."""
    refresh_devices()
    default_index = sd.default.device[0]
    print("\nInput devices:\n")
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        marker = " <-- default" if idx == default_index else ""
        host = sd.query_hostapis(dev["hostapi"])["name"]
        print(
            f"  [{idx:>2}] {dev['name']}  "
            f"({dev['max_input_channels']} ch, {int(dev['default_samplerate'])} Hz, {host})"
            f"{marker}"
        )
    print()


def _resample(audio, from_rate, to_rate):
    """Rate-convert mono float32 audio, preferring scipy's polyphase filter."""
    if from_rate == to_rate:
        return audio
    try:
        from math import gcd

        from scipy.signal import resample_poly

        divisor = gcd(int(from_rate), int(to_rate))
        return resample_poly(audio, to_rate // divisor, from_rate // divisor).astype(np.float32)
    except ImportError:
        # Linear interpolation is worse for ASR but better than failing outright.
        duration = len(audio) / from_rate
        target_len = int(duration * to_rate)
        return np.interp(
            np.linspace(0.0, duration, target_len, endpoint=False),
            np.linspace(0.0, duration, len(audio), endpoint=False),
            audio,
        ).astype(np.float32)


class AudioRecorder:
    """Open-ended recording: start(), then stop() whenever the user toggles off."""

    def __init__(self, config):
        self.config = config
        self._stream = None
        self._blocks = []
        self._lock = threading.Lock()
        self._stream_rate = config.sample_rate
        self._overrun = False
        self.device_name = None
        # Smoothed 0..1 loudness, read by the overlay to animate while you talk.
        self.level = 0.0

        # Streaming: completed phrases land here as raw (audio, rate) pairs.
        self.streaming = bool(config.streaming)
        self.phrases = queue.Queue()
        self._phrase_blocks = []
        self._silence_run = 0.0
        self._phrase_seconds = 0.0
        self._speech_seconds = 0.0
        self._had_speech = False
        self._noise_floor = None

    @property
    def is_recording(self):
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status):
        # Speech rms sits around 0.01-0.2; sqrt-scale it so quiet talking still
        # moves the orb, then smooth so it pulses instead of flickering.
        rms = float(np.sqrt(np.mean(np.square(indata.astype(np.float64)))))
        target = min(1.0, (rms / 0.15) ** 0.5)
        self.level += (target - self.level) * 0.35

        if self.streaming:
            self._segment(indata, frames, rms)
            return

        with self._lock:
            max_frames = self.config.max_recording_seconds * self._stream_rate
            if sum(len(b) for b in self._blocks) >= max_frames:
                self._overrun = True
                return
            self._blocks.append(indata.copy())

    def _is_speech(self, rms):
        """Adaptive voice detection.

        A fixed threshold cannot serve both a loud headset and a quiet built-in
        array, so track the noise floor instead. Two rules keep it honest:

        - Start at the absolute floor, not at the first block. Seeding it with
          whatever arrives first means talking immediately after clicking sets
          the floor to your own voice, and nothing is ever heard as speech.
        - Only adapt during silence. Letting speech raise the floor makes a long
          uninterrupted sentence gradually mute itself.
        """
        multiplier = max(self.config.vad_speech_multiplier, 1e-6)
        if self._noise_floor is None:
            self._noise_floor = self.config.vad_min_rms / multiplier

        threshold = max(self._noise_floor * multiplier, self.config.vad_min_rms)
        speaking = rms > threshold

        if not speaking:
            # Drop towards a quieter room quickly, creep upwards slowly.
            rate = 0.20 if rms < self._noise_floor else 0.05
            self._noise_floor += (rms - self._noise_floor) * rate

        return speaking

    def _segment(self, indata, frames, rms):
        """Accumulate audio and emit a phrase once you pause. Runs in the
        PortAudio callback, so it stays to arithmetic and a queue put."""
        block_seconds = frames / float(self._stream_rate)
        speaking = self._is_speech(rms)

        self._phrase_blocks.append(indata.copy())
        self._phrase_seconds += block_seconds

        if speaking:
            self._had_speech = True
            self._silence_run = 0.0
            self._speech_seconds += block_seconds
        else:
            self._silence_run += block_seconds

        if not self._had_speech:
            # Keep only a short run-up of leading silence, or an idle mic would
            # grow the buffer forever waiting for you to start.
            while self._phrase_seconds > 1.0 and len(self._phrase_blocks) > 1:
                dropped = self._phrase_blocks.pop(0)
                self._phrase_seconds -= len(dropped) / float(self._stream_rate)
            return

        ended = self._silence_run >= self.config.phrase_silence_seconds
        too_long = self._phrase_seconds >= self.config.max_phrase_seconds
        if ended or too_long:
            self._emit_phrase()

    def _emit_phrase(self):
        """Hand the buffered phrase to the worker and start a fresh one."""
        blocks, self._phrase_blocks = self._phrase_blocks, []
        self._phrase_seconds = 0.0
        spoken, self._speech_seconds = self._speech_seconds, 0.0
        self._silence_run = 0.0
        self._had_speech = False

        # Measure the speech itself, not the buffer: a cough wrapped in silence
        # easily clears a duration check applied to the whole segment, and
        # Whisper will happily hallucinate a sentence out of it.
        if not blocks or spoken < self.config.min_phrase_seconds:
            return
        audio = np.concatenate(blocks, axis=0).flatten().astype(np.float32)
        self.phrases.put((audio, self._stream_rate))

    def get_phrase(self, timeout=0.25):
        """Next completed phrase, resampled for Whisper. Resampling happens
        here rather than in the callback to keep the audio thread free."""
        item = self.phrases.get(timeout=timeout)
        if item is END_OF_SESSION:
            return END_OF_SESSION
        audio, rate = item
        return _resample(audio, rate, self.config.sample_rate)

    def start(self):
        """Begin capturing from the device that is active at this moment."""
        if self._stream is not None:
            return

        refresh_devices()
        device_index, device_info = current_input_device()
        self.device_name = device_info["name"]

        with self._lock:
            self._blocks = []
        self._overrun = False
        self._phrase_blocks = []
        self._phrase_seconds = 0.0
        self._silence_run = 0.0
        self._had_speech = False
        self._noise_floor = None

        errors = []
        for device, rate in self._open_candidates(device_index, device_info):
            try:
                self._stream = sd.InputStream(
                    samplerate=rate,
                    channels=1,          # PortAudio downmixes multi-capsule mic arrays
                    dtype="float32",
                    device=device,
                    callback=self._callback,
                    # ~50ms blocks: fine enough to catch a pause promptly and to
                    # drive the orb smoothly, coarse enough to stay cheap.
                    blocksize=int(rate * 0.05),
                )
                self._stream.start()
                self._stream_rate = rate
                if device is not None and device != device_index:
                    self.device_name = sd.query_devices(device, "input")["name"]
                    print(f"[audio] fell back to [{device}] {self.device_name} @ {rate} Hz")
                elif device is None:
                    self.device_name = f"system default @ {rate} Hz"
                    print(f"[audio] fell back to the system mapper @ {rate} Hz")
                return
            except Exception as exc:
                self._stream = None
                errors.append(f"    device={device} rate={rate}: {exc}")

        # Report the real reasons. Swallowing them turns a fixable driver
        # problem into an unexplained "could not start recording".
        raise RuntimeError(
            f"no input could be opened ({self.device_name}):\n" + "\n".join(errors[-6:])
        )

    def _open_candidates(self, device_index, device_info):
        """Device/rate pairs to try, best first.

        A Bluetooth headset often advertises a hands-free mic that its MME
        endpoint then refuses to open, while the same hardware works through
        another host API or through the system mapper. Trying only the default
        device at one or two rates leaves you with no microphone at all.
        """
        native = int(device_info["default_samplerate"])
        target = self.config.sample_rate
        pairs = []

        configured = self.config.input_device
        if configured is not None:
            info = sd.query_devices(configured, "input")
            pairs += [(configured, target), (configured, int(info["default_samplerate"]))]
        else:
            pairs += [(device_index, target), (device_index, native)]
            # The same hardware exposed through DirectSound or WASAPI.
            name = device_info["name"].strip().lower()
            for idx, dev in enumerate(sd.query_devices()):
                if idx == device_index or dev["max_input_channels"] < 1:
                    continue
                if dev["name"].strip().lower() == name:
                    pairs += [(idx, target), (idx, int(dev["default_samplerate"]))]
            # Finally let PortAudio pick for itself.
            pairs += [(None, native), (None, 44100), (None, 48000), (None, target)]

        seen, unique = set(), []
        for pair in pairs:
            if pair not in seen:
                seen.add(pair)
                unique.append(pair)
        return unique

    def stop(self):
        """Stop capturing and return mono float32 audio at the configured rate.

        Returns None if nothing usable was captured.
        """
        if self._stream is None:
            return None

        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
            self.level = 0.0

        if self.streaming:
            # Flush whatever you were mid-sentence on, then tell the worker the
            # session is over so it can restore the clipboard.
            self._emit_phrase()
            self.phrases.put(END_OF_SESSION)
            return None

        with self._lock:
            blocks, self._blocks = self._blocks, []

        if not blocks:
            return None
        if self._overrun:
            print(f"[audio] hit the {self.config.max_recording_seconds}s cap; truncated")

        audio = np.concatenate(blocks, axis=0).flatten().astype(np.float32)
        if len(audio) / self._stream_rate < self.config.min_recording_seconds:
            return None

        return _resample(audio, self._stream_rate, self.config.sample_rate)


def mic_test(config, seconds=1.0):
    """Report the signal level of every input, to spot a muted mic.

    A muted endpoint still opens and still returns samples, so a plain "no
    speech detected" is ambiguous. Comparing levels across devices is not:
    the Windows mixer paths go quiet while the raw WDM-KS path does not.
    """
    refresh_devices()
    print("\nWorking mic in a quiet room: rms ~0.0005 or above.")
    print("Muted mic or level 0:        rms ~0.00003 (one 16-bit LSB).\n")
    print(f"{'idx':>4} {'rms':>10} {'peak':>10}  device")
    print("-" * 74)

    working = []
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        name = dev["name"].replace("\n", " ")[:42]
        for rate in (config.sample_rate, int(dev["default_samplerate"])):
            try:
                block = sd.rec(int(seconds * rate), samplerate=rate,
                               channels=1, dtype="float32", device=index)
                sd.wait()
                # float64: some endpoints hand back values that overflow float32
                # when squared, which would turn the check into a RuntimeWarning.
                block = block.flatten().astype(np.float64)
                rms = float(np.sqrt(np.mean(np.square(block))))
                peak = float(np.abs(block).max())
                # Real float32 audio stays within [-1, 1]. Some WDM-KS endpoints
                # hand back raw bytes that reinterpret as huge floats; that is
                # not a usable capture, so don't offer it as one.
                if not np.isfinite(peak) or peak > 1.5:
                    print(f"{index:>4} {'garbage':>10} {'garbage':>10}  {name}  (not real audio)")
                    break
                flag = ""
                if rms > 0.0002:
                    flag = "  <== signal"
                    working.append((index, name))
                print(f"{index:>4} {rms:>10.6f} {peak:>10.6f}  {name}{flag}")
                break
            except Exception as exc:
                if rate != config.sample_rate:
                    print(f"{index:>4} {'-':>10} {'-':>10}  {name}  ({str(exc)[:26]})")

    print()
    if not working:
        print("No input produced any signal. The mic is muted or its level is 0:")
        print("  - the mic-mute key on your laptop (often F4, with an LED)")
        print("  - Settings > System > Sound > Microphone > Input volume")
        print("  - mmsys.cpl > Recording > Properties > Levels")
        return 1
    print("Inputs with signal: " + ", ".join(f"[{i}] {n}" for i, n in working))
    print('Set "input_device" in config.json to force one of these.')
    return 0


def record_fixed(config, seconds):
    """Blocking fixed-length capture, used by --selftest."""
    refresh_devices()
    _, info = current_input_device()
    print(f"[audio] recording {seconds}s from: {info['name']}")
    rate = config.sample_rate
    try:
        audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="float32")
        sd.wait()
    except Exception:
        rate = int(info["default_samplerate"])
        audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="float32")
        sd.wait()
    return _resample(audio.flatten().astype(np.float32), rate, config.sample_rate)
