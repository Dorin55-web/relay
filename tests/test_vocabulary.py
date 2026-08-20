"""The words Whisper is told to expect, and what they must not cost.

Whether a vocabulary makes the model hear "Antigravity" instead of "anti
gravity" cannot be checked here. That needs a person, a microphone and the
model loaded. What can be checked is everything around it, and that is where
this setting can do harm rather than good.

An initial_prompt is context prepended to the decoder, not a filter. It goes to
the model on the same call as the audio, it can come back out as though it had
been heard, and faster-whisper drops the front of one that is too long without
saying so. So most of what is checked here is negative: that an empty
vocabulary leaves the call exactly as it was, that the warm-up never gets one,
that a list too long to fit is cut where you can see it, and that a whole list
read back is not pasted into the box you were dictating into.

The model is stood in for. Nothing here loads Whisper or opens a microphone.
"""
import contextlib
import inspect
import io
import json
import sys
import tempfile
from pathlib import Path

import context  # noqa: E402,F401
context.isolate_state()

import numpy as np                              # noqa: E402
from relay import config as config_mod          # noqa: E402
from relay import transcriber as whisper        # noqa: E402
from relay.config import DEFAULTS, load_config  # noqa: E402

report = context.Report()
check = report.check

HERE = Path(tempfile.mkdtemp(prefix="relay-vocab-"))

# Loud enough to clear both rms gates in translate(). The model is stood in
# for, so what the samples are beyond that does not matter.
SPEECH = np.full(16000, 0.05, dtype=np.float32)

UNSET = object()   # so a vocabulary of null can be asked for on purpose


class Segment:
    def __init__(self, text):
        self.text = text


class Model:
    """Stands in for WhisperModel: records the call, says what it is told to."""

    def __init__(self):
        self.calls = []
        self.says = ["salut"]

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return iter([Segment(t) for t in self.says]), None


def quietly(fn, *args, **kwargs):
    """Run it, and hand back what it printed as well as what it returned.

    transcriber prints a timing line per dictation and a complaint about a
    vocabulary that will not fit. Both are wanted in the log and neither is
    wanted running down the middle of this suite's own output - and one of
    them is the thing being checked, which needs capturing anyway.
    """
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        out = fn(*args, **kwargs)
    return out, said.getvalue()


def engine(vocabulary=UNSET, **overrides):
    """A WhisperEngine on the defaults, with the model already stood in for."""
    cfg = load_config(HERE / "no-such-file.json")
    if vocabulary is not UNSET:
        cfg["vocabulary"] = vocabulary
    cfg.update(overrides)
    eng, _ = quietly(whisper.WhisperEngine, cfg)
    eng.model = Model()
    return eng


def prompt_for(vocabulary):
    """What the model is handed as initial_prompt for this setting."""
    eng = engine(vocabulary=vocabulary)
    quietly(eng.translate, SPEECH)
    return eng.model.calls[0]["initial_prompt"]


print("\n--- the setting exists, and is empty until asked for ---")
check("it is a default", "vocabulary" in DEFAULTS)
check("and the default is nothing", DEFAULTS["vocabulary"] == [],
      str(DEFAULTS["vocabulary"]))
check("a machine with no config.json gets none",
      load_config(HERE / "nothing-here.json").vocabulary == [],
      str(load_config(HERE / "nothing-here.json").vocabulary))

path = HERE / "mine.json"
path.write_text(json.dumps({"vocabulary": ["Antigravity", "opencode"]}),
                encoding="utf-8")
check("and one that names words gets those",
      load_config(path).vocabulary == ["Antigravity", "opencode"],
      str(load_config(path).vocabulary))

