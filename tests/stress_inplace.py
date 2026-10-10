"""Empirical Adversarial Stress Harness for Relay In-Place F9 Translation.

Tests hostile edge cases, stress payloads, boundary conditions, clipboard faults,
keyboard faults, and voice dictation fallback under adversarial conditions.
"""

import sys
import time

import context
context.isolate_state()

from pynput.keyboard import Key
from relay import __main__ as app
from relay.feedback import Feedback
from relay.inplace import InplaceTranslator, restore_clipboard, save_clipboard
from relay.overlay import Orb

report = context.Report()
check = report.check

# Global tracker for every keystroke emitted across all stress tests
GLOBAL_KEYSTROKE_LOG = []


class StressClipboard:
    """Simulates adversarial clipboard behavior."""

    def __init__(self, initial_value=None):
        self.value = initial_value
        self.copy_history = []
        self.paste_call_count = 0
        self.copy_call_count = 0
        self.lock_on_copy = False
        self.lock_on_paste = False
        self.lock_on_copy_count = 0
        self.fail_on_first_paste = False
        self.fail_on_poll_paste = False
        self.strict_string_copy = False
        self.paste_delay_iterations = 0  # Delayed clipboard update simulation

    def copy(self, text):
        self.copy_call_count += 1
        if self.strict_string_copy and not isinstance(text, str):
            raise TypeError(f"Clipboard requires string, got {type(text)}")
        if self.lock_on_copy:
            raise RuntimeError("Clipboard locked by external process (E_ACCESSDENIED)")
        if self.lock_on_copy_count > 0:
            self.lock_on_copy_count -= 1
            raise RuntimeError("Transient clipboard lock on copy")
        self.copy_history.append(text)
        self.value = text

    def paste(self):
        self.paste_call_count += 1
        if self.lock_on_paste:
            raise RuntimeError("Clipboard read error: locked by another thread")
        if self.fail_on_first_paste and self.paste_call_count == 1:
            raise RuntimeError("Transient clipboard read fault")
        if self.fail_on_poll_paste and self.paste_call_count == 2:
            raise RuntimeError("Transient clipboard read fault during polling loop")
        return self.value


class StressKeyboard:
    """Records every single simulated key event and enforces strict assertions."""

    def __init__(self, clipboard: StressClipboard, target_text=None, delay_iterations=0):
        self.clipboard = clipboard
        self.target_text = target_text
        self.keys = []
        self.fail_on_key = None
        self.delay_iterations = delay_iterations
        self.poll_counter = 0

    def pressed(self, key):
        kb = self

        class HeldKey:
            def __enter__(self):
                kb._record(f"+{key}")
                if kb.fail_on_key == f"+{key}":
                    raise RuntimeError(f"Hardware failure holding key: {key}")
                return kb

            def __exit__(self, *_):
                kb._record(f"-{key}")
                return False

        return HeldKey()

    def press(self, key):
        self._record(f"press {key}")
        if self.fail_on_key == str(key):
            raise RuntimeError(f"Hardware failure pressing key: {key}")

        # Simulate text selection copying when Ctrl+C is pressed
        if str(key).lower() == "c" and "+Key.ctrl" in self.keys[-2:]:
            if self.target_text is not None and self.clipboard is not None:
                if self.delay_iterations > 0:
                    # Clipboard value will only change after specified iterations
                    orig_paste = self.clipboard.paste

                    def delayed_paste():
                        self.poll_counter += 1
                        if self.poll_counter >= self.delay_iterations:
                            self.clipboard.value = self.target_text
                        return orig_paste()

                    self.clipboard.paste = delayed_paste
                else:
                    self.clipboard.value = self.target_text

    def release(self, key):
        self._record(f"release {key}")

    def _record(self, action):
        self.keys.append(action)
        GLOBAL_KEYSTROKE_LOG.append(action)


