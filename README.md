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
3. Speak. The dot expands into a capsule whose bars ride your voice.
4. Press **F9** again to stop.

Text appears as you speak, phrase by phrase. Nothing is submitted for you —
press Enter yourself when you are happy with it.

**The dot** can be dragged anywhere; its position is remembered. The capsule
opens towards whichever side of the screen has room.

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
dictation uses, so no clicking around first. Hover to see the whole prompt
before you commit to it.

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

**Nothing works in an app running as Administrator.**
Windows blocks a normal process from sending input to an elevated window. Run
Relay as Administrator too, or use the app unelevated.

**First launch seems frozen.**
It is downloading the 1.5 GB model. This happens once.

---

## Project layout

```
relay/
├── __main__.py          entry point, state machine, worker threads
├── cuda_setup.py        registers the pip-installed CUDA DLLs (must run first)
├── config.py            defaults, layered under config.json
├── audio.py             capture, device tracking, VAD phrase segmentation
├── transcriber.py       Whisper loading, warm-up, translation
├── injector.py          clipboard save → set → Ctrl+V → restore
├── prompts.py           the right-click template library, over prompts.json
├── prompt_editor.py     the window that edits it
├── target.py            remembers the window you last typed in
├── uia.py               finds and focuses the text box via UI Automation
├── overlay.py           the floating dot and level meter (PySide6)
├── feedback.py          console status
├── logsetup.py          redirects output to the log when there is no console
└── single_instance.py   refuses to start twice
```

### Three things that are less obvious than they look

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

---

## Licence

MIT