# Not for its value, but so somebody opening the file can see the key is there
# to be filled in. A setting nobody can find is a setting nobody uses.
shipped = json.loads(config_mod.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
check("the shipped config.json names it", "vocabulary" in shipped,
      str(sorted(shipped))[:60])


print("\n--- an empty vocabulary changes nothing about the call ---")
# The whole point of the default. An empty string is not the same as None:
# faster-whisper tokenises whatever it is given and prepends it, so "" would
# put a stray token in front of every dictation on every machine.
eng = engine()
quietly(eng.translate, SPEECH)
call = eng.model.calls[0]
check("the engine holds no vocabulary", eng.vocabulary == "", repr(eng.vocabulary))
check("and hands the model None rather than that empty string",
      call["initial_prompt"] is None, repr(call["initial_prompt"]))
check("nothing else about the call moved",
      {k: v for k, v in call.items() if k != "initial_prompt"} == {
          "task": "translate", "language": "ro", "beam_size": 1,
          "vad_filter": True, "condition_on_previous_text": False},
      str(call))

for empty in ([], "", "   ", [""], ["  ", ""], None, 7, {"a": 1}):
    check(f"{empty!r} is no vocabulary", prompt_for(empty) is None,
          repr(prompt_for(empty)))


print("\n--- and a vocabulary reaches the model as one comma-separated line ---")
check("the words go through",
      prompt_for(["Antigravity", "opencode", "autopilot"])
      == "Antigravity, opencode, autopilot",
      repr(prompt_for(["Antigravity", "opencode", "autopilot"])))
check("a string is taken as written too",
      prompt_for("Relay, Telegram") == "Relay, Telegram",
      repr(prompt_for("Relay, Telegram")))

# Hand-edited JSON. A number or a null in the list must not take the load down
# while Whisper is starting, where there is no orb yet to report it with.
messy = ["Relay", 7, None, "  ", {"x": 1}, "Telegram"]
check("what is not a word is skipped, and the words survive",
      prompt_for(messy) == "Relay, Telegram", repr(prompt_for(messy)))

eng = engine(vocabulary=["Antigravity"])
quietly(eng.translate, SPEECH)
check("and the task is still translate", eng.model.calls[0]["task"] == "translate",
      eng.model.calls[0]["task"])


print("\n--- every phrase gets it, not just the first ---")
# Streaming calls transcribe() once per phrase, so this is what "it works while
# you are still talking" actually means.
eng = engine(vocabulary=["Antigravity"])
for _ in range(3):
    quietly(eng.translate, SPEECH)
check("three phrases, three prompts",
      [c["initial_prompt"] for c in eng.model.calls] == ["Antigravity"] * 3,
      str([c["initial_prompt"] for c in eng.model.calls]))


print("\n--- the warm-up deliberately runs without one ---")
# It runs on a second of exact zeros with no vad_filter in front of it, which
# is the one input that makes a prompt come back as text. It would buy nothing
# either: what the warm-up pays for is kernel compilation, which a prompt does
# not change, and the run would get slower as the vocabulary grew.
eng = engine(vocabulary=["Antigravity", "opencode"])
_, said = quietly(eng._warmup)
check("the warm-up asked for no prompt", "initial_prompt" not in eng.model.calls[0],
      str(eng.model.calls[0]))
check("and still warmed up", "warmed up" in said, said.strip()[:60])
quietly(eng.translate, SPEECH)
check("while the dictation after it has one",
      eng.model.calls[1]["initial_prompt"] == "Antigravity, opencode",
      repr(eng.model.calls[1]["initial_prompt"]))


print("\n--- a list too long for Whisper's prompt is cut where you can see it ---")
# faster-whisper keeps the last 223 tokens and drops the front in silence, so
# the names listed first - the ones written down before the others - would be
# the ones that quietly stopped working.
many = [f"Antigravity{n:03d}" for n in range(80)]
long_prompt, said = quietly(whisper._vocabulary_prompt, many)
kept = long_prompt.split(", ")
check("it is cut", len(long_prompt) <= whisper.MAX_VOCABULARY_CHARS,
      str(len(long_prompt)))
check("and never mid-word", all(word in many for word in kept), long_prompt[-40:])
check("the front of the list is what survives",
      kept == many[:len(kept)], str(kept[:2]))
check("you are told that it happened", "ignoring" in said, said.strip()[:90])
check("and how much of your list is not being used",
      "of 80 entries" in said, said.strip()[:90])
check("a realistic number of names still fits", len(kept) >= 30, str(len(kept)))

short = ["Antigravity", "opencode", "commit", "repository", "autopilot",
         "Relay", "Telegram"]
out, said = quietly(whisper._vocabulary_prompt, short)
check("and a list this size is not cut at all", out == ", ".join(short), out)
check("nor complained about", said == "", repr(said))


print("\n--- the vocabulary read back is not pasted into your editor ---")
# The risk this setting brings with it. HALLUCINATIONS cannot catch it: the
# echo is made of our own words rather than "Thank you."
words = ["Antigravity", "opencode", "autopilot"]


def heard(said, vocabulary=words):
    """What translate() gives back when the model says this."""
    eng = engine(vocabulary=vocabulary)
    eng.model.says = said if isinstance(said, list) else [said]
    return quietly(eng.translate, SPEECH)[0]


for echo in ["Antigravity, opencode, autopilot",
             "antigravity opencode autopilot",
             " Antigravity, Opencode, Autopilot. ",
             "Antigravity, opencode, autopilot."]:
    got = heard(echo)
    check(f"{echo.strip()[:26]!r} is refused", got == "", repr(got))


print("\n--- but a phrase that merely uses one of the words is not ---")
# The narrowness of the rule matters more than the rule. This is the case the
# whole setting exists to make work, and eating it would be worse than the echo.
for real in ["Antigravity", "commit this to Antigravity",
             "opencode and autopilot", "Antigravity, opencode, and the rest"]:
    got = heard(real)
    check(f"{real[:30]!r} is kept", got == real, repr(got))

got = heard("Antigravity, opencode, autopilot", vocabulary=[])
check("with no vocabulary set nothing is refused for echoing",
      got == "Antigravity, opencode, autopilot", repr(got))
got = heard("Thank you.")
check("the filler Whisper always invented is still dropped", got == "", repr(got))
got = heard(["Antigravity, opencode, autopilot", " and then this"])
check("and one echoed segment does not take the rest of the phrase with it",
      got == "and then this", repr(got))


print("\n--- the silent-audio gates still come first ---")
# A prompt must never be the reason a muted microphone produces words.
eng = engine(vocabulary=words)
out, said = quietly(eng.translate, np.zeros(16000, dtype=np.float32))
check("a muted microphone is not sent to the model", eng.model.calls == [],
      str(eng.model.calls))
check("it is reported instead", "no signal" in said, said.strip()[:60])
check("and nothing came back", out == "", repr(out))


print("\n--- faster-whisper still takes the argument being passed ---")
# A version bump that renamed or dropped this would leave the setting reading,
# defaulting, capping and logging exactly as it does now, and doing nothing.
params = inspect.signature(whisper.WhisperModel.transcribe).parameters
check("initial_prompt is a parameter of transcribe()",
      "initial_prompt" in params, str(list(params)[:5]))
check("and so is the flag it has to survive",
      "condition_on_previous_text" in params, str(list(params)[:5]))

sys.exit(report.finish())