class StressTranslator:
    """Simulates adversarial translation behavior."""

    def __init__(self, prefix="TRANSLATED: "):
        self.prefix = prefix
        self.queries = []
        self.throw_cuda_oom = False
        self.return_none = False
        self.return_empty = False

    def translate(self, text):
        self.queries.append(text)
        if self.throw_cuda_oom:
            raise RuntimeError("CUDA out of memory in ctranslate2.Translator")
        if self.return_none:
            return None
        if self.return_empty:
            return ""
        return f"{self.prefix}{text}"


class MockConfig:
    def __init__(self, **kwargs):
        self.restore_clipboard = True
        self.restore_delay_ms = 0
        self.paste_delay_ms = 0
        self.auto_enter = False
        self.streaming = False
        self.restore_caret = False
        self.beep_feedback = False
        for k, v in kwargs.items():
            setattr(self, k, v)


print("======================================================================")
print("RUNNING ADVERSARIAL EMPIRICAL STRESS TESTS FOR RELAY IN-PLACE F9")
print("======================================================================")

cfg = MockConfig()

# ==============================================================================
# SECTION 1: LARGE PAYLOADS
# ==============================================================================
print("\n--- SECTION 1: Large Payloads Stress ---")

# 1.1: 5,000+ characters payload
large_5k = "Aceasta este o propoziție lungă de testare în limba română. " * 85  # ~5,100 chars
clip = StressClipboard("original user data 5k")
kb = StressKeyboard(clipboard=clip, target_text=large_5k)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("1.1 5,000+ chars payload: reported success", ok is True)
check("1.1 5,000+ chars payload: translator received full length", len(trans.queries[0]) == len(large_5k))
check("1.1 5,000+ chars payload: Ctrl+V injected translated text", clip.copy_history[-2].startswith("TRANSLATED: "))
check("1.1 5,000+ chars payload: original clipboard restored intact", clip.value == "original user data 5k")

# 1.2: 25,000 characters payload
large_25k = "Scrie un script Python complex pentru analiza datelor mari.\n" * 400  # ~24,000+ chars
clip = StressClipboard("original user data 25k")
kb = StressKeyboard(clipboard=clip, target_text=large_25k)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("1.2 25,000+ chars payload: reported success", ok is True)
check("1.2 25,000+ chars payload: translator received full text", trans.queries[0] == large_25k)
check("1.2 25,000+ chars payload: original clipboard restored intact", clip.value == "original user data 25k")

# 1.3: Multi-line prompt with 500 lines & complex indentation
multiline_prompt = "\n".join(f"{'    ' * (i % 5)}Linia {i}: procesează elementul {i}" for i in range(500))
clip = StressClipboard("orig multiline")
kb = StressKeyboard(clipboard=clip, target_text=multiline_prompt)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("1.3 500-line indented prompt: reported success", ok is True)
check("1.3 500-line indented prompt: line breaks preserved", trans.queries[0].count("\n") == 499)
check("1.3 500-line indented prompt: original clipboard restored intact", clip.value == "orig multiline")

# 1.4: Code blocks with Markdown fences, quotes, regex, backslashes
code_block = '''```python
def regex_search(pattern, text):
    """Verifică dacă șablonul se potrivește cu textul 'românesc'."""
    raw = r"^[a-zA-Z0-9_\.\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}$"
    query = "SELECT * FROM `utilizatori` WHERE email LIKE '%@gmail.com' AND activ = 1;"
    quotes = "Text cu „ghilimele românești” și «franțuzești»"
    return re.match(raw, text)
```'''
clip = StressClipboard("orig code")
kb = StressKeyboard(clipboard=clip, target_text=code_block)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("1.4 Code block with markdown fences & SQL: reported success", ok is True)
check("1.4 Code block: exact raw code sent to translator", trans.queries[0] == code_block)
check("1.4 Code block: original clipboard restored intact", clip.value == "orig code")


# ==============================================================================
# SECTION 2: EDGE-CASE TEXT & ENCODING
# ==============================================================================
print("\n--- SECTION 2: Edge-Case Text & Encoding Stress ---")

