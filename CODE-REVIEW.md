# Code review — dead code, duplication, leftover scaffolding

A pass over all 36 tracked files (5,806 lines of Python), ordered by what is
worth doing first.

> **Findings 2, 4, 6, 7 and 10 have been applied.** They are kept below with
> what was found and why, since the reasoning is the part worth keeping; each
> is marked **Done**. Findings 1, 3, 5, 8 and 9 are still open.

Every claim was checked mechanically — module-level names resolved with an AST
walk over the whole package, imports cross-referenced against actual use,
duplicate blocks found by matching runs of four or more identical non-blank
lines — and then read by hand. Where a first pass was wrong, that is noted,
because two of the checks reported findings that turned out to be artefacts of
the check itself.

---

## The short version

| | Finding | Size |
|---|---|---|
| 1 | **The tests are not in the repo.** 14 suites live in a temp directory | ~1,400 lines at risk |
| 2 ✅ | `Orb.flash()` does nothing, and is called from 9 places | ~27 lines |
| 3 | Three windows carry three copies of the same stylesheet and palette | ~90 lines |
| 4 ✅ | Debug logging left in the resize path, printing on every drag | 2 lines |
| 5 | Four `_fire_*` methods that differ only in the message | ~30 lines |
| 6 ✅ | Three unused module-level names | 3 lines |
| 7 ✅ | `r_size_mul` is computed and never read | 3 lines |
| 8 | `open_editor()` and `open_picker()` are the same function | ~20 lines |
| 9 | The watchdog is diagnostic scaffolding, still running in production | 88 lines |
| 10 ✅ | Smaller things: README tree, `__version__`, two unused imports | — |

---

## 1. The tests are not in the repo — the biggest problem here

There are **14 test suites** and **none of them are tracked**:

```
git ls-files | grep test    ->    (nothing)
```

They live in the session scratchpad under `AppData\Local\Temp`, which is
wiped. They cover things that were genuinely hard to get right and that
nothing else would catch:

| Suite | What it pins down |
|---|---|
| `test_looks.py` | 9 looks at 8 sizes, `js_round`, the radius floor, the hit-testable disc |
| `test_tooltip.py` | the 3.5 s rest, driven through the real event loop |
| `test_compose.py` | the translator, the Send button, stale-result dropping |
| `test_editor.py` | prompt round-trips, corrupt-file recovery, no temp file left |
| `test_survives_editor.py` | the orb not dying when the editor closes |
| `test_resize_guard.py` | the frameless edges |
| `test_watchdog.py` | the stall detector |
| `test_bluetooth.py` | the hands-free mic avoidance |
| plus 6 probes | `probe_live_clicks.py` measured 18% clickable — that is how the click bug was found at all |

Several encode findings that were expensive to reach and are invisible in the
code: that `round(2.5)` differs between Python and JavaScript, that the radius
floor lives in the painter, that the layered-window hit test needs alpha ≥ 1.
If those suites vanish, the next change to `relay/orbs/` has nothing standing
behind it.

**Suggested:** a `tests/` directory in the repo, paths made relative, and a
one-line runner. This is the only item on the list that gets worse with time.

---

## 2. `Orb.flash()` does nothing, and nine call sites feed it — **Done**

`relay/overlay.py:550`:

```python
def flash(self, kind):
    """Kept so callers need not care, but deliberately does nothing.

    Success and failure used to tint the dot green or red. ...
    """
```

`relay/__main__.py` calls it **9 times**, 8 of them guarded:

```python
if self.orb:
    self.orb.flash("error")
```

Lines 185, 223, 245, 249, 271, 277, 282, 286, 473. The `"ok"` / `"error"`
argument is dead too — nothing reads `kind`.

This is honest leftover: the orb used to tint, the tint was dropped, and the
method was kept so callers would not need touching. That was the right call at
the time; the callers are still untouched, and now the shape of the old
feature is spread across the file.

**Suggested:** delete the method and the 9 call sites. The states it was
signalling are already carried by `set_state`, and the log already says how a
dictation went.

---

## 3. Three windows, three copies of the same chrome

`compose.py`, `prompt_editor.py` and `look_picker.py` each define their own
`STYLESHEET`. Measured overlap of identical lines:

