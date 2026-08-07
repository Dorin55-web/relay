# Tests

```bash
python tests/run.py
```

Runs all nine suites, each in its own process, and prints a line per suite.
About a minute for the lot.
Exit code is non-zero if any failed. To run a few:

```bash
python tests/run.py looks tooltip
```

These are not unit tests against mocks. Every one builds a real
`QApplication`, most build real windows, and a few ask Windows itself what it
would do. That is deliberate: almost nothing interesting here can be checked
any other way. Whether a click lands on a transparent pixel is a question for
the window manager, not for a mock.

## The suites

| Suite | What it holds down | ~time |
|---|---|---|
| `test_looks.py` | The nine orb drawings at eight sizes: no dot outside the box or under the radius floor, every look moves, the size retunes across the tuned span and magnifies outside it, the config round-trips, and every look is opaque enough to click | 2 s |
| `test_tooltip.py` | The prompt behind a menu label waits 3.5 s and restarts on any movement, driven through a real event loop | 3 s |
| `test_compose.py` | The typed-text path: Romanian in, English out, stale results dropped, the Send button naming the right target | 6 s |
| `test_editor.py` | The prompt editor driven by its own buttons — add, delete, reorder, save — plus recovery from a corrupt `prompts.json` and no temp file left behind | 33 s |
| `test_survives_editor.py` | The orb outliving the editor. `Qt.Tool` windows are not primary windows, so without `quitOnLastWindowClosed(False)` closing the editor takes the app down | 2 s |
| `test_resize_guard.py` | Which border pixels start a resize and which must not, walked one pixel at a time | 5 s |
| `test_watchdog.py` | The stall detector catching a real stall and naming the code that caused it | 10 s |
| `test_bluetooth.py` | The hands-free mic being avoided — and, more importantly, recording still working on a machine where the headset is the only microphone | 3 s |
| `test_first_open.py` | That opening the write window does not stall. Has to be its own process: the cost it guards is paid once per process, so checking for it after another suite has opened a window passes whatever the code does | 3 s |

`test_compose.py` needs the Marian translation model. The first run downloads
it; later ones are fast. `test_bluetooth.py` opens real audio devices.

## Nothing here touches your files

`context.isolate_state()` — called at the top of every suite — points
`prompts.json` and the orb's saved position at a throwaway directory.

That is not caution for its own sake. Before it existed, `test_looks` grew the
orb to 120 px, which pushed its saved anchor off the edge of the screen, and
the clamp that pulled it back wrote the new position out: **running the suite
moved the real orb 31 pixels to the left.** A test run that rearranges your
desktop is a test run you stop doing.

## `probes/`

Nine scripts that report numbers instead of passing or failing. They are for
when something needs investigating, and `run.py` deliberately ignores them —
a runner cannot tell whether a measurement went well.

| Probe | The question it answers |
|---|---|
| `window_open_cost.py` | What opening each of the three windows costs, with a heartbeat on the GUI thread. This is the one that found the first-open stall, and showed it belonged to whichever window went first rather than to the write window |
| `live_click_map.py` | How much of the orb currently on screen can actually be clicked. This is the one that found the click bug: 18%, in exactly the shape of the drawing |
| `click_map.py` | The same map, per look, without needing Relay running |
| `click_alpha.py` | The faintest backing Windows will still let you click. Answer: 1 in 255 |
| `gil_starve.py` | Whether building a window starves the thread holding the mouse hook |
| `hooks_down.py` | The same, with the real hooks installed, both ways round |
| `mouse_move.py` | What one sweep of the mouse across a window costs |
| `model_load_stall.py` | How long the GUI thread stops for when the text model first loads |
| `typing_load.py` | What typing a paragraph costs, when every pause fires a translation |
| `stream_leak.py` | Whether a start/stop cycle leaves PortAudio streams open |

Run one directly:

```bash
python tests/probes/live_click_map.py
```

`live_click_map.py` needs Relay to be running; the rest do not.