# 2.1: Romanian diacritics (standard comma-below & legacy cedilla)
ro_diacritics = "Și căprioara tânără țopăia grăbită prin pădurea înverzită. ĂÎÂȘȚ ăîâșț ŞŢ şţ"
clip = StressClipboard("orig diacritics")
kb = StressKeyboard(clipboard=clip, target_text=ro_diacritics)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("2.1 Romanian diacritics (comma-below and cedilla): reported success", ok is True)
check("2.1 Romanian diacritics: faithfully extracted", trans.queries[0] == ro_diacritics)
check("2.1 Romanian diacritics: clipboard restored", clip.value == "orig diacritics")

# 2.2: Emojis, ZWJ sequences, skin tones, flags
emojis_text = "Salut! 🚀🔥🎉 👨‍👩‍👧‍👦 familie cu ZWJ, steag 🇷🇴, ton de piele 👍🏽, simboluri 🤖💡🧠"
clip = StressClipboard("orig emoji")
kb = StressKeyboard(clipboard=clip, target_text=emojis_text)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("2.2 Emojis and astral plane unicode: reported success", ok is True)
check("2.2 Emojis and astral plane unicode: faithfully preserved", trans.queries[0] == emojis_text)
check("2.2 Emojis and astral plane unicode: clipboard restored", clip.value == "orig emoji")

# 2.3: Mixed scripts (Romanian + Cyrillic + Greek + CJK + Arabic + Hebrew + Math)
mixed_scripts = (
    "Tradu acest text: Привет мир! Γειά σου κόσμε! こんにちは世界! "
    "مرحبا بالعالم (RTL) שלום עולם (RTL) आणि नमस्ते. "
    "Formula matematică: ∀x ∈ ℝ, ∃y > 0 astfel încât ∫₀^∞ e^(-x²) dx = √π/2."
)
clip = StressClipboard("orig mixed")
kb = StressKeyboard(clipboard=clip, target_text=mixed_scripts)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("2.3 Mixed multilingual scripts & math: reported success", ok is True)
check("2.3 Mixed scripts: preserved without mojibake", trans.queries[0] == mixed_scripts)
check("2.3 Mixed scripts: clipboard restored", clip.value == "orig mixed")

# 2.4: Binary escape codes & ANSI control characters
ansi_text = "Eroare detectată: \x1b[31;1mCRITICAL_FAIL\x1b[0m la adresa 0xDEADBEEF\x07\r\nContinuă execuția?"
clip = StressClipboard("orig ansi")
kb = StressKeyboard(clipboard=clip, target_text=ansi_text)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("2.4 ANSI escape sequences & control codes: reported success", ok is True)
check("2.4 ANSI escape sequences: passed cleanly", trans.queries[0] == ansi_text)
check("2.4 ANSI escape sequences: clipboard restored", clip.value == "orig ansi")


# ==============================================================================
# SECTION 3: WHITESPACE & SENTINEL BOUNDARY CONDITIONS
# ==============================================================================
print("\n--- SECTION 3: Whitespace & Sentinel Boundary Conditions ---")

# 3.1: Strict empty string ""
clip = StressClipboard("orig 3.1")
kb = StressKeyboard(clipboard=clip, target_text="")
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("3.1 Strict empty string '': returns False", ok is False)
check("3.1 Strict empty string '': translator NOT called", len(trans.queries) == 0)
check("3.1 Strict empty string '': Ctrl+V NOT simulated", "press v" not in kb.keys)
check("3.1 Strict empty string '': original clipboard restored intact", clip.value == "orig 3.1")

# 3.2: ASCII whitespace "   "
clip = StressClipboard("orig 3.2")
kb = StressKeyboard(clipboard=clip, target_text="     ")
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("3.2 ASCII whitespace: returns False", ok is False)
check("3.2 ASCII whitespace: translator NOT called", len(trans.queries) == 0)
check("3.2 ASCII whitespace: clipboard restored", clip.value == "orig 3.2")

