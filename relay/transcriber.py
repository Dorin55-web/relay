"""Whisper wrapper: Romanian speech in, English text out, in a single pass."""

import time

import numpy as np

from .cuda_setup import enable_cuda_dlls

# Register the CUDA DLL directories before faster_whisper pulls in ctranslate2.
enable_cuda_dlls()

from faster_whisper import WhisperModel  # noqa: E402  (import order is deliberate)

# Whisper invents filler on silent or near-silent audio. These are the phrases it
# reaches for most often; if a result is nothing but one of them, it is not speech.
HALLUCINATIONS = {
    "thank you.", "thank you", "thanks for watching!", "thanks for watching.",
    "you", "you.", ".", "bye.", "bye", "so", "so.", "okay.", "okay",
    "please subscribe.", "subtitles by the amara.org community",
    "subs by www.zeoranger.co.uk", "amara.org", "the end.", "[music]", "(music)",
}

# At or below roughly one 16-bit LSB (1/32768) the capture path is producing
# quantisation noise only - a muted mic, not a quiet room.
MUTED_RMS = 0.00005

# Tried in order until one loads; keeps the tool usable without a working GPU.
FALLBACK_CHAIN = [
    ("cuda", "float16"),
    ("cuda", "int8_float16"),
    ("cpu", "int8"),
]


def _is_hallucination(text):
    return text.strip().lower().strip("\"'") in HALLUCINATIONS


class WhisperEngine:
    def __init__(self, config):
        self.config = config
        self.model = None
        self.device = None
        self.compute_type = None

    def load(self):
        """Load the model, degrading gracefully if CUDA is unavailable."""
        requested = (self.config.device, self.config.compute_type)
        chain = [requested] + [c for c in FALLBACK_CHAIN if c != requested]
        if self.config.device == "cpu":
            chain = [c for c in chain if c[0] == "cpu"]

        errors = []
        for device, compute_type in chain:
            try:
                print(f"[whisper] loading {self.config.model_size} on {device}/{compute_type} ...")
                started = time.time()
                model = WhisperModel(
                    self.config.model_size,
                    device=device,
                    compute_type=compute_type,
                )
                self.model = model
                self.device = device
                self.compute_type = compute_type
                print(f"[whisper] ready in {time.time() - started:.1f}s ({device}/{compute_type})")
                if device == "cpu" and self.config.device != "cpu":
                    print("[whisper] NOTE: running on CPU - expect several seconds per dictation")
                self._warmup()
                return
            except Exception as exc:
                errors.append(f"{device}/{compute_type}: {exc}")

        raise RuntimeError("could not load Whisper.\n  " + "\n  ".join(errors))

    def _warmup(self):
        """Run one throwaway inference so the first real dictation isn't slow.

        Kernel compilation and buffer allocation happen on the first call; without
        this the first dictation takes ~10s and looks like the tool has hung.
        """
        started = time.time()
        silence = np.zeros(self.config.sample_rate, dtype=np.float32)
        segments, _ = self.model.transcribe(silence, language=self.config.source_language)
        list(segments)  # the generator is lazy; consume it to force the work
        print(f"[whisper] warmed up in {time.time() - started:.1f}s")

    def translate(self, audio):
        """Audio -> English text. Returns "" when there is nothing worth pasting."""
        if audio is None or len(audio) == 0:
            return ""

        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < MUTED_RMS:
            # One 16-bit LSB is 1/32768 = 0.0000305. Reading at or below that
            # means the capture path delivered quantisation noise and nothing
            # else, which is a muted endpoint rather than a quiet room.
            print(
                f"[whisper] microphone delivered no signal (rms {rms:.6f}).\n"
                "          The mic is muted or its level is 0 - check the mic-mute\n"
                "          key on your laptop, or mmsys.cpl > Recording > Levels."
            )
            return ""
        if rms < self.config.silence_rms_threshold:
            print(f"[whisper] audio is silent (rms {rms:.5f}); skipping")
            return ""

        started = time.time()
        segments, info = self.model.transcribe(
            audio,
            task=self.config.task,           # "translate" -> English regardless of input
            language=self.config.source_language,
            beam_size=self.config.beam_size,
            vad_filter=True,                 # drop silence; cuts hallucinations and time
            condition_on_previous_text=False,  # otherwise short clips loop on repeats
        )

        parts = [seg.text for seg in segments if not _is_hallucination(seg.text)]
        text = " ".join(part.strip() for part in parts).strip()
        text = " ".join(text.split())  # collapse the whitespace Whisper leaves behind

        elapsed = max(time.time() - started, 1e-6)
        audio_seconds = len(audio) / self.config.sample_rate
        print(
            f"[whisper] {audio_seconds:.1f}s audio -> {elapsed:.1f}s "
            f"({audio_seconds / elapsed:.1f}x realtime)"
        )

        if not text or _is_hallucination(text):
            return ""
        return text
