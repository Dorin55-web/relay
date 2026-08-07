# Relay

Dictate in Romanian, get English text in whatever you are typing into.

Speech never leaves the machine: Whisper runs locally on the GPU, and the
translation happens inside the same model pass. No API keys, no network, no
per-minute cost.

Built for talking to AI assistants, but it pastes into any window that accepts
text.

---

## How it works

```
F9 / click the dot
   → sounddevice captures the current default microphone (16 kHz mono)
   → energy-based VAD splits speech into phrases at your natural pauses
   → faster-whisper large-v3, task="translate"   ← translation happens here
   → clipboard → Ctrl+V into the window you last typed in
   → your original clipboard is restored
```

Translation is not a separate step. Whisper has a `translate` task that emits
English directly from Romanian audio, in a single pass through the model.

**Live by default.** Each phrase is pasted when you pause, so text appears while
you are still speaking rather than all at once at the end. Pastes only ever
append, so nothing you typed by hand can be overwritten.

---

## Requirements

| | |
|---|---|
| OS | Windows 10/11 |
| Python | 3.10+ |
| GPU | NVIDIA with ~5 GB free VRAM (optional — falls back to CPU) |
| Disk | ~1.5 GB for the model, downloaded once |

CUDA libraries are installed through pip; no separate CUDA toolkit is needed.

---

## Install