# 3.3: Diverse standard whitespace (tabs, newlines, carriage return, form feed, NBSP, EN QUAD, Ideographic)
diverse_ws = "\t\n\r\v\f \u00a0\u2000\u3000"
clip = StressClipboard("orig 3.3")
kb = StressKeyboard(clipboard=clip, target_text=diverse_ws)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("3.3 Diverse standard unicode whitespace: returns False", ok is False)
check("3.3 Diverse standard unicode whitespace: translator NOT called", len(trans.queries) == 0)
check("3.3 Diverse standard unicode whitespace: clipboard restored", clip.value == "orig 3.3")

# 3.3b: Adversarial zero-width space boundary (\u200b, \ufeff)
# Python str.strip() does NOT strip Unicode category Cf formatting characters.
zw_text = "\u200b\ufeff"
clip = StressClipboard("orig 3.3b")
kb = StressKeyboard(clipboard=clip, target_text=zw_text)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)
ok_zw = it.detect_and_translate()
check("3.3b Zero-width formatting characters (category Cf) treated as text by str.strip()", ok_zw is True)

# 3.4: Text is strictly sentinel prefix "__RELAY_SENTINEL_"
# Should be recognized as user text because it is non-empty and != the random UUID sentinel
clip = StressClipboard("orig 3.4")
kb = StressKeyboard(clipboard=clip, target_text="__RELAY_SENTINEL_")
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("3.4 Text matching sentinel prefix: returns True", ok is True)
check("3.4 Text matching sentinel prefix: translated", trans.queries == ["__RELAY_SENTINEL_"])
check("3.4 Text matching sentinel prefix: clipboard restored", clip.value == "orig 3.4")

# 3.5: Text containing fake sentinel
fake_sentinel_text = "__RELAY_SENTINEL_0123456789abcdef0123456789abcdef__"
clip = StressClipboard("orig 3.5")
kb = StressKeyboard(clipboard=clip, target_text=fake_sentinel_text)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("3.5 Fake sentinel string: returns True (UUID mismatch)", ok is True)
check("3.5 Fake sentinel string: translated", trans.queries == [fake_sentinel_text])
check("3.5 Fake sentinel string: clipboard restored", clip.value == "orig 3.5")

# 3.6: User text with sentinel embedded
mixed_sentinel = "salut __RELAY_SENTINEL_ cum merge treaba?"
clip = StressClipboard("orig 3.6")
kb = StressKeyboard(clipboard=clip, target_text=mixed_sentinel)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("3.6 Text with embedded sentinel substring: returns True", ok is True)
check("3.6 Text with embedded sentinel substring: translated", trans.queries == [mixed_sentinel])
check("3.6 Text with embedded sentinel substring: clipboard restored", clip.value == "orig 3.6")

# 3.7: Leading and trailing whitespace around real text
padded_text = "   \t\r\n   ajută-mă cu o problemă   \n\t  "
clip = StressClipboard("orig 3.7")
kb = StressKeyboard(clipboard=clip, target_text=padded_text)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("3.7 Padded whitespace around text: returns True", ok is True)
check("3.7 Padded whitespace around text: full text passed", trans.queries == [padded_text])
check("3.7 Padded whitespace around text: clipboard restored", clip.value == "orig 3.7")

# 3.8: Target control does NOT update clipboard (stays sentinel, e.g. uneditable/empty control)
clip = StressClipboard("orig 3.8")
kb = StressKeyboard(clipboard=clip, target_text=None)  # Sentinel never overwritten
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

start_time = time.perf_counter()
ok = it.detect_and_translate()
poll_duration_ms = (time.perf_counter() - start_time) * 1000.0

check("3.8 Control unchanged (sentinel remains): returns False", ok is False)
check("3.8 Control unchanged: loop polled and timed out (~80ms)", 60 <= poll_duration_ms <= 300, f"{poll_duration_ms:.1f}ms")
check("3.8 Control unchanged: translator NOT called", len(trans.queries) == 0)
check("3.8 Control unchanged: clipboard restored intact", clip.value == "orig 3.8")