```
compose vs prompt_editor    19 lines   (59% of compose, 33% of prompt_editor)
compose vs look_picker      15 lines   (47% of compose, 28% of look_picker)
prompt_editor vs look_picker 28 lines  (48% / 53%)
```

The palette is worse than duplicated — it is duplicated *and* borrowed:

- `prompt_editor.py:22-27` defines `BG PANEL LINE TEXT MUTED`
- `look_picker.py:25-30` defines the same five names, same five hex values
- `compose.py:19` does `from .prompt_editor import BG, LINE, MUTED, PANEL, TEXT`

So the write window imports its colours from the prompt editor, which is not a
theme module and has no reason to be one. Change a colour and you must find
two definitions and one import.

The white Save button is pasted twice, identically, with the same comment
explaining the same Windows quirk:

- `prompt_editor.py:186-195`
- `look_picker.py:188-197`

**Suggested:** a `relay/theme.py` holding the five colours, the shared
stylesheet base and the `primary_button()` helper; each window keeps only the
rules that are actually its own.

---

## 4. Debug logging left in the resize path — **Done**

`relay/window.py:218`:

```python
print(f"[window] resize from {int(edges)} at "
      f"{event.position().x():.0f},{event.position().y():.0f}")
```

Written while chasing the cursor-lock bug. It fires on **every** edge drag of
both frameless windows and writes to `relay.log`. Nothing reads it now.

The other `[window]` print, at line 132, is a real error path and should stay.

**Suggested:** delete line 218-219.

---

## 5. Four `_fire_*` methods that differ only in the message

`relay/overlay.py:509-538` — `_fire_prompt`, `_fire_compose`, `_fire_pick_look`,
`_fire_edit_prompts` are the same six lines four times:

```python
def _fire_X(self):
    if self.on_X is None:
        return
    try:
        self.on_X()
    except Exception as exc:
        print(f"[orb] could not <do the thing>: {exc}")
```

`_fire_toggle` is a fifth variant that swallows the exception silently instead
of printing, which is an inconsistency rather than a decision as far as I can
tell.

**Suggested:** one `_fire(callback, what, *args)` helper. Five bodies become
five one-line calls, and the odd one out either gets a message or a comment
saying why it does not want one.

---

## 6. Three module-level names nothing reads — **Done**

| Where | Name | Note |
|---|---|---|
| `overlay.py:44` | `ICON_PATH` | The path is used, but from `install.ps1` and the README — this constant is not read by any Python |
| `overlay.py:46` | `DEFAULT_ORB_SIZE` | Left from before the size lived in `config.json` |
| `overlay.py:456` | `Orb._screen_geometry()` | Left from the capsule, which had to know which way to open. `_on_screen()` calls `screenAt` directly now |

`relay/__init__.py:3` declares `__version__ = "1.0.0"` and nothing anywhere
reads it — not the installer, not the README, not the app.

A first pass also flagged `_Tee.isatty()` in `logsetup.py:44` as unreachable.
It is not: it is part of the file-object protocol and libraries call it —
`tqdm` in particular checks it before drawing progress bars. Leave it.

---

## 7. `r_size_mul` is computed and never read — **Done**

`relay/orbs/profiles.py:57`:

```python
# Remember the multiplier itself: the morph outline's radius comes from
# spacing rather than from any single key here.
out["r_size_mul"] = out.get("r_size_mul", 1.0) * scale
```

Faithful to the original, where the morph painter reads it. Relay's `morph.py`
does not — it uses `spread` and `r_dot`. Checked across all five drawing
modules: `r_size_mul` appears nowhere but the line that sets it.

**Suggested:** delete the three lines, or keep them with a comment saying they
exist for parity with the upstream tuning rather than for this code.

---

## 8. `open_editor()` and `open_picker()` are the same function

`prompt_editor.py:370-381` and `look_picker.py:413-424` — identical
show-or-raise singletons, differing only in the class they construct:

```python
global _window
if _window is not None and _window.isVisible():
    _window.raise_()
    _window.activateWindow()
    return _window
_window = <Class>(...)
_window.show()
_window.raise_()
_window.activateWindow()
return _window
```

`open_compose()` is a third variant, and it genuinely differs — it has to cope
with a prebuilt window and refresh its callbacks. That one should stay as it
is.

**Suggested:** a `show_or_raise(state, build)` helper in `window.py`, used by
the two identical ones.

