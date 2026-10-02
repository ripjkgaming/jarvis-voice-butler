# Computer use

Jarvis can delegate a visual task in an already-open desktop app to an installed,
authenticated Codex CLI worker. The default executor is **`gpt-6-luna` with
`low` reasoning**. The voice agent remains the planner; the worker proposes one
JSON action from the latest screenshot and Python validates and performs it.
It never silently substitutes Astra or another model.

The tools are registered on the existing system-control specialist:

- `computer_use(task, max_steps=12)` starts one background task and returns its ID.
- `computer_use_status()` returns current progress or the last result.
- `computer_use_cancel()` stops further steps and drains any input already started.

For example, after opening the app the user named, delegate “In the open notes
app, find the search field and search for the physics revision note.” Give the
whole task and named target. Ordinary clicks, navigation and text entry use the
existing silent, single-use action arming; no added voice approval per step.
Jarvis announces the result through its existing session when the task settles.
The HUD activity item records task status; status includes an input count and
before/after observation IDs and hashes. A start acknowledgment is never success.

## Configuration

Set these in Jarvis's environment, not in the user's global Codex configuration:

| Variable | Default | Meaning |
| --- | --- | --- |
| `JARVIS_LOCAL` | existing setting | Must be `1`; cloud deployments cannot control a PC. |
| `JARVIS_COMPUTER_USE` | `1` | Set `0` to disable starting workers. |
| `JARVIS_COMPUTER_USE_MODEL` | `gpt-6-luna` | Exact executor model, verified against the CLI model catalog. |
| `JARVIS_COMPUTER_USE_EFFORT` | `low` | Must be supported by that exact model. |
| `JARVIS_CODEX_BIN` | `codex` on PATH, then the installed app bundle | Optional explicit CLI executable. |
| `JARVIS_COMPUTER_USE_MAX_STEPS` | `12` | Input limit, clamped to 1–30; a tool request can lower it. |
| `JARVIS_COMPUTER_USE_TIMEOUT` | `180` | Total task seconds, clamped to 10–600. |
| `JARVIS_COMPUTER_USE_TURN_TIMEOUT` | `45` | Startup/proposal seconds, clamped to 5–120. |
| `JARVIS_DESKTOP_SANDBOX` | unset | Existing private X display, e.g. `:99`; must already be running. |
| `JARVIS_SANDBOX_BUS_ADDRESS` | existing sandbox convention | Optional private session bus for the sandbox. |

The CLI route reuses the user's saved **ChatGPT login**. It does not read or copy
credential files, add an API-key path, or edit global configuration. Inherited
OpenAI API-key variables are removed from the child environment. Custom OpenAI
provider endpoints are rejected. Account/model failures stop before screenshot
capture or input. Catalog presence is not a guarantee of inference entitlement;
quota, account access or provider errors are reported without fallback.

The verified CLI version is **`codex-cli 0.159.2`**. Other versions fail closed
until their protocol and absence of executable tools have been reverified. The
worker uses the official stdio app-server, one ephemeral thread per task, explicit
model/effort, read-only policy, empty execution environments, and disabled built-in
tools, apps, plugins, hooks and MCP servers. An offline test below verifies the
actual outgoing model request exposes **zero tools**. Unexpected tool/approval
requests or model reroutes terminate the worker. The full goal is sent once;
later turns send the new image, compact observation and preceding action evidence.

## Surfaces and limits

The model process is headless. The default **surface is the entire visible Linux
desktop**; this is not window or application containment. Screenshots use
`spectacle`/ImageMagick and input uses the existing writable `/dev/uinput` backend
with a US keyboard layout. A configured private X display uses ImageMagick and
`xdotool` exclusively. Invalid or failed sandbox configuration cannot fall back
to the real desktop. The tool does not create a display, launch an app, or open a
browser itself. App opening remains with Jarvis's existing tools.

Supported actions are pointer move, single click, wheel scroll (1–5 notches),
1–500 printable ASCII characters, and the existing allowlisted individual keys.
There are no key chords, clipboard access, arbitrary commands, URLs as executable
actions, or multiline typing. Sending/publishing, purchases/bookings, destructive
operations, account/security changes, credentials, logins and CAPTCHA handling
stop at the boundary; the parent must use existing scoped tools and their own
authorization rules. This worker prepares ordinary navigation/draft steps only.

Every input requires a current, unused observation, valid coordinates/schema,
unchanged foreground identity/focus/geometry and matching screenshot/input
geometry. Uncached metadata brackets every capture and is checked again just
before injection. KDE uses a private DBus receiver and its own read-only, one-shot
KWin script, then unloads that script. A sandbox uses pinned X11 queries and never
uses the host's active-window watcher. Missing or ambiguous metadata stops input.
Geometry queries run before the screenshot; final input requires capture evidence
no more than three seconds old, including capture and final-metadata latency.