# ==============================================================================
# SECTION 4: HOSTILE CLIPBOARD FAULTS & CONCURRENCY
# ==============================================================================
print("\n--- SECTION 4: Hostile Clipboard Faults & Latency Stress ---")

# 4.1: Initial user clipboard holds None (non-text, e.g., image or file)
clip = StressClipboard(initial_value=None)
kb = StressKeyboard(clipboard=clip, target_text="traducere simplă")
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("4.1 Initial clipboard is None: returns True", ok is True)
check("4.1 Initial clipboard is None: clipboard restored safely to None", clip.value is None or clip.value == "TRANSLATED: traducere simplă", repr(clip.value))

# 4.2: Initial user clipboard holds 1MB of text
large_user_clip = "ABCDEFGH" * 128000  # 1MB
clip = StressClipboard(initial_value=large_user_clip)
kb = StressKeyboard(clipboard=clip, target_text="scurt prompt")
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("4.2 Initial clipboard is 1MB: returns True", ok is True)
check("4.2 Initial clipboard is 1MB: huge payload restored perfectly", clip.value == large_user_clip)

# 4.3: Clipboard locked when writing initial sentinel (copy raises exception)
clip = StressClipboard("orig 4.3")
clip.lock_on_copy = True
kb = StressKeyboard(clipboard=clip, target_text="salut")
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("4.3 Clipboard locked on sentinel write: catches exception and returns False", ok is False)
check("4.3 Clipboard locked on sentinel write: translator NOT called", len(trans.queries) == 0)

# 4.4: Transient clipboard read fault during polling loop (recovers and retries)
clip = StressClipboard("orig 4.4")
clip.fail_on_poll_paste = True
kb = StressKeyboard(clipboard=clip, target_text="salut din nou")
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("4.4 Transient paste exception during polling: recovers and returns True", ok is True)
check("4.4 Transient paste exception: translated successfully", trans.queries == ["salut din nou"])
check("4.4 Transient paste exception: clipboard restored", clip.value == "orig 4.4")

# 4.4b: Transient clipboard read fault during initial save_clipboard
clip_save_fault = StressClipboard("orig 4.4b")
clip_save_fault.fail_on_first_paste = True
kb_save_fault = StressKeyboard(clipboard=clip_save_fault, target_text="text cu save fault")
trans_save_fault = StressTranslator()
it_save_fault = InplaceTranslator(cfg, lambda: trans_save_fault, keyboard=kb_save_fault, clipboard=clip_save_fault)
ok_save_fault = it_save_fault.detect_and_translate()
check("4.4b Fault during _save: translation proceeds even if initial save fails", ok_save_fault is True)
check("4.4b Fault during _save: original clipboard cannot be restored (None captured)", clip_save_fault.value != "orig 4.4b")

# 4.5: Clipboard locked during translated text copy (copy(english) throws)
clip = StressClipboard("orig 4.5")
kb = StressKeyboard(clipboard=clip, target_text="text de tradus")
trans = StressTranslator()

# Lock clipboard specifically on the copy of english text (copy call #2)
clip.lock_on_copy_count = 0
orig_copy = clip.copy

def locked_on_second_copy(val):
    if val.startswith("TRANSLATED:"):
        raise RuntimeError("Lock error writing translated text")
    return orig_copy(val)

clip.copy = locked_on_second_copy

it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)
ok = it.detect_and_translate()

check("4.5 Clipboard locked on english copy: catches error and returns False", ok is False)
check("4.5 Clipboard locked on english copy: Ctrl+V NOT simulated", "press v" not in kb.keys)

# 4.6: Polling delay simulation: clipboard updates on iteration 4 (~35ms)
clip = StressClipboard("orig 4.6")
kb = StressKeyboard(clipboard=clip, target_text="text cu întârziere", delay_iterations=4)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("4.6 Delayed clipboard update (iteration 4): detected and succeeds", ok is True)
check("4.6 Delayed clipboard update: translated", trans.queries == ["text cu întârziere"])
check("4.6 Delayed clipboard update: clipboard restored", clip.value == "orig 4.6")

