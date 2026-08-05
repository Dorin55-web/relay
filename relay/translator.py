"""Romanian text to English text, offline.

Whisper translates *audio*: its translate task takes sound, not letters, so it
cannot help with anything you type. This is a separate, much smaller model - an
OPUS-MT Romance-to-English marian - run on the CTranslate2 runtime that
faster-whisper already brings in, so nothing new is needed but a tokenizer.

It is not as good as Whisper on speech. It works a sentence at a time with no
wider context, so an ambiguous word gets the common reading rather than the one
your paragraph implies. Read what comes out before you send it.
"""

import re
import threading

MODEL_REPO = "gaudi/opus-mt-roa-en-ctranslate2"

# Marian will loop on itself forever without these: the end token tells it where
# your sentence stops, and the penalties stop it repeating a clause it likes.
END_TOKEN = "</s>"
BEAM_SIZE = 4
REPETITION_PENALTY = 1.1
NO_REPEAT_NGRAM = 4
MAX_DECODING_LENGTH = 256

# Sentences remembered between calls. A long document is a few dozen; this is
# generous enough that nothing you are working on falls out, and small enough
# that it cannot grow without bound over a long session.
CACHE_SENTENCES = 400

# Sentence at a time: the model was trained that way, and feeding it a whole
# paragraph makes it drop clauses off the end.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def split_lines(text):
    """Each line as its list of sentences; a blank line as an empty list.

    Two levels rather than one flat list, so the line breaks you typed come
    back where you put them. Flattening loses which sentences shared a line.
    """
    return [
        [part for part in _SENTENCE_END.split(line.strip()) if part]
        if line.strip() else []
        for line in text.split("\n")
    ]


class TextTranslator:
    """Loads on first use, not at start-up.

    Most sessions never type anything - they dictate - and there is no reason
    to spend the VRAM or the seconds until someone actually opens the window.
    """

    def __init__(self, config=None):
        self.config = config
        self._translator = None
        self._source = None
        self._target = None
        self._lock = threading.Lock()
        # Insertion-ordered, so the oldest entry is the one that goes.
        self._cache = {}
        self.device = None
        self.error = None

    @property
    def ready(self):
        return self._translator is not None

    def load(self):
        """Download if needed, then load. Safe to call from several threads."""
        with self._lock:
            if self._translator is not None:
                return True
            try:
                self._load_locked()
                return True
            except Exception as exc:
                self.error = str(exc)
                print(f"[mt] could not load the text translator: {exc}")
                return False

    def _load_locked(self):
        import ctranslate2
        import sentencepiece as spm
        from huggingface_hub import snapshot_download

        print(f"[mt] loading {MODEL_REPO} ...")
        path = snapshot_download(MODEL_REPO)

        self._source = spm.SentencePieceProcessor(model_file=f"{path}/source.spm")
        self._target = spm.SentencePieceProcessor(model_file=f"{path}/target.spm")

        # Same ladder as the speech model, for the same reason: a machine with
        # no usable GPU should still get a working translator, just slower.
        attempts = [("cuda", "float16"), ("cuda", "int8_float16"), ("cpu", "int8")]
        errors = []
        for device, compute_type in attempts:
            try:
                self._translator = ctranslate2.Translator(
                    path, device=device, compute_type=compute_type
                )
                self.device = f"{device}/{compute_type}"
                print(f"[mt] ready on {self.device}")
                return
            except Exception as exc:
                errors.append(f"{device}/{compute_type}: {exc}")
        raise RuntimeError("no usable device:\n  " + "\n  ".join(errors))

    def translate(self, text):
        """Romanian in, English out. Returns '' for empty input.

        Raises if the model cannot be loaded, so the caller can say so rather
        than silently handing back the Romanian it was given.
        """
        if not text or not text.strip():
            return ""
        if self._translator is None and not self.load():
            raise RuntimeError(self.error or "the text translator is not available")

        lines = split_lines(text)
        payload = [sentence for line in lines for sentence in line]
        if not payload:
            return ""

        # Typing a paragraph re-translates it from the top on every pause, so
        # by the last clause the earlier sentences have been through the model
        # several times over for the same answer. Only the ones never seen
        # before are sent.
        fresh = [s for s in dict.fromkeys(payload) if s not in self._cache]
        if fresh:
            batch = [self._source.encode(s, out_type=str) + [END_TOKEN]
                     for s in fresh]
            with self._lock:
                results = self._translator.translate_batch(
                    batch,
                    beam_size=BEAM_SIZE,
                    repetition_penalty=REPETITION_PENALTY,
                    no_repeat_ngram_size=NO_REPEAT_NGRAM,
                    max_decoding_length=MAX_DECODING_LENGTH,
                )
            for sentence, result in zip(fresh, results):
                self._remember(sentence, self._target.decode(result.hypotheses[0]))

        # Sentences rejoin within their line; lines rejoin with the breaks you
        # typed, blank ones included.
        return "\n".join(
            " ".join(self._cache[s] for s in line) for line in lines
        )

    def _remember(self, source, english):
        """Cache one sentence, oldest out first once the window is full."""
        self._cache[source] = english
        while len(self._cache) > CACHE_SENTENCES:
            self._cache.pop(next(iter(self._cache)))