Each action declares the whole visible control's `region` on the 0–1000 desktop
grid. The region must lie inside the foreground window, span at least 8×8 pixels
and occupy at most half that window. Its protected pixels include a 12-pixel
margin and an independent 96×64 crop around point actions. Click/move/scroll
require every protected pixel to remain unchanged. Scroll also names its point
and moves the pointer there before scrolling. Unrelated clock/HUD animation is
allowed within bounded remote changes: 15% of the whole image, 8% of the foreground
and 12 of its 48 coarse cells. Broad scene changes and visible overlays covering
the target stop input even if its local crop matches.

Type/press require this task's own preceding verified left click, the exact same
region and unchanged foreground metadata. Each subsequent input consumes that
anchor, including pointer movement or typing; type then Return requires another
grounded click. A single uniform 1–3-pixel-wide, 8–40-pixel-high caret-shaped difference
triggers up to 800 ms of cancellable sampling: it must return to the original
pixels at the same location with the same alternate pattern and stable surrounding evidence. Shape alone never
permits typing. A static narrow glyph, moved/multiple caret, changed field outline
or changed metadata is rejected. Ambiguous animated targets can still stop a task.

Foreground identity cannot prove focused text-control identity. A programmatic
same-window focus change with no visible caret/outline is undetectable without
accessibility or app metadata. Our own grounded click plus strict visual region
is conservative evidence, not proof of the focused field. Fractional/scaled
geometry that does not match captured pixels is unsupported. The sandbox's
stacking query requires the pinned xdotool version and direct root-child windows;
unsupported window-manager reparenting fails closed.

A changed scene allows at most **two fresh observations and new proposals**,
within the original time budget; old coordinates are never replayed automatically.
Two inputs with no visual change stop the loop. Completion requires an explicit
visual result and is labeled `verification: model_visual`; it is a model judgment,
not an application-level proof. Semantic target/risk classification also depends
on the model; the schema and prompt do not provide per-app security isolation.

A process-shared file lease under `JARVIS_HOME` serializes computer-use tasks and
the existing direct desktop click/type/key/scroll tools. Other applications and
the human user can still change the screen; window/app operations are instructed
to wait until the task finishes. Cancellation never releases the lease while an
input thread, screenshot child or worker cleanup is still active. A current input
may finish before stopping; stop is not rollback. Process exit releases the lease.

Each task keeps screenshots in a private temporary directory (files mode `0600`)
and removes raw captures, observations and its worker at teardown. Action logs
omit typed text and screen contents. Task results stay in memory; restarting
Jarvis does not resume unfinished input. Screenshots are sent to the configured
Codex model for visual reasoning; ephemeral local files do not imply provider-side
zero retention.

## Isolated verification

From `jarvis_new`, the regular suite uses fake workers, screenshots and input:

```sh
uv run pytest -q tests/test_computer_use.py tests/test_computer_use_ownership.py tests/test_computer_surface.py tests/test_computer_focus.py tests/test_computer_freshness.py tests/test_codex_worker.py tests/test_desktop.py tests/test_system.py tests/test_handoff_desktop.py
```

The optional installed-CLI protocol test uses a localhost fake Responses server,
a generated one-pixel image and an HTTP 400 response. It sends **no inference
request to OpenAI**, passes no Authorization header to the fake server, and
launches no desktop app, browser or microphone:

```sh
JARVIS_CODEX_OFFLINE_SMOKE=1 uv run pytest -q tests/test_codex_worker.py
```

The durable probe is
`test_installed_cli_exposes_no_tools_to_local_fake_provider` in that test file.
It checks exact model, image input and an empty/absent tool list on the real CLI's
outgoing request. These tests also cover invalid schemas, stale/tampered images,
sandbox isolation, cancellation during spawning/capture/input/cleanup, model
unavailability/rerouting, bounded output, ownership, time/step/stall limits and
agent registration.

The default was changed to `gpt-6-luna` at the user’s request on 2026-10-02;
reasoning remains `low` and the same isolation and per-action validation apply.

Earlier on 2026-10-02 a separate read-only preflight verified the installed ChatGPT login,
`gpt-5.6-terra` catalog entry with image input and low effort, and ephemeral thread
creation. **No live model turn, desktop input, microphone call or visual-accuracy
benchmark was run.** Live entitlement, task success rate and latency remain
unverified. The current unit tests cannot establish those.

Official references: [Codex app-server](https://learn.chatgpt.com/docs/app-server),
[headless CLI/authentication](https://learn.chatgpt.com/docs/non-interactive-mode),
[GPT-6 Luna modalities](https://developers.openai.com/api/docs/models/gpt-6-luna).