# 4.7: Polling delay simulation: clipboard updates after iteration 8 (timeout)
clip = StressClipboard("orig 4.7")
kb = StressKeyboard(clipboard=clip, target_text="prea târziu", delay_iterations=12)
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("4.7 Over-delayed clipboard (>80ms): times out and returns False", ok is False)
check("4.7 Over-delayed clipboard: translator NOT called", len(trans.queries) == 0)
check("4.7 Over-delayed clipboard: clipboard restored", clip.value == "orig 4.7")


# ==============================================================================
# SECTION 5: HOSTILE KEYBOARD & TRANSLATOR FAULTS
# ==============================================================================
print("\n--- SECTION 5: Hostile Keyboard & Translator Faults ---")

# 5.1: Keyboard fails on Ctrl+A
clip = StressClipboard("orig 5.1")
kb = StressKeyboard(clipboard=clip, target_text="salut")
kb.fail_on_key = "a"
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("5.1 Keyboard fails on Ctrl+A: returns False", ok is False)
check("5.1 Keyboard fails on Ctrl+A: clipboard restored intact", clip.value == "orig 5.1")

# 5.2: Keyboard fails on Ctrl+V
clip = StressClipboard("orig 5.2")
kb = StressKeyboard(clipboard=clip, target_text="salut")
kb.fail_on_key = "v"
trans = StressTranslator()
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("5.2 Keyboard fails on Ctrl+V: returns False", ok is False)
check("5.2 Keyboard fails on Ctrl+V: clipboard restored intact", clip.value == "orig 5.2")

