"""Romanian text to English text, without the model.

The marian model is a download and a GPU load, and none of what can go wrong
here needs it: the sentence splitting, the line breaks coming back where you
typed them, and the cache that stops a paragraph being re-translated from the
top on every keystroke are all arithmetic and bookkeeping.

So the runtime is stood in for by something that upper-cases. What is being
checked is that the right sentences are sent, once each, and that the answer is
put back together in the shape it went in.
"""
import sys

import context  # noqa: E402,F401
context.isolate_state()

from relay import translator as mt  # noqa: E402
from relay.translator import TextTranslator, split_lines  # noqa: E402

report = context.Report()
check = report.check


class Hypothesis:
    def __init__(self, tokens):
        self.hypotheses = [tokens]


class Runtime:
    """Stands in for the CTranslate2 translator. Counts what it was asked."""

    def __init__(self):
        self.batches = []
        self.sentences = []

    def translate_batch(self, batch, **kwargs):
        self.batches.append(batch)
        out = []
        for tokens in batch:
            words = [t for t in tokens if t != mt.END_TOKEN]
            self.sentences.append(" ".join(words))
            out.append(Hypothesis([w.upper() for w in words]))
        return out


class Pieces:
    """Stands in for sentencepiece: words in, words out."""

    @staticmethod
    def encode(text, out_type=str):
        return text.split()

    @staticmethod
    def decode(tokens):
        return " ".join(tokens)


def build():
    engine = TextTranslator(config=None)
    engine._translator = Runtime()
    engine._source = Pieces()
    engine._target = Pieces()
    return engine, engine._translator


print("\n--- sentences ---")
check("one sentence", split_lines("Salut acolo.") == [["Salut acolo."]],
      str(split_lines("Salut acolo.")))
check("two on a line",
      split_lines("Unu. Doi!") == [["Unu.", "Doi!"]], str(split_lines("Unu. Doi!")))
check("a blank line stays blank",
      split_lines("Unu.\n\nDoi.") == [["Unu."], [], ["Doi."]],
      str(split_lines("Unu.\n\nDoi.")))
check("nothing at all", split_lines("") == [[]], str(split_lines("")))
check("a line with no full stop is still a sentence",
      split_lines("fara punct") == [["fara punct"]], str(split_lines("fara punct")))


print("\n--- the shape you typed comes back ---")
engine, runtime = build()
out = engine.translate("unu doi.\n\ntrei patru.")
check("the blank line survives", out == "UNU DOI.\n\nTREI PATRU.", repr(out))

engine, runtime = build()
out = engine.translate("unu. doi.\ntrei.")
check("and so does the line break", out == "UNU. DOI.\nTREI.", repr(out))
check("both sentences on the first line rejoined",
      out.splitlines()[0] == "UNU. DOI.", repr(out.splitlines()[0]))


print("\n--- empty input is not sent to the model ---")
engine, runtime = build()
check("nothing", engine.translate("") == "")
check("spaces", engine.translate("   \n  ") == "")
check("and the model was never asked", runtime.batches == [], str(runtime.batches))


print("\n--- each sentence is translated once ---")
# Typing a paragraph re-translates it from the top on every pause. Without the
# cache the first sentence goes through the model once per keystroke pause.
engine, runtime = build()
engine.translate("unu doi.")
engine.translate("unu doi. trei patru.")
engine.translate("unu doi. trei patru. cinci.")
check("three calls, five sentences, three actually translated",
      len(runtime.sentences) == 3, str(runtime.sentences))
check("and the answer is still whole",
      engine.translate("unu doi. trei patru. cinci.") == "UNU DOI. TREI PATRU. CINCI.",
      engine.translate("unu doi. trei patru. cinci."))

engine, runtime = build()
engine.translate("aceeasi. aceeasi. aceeasi.")
check("a sentence repeated in one go is sent once",
      len(runtime.sentences) == 1, str(runtime.sentences))
check("and appears everywhere it was typed",
      engine.translate("aceeasi. aceeasi. aceeasi.") == "ACEEASI. ACEEASI. ACEEASI.")


print("\n--- the cache does not grow without end ---")
engine, runtime = build()
for i in range(mt.CACHE_SENTENCES + 120):
    engine.translate(f"propozitia numarul {i}.")
check("it stops at the limit", len(engine._cache) <= mt.CACHE_SENTENCES,
      str(len(engine._cache)))


print("\n--- and a long document does not fall through the gap it leaves ---")
# The answer used to be assembled by reading each sentence back out of the
# cache. A document with more sentences than the cache holds evicts its own
# earlier sentences while the later ones are still being stored, and the
# assembly then asks for one that is no longer there - a KeyError, from the
# window, on a paste that was perfectly valid.
engine, runtime = build()
document = " ".join(f"propozitia {i}." for i in range(mt.CACHE_SENTENCES + 50))
try:
    out = engine.translate(document)
    failed = None
except Exception as exc:
    out, failed = "", f"{type(exc).__name__}: {exc}"
check("it comes back at all", failed is None, str(failed))
check("with every sentence in it",
      out.count("PROPOZITIA") == mt.CACHE_SENTENCES + 50,
      str(out.count("PROPOZITIA")))

print("\n--- including one it had already seen and since forgotten ---")
engine, runtime = build()
engine.translate("prima propozitie.")
filler = " ".join(f"umplutura {i}." for i in range(mt.CACHE_SENTENCES + 10))
engine.translate(filler)
check("the first one has been evicted", "prima propozitie." not in engine._cache)
try:
    out = engine.translate("prima propozitie. si inca una.")
    failed = None
except Exception as exc:
    out, failed = "", f"{type(exc).__name__}: {exc}"
check("asking for it again works", failed is None, str(failed))
check("and it is translated afresh", out == "PRIMA PROPOZITIE. SI INCA UNA.", repr(out))


print("\n--- a model that will not load says so rather than lying ---")
# Handing back the Romanian would look like a translation that came out oddly.
engine = TextTranslator(config=None)
engine.load = lambda: False
engine.error = "no CUDA and no CPU fallback"
try:
    engine.translate("ceva")
    raised = None
except Exception as exc:
    raised = str(exc)
check("it raises", raised is not None, str(raised))
check("and says why", raised and "CUDA" in raised, str(raised))
check("ready is honest", engine.ready is False)


print("\n--- and one that is loaded is only loaded once ---")
engine, runtime = build()
loads = []
engine._load_locked = lambda: loads.append(True)
check("already loaded", engine.load() is True)
check("nothing was reloaded", loads == [], str(loads))

sys.exit(report.finish())