```bash
git clone <your-repo-url>
cd relay
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then create the shortcuts and enable start-up with Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The `-ExecutionPolicy Bypass` is not optional for most people: Windows refuses
to run unsigned scripts under the default policy, and a copy extracted from a
downloaded ZIP is blocked even under `RemoteSigned`. It applies to this one
command only and changes nothing on your system.

Skipping `install.ps1` altogether is fine — it only creates the shortcuts. The
app runs from `run.bat` without it.

That puts **Relay** and **Relay - Mic Test** on the Desktop and
adds a Startup entry. Remove the auto-start later with `.\install.ps1 -Remove`.

The first launch downloads the model (~1.5 GB) into
`%USERPROFILE%\.cache\huggingface\hub`. Subsequent launches take about 7 seconds.

---

## Use

Start it from the **Relay** shortcut. No console window appears — just a
small dot floating above your other windows.

1. Click into the text box you want to dictate into, once.
2. Press **F9**, or click the dot.
3. Speak. The orb changes to its listening look and hurries as it hears you.
4. Press **F9** again to stop.

Text appears as you speak, phrase by phrase. Nothing is submitted for you —
press Enter yourself when you are happy with it.

---

## The orb

Drag it anywhere; its position is remembered. It animates the whole time it is
on screen, and it wears **a different drawing in each of its three states** —
resting, listening, working. Right-click it and pick **Change how it looks…**

**Nothing is drawn but the dots.** There is no disc behind them and no shadow
around them — the mark floats on whatever is underneath it. The one thing the
disc used to buy was contrast: these are pale dots, and they lean on the
background being dark. On a dark desktop or a dark editor they read cleanly, on
white the far dots are dark enough to carry the shape on their own, and the
weak case is a mid-grey window where neither end has much to work with.

There is one thing behind them, and it is there to be clicked rather than seen.
Windows decides what a click hits on a see-through window by looking at the
alpha of that exact pixel, so with nothing under the dots, only the dots
themselves could be clicked — 18% of the orb, and 5% when the look is an
outline. That is what a dot that ignores two clicks out of three feels like.
The backing is one part in 255: enough for the hit test, and a four-tenths of a
percent change to what is behind it.

There are nine to choose from, each animating in its own tile so the choice is
something you look at rather than read:

| | | |
|---|---|---|
| **working** — particles running their orbits | **searching** — a globe under a sweeping scan | **solving** — bands twisting and clicking back |
| **listening** — a waveform rolling through the rings | **connecting** — a constellation wiring itself up | **weaving** — three strands plaiting over a ball |
| **composing** — a sash undulating on its band | **breathing** — a ring swelling and pinching | **shaping** — an outline cycling through shapes |

Each state also carries **its own speed**, from 0.1× to 3×, on top of whatever
that look was tuned at. Out of the box: *breathing* while it waits, *listening*
while you talk, *working* while it transcribes.

**Size is a slider**, 16 px to 120 px, whole pixels the whole way. It is not a
choice between fixed steps because the drawings are not fixed at those steps:
between **20 and 64** — the two sizes the originals were actually tuned at —
the tuning itself is interpolated, so the mark is retuned as it grows rather
than stretched. Fewer dots drawn larger as it shrinks, more dots drawn finer as
it grows. The picker says which side of that span you are on.

Past either end there is nothing left to interpolate towards, so the nearer
drawing is simply magnified. Carrying the interpolation on would be worse than
useless: the multipliers grow with size faster than linearly, and at 120 px the
sash would resolve to nine thousand dots — forty times the frame cost, for a
mark nobody could read anyway.

Two things do *not* change when it is magnified: how fast it moves, and its
proportions. The one exception is the smallest dots, which have a floor under
them in real pixels and so do not scale past it.

Changing state cross-fades over about a third of a second. The two drawings
share no geometry at all, so cutting between them reads as the orb being
replaced rather than changing.

**Speaking hurries whichever look is on**, by up to 60%. That is one handle
rather than nine, and it is the only one they all have in common — a lattice
has rings to ripple, a constellation has none. The response is quick on the way
up so it catches the start of a word and slow on the way down so it rides
speech instead of flickering through the gaps.

Everything is written to `config.json` when you press Save, and nothing before
then: closing the picker puts back what was there, so trying all nine costs
nothing.

It runs at thirty frames a second rather than sixty. These looks move slowly
enough to read at that rate, and something that animates all day should not
cost twice what it needs to. The dearest of them is about 3.6 ms a frame, so
the orb sits at roughly a tenth of one core.

The taskbar icon is a **still globe** of dots instead — a different drawing for
a different job. An icon never moves and has to survive sixteen pixels, where
these looks are hundreds of sub-pixel dots and no shape at all.

All nine are ported from [thinking-orbs](https://github.com/Jakubantalik/thinking-orbs)
(MIT, see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)), redrawn in
QPainter so Relay keeps its dependencies.

---

## Typing instead of talking

Right-click the dot and pick **Write instead...**. Type Romanian in the top box;
the English appears underneath as you pause. `Copy` puts it on the clipboard,
`Send` puts it into the box you last typed in — the same target dictation uses.
`Ctrl+Enter` translates and sends in one go, `Escape` closes.

The English box is editable. Fix it there before sending, and what you send is
what you fixed, not what came out of the model.

For a term Whisper keeps mangling, something you are copying off a page, or a
room where talking is not an option.

The window is built **and shown once off screen** while the speech model is
loading, so the click that opens it costs about 20 ms. Building it without
showing it was not enough: the first time any window in the process is shown
costs a further 400–500 ms — Qt creating the native window and realising its
paint backend — and that is a cost per process, not per window. Measured, it
landed on whichever of Relay's three windows was opened first, which is why it
looked like the write window's fault. Paying it at start-up means none of the
three ever does.

> **This is a different, much weaker model than the one dictation uses.**
> Whisper's translate task takes *audio* — it cannot help with anything you
> type, so this is a separate 300 MB Marian model, downloaded once on first use.
> It works a sentence at a time with no wider context, so an ambiguous word gets
> its common reading rather than the one your paragraph implies: *"Nu scrie
> nimic încă"* comes back as *"It doesn't write anything yet"* rather than
> *"Don't write anything yet"*. Read it before you send it.
>
> Nothing is downloaded or loaded until you open the window for the first time.
> Dictation never touches it.

---

## Prompt templates

Right-click the dot. Ten scaffolds you reach for constantly, numbered and
grouped by the kind of work they start:

| | |
|---|---|
| 1–2 | **Diagnose** — find the cause before touching anything |
| 3–4 | **Review** — read the code, report what is there |
| 5–6 | **Plan** — decide the approach before writing any of it |
| 7–8 | **Build** — implement and fix |
| 9–10 | **Check** — test, and hand off to the next session |

Click one and it goes straight into the box you last typed in — the same target
dictation uses, so no clicking around first.

**Stop on one for three and a half seconds** and the whole prompt appears, so
you can read it before committing. It takes that long on purpose: Qt's own
tooltips are quick, and quicker still once one has been shown, so running an
eye down ten templates used to trail a paragraph of prompt text after the
pointer the whole way. Moving at all starts the count again from nothing, and
items whose tooltip would only repeat their own label never show one.

Each is a skeleton with `<angle brackets>` where your specifics go. Inserting
one never presses Enter, even with `auto_enter` on: the blanks are still in it.

**They are yours to rewrite.** `Edit prompts...` opens an editor: pick one on the
left, change its group, name and text on the right, reorder with the arrows.
Save and the next right-click shows your version — no restart.

The numbers are the point, so the editor keeps ten of them on screen without
scrolling and caps the list there; past ten, a right-click menu stops being
faster than typing. Prompts are stored in `prompts.json`, written through a
temporary file so an interrupted save cannot leave you with half of one. Edit
that file by hand if you prefer — if it ever ends up unreadable the built-in set
comes back, so you cannot lock yourself out of the menu.

**The target sticks.** Once you have clicked into a text box, every dictation
goes there — even if you Alt+Tab away, or a notification steals focus. It only
moves when you deliberately click into a different text box. On Chromium apps
the box is located through UI Automation, so it survives the window being moved,
resized, or reflowed.

---

## Configuration

Settings live in `config.json`, the right-click templates in `prompts.json`.

| Key | Default | What it does |
|---|---|---|
| `hotkey` | `"f9"` | Start/stop key. Single key, no modifiers. |
| `task` | `"translate"` | `"translate"` outputs English; `"transcribe"` keeps Romanian. |
| `source_language` | `"ro"` | The language you speak. |
| `model_size` | `"large-v3"` | See the note below before changing this. |
| `compute_type` | `"float16"` | Drop to `"int8_float16"` if VRAM is tight. |
| `streaming` | `true` | Paste each phrase as you finish it. |
| `phrase_silence_seconds` | `0.7` | Pause length that ends a phrase. |
| `vad_min_rms` | `0.0008` | Lower it if a quiet mic is not being heard. |
| `auto_enter` | `false` | `true` submits the prompt for you. |
| `remember_target` | `true` | Keep pasting into the last box you clicked. |
| `restore_caret` | `true` | Put the cursor back in that box automatically. |
| `input_device` | `null` | `null` follows the Windows default. |
| `avoid_bluetooth_mic` | `true` | Step around a headset's mic when another one exists. |

> **Do not use `large-v3-turbo`.** It is roughly 4x faster, but OpenAI distilled
> it on transcription only and documents that it is not trained for the
> translation task — the exact step that turns your Romanian into English.

---

## Command line

```bash
run.bat --mic-test      # signal level on every input; finds a muted mic
run.bat --check-cuda    # confirm the GPU is visible to ctranslate2
run.bat --selftest      # record 5s, print the translation, paste nothing
run.bat --list-devices  # show audio inputs
run.bat --no-ui         # hotkey only, no floating dot
```

`run.bat` opens a console, which the shortcut deliberately does not — use it
when you want to watch the output live.

---

## Tests

```bash
python tests/run.py
```

Nine suites, each in its own process, about a minute for the lot. They
build real windows and ask Windows itself what it would do, because most of
what they check cannot be checked any other way — whether a click lands on a
transparent pixel is a question for the window manager, not for a mock.

Nothing they do touches your `prompts.json` or the orb's saved position; both
are redirected to a throwaway directory first. `tests/probes/` holds nine more
scripts that report numbers rather than passing or failing, for when something
needs investigating. See [tests/README.md](tests/README.md).

---

## Troubleshooting

Because there is no console, everything is written to **`relay.log`** in
the project folder: which microphone was used, what was transcribed, how long it
took, and which window received the paste.

**Nothing is recognised, or "microphone delivered no signal".**
The mic is muted or its level is zero. The capture stream still opens and still
returns samples, so no error is raised. Run `run.bat --mic-test`: if every input
reads around `0.00003` — one 16-bit LSB — check the mic-mute key on your laptop
(often F4, with an LED), then Windows Sound settings, then `mmsys.cpl` →
Recording → Levels. A working mic in a quiet room reads about `0.0005` or above.

**Text goes to the wrong place, or nowhere.**
Click once into the target text box. The log line `[paste] target window:` shows
where it actually landed.

**My Bluetooth headset goes silent, and its mic stops working.**
A Bluetooth headset cannot carry its microphone and stereo audio at the same
time. The mic lives on the hands-free profile, and opening it tears the stereo
one down — your music stops. Windows renegotiates when the mic closes, and when
that renegotiation fails the headset is left holding neither: no sound, no mic.

Relay steps around this by default. If your default input is a headset mic and
there is any other microphone, it records from that one and the headset never
leaves stereo. The log says so when it happens. Set `avoid_bluetooth_mic` to
`false` to use the headset mic anyway.

You lose nothing by it. That mic runs at 8–16 kHz mono, against 48 kHz on a
typical built-in array, so the laptop microphone is the better input for Whisper
as well as the one that leaves your audio alone.

If a headset is already stuck, turn it off and on again, or toggle Bluetooth —
Windows renegotiates from scratch.

**Nothing works in an app running as Administrator.**
Windows blocks a normal process from sending input to an elevated window. Run
Relay as Administrator too, or use the app unelevated.

**First launch seems frozen.**
It is downloading the 1.5 GB model. This happens once.

---

## Project layout

```
assets/relay.ico         the icon the shortcuts point at, 16px to 256px
tests/                   eight suites and a runner; probes/ under it
relay/
├── __main__.py          entry point, state machine, worker threads
├── cuda_setup.py        registers the pip-installed CUDA DLLs (must run first)
├── config.py            defaults, layered under config.json
├── audio.py             capture, device tracking, VAD phrase segmentation
├── transcriber.py       Whisper loading, warm-up, translation
├── injector.py          clipboard save → set → Ctrl+V → restore
├── prompts.py           the right-click template library, over prompts.json
├── prompt_editor.py     the window that edits it
├── translator.py        Romanian text to English text (Marian, not Whisper)
├── compose.py           the window you type into
├── target.py            remembers the window you last typed in
├── uia.py               finds and focuses the text box via UI Automation
├── overlay.py           the floating orb (PySide6)
├── orbs/                the nine looks it can wear
├── look_picker.py       the window that assigns one to each state
├── sphere.py            the still globe the icon is drawn from
├── window.py            the frameless chrome both windows wear
├── watchdog.py          reports the stretches where nothing Python can run
├── feedback.py          console status
├── logsetup.py          redirects output to the log when there is no console
└── single_instance.py   refuses to start twice
```

`watchdog.py` is instrumentation rather than a feature. It wakes every 50 ms
and says nothing unless the interpreter has been unable to run for longer than
a low-level Windows hook is allowed to take, in which case it writes a
`[stall]` block into `relay.log` with a stack for every thread. That is what
those blocks are if you see them.

The icon in the taskbar is drawn at run time rather than loaded from that file,
so it is always in step with the code. The file is only for the shortcuts — a
`.lnk` resolves its icon once and cannot follow anything.

### Six things that are less obvious than they look

**CUDA DLLs on Windows.** Since Python 3.8, `PATH` is ignored when resolving DLL
dependencies of extension modules. The pip `nvidia-*` wheels put cuBLAS and cuDNN
somewhere nothing looks at, so ctranslate2 silently reports zero CUDA devices.
`os.add_dll_directory()` is the fix, and it has to run before `faster_whisper` is
imported anywhere.

**The keyboard hook must return immediately.** Windows silently unhooks a
low-level keyboard hook that overruns `LowLevelHooksTimeout` (300 ms by default).
Opening an audio stream takes about 300 ms, so the hotkey callback only queues a
request and a separate thread does the work.

**The dot must not take focus.** `WS_EX_NOACTIVATE` stops clicking it from
becoming the foreground window. Without that, the simulated `Ctrl+V` lands on the
dot instead of the box you were aiming at, and the text goes nowhere.

**The taskbar icon is not the window icon.** Windows groups taskbar buttons by
the process's application id, and without one of its own everything here files
under the Python interpreter hosting it — showing Python's icon no matter what
`setWindowIcon` says. `SetCurrentProcessExplicitAppUserModelID`, before the first
window exists, is what makes the button the program's own.

**Closing a window can close the program.** Qt quits once the last *primary*
window closes, and a `Qt.Tool` window — which the dot is — does not count as
one. Nothing else here opened an ordinary window, so the rule never fired until
the prompt editor did: closing it, including the close that follows a save, took
the whole program down and the dot disappeared for good.
`setQuitOnLastWindowClosed(False)` is the fix.

**A cross-fade between two drawings has a hole in the middle.** The mark's three
bars used to fade out over the first half of the capsule opening while the
meter's eight faded in over the second — alphas of `(0.5 - open) * 2` and
`(open - 0.5) * 2`, which are *both zero* at exactly half. Every start and stop
passed through a frame with no bars at all. They are one set of bars now, each
travelling between where the mark puts it and where the meter does, so the row is
never empty and the mark unrolls instead of being swapped.

---

## Licence

MIT