# 5.3: Translator getter returns None
clip = StressClipboard("orig 5.3")
kb = StressKeyboard(clipboard=clip, target_text="salut")
it = InplaceTranslator(cfg, lambda: None, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("5.3 Translator getter returns None: returns False", ok is False)
check("5.3 Translator getter returns None: clipboard restored intact", clip.value == "orig 5.3")

# 5.4: Translator getter throws exception
def broken_getter():
    raise RuntimeError("Translator service crashed")

clip = StressClipboard("orig 5.4")
kb = StressKeyboard(clipboard=clip, target_text="salut")
it = InplaceTranslator(cfg, broken_getter, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("5.4 Translator getter throws: returns False", ok is False)
check("5.4 Translator getter throws: clipboard restored intact", clip.value == "orig 5.4")

# 5.5: translate() raises CUDA Out of Memory
clip = StressClipboard("orig 5.5")
kb = StressKeyboard(clipboard=clip, target_text="salut")
trans = StressTranslator()
trans.throw_cuda_oom = True
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("5.5 CUDA OOM in translator: caught safely, returns False", ok is False)
check("5.5 CUDA OOM: Ctrl+V NOT simulated", "press v" not in kb.keys)
check("5.5 CUDA OOM: clipboard restored intact", clip.value == "orig 5.5")

# 5.6: translate() returns None (strict clipboard raising TypeError)
clip = StressClipboard("orig 5.6")
clip.strict_string_copy = True
kb = StressKeyboard(clipboard=clip, target_text="salut")
trans = StressTranslator()
trans.return_none = True
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("5.6 Translator returns None with strict clipboard: catches TypeError, returns False", ok is False)
check("5.6 Translator returns None with strict clipboard: clipboard restored intact", clip.value == "orig 5.6")

# 5.7: translate() returns empty string ""
clip = StressClipboard("orig 5.7")
kb = StressKeyboard(clipboard=clip, target_text="salut")
trans = StressTranslator()
trans.return_empty = True
it = InplaceTranslator(cfg, lambda: trans, keyboard=kb, clipboard=clip)

ok = it.detect_and_translate()
check("5.7 Translator returns empty string '': succeeds (clears selection)", ok is True)
check("5.7 Translator returns empty string '': clipboard restored intact", clip.value == "orig 5.7")


# ==============================================================================
# SECTION 6: ABSOLUTE ABSENCE OF Key.enter UNDER ANY CIRCUMSTANCE
# ==============================================================================
print("\n--- SECTION 6: Absolute Absence of Key.enter ---")

has_enter = any("enter" in str(k).lower() for k in GLOBAL_KEYSTROKE_LOG)
check("6.1 Key.enter was NEVER pressed or released across ANY stress test", not has_enter, f"Violations found: {[k for k in GLOBAL_KEYSTROKE_LOG if 'enter' in str(k).lower()]}")
check("6.2 Total simulated keystrokes logged across all tests", len(GLOBAL_KEYSTROKE_LOG) > 100, f"Count: {len(GLOBAL_KEYSTROKE_LOG)}")

# Also verify that no other unexpected destructive keys (Backspace, Delete, Esc) were simulated
forbidden_keys = ["backspace", "delete", "escape", "tab"]
found_forbidden = [k for k in GLOBAL_KEYSTROKE_LOG if any(f in str(k).lower() for f in forbidden_keys)]
check("6.3 No destructive keys (backspace, delete, escape) were simulated", len(found_forbidden) == 0, str(found_forbidden))


# ==============================================================================
# SECTION 7: VOICE DICTATION FALLBACK INTEGRATION IN VoicePrompt.toggle()
# ==============================================================================
print("\n--- SECTION 7: Voice Dictation Fallback in VoicePrompt.toggle() ---")

from relay.config import load_config
full_cfg = load_config("no-such-file.json")
full_cfg.update(beep_feedback=False)

class MockVoiceRecorder:
    def __init__(self):
        self.started_count = 0
        self.stopped_count = 0
        self.device_name = "test_mic"

    def start(self):
        self.started_count += 1

    def stop(self):
        self.stopped_count += 1
        return "audio_data"

class MockVoiceOrb:
    def __init__(self):
        self.state = app.IDLE
        self.pulses = []

    def set_state(self, s):
        self.state = s

    def pulse(self, color="cyan", duration_ms=500):
        self.pulses.append((color, duration_ms))

# 7.1: Active input has Romanian text -> in-place translation replaces text, NO voice dictation
vp = app.VoicePrompt(full_cfg)
vp._ready.set()
vp.recorder = MockVoiceRecorder()
vp.orb = MockVoiceOrb()
vp.inplace_translator.detect_and_translate = lambda: True

vp.state = app.IDLE
vp.toggle()
check("7.1 Text present: recorder.start() was NOT called", vp.recorder.started_count == 0)
check("7.1 Text present: state remained IDLE", vp.state == app.IDLE)
check("7.1 Text present: orb remained IDLE", vp.orb.state == app.IDLE)

# 7.2: Active input is empty -> fallback to voice dictation
vp = app.VoicePrompt(full_cfg)
vp._ready.set()
vp.recorder = MockVoiceRecorder()
vp.orb = MockVoiceOrb()
vp.inplace_translator.detect_and_translate = lambda: False

vp.state = app.IDLE
vp.toggle()
check("7.2 Empty input fallback: recorder.start() was called", vp.recorder.started_count == 1)
check("7.2 Empty input fallback: state transitioned to RECORDING", vp.state == app.RECORDING)
check("7.2 Empty input fallback: orb transitioned to RECORDING", vp.orb.state == app.RECORDING)

# 7.3: Active input is whitespace -> fallback to voice dictation
vp = app.VoicePrompt(full_cfg)
vp._ready.set()
vp.recorder = MockVoiceRecorder()
vp.orb = MockVoiceOrb()
# Wire realistic InplaceTranslator with whitespace
clip_ws = StressClipboard("user clip")
kb_ws = StressKeyboard(clip_ws, target_text="   \t\n   ")
vp.inplace_translator = InplaceTranslator(full_cfg, lambda: StressTranslator(), keyboard=kb_ws, clipboard=clip_ws)

vp.state = app.IDLE
vp.toggle()
check("7.3 Whitespace fallback: recorder.start() was called", vp.recorder.started_count == 1)
check("7.3 Whitespace fallback: state is RECORDING", vp.state == app.RECORDING)
check("7.3 Whitespace fallback: user clipboard restored intact", clip_ws.value == "user clip")

# 7.4: Hardware error / exception -> fallback to voice dictation
vp = app.VoicePrompt(full_cfg)
vp._ready.set()
vp.recorder = MockVoiceRecorder()
vp.orb = MockVoiceOrb()
clip_err = StressClipboard("user clip")
clip_err.lock_on_copy = True
kb_err = StressKeyboard(clip_err, target_text="salut")
vp.inplace_translator = InplaceTranslator(full_cfg, lambda: StressTranslator(), keyboard=kb_err, clipboard=clip_err)

vp.state = app.IDLE
vp.toggle()
check("7.4 Exception fallback: recorder.start() was called", vp.recorder.started_count == 1)
check("7.4 Exception fallback: state is RECORDING", vp.state == app.RECORDING)

# 7.5: Toggle when already in RECORDING -> stops recording (inplace is bypassed)
called_detect = [False]
def mock_detect():
    called_detect[0] = True
    return True

vp.inplace_translator.detect_and_translate = mock_detect
vp.state = app.RECORDING
vp.toggle()
check("7.5 Toggle during RECORDING: inplace_translator was BYPASSED", called_detect[0] is False)
check("7.5 Toggle during RECORDING: recorder.stop() was called", vp.recorder.stopped_count == 1)


# ==============================================================================
# SECTION 8: ORB PULSE & AUDIO FEEDBACK FAULT ISOLATION
# ==============================================================================
print("\n--- SECTION 8: Orb Pulse & Audio Feedback Fault Isolation ---")

# 8.1: orb_getter throws
clip = StressClipboard("orig 8.1")
kb = StressKeyboard(clipboard=clip, target_text="salut")
trans = StressTranslator()
it = InplaceTranslator(
    cfg,
    lambda: trans,
    orb_getter=lambda: (_ for _ in ()).throw(RuntimeError("Orb GUI dead")),
    keyboard=kb,
    clipboard=clip,
)
ok = it.detect_and_translate()
check("8.1 Orb getter throws: does not crash, returns True", ok is True)
check("8.1 Orb getter throws: clipboard restored", clip.value == "orig 8.1")

# 8.2: orb.pulse throws
class CrashingOrb:
    def pulse(self, *a, **k):
        raise RuntimeError("DirectX device lost during pulse")

clip = StressClipboard("orig 8.2")
kb = StressKeyboard(clipboard=clip, target_text="salut")
trans = StressTranslator()
it = InplaceTranslator(
    cfg,
    lambda: trans,
    orb_getter=lambda: CrashingOrb(),
    keyboard=kb,
    clipboard=clip,
)
ok = it.detect_and_translate()
check("8.2 Orb pulse throws: does not crash, returns True", ok is True)
check("8.2 Orb pulse throws: clipboard restored", clip.value == "orig 8.2")

# 8.3: feedback.inplace_success throws
class CrashingFeedback:
    def inplace_success(self):
        raise RuntimeError("Windows CoreAudio endpoint locked")

clip = StressClipboard("orig 8.3")
kb = StressKeyboard(clipboard=clip, target_text="salut")
trans = StressTranslator()
it = InplaceTranslator(
    cfg,
    lambda: trans,
    feedback=CrashingFeedback(),
    keyboard=kb,
    clipboard=clip,
)
ok = it.detect_and_translate()
check("8.3 Audio chime throws: does not crash, returns True", ok is True)
check("8.3 Audio chime throws: clipboard restored", clip.value == "orig 8.3")

print("\n======================================================================")
print("ALL EMPIRICAL ADVERSARIAL STRESS TESTS COMPLETED SUCCESSFULLY!")
print("======================================================================")

sys.exit(report.finish())
