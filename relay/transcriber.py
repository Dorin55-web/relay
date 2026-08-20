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

# Whisper's prompt slot is half its context. faster-whisper hands the decoder
# the LAST 223 tokens of the prompt and drops the front without a word, so an
# over-long vocabulary quietly stops biasing the names listed first - the ones
# you cared about enough to write down before the others. Measured against the
# large-v3 tokenizer, names of this kind run about 2.8 characters to the token,
# so 600 characters is 218 of them: just under. Past that we drop whole entries
# off the end and say which, which is at least a failure you can read.
MAX_VOCABULARY_CHARS = 600


def _is_hallucination(text):
    return text.strip().lower().strip("\"'") in HALLUCINATIONS


def _bare(text):
    """Lower case, no punctuation, single spaces - for comparing two texts."""
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def _vocabulary_prompt(words):
    """The configured vocabulary as the single string Whisper takes, or "".

    A list is the shape config.json wants, because it reads as a vocabulary and
    cannot drift into prose - and prose is the thing most likely to be echoed
    back. A comma-separated string is what somebody will write anyway, and
    quietly ignoring it would be worse than accepting it.
    """
    if isinstance(words, str):
        entries = [w.strip() for w in words.split(",")]
    elif isinstance(words, (list, tuple)):
        # Hand-edited JSON: a list can hold a number or a null, and neither is
        # a word. Skipping them beats a TypeError while the model is loading.
        entries = [w.strip() for w in words if isinstance(w, str)]
    else:
        return ""
    entries = [w for w in entries if w]

    kept, used = [], 0
    for index, entry in enumerate(entries):
        if used + len(entry) > MAX_VOCABULARY_CHARS:
            print(f"[whisper] the vocabulary is longer than Whisper's prompt holds; "
                  f"ignoring {len(entries) - index} of {len(entries)} entries, "
                  f"from {entry!r} on")
            break
        kept.append(entry)
        used += len(entry) + 2   # the ", " each one costs once they are joined
    return ", ".join(kept)


class WhisperEngine:
    def __init__(self, config):
        self.config = config
        self.model = None
        self.device = None
        self.compute_type = None
        # Built once, not per phrase: the complaint about a list too long to
        # fit belongs at start-up, not on every sentence you say.
        self.vocabulary = _vocabulary_prompt(config.get("vocabulary"))

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

        Deliberately without the vocabulary. What is being bought here is the
        kernel compilation and the buffers, and a prompt changes neither. What
        it would change is this one input: a second of exact zeros, with no
        vad_filter in front of it, which is precisely the condition under which
        a prompt comes back as text. The model would have the whole list to say
        instead of nothing, so the warm-up would get slower - and slower by an
        amount that grows with the vocabulary - to produce a result nobody
        reads. The task and beam size are left off for the same reason.
        """
        started = time.time()
        silence = np.zeros(self.config.sample_rate, dtype=np.float32)
        segments, _ = self.model.transcribe(silence, language=self.config.source_language)
        list(segments)  # the generator is lazy; consume it to force the work
        print(f"[whisper] warmed up in {time.time() - started:.1f}s")

    def _is_echo(self, text):
        """Whether Whisper read the vocabulary back instead of transcribing.

        An initial_prompt is context prepended to the decoder, not a filter, so
        given audio it cannot make anything of, the model can emit the prompt
        itself - and your own list of tool names is pasted into the box you
        were dictating into. HALLUCINATIONS does not catch that: the echo is
        made of our words, not "Thank you."

        Most of what keeps it away is upstream of here. The two rms gates and
        vad_filter mean the model is only ever asked about audio with speech in
        it, which is where echoing is rare; the vocabulary is empty unless
        somebody asks for it; and MAX_VOCABULARY_CHARS keeps a leaked one
        short. This is the last catch, and a deliberately narrow one - only the
        whole list, never a part of it, because a phrase that merely contains a
        listed name is the case this setting exists to make work.
        """
        return bool(self.vocabulary) and _bare(text) == _bare(self.vocabulary)

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
            # Bias the decoder towards names it would otherwise guess at.
            # None rather than "": an empty string is still tokenised and
            # prepended, so it would change the call for everyone who never
            # asked for a vocabulary. Prepended context and not a filter - see
            # _is_echo for what that costs and what holds it down.
            #
            # condition_on_previous_text=False above does not cancel this out.
            # faster-whisper seeds the prompt before the first 30s window and
            # only resets afterwards, and a phrase here is at most 15s: one
            # window, so the vocabulary reaches every one of them.
            initial_prompt=self.vocabulary or None,
        )

        parts = [seg.text for seg in segments
                 if not _is_hallucination(seg.text) and not self._is_echo(seg.text)]
        text = " ".join(part.strip() for part in parts).strip()
        text = " ".join(text.split())  # collapse the whitespace Whisper leaves behind

        elapsed = max(time.time() - started, 1e-6)
        audio_seconds = len(audio) / self.config.sample_rate
        print(
            f"[whisper] {audio_seconds:.1f}s audio -> {elapsed:.1f}s "
            f"({audio_seconds / elapsed:.1f}x realtime)"
        )

        if not text or _is_hallucination(text) or self._is_echo(text):
            return ""
        return text