---

## 9. The watchdog is diagnostic scaffolding still running in production

`relay/watchdog.py` (88 lines) polls every **50 ms** on its own thread for the
whole life of the app, and `__main__.py` starts it at line 476 and prints its
summary at 509.

It was installed to catch the cursor-lock freeze — which is **still
unresolved**, so this is not dead code, it is instrumentation with a live
purpose. But it is worth naming as a decision rather than letting it drift
into permanence: a 20 Hz thread and a `sys._current_frames()` dump on every
long gap is not something an app ships with by default.

**Suggested:** leave it until the freeze is understood, then either remove it
or put it behind a `--watchdog` flag. Worth a line in the README either way,
since it currently writes `[stall]` blocks into the log that look alarming and
are not explained anywhere.

---

## 10. Smaller things — **Done**

**The README's file tree omits two modules.** `relay/watchdog.py` and
`relay/window.py` are both listed nowhere in the tree block, though every other
module is. `window.py` in particular is the shared frameless chrome — a reader
following the tree would not know it exists.

**Two unused imports:**

- `compose.py:16` — `QWidget`
- `prompt_editor.py:14` — `Qt`, and `:16` — `QWidget`

**`ctranslate2` is imported directly but not pinned.** `relay/translator.py` and
`relay/cuda_setup.py` both `import ctranslate2`, but `requirements.txt` only
gets it transitively through `faster-whisper==1.2.1`. That works today because
the faster-whisper pin is exact. Worth a comment saying so, or an explicit pin.

**Geometry duplicated inside `relay/orbs/`, deliberately.** Two cases:

- the ghost-sphere loop is byte-identical in `paths.py:90-95` and `:136-141`
- the lat/long lattice walk repeats three times in `lattice.py` (lines 36, 137, 174)

Both are duplicated in the upstream project too. Factoring them out would make
the port harder to diff against its source, which is the thing that makes it
auditable. **My recommendation is to leave these alone** and note why — but
they show up in any duplication scan, so they are listed here to save the next
person rediscovering them.

---

## What is *not* wrong

Worth recording, so the same ground is not covered twice:

- **No commented-out code anywhere.** Checked by parsing every comment line as
  Python and looking for anything that compiles as a statement.
- **No `TODO`, `FIXME`, `XXX` or `HACK` markers.**
- **Every `config.json` key is read by something.** All 31 defaults resolve to
  a reader outside `config.py`; the user's file has no orphan keys.
- **Every entry in `requirements.txt` is imported** by at least one module.
- **No stray sensitive files.** `transcripts/` and `prompt_report.md` are
  neither present nor tracked. `.gitignore` covers `relay.log` and
  `.orb_position.json`, which are the only two runtime files on disk.
- **The `orbs` public surface is nearly all used.** Of 16 exported names, only
  `TUNED_SIZES` has no reader outside the package — and it is used by the test
  suite, which is finding 1.

---

## Suggested order

1. **Get the tests into the repo** (finding 1) — everything else is easier to
   change safely once this is done, and only this one degrades with time.
2. Delete `flash()` and its 9 call sites (2), the resize debug print (4), the
   three unused names (6), `r_size_mul` (7), the two unused imports (10).
   All mechanical, all independently verifiable.
3. Extract `relay/theme.py` (3) and the two small helpers (5, 8).
4. Decide about the watchdog (9) when the freeze is understood.

## What was applied

Findings 2, 4, 6, 7 and 10, in one commit: **45 lines of code removed, 12
added back**, a net of **-33** across `relay/`, plus 11 lines of documentation
in the README and `requirements.txt`. No behaviour changed.

Two things fell out of the cuts that the review had not predicted:

- removing `DEFAULT_ORB_SIZE` made `overlay.py`'s `DEFAULTS` import dead, so
  that went too
- `scale_radii` was left with no comment explaining why it does *not* carry
  `r_size_mul`, which is exactly the sort of thing someone re-adds later. It
  now says so in its docstring.

Re-ran the same sweeps afterwards: no unused imports anywhere in the package,
and nothing defined-and-unreferenced except `_Tee.isatty()`, which is the known
false positive. All 8 test suites pass and the app runs.

Findings 1, 3, 5, 8 and 9 remain open. **Finding 1 is the one that matters** —
it is still the only item that gets worse with time.
