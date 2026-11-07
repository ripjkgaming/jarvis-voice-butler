# LOCKE//LINK: build plan (phase 2)

**Status:** the brief (`BRIEF.md`) is approved. All five open questions take their recommended
defaults. This document is the build contract for the implementing agent.

**How to read it:**
- If this plan and the brief disagree, this plan wins. Deviations are listed in §0.
- "MUST" is binding.
- Exact names (routes, files, env vars, cookies) are binding. Rename only with a recorded reason.

**Contents**
- §0 Locked decisions and deviations
- §1 Architecture and repo layout
- §2 Screens
- §3 Component inventory
- §4 Motion
- §5 State model
- §6 Bridge API additions
- §7 Security
- §8 Voice transport in the browser
- §9 Offline and reconnect
- §10 Accessibility
- §11 Performance budgets
- §12 Test plan
- §13 Build, deploy and release
- §14 Phased task list
- §15 Retire JarvisLink 3.0

---

## 0. Locked decisions and deviations

### Answers to the five open questions
1. **Native JarvisLink 3.0 (v3.6.1, `versionCode 19`) is retired**, only after the gate in
   §15 R0 passes. Until then it stays installed on the phone and keeps working.
2. **Guest ON does not lock the screen.** LOCK is a separate tile.
3. **Gate strength:**
   - Passphrase on a cold launch.
   - Fingerprint (a WebAuthn passkey) to re-enter after the soft lock.
   - A fresh fingerprint for every unlock, guest OFF, restart and shutdown.
   - Lock and guest ON fire instantly.
4. **Power on DECK:** sleep, restart and shutdown. Restart and shutdown use hold-to-confirm
   (1200 ms) plus the fingerprint. Logout is cut.
5. **No push and no laptop-initiated rings.** Navigation:
   - Tap the reactor's `⌗` satellite or the DECK header ring.
   - Say "deck" or "core".
   - Press system back.
   - There is no swipe anywhere.

   The mark's glyph-to-state mapping follows BRIEF.md.

### Deviations from the brief (with reasons)
- **The REMOTE tile is dropped from v1.**
  - *Why:* the remote broker in `src/remote/PROTOCOL.md` was never built. The bridge has no
    `/remote/*` routes.
  - *Effect:* DECK has **6 tiles**: GUEST, LOCK, UNLOCK, SCREENS, POWER, MIC. The reactor has
    **5 detachable segments**; MIC comes from the command line.
  - *Hook:* `caps.remote` in `/phone/v1/state` stays `false`. A later version adds the tile when
    the broker exists.
- **Battery telemetry stays.**
  - *Why:* the HUD instruments (`frontend/components/stark/instruments.tsx` and
    `hud/school-strip.tsx`) display `sys.phone`.
  - *Effect:* the web app posts `navigator.getBattery()` to the existing `POST /phone/telemetry`
    shape every 60 s while it is visible. There is no UI for this on the phone.
- **Core polygon is 16 vertices, not 8.** This was a fix inside the brief; the source of truth is
  `icon/mark-glyphs.json`.

---

## 1. Architecture and repo layout

```
phone (Chrome / WebAPK)                     laptop (ripjk.tailea4e7f.ts.net)
┌────────────────────────┐  tailnet HTTPS   ┌──────────────────────────────────────────┐
│ LOCKE//LINK PWA        │ ───────────────► │ tailscale serve :443                     │
│  GATE · CORE · DECK    │                  │   /        → ~/.jarvis/phone-web/current │
│  service worker shell  │                  │   /api     → http://127.0.0.1:4317       │
│  AudioWorklets         │ ◄─── wss ──────► │   /voice   → http://127.0.0.1:7880/voice │
└────────────────────────┘                  │ bridge.py (loopback) + phone_device.py   │
                                            │ voice hub (src/jarvis_rt/transport)      │
                                            └──────────────────────────────────────────┘
```

### New files
| Path | Purpose |
|---|---|
| `phone-web/` | The PWA: Vite, TypeScript and Preact. Its own `package.json` and pnpm lock. |
| `phone-web/src/` | App code (layout in §3). |
| `phone-web/public/icons/` | Rasterised PNGs generated from `docs/phone-app/icon/*.svg` (§13). |
| `src/phone_device.py` | Pairing, device credentials, gate sessions, auth for `/phone/v1/*`. |
| `src/phone_webauthn.py` | Minimal WebAuthn: none-attestation registration and ES256 assertion verification. |
| `src/phone_control.py` | `/phone/v1/control` actions and readbacks, plus the `/phone/v1/state` aggregator. |
| `scripts/phone_pair.py` | Laptop CLI: print a pairing QR, list devices, revoke a device. |
| `scripts/phone_web_deploy.sh` | Build, atomically swap the release, apply the `tailscale serve` mounts. |
| `tests/test_phone_device.py`, `test_phone_webauthn.py`, `test_phone_gate.py`, `test_phone_control.py`, `test_phone_state.py`, `test_bridge_api_prefix.py` | Python tests (§12). |

### Pinned toolchain (exact versions)
- `vite@5.4.11`
- `preact@10.24.3`
- `@preact/signals@1.3.1`
- `typescript@5.6.3`
- `vitest@2.1.8`
- `@playwright/test@1.49.1`

There is no CSS framework and no animation library; the motion engine is hand-written (§4).

**Python dependencies (`pyproject.toml`):**
- Make `cryptography>=43` explicit. It is already in `.venv` at version 50.
- Add `segno==1.6.1` for the QR code in the pairing CLI.

### Code reuse
- **ASCII engine:** import `frontend/lib/ascii.ts` through a Vite alias, `@hud/ascii` →
  `../frontend/lib/ascii.ts`. It has no imports, so it is safe to share. The PWA uses its
  `reactorField`, `glyphAtlas`, `scramble`, `prefersReducedMotion` and `lamp` exports.
  **Do not fork it.**
- **Fonts:** copy `frontend/fonts/CommitMono-400-Regular.otf` and `CommitMono-700-Regular.otf`
  into the build, subset to ASCII, box drawing, the block glyphs in `ascii.ts` and
  `⌗⌬◁▲▶●○`. Display labels use `Rajdhani-SemiBold.ttf`.

### Colour tokens (`phone-web/src/theme.css`, on `:root`)
| Token | Value |
|---|---|
| `--navy-hi` | `#12295c` |
| `--navy-lo` | `#04081a` |
| `--ink` | `#02040c` (page background) |
| `--cyan` | `#5fe3ff` |
| `--ice` | `#e8fbff` |
| `--amber` | `#ffb347` |
| `--teal` | `#2ee6d6` |
| `--red` | `#ff5a5a` (failures only) |
| `--dim` | `rgba(232,251,255,.45)` |

- The app is dark only; there is no light theme.
- Every visual must show real data. If a value is unknown, show `--` with the `dim` colour.
  **Never fake a value.**

---

## 2. Screens

**Frame:**
- Portrait only, 360–430 CSS px wide.
- Side gutter 16 px; respect `env(safe-area-inset-*)`.
- All three pages are absolutely positioned layers inside one `#stage`. `W` is the viewport
  width.

### 2.1 Status strip (shared by all pages, never moves)
- 28 px tall, at the top.
- A single row of ASCII lamps (`●` on, `◐` warn, `○` off, `--` unknown) with short codenames.

| Lamp | Source | Values |
|---|---|---|
| `LNK` | client RTT of the last `/phone/v1/state` | ms, or `OFF` |
| `CALL` | `voice.call_live` and the local call phase | `LIVE` / `IDLE` / `RCN` (reconnecting) |
| `GST` | `laptop.guest` | `ON` (amber) / `OFF` |
| `LCK` | `laptop.locked` | `LOCK` / `OPEN` / `--` |
| `PWR` | `laptop.power` | `87%⚡`, `87%`, or `AC` |
| `PRS` | `laptop.presence` | `DESK` / `AWAY` / `--` |
| `UPD` | the service worker has a new version | shown only when one is waiting |

- On GATE, only `LNK` and `UPD` show. The other lamps get `opacity:0` but keep their DOM nodes,
  because they are morph targets.

### 2.2 GATE
| Element | Placement | Notes |
|---|---|---|
| Mark | centre, 160 px square, at 38% of the viewport height | Loops the ignition cycle (§4.4). |
| Glyph row | 28 px under the strip | Pairing and hint text in ASCII, e.g. `UNPAIRED // RUN phone_pair ON HOST`, or `DEVICE jl-3f2a // GATE SEALED`. It becomes the status strip in the morph. |
| Decode line | bottom, 48 px tall, full width minus gutters | A `<input type=password autocomplete=current-password>` that is visually hidden. On top of it, a canvas or `<pre>` echo shows one `▓` per character, and the last character scrambles in with `scramble()`. The return key submits. It becomes the command line in the morph. |
| Fingerprint button `⌬` | right end of the decode line, 48×48 | Shown only in the soft-locked state (§7.3). |
| Error line | above the decode line | `DENIED · 4 LEFT`, or `LOCKOUT 60s` with a countdown from the server's `retry_after`. |

**Gate modes:**
- **Unpaired:** the decode line is disabled and the glyph row shows instructions.
- **Paired with no passkey yet:** this happens straight after pairing. The app prompts passkey
  registration automatically, then shows the passphrase prompt.
- **Cold:** the passphrase is required.
- **Soft-locked:** the fingerprint or the passphrase is accepted.

### 2.3 CORE
| Element | Placement | Notes |
|---|---|---|
| Reactor | centred, at 40% of the viewport height, diameter `R = min(0.62·W, 260)` px | Three layers: (a) an ASCII `reactorField` canvas at 30 fps while idle and 60 fps while a call is live; (b) **5 segment elements** (SVG arcs of 40° each) at angles **324°, 36°, 108°, 180°, 252°** (clockwise from north) on radius `R/2 − 6`; (c) the **mark** (an SVG with a 108 viewBox, rendered at `0.42·R`) in the centre. **Tap** starts or ends the call. **Long press** (450 ms) toggles phone mic mute during a call. |
| Nav satellite `⌗` | on the reactor's north axis, `R/2 + 22` px from its centre | A 48×48 hit target that opens DECK. |
| Captions | under the reactor, 5 lines, max height `5 × 18 px` | Newest line last. Finals use `ice`; partials use `cyan` at 60%. Command replies render as `JARVIS> …`; when the reply came through `via_route`, they get a `⚡12ms` suffix. |
| Command line | bottom, 48 px | Text input plus a `↵` send button, max 2000 chars. Posts to `/phone/v1/command` with `instant:true`. Disabled while offline. |
| Toast | above the command line | One line, auto-hides after 4 s; red for failures. |

### 2.4 DECK
| Element | Placement | Notes |
|---|---|---|
| Header ring | top-left, 56 px, directly under the strip | The shrunken reactor ring with the live mark inside it. **Tap** goes back to CORE. To its right: the title `DECK // ripjk` (Rajdhani 600, 20 px) and the **caption ticker** (one line, the latest final caption, ellipsised). |
| Tile grid | 2 columns, gap 8 px, tile height 92 px | Row 1: **GUEST, LOCK**. Row 2: **UNLOCK, SCREENS**. Row 3: **POWER** (spans 2 columns). |
| MIC tile | bottom row, spans 2 columns, 64 px | PC mic mute toggle (the wake listener). |
| POWER expanded | POWER grows to 132 px and reveals 3 hold buttons: `SLEEP` (tap), `RESTART` and `SHUTDOWN` (each hold 1200 ms then fingerprint) | The expansion is itself a FLIP spring morph. Tapping POWER again, or any other tile, collapses it. |

**Tile anatomy** (and the meaning of a tap):
- Top left: the label (Rajdhani 600, 15 px).
- Top right: a lamp.
- Middle: the state value (Commit Mono 700, 22 px; real readback only).
- Bottom: the age of that value (`2s`) and a `⌬` glyph if the action needs a fingerprint.

| Tile | Shows | Tap |
|---|---|---|
| GUEST | `ON` / `OFF` | Toggle. OFF needs `⌬`. |
| LOCK | `LOCKED` / `OPEN` / `--` | Lock (instant). |
| UNLOCK | same lock state, from the other side | Unlock (`⌬`). |
| SCREENS | `ON` / `OFF` / `--` | Toggle: `screens_off` or `screens_on`. |
| POWER | battery and AC | Expand or collapse. |
| MIC | `LIVE` / `MUTED` / `--` | Toggle: `mic_mute` or `mic_unmute`. |

---

## 3. Component inventory (`phone-web/src/`)

| File | Responsibility |
|---|---|
| `main.tsx` | Boot: register the SW, read `#pair=` from the URL fragment, mount `<App>`. |
| `app.tsx` | `#stage` with the 3 page layers, the strip, the toast, and the global key/back handler (`popstate`). |
| `store.ts` | The signals store from §5, plus pure reducers. |
| `api.ts` | `fetch` wrapper: `/api` prefix, `X-Jarvis-Phone: 1`, `credentials:'same-origin'`, 8 s timeout, typed errors (`offline`, `auth`, `gate`, `stepup`, `server`). **POSTs never retry.** |
| `poll.ts` | `/phone/v1/state` poller: 2 s while visible, stops while hidden, immediate refresh after each control. |
| `webauthn.ts` | `navigator.credentials.create/get` with base64url helpers, plus the step-up flow (challenge → get → return the assertion). |
| `voice/call.ts` | Call lifecycle state machine (§8, §9). |
| `voice/ws.ts` | WebSocket framing: binary kind byte, JSON topics, ping/pong. |
| `voice/mic-worklet.ts` | AudioWorklet: Float32 at 16 kHz → PCM16 LE, 512-sample frames, posts the RMS level. |
| `voice/play-worklet.ts` | AudioWorklet: PCM16 24 kHz ring buffer (120 ms target, 1 s max), a `flush` command, posts the RMS level. |
| `motion/spring.ts` | Spring integrator and the `Spring` class (§4.1). |
| `motion/loop.ts` | The single rAF loop: registers active springs and stops when all have settled. |
| `motion/flip.ts` | Measures morph pairs and computes from/to transforms. |
| `motion/page-morph.ts` | Morph maps per page pair, pair springs, retargeting (§4.3). |
| `motion/mark.ts` | Mark model: loads `mark-glyphs.json` (copied at build time), per-part springs, writes into the SVG (§4.4). |
| `motion/haptics.ts` | `navigator.vibrate` patterns (§4.6). |
| `ui/StatusStrip.tsx` | §2.1 |
| `ui/Mark.tsx` | The mark SVG: 1 ring circle, 4 satellite `<g>`s, 1 core `<path>`. |
| `ui/Reactor.tsx` | Canvas reactor field, 5 segments, the mark, the nav satellite. |
| `ui/Captions.tsx`, `ui/CommandLine.tsx`, `ui/Toast.tsx` | §2.3 |
| `ui/DecodeLine.tsx` | §2.2. Shares its outer box styling with `CommandLine` so the morph between them reads as one object. |
| `ui/Tile.tsx`, `ui/PowerTile.tsx`, `ui/HoldButton.tsx` | §2.4. `HoldButton` shows progress with a `scaleX` bar. |
| `pages/Gate.tsx`, `pages/Core.tsx`, `pages/Deck.tsx` | Layout only. Every morphable element carries `data-morph="<key>"`. |
| `sw.ts` | Service worker (§9.3). |
| `theme.css` | Tokens and base styles. |
| `manifest.webmanifest` | §13 |

---

## 4. Motion

### 4.1 Spring engine (`motion/spring.ts`)
- **Model:** mass 1, stiffness `k`, damping `c`. Acceleration `a = −k(x − target) − c·v`.
- **Integration:** semi-implicit Euler at a fixed `dt = 1/240 s`. Each rAF consumes the elapsed
  time in fixed substeps, and the frame delta is clamped to 64 ms.
- **Settled:** when `|x − target| < ε` and `|v| < 10·ε`. Normalised springs (0..1) use
  `ε = 0.0005`; px values use `ε = 0.05`. At that point `x` snaps to `target` and the spring
  unregisters from the loop.
- **`setTarget(t)`** only changes the target. **Position and velocity are never reset.** This is
  the whole interruptibility contract.
- **Presets:**

| Preset | k | c | Use |
|---|---|---|---|
| `page` | 380 | 39 (critical, 2√k) | page morph pairs, settles in about 350 ms |
| `mark` | 260 | 32 (critical) | mark parts, about 450 ms |
| `micro` | 700 | 42 (ζ 0.8) | tile press, toggles, power expand |
| `shake` | 900 | 18 (ζ 0.3) | error nudge |

### 4.2 Render rules
- **During motion, only `transform` and `opacity` are written,** through `el.style.transform`
  and `el.style.opacity`. Moving elements get `will-change: transform, opacity`, which is
  removed on settle.
- **Never read layout inside rAF.** `getBoundingClientRect` runs only in `flip.measure()`.
- **When `flip.measure()` runs:**
  - on boot after fonts load (`document.fonts.ready`);
  - on resize or orientation change;
  - on POWER expand or collapse;
  - never during a morph.
- **The two exceptions to "transform/opacity only":**
  - The mark's core `<path d>`: under 128 px, one path, under 0.3 ms per frame.
  - The ASCII canvases (the reactor field and the decode echo).

### 4.3 Page morph (the headline transition)
**Mechanics:**
- All three pages are always mounted.
- Each settled page has `visibility:visible`, `pointer-events:auto` and `inert=false`. Every
  other page is hidden **after** its outgoing morph settles. During a morph both pages are
  visible, and pointer events stay live on the target page.

**Pair model:**
- Each morph pair `P` has keys `(from, to)` and **one** normalised spring `p ∈ [0, 1]`
  (preset `page`).
- `p = 0` means the source page's layout and `p = 1` means the target's.
- From the measured rects, compute a delta transform
  `Δ = translate(dx, dy) scale(sx, sy) rotate(θ)` that maps the *to* element onto the *from*
  rect.

**Each frame:**
- **Incoming element:** `transform = lerpT(Δ, identity, p)` and
  `opacity = clamp((p − 0.15) / 0.35)`.
- **Outgoing element:** `transform = lerpT(identity, Δ⁻¹, p)` and
  `opacity = 1 − clamp((p − 0.25) / 0.35)`.
- **Text inside either element** never scales visibly. Labels are separate children with
  opacity `sstep(0.55, 0.85, p)` (incoming) or `1 − sstep(0.1, 0.4, p)` (outgoing), and the
  incoming label runs `scramble()` decode over 180 ms once `p > 0.55`.

`lerpT` interpolates translate, scale and rotate component-wise, which is the same as
transform-origin centre math. Rotation takes the shortest path.

**Unpaired elements:**
- Incoming: `opacity = sstep(0.4, 1, p)` and `scale = 0.9 + 0.1·p`.
- Outgoing: the mirror.
- They share a single page-level spring.

**Stagger:**
- Pair `i` gets its `setTarget` call delayed by `delay_i`, applied **only** when the morph
  starts from rest.
- If a morph is interrupted, every pair retargets immediately with no delay. Delays use the
  loop's clock, not `setTimeout`.

**Interrupt:**
- A trigger that arrives mid-morph calls `setTarget` on every pair spring of the active map,
  with the new target value. A reverse (e.g. DECK→CORE during CORE→DECK) just targets 0.
- A trigger to a third page mid-morph (e.g. CORE→GATE while going to DECK) first retargets
  the current map back to 0. Once the CORE layout is reached (`p < 0.02` on all pairs), the new
  map starts. This is the only queued case, and it still never jumps.
- Input taps during a morph are honoured.

**Morph maps** (`motion/page-morph.ts`, keys = `data-morph` values):

| Map | from → to | Extra |
|---|---|---|
| GATE→CORE | `gate.mark` → `core.mark` | Delay 0. The mark's glyph also retargets to `star` (§4.4). |
| | `gate.decode` → `core.cmd` | Delay 30 ms. |
| | `gate.glyphrow` → `strip` (the full lamp row) | Delay 60 ms. The lamp nodes fade in by `sstep(0.5, 1, p)`. |
| | (none) → `core.captions` | Unpaired, but uses `scaleY` from 0 with origin top. |
| | (none) → `core.segments`, `core.nav` | Unpaired. The segments rotate in from −30° in addition to fading. |
| CORE→DECK | `core.ring` (reactor canvas + segment container) → `deck.headring` | Delay 0. The canvas is scaled as a layer; the field keeps rendering at 30 fps inside it. |
| | `core.mark` → `deck.mark` | Delay 0. |
| | `core.seg.324` → `deck.tile.GUEST` | Delays: 18 ms × rank. The rank is the position clockwise from 324°: GUEST 0, LOCK 1, SCREENS 2, POWER 3, UNLOCK 4. |
| | `core.seg.36` → `deck.tile.LOCK` | |
| | `core.seg.108` → `deck.tile.SCREENS` | |
| | `core.seg.180` → `deck.tile.POWER` | |
| | `core.seg.252` → `deck.tile.UNLOCK` | Each segment's Δ includes `rotate(segment angle)` and a scale of about 0.12. The tile border is drawn by the same element, so the segment visibly *becomes* the tile. |
| | `core.cmd` → `deck.tile.MIC` | Delay 40 ms. |
| | `core.captions` → `deck.ticker` | Delay 20 ms. The captions box collapses with `scaleY` to 0.15 while the ticker decodes in. |
| | `core.nav` (`⌗`) → `deck.title` | Delay 0. The glyph fades out as the title decodes in. |
| | `strip` → `strip` | Shared and static; it is not animated. |
| DECK→CORE | the reverse of CORE→DECK (target 0) | |
| CORE→GATE | the reverse of GATE→CORE | Triggered by gate close: idle timeout, explicit close, a 401 or 403 with gate state, or WS close 4013. |

**Triggers:**
- `⌗` tap and header-ring tap.
- Voice:
  - When the `/phone/v1/command` reply has `{"ui":"deck"}` or `{"ui":"core"}`, or the user's
    own text matches `^(open )?(the )?deck$` or `^core$` (checked client-side before sending).
    These words are handled locally and are not sent to the bridge.
  - During a live call, the same words heard in a final `caption` from the user (`role:user`
    when present; otherwise skip) are handled the same way.
- Android back: `popstate`. The app pushes one history entry per page change so that back
  maps to the reverse morph; back on GATE or CORE exits the app normally.
- **Swipe gestures are never bound.** Overscroll is disabled with `overscroll-behavior: none`.

**Reduced motion:** when `prefersReducedMotion()` is true, every map is replaced by a 120 ms
opacity crossfade of the two pages (a CSS transition on opacity only), and the mark snaps
(§4.4).

### 4.4 Mark morph (`motion/mark.ts`)
**Source:**
- `docs/phone-app/icon/mark-glyphs.json`, copied into the build as `src/motion/mark-glyphs.json`
  by the icons script.
- Parts: 1 ring, 4 satellites, and a core polygon with 16 vertices.

**Springs (preset `mark`):**
- Each core vertex has an x spring and a y spring (32 springs).
- Each satellite has springs for x, y, scale, rotate and opacity (20 springs).
- The ring has springs for colour (RGB, 3), dash amount (1) and spin rate (1).
- The core fill colour uses RGB springs (3).
- Colours interpolate in linear RGB.

**Writes:**
- The core `d` attribute.
- Satellite `transform` and `opacity`.
- The ring `stroke` and `stroke-dasharray`. The dash amount `g ∈ [0, 1]` maps to
  `dasharray = "3 {2.2·g}"`, and `g = 0` means solid.

**State → glyph:**
- `locked`: when `laptop.locked === true`. This overrides everything else.
- Otherwise, by the call's agent state:

| Agent state | Glyph |
|---|---|
| `listening` | `orbit` |
| `thinking` | `flame` |
| `speaking` | `play` |
| `idle`, or no call | `star` |

- **Guest modifier:** while `laptop.guest` is true, it applies on top of any glyph. The ring
  colour targets `amber` and the dash amount targets 1.

**Live modulation:** additive, and does not change targets.
- **listening:** the satellite orbit radius is `14 + 6·micRms` px in mark units, and the
  satellites orbit at 0.5 rev/s (a rotate transform on the satellite group).
- **speaking:** the core scales by `1 + 0.12·playRms` (a transform on the core group).
- **thinking:** flicker; the core's `scaleY` follows `1 + 0.05·sin(t·13) + 0.03·sin(t·29)`.
- **idle:** the ring spins at 1 rev/20 s (a rotate transform on the satellites group). Skip the
  spin when reduced motion is on.

**Ignition cycle (splash and GATE):**
- The order is orbit → flame → play → star, holding each glyph for 600 ms.
- GATE loops it until the gate opens, then retargets to the live state glyph from wherever
  the mark currently is.
- **Splash:** the WebAPK splash is Chrome's static one. The in-app first paint shows the mark
  at the centre and runs one cycle while `/phone/v1/state` loads.

**Interrupt:** a state change mid-morph calls `setTarget` on all part springs; the cycle timer is
cancelled.

**Reduced motion:** the mark swaps to the target glyph with a 120 ms opacity crossfade between
two stacked SVGs. There is no modulation.

### 4.5 Micro-interactions
| Interaction | Motion |
|---|---|
| Tile press | `scale` 1 → 0.96 on pointerdown and back on pointerup (`micro`). |
| Toggle commit | The lamp's opacity pulses 1 → 0.4 → 1 (`micro`), and the value text `scramble`s to the new readback over 180 ms. |
| Pending action | The lamp glyph cycles through `SPIN` from `ascii.ts` at 12 fps until the response arrives. A timeout after 8 s counts as a failure. |
| Failure | `translateX` shake: target +6 px then 0 (`shake`), red lamp for 2 s, and a toast. |
| HoldButton | A progress bar with `scaleX` 0 → 1 over 1200 ms, linear. Releasing early springs back to 0 (`micro`). On complete, the fingerprint prompt opens. |
| POWER expand | FLIP: the tile height and the reveal of its 3 buttons, using `scaleY` on the container with counter-scaled children and the `micro` preset. Re-measure afterwards. |
| Decode-line keypress | The new `▓` scrambles in over 80 ms. |
| Wrong passphrase | Shake plus a short scramble of the error line. |
| Command send | `↵` flashes; the reply line decodes in from `NOISE` over 240 ms. |

### 4.6 Haptics (`navigator.vibrate`; no-ops where unsupported)
| Event | Pattern (ms) |
|---|---|
| Tap on a tile, `⌗` or the reactor | `8` |
| Action verified (readback matches) | `[12, 40, 12]` |
| Action failed | `[40, 30, 40]` |
| Hold progress at 25, 50 and 75 % | `5` each |
| Call connected | `[10, 60, 20]` |
| Call dropped | `[60]` |
| Gate opened | `[8, 30, 8, 30, 16]` |

Haptics are never the only feedback for anything.

---

## 5. State model (`store.ts`, Preact signals)

```ts
type Lamp<T> = { value: T | null; at: number /* ms epoch of readback */ };

link:    { phase: 'online'|'offline'|'connecting'; rttMs: number|null; lastOk: number|null }
auth:    { paired: boolean; passkey: boolean;
           gate: 'cold'|'soft'|'open'; gateExpiresAt: number|null; softAt: number|null;
           lockoutUntil: number|null; triesLeft: number|null }
laptop:  { guest: Lamp<boolean>; locked: Lamp<boolean>; screens: Lamp<'on'|'off'>;
           micMuted: Lamp<boolean>; presence: Lamp<'present'|'absent'|'unknown'>;
           power: Lamp<{ percent: number; charging: boolean }> }
voice:   { callLive: boolean; owner: string|null; agentState: string|null; waking: boolean }
call:    { phase: 'idle'|'minting'|'connecting'|'live'|'reconnecting'|'ending'|'error';
           muted: boolean; room: string|null; attempt: number; error: string|null;
           agent: 'idle'|'listening'|'thinking'|'speaking'; micRms: number; playRms: number }
captions:{ lines: Array<{ id: string; who: 'user'|'jarvis'; text: string; final: boolean;
           viaRoute?: number }> }   // max 50 kept, 5 shown
page:    { current: 'gate'|'core'|'deck'; target: 'gate'|'core'|'deck' }
actions: Record<ControlAction, { phase: 'idle'|'pending'|'ok'|'failed'; error?: string; at: number }>
sw:      { updateReady: boolean }
```

- **Derived:** `markGlyph` (§4.4), `stripLamps`, and `canControl` (= `link.online` and
  `auth.gate === 'open'`).
- **Only one writer per slice:**
  - `poll.ts` writes `laptop`, `voice` and `link`.
  - `call.ts` writes `call` and `captions`.
  - `api.ts` writes `auth` on auth errors.
- **Persisted** (in `localStorage` wrapped in try/catch, comfort only): the last page and the
  last 50 caption lines. **Never auth material.**

---

## 6. Bridge API additions

### General rules
- All new routes live in `src/bridge.py`'s handlers and delegate to the new modules.
- **Responses** are JSON with `ok: bool`, and `error` when `ok` is false.
- **Body size caps:** 4 KiB, except passkey registration at 16 KiB.
- `/phone/v1/*` must handle `OPTIONS`; `do_OPTIONS` already covers it.

### 6.0 Prefix normalisation
- At the top of `do_GET` and `do_POST`, if the path starts with `/api/`, strip the `/api`
  prefix before any route matching.
- This makes the `tailscale serve` mount work whether or not Tailscale strips the mount path.
  Covered by `test_bridge_api_prefix.py`.

### 6.1 Auth hook (`phone_device.authorize(handler, route) -> Principal | None`)
**Placement:** call it **before** the existing `_authorized()` check, only for routes that start
with `/phone/v1/` and are listed below.

**Accepted credentials:**
- **(a) Bearer token**, exactly as today: an owner CLI or a test.
  `Principal(kind='bearer', gate='open', device=None)`.
- **(b) Device cookie:** cookie `jl_dev=<device_id>.<secret_b64url>`, plus the header
  `X-Jarvis-Phone: 1`, plus an `Origin` that equals `JARVIS_PHONE_ORIGIN` if present. The
  device must exist and `sha256(secret)` must match.
  `Principal(kind='device', device=…, gate=<state from the jl_gate cookie>)`.

**Results:**
- If neither credential matches: **401** `{"ok":false,"error":"unauthorized","auth":"pair"}`.
- If the route needs an open gate and it isn't: **401**
  `{"ok":false,"error":"gate","gate":"cold"|"soft"}`.

**Config:**
- `JARVIS_PHONE_ORIGIN`: e.g. `https://ripjk.tailea4e7f.ts.net`. Required in order to enable
  device auth.
- The RP ID is the origin's hostname.

**Route table:**

| Route | Method | Auth | Gate open? | Guest gate exempt? | Body / response |
|---|---|---|---|---|---|
| `/phone/v1/pair` | POST | pairing code only | no | yes | `{code, name}` → sets `jl_dev`; `{ok, device_id, registration: PublicKeyCredentialCreationOptions}` |
| `/phone/v1/passkey` | POST | device | no (only within 10 min of pairing, or with an open gate) | yes | `{credential}` (the JSON-encoded registration response) → `{ok}` |
| `/phone/v1/challenge` | POST | device | no | yes | `{purpose: "gate"\|ControlAction}` → `{challenge, rpId, allowCredentials, timeout:60000, userVerification:"required"}` |
| `/phone/v1/gate` | POST | device | no | yes | `{passphrase}` or `{assertion}` → sets `jl_gate`; `{ok, gate:"open", expires_in_s, soft_after_s:600}`; on failure `{ok:false, error, tries_left, retry_after}` |
| `/phone/v1/gate/close` | POST | device | no | yes | `{}` → clears `jl_gate` |
| `/phone/v1/state` | GET | device or bearer | yes | yes | §6.3 |
| `/phone/v1/control` | POST | device or bearer | yes | **yes** | §6.4 |
| `/phone/v1/command` | POST | device or bearer (as today) | yes | already handled before the guest gate | unchanged; the reply gains an optional `ui` field (§4.3) |
| `/phone/v1/session` | POST | device or bearer (as today) | yes | unchanged | unchanged. The PWA ignores `serverUrl` and dials `wss://<origin>/voice` (§8). |
| `/phone/v1/capabilities` | GET | device or bearer | no | yes | gains `features.control`, `features.state`, `features.webauthn` set to true |
| `/phone/telemetry` | POST | device or bearer | yes | already exempt | unchanged shape |

### 6.2 Cookies
- **`jl_dev`:** `HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=15552000` (180 days).
- **`jl_gate`:** `HttpOnly; Secure; SameSite=Strict; Path=/`, with no Max-Age (a session cookie).
  - Its value is `<gate_id>`, a random 32-byte b64url string.
  - Gate records live **in bridge memory only**:
    `{gate_id: {device_id, passphrase_at, last_seen}}`.
  - **Open** while `now − last_seen < 600 s` and `now − passphrase_at < 43200 s`.
  - **Soft** while `now − last_seen ≥ 600` but `now − passphrase_at < 43200`. In this state a
    `{assertion}` reopens the gate.
  - **Cold** otherwise. A bridge restart also makes every gate cold.
- Every authorised device request with an open gate updates `last_seen`.

### 6.3 `GET /phone/v1/state`
```json
{
  "ok": true, "api_version": 1, "ts": 1762500000.123,
  "laptop": {
    "host": "ripjk",
    "guest": false,
    "locked": false,
    "screens": "on",
    "mic_muted": false,
    "presence": "present",
    "power": {"percent": 87, "charging": true}
  },
  "voice": {"call_live": false, "voice_owner": null, "agent_state": null, "waking": false},
  "gate": {"state": "open", "expires_in_s": 40000, "soft_in_s": 600},
  "caps": {"remote": false, "presence": true, "webauthn": true},
  "ages": {"locked": 0.4, "screens": 3.1, "mic_muted": 0.2, "presence": 22.0}
}
```

**Sources** (each one cached; **never block the request on a slow probe**: return the cached
value or `null`, and refresh in a background thread):

| Field | Source |
|---|---|
| `guest` | `watch_mode.guest_required()` (live, cheap) |
| `locked` | new `power_actions.lock_state(run)`, which runs the existing `LOCK_READ_ARGV` (`gdbus … ScreenSaver.GetActive`, 2 s timeout) and parses it with `parse_lock_active`. Cache TTL 2 s. Returns `null` on failure. The bridge must run with `DBUS_SESSION_BUS_ADDRESS`; if it is missing, log once and return `null`. |
| `screens` | `run_phone_tool("screens_state", {})`: `"off"` if every output is disabled, otherwise `"on"`. Cache TTL 10 s (kscreen-doctor is slow). |
| `mic_muted` | `_wake_rpc({"status": True})`, then read the mute field (match the existing `GET /mic` reply). Cache TTL 2 s. |
| `presence` | `presence.check_presence()["state"]` (or the equivalent key). Cache TTL 60 s, refreshed by a background thread only while some device polled in the last 30 s. Disabled when `JARVIS_PHONE_PRESENCE=0`, in which case it is `"unknown"` and `caps.presence` is false. |
| `power` | `_laptop_power()` (existing) |
| `voice.*` | the same logic as `GET /room`: `read_hud_room`, `read_voice_snapshot`, `read_hud_waking` |

### 6.4 `POST /phone/v1/control`
**Request:** `{"action": "<ControlAction>", "assertion"?: {...}}`

**Rules:**
- The phone is always admin: **this route is handled before the bridge's guest gate** (the
  `watch_mode.guest_required()` block around `bridge.py:3781`), in the same early section as
  `/phone/v1/command`.
- It must not call `taskbar.dispatch_power` (which blocks guests). It calls
  `power_actions.dispatch_sync` directly.
- Every call goes to `log_action("phone_control", f"{action} {status}")`.

**Step-up actions** (a `*` in the table below): require an `assertion` for a challenge issued
with `purpose == action` in the last 60 s. The challenge is single use and verified by
`phone_webauthn.verify_assertion`. If it is missing or invalid: **403**
`{"ok":false,"error":"stepup","stepup":action}`.

| Action | Implementation | Readback |
|---|---|---|
| `guest_on` | `watch_mode.enter_guest()` (no camera, no card) | `guest_required()` is true |
| `guest_off`* | `watch_mode.request_stop()`, then `watch_mode.restore_owner()` (same as `/watch/owner`) | `guest_required()` is false |
| `lock` | `power_actions.dispatch_sync("lock", run)` (after the fix in §6.5) | `lock_state()` is true, polled 3× at 300 ms |
| `unlock`* | `run_phone_tool("unlock", {})`, i.e. `loginctl unlock-sessions` | `lock_state()` is false, polled 3× at 300 ms |
| `screens_off` | `run_phone_tool("screen_off", {})` (all outputs) | `screens_state` |
| `screens_on` | `run_phone_tool("screens_restore", {})` | `screens_state` |
| `sleep` | `power_actions.dispatch_sync("sleep", run)` | none (`dispatched_unverified`) |
| `restart`* | `power_actions.dispatch_sync("restart", run)` | none |
| `shutdown`* | `power_actions.dispatch_sync("shutdown", run)` | none |
| `mic_mute` / `mic_unmute` | `_wake_rpc({"mute": true/false})` | the reply's mute state |

**Response:**
```json
{"ok": true, "action": "lock", "status": "verified"|"dispatched_unverified"|"failed"|"mismatched",
 "readback": {"locked": true}, "state": { ...same "laptop" object as §6.3... }}
```
`ok` is false only when `status == "failed"`.

The client treats `mismatched` as a failure and `dispatched_unverified` as success with the
lamp shown in `warn`.

`run_phone_tool` must run **outside** any private phone context, so its first guard
(`phone_session.private() and guest_required()`) does not fire. Add a test that `guest_off`
and `unlock` work while `guest_required()` is true.

### 6.5 Lock fix
- `src/power_actions.py` `argv("lock")` and `src/taskbar.py` `power_argv("lock")` change from
  `loginctl lock-session` to **`loginctl lock-sessions`**. This matches the voice tool and the
  comment at `bridge.py:2457`.
- Update the existing tests that assert the old argv.

### 6.6 Pairing CLI (`scripts/phone_pair.py`)
- **`uv run python scripts/phone_pair.py`:**
  - Generates a code of 10 Crockford base32 characters (50 bits).
  - Writes `{sha256(code): expires_at}` to `~/.jarvis/phone/pair.json` (0600, 600 s TTL, max 3
    pending codes).
  - Prints an ANSI QR (segno) of `https://<origin>/#pair=<code>`, plus the plain code and URL
    for manual entry.
- `--list` lists the devices. `--revoke <device_id>` revokes one.
- **`POST /phone/v1/pair` behaviour:**
  - Consumes the code (single use) and creates a device record in `~/.jarvis/phone/devices.json`
    (0600). Format:
    `{id, name, secret_sha256, created, last_seen, credentials: [{id, x, y, sign_count, created}]}`.
  - At most 3 devices; a 4th is refused with **409**.
  - Rate limit: 5 attempts per minute globally, and code comparison runs in constant time.

### 6.7 Docs to update in the same change
- `docs/PHONE_API.md`: rewrite it for the PWA and these routes. Keep a short "Legacy APK" note
  until §15 R3.
- `docs/voice-transport.md`: **no change needed.** No frames, topics, tokens or close codes
  change. If one does change, update it in the same change as AGENTS.md requires.
- `README.md`: the phone section points to `docs/phone-app/` and to pairing.

---

## 7. Security

### 7.1 Network
- `tailscale serve` is reachable only on the tailnet. There is no Funnel; **never run
  `tailscale funnel`.**
- After the cut-over (§15 R4), set `JARVIS_BRIDGE_BIND` back to `127.0.0.1`. Until then, the
  old APK still needs the tailnet bind.
- **Prerequisite:** HTTPS certificates must be enabled for the tailnet (in the Tailscale admin
  DNS settings). The builder checks this with `tailscale cert --help` and a dry run of
  `tailscale serve status`. If they are off, stop and ask the user.

### 7.2 Credentials
- The bridge bearer token never reaches the browser.
- The device secret lives only in the HttpOnly cookie.
- Passkeys: platform authenticator, `residentKey: "preferred"`, `userVerification: "required"`,
  `attestation: "none"`, algorithm ES256 (−7) only.
- **Assertion checks** (`phone_webauthn.verify_assertion`):
  - `clientDataJSON.type == "webauthn.get"`
  - the challenge matches and has not been used
  - `origin == JARVIS_PHONE_ORIGIN`
  - `rpIdHash == sha256(rpId)`
  - the UP and UV flags are set
  - `signCount` is greater than the stored count, unless both are 0
  - the ECDSA P-256 SHA-256 signature over `authData ‖ sha256(clientDataJSON)` verifies
    (`cryptography`)
- **CBOR:** a minimal decoder in `phone_webauthn.py` covering major types 0–5 and 7, only what
  `attestationObject` and the COSE key need. Reject indefinite lengths.

### 7.3 Passphrase gate
- The phone uses the same gate file as the deck TUI: `~/.config/locke/gate`, in the format
  `salt_hex:hash_hex`, using `hashlib.scrypt(pw, salt, n=16384, r=8, p=1)`.
- The path can be overridden with `JARVIS_PHONE_GATE`.
- If the gate file is missing, gate attempts return **503** `"gate not configured; run deck
  passwd"`.
- **Lockout:** after 5 consecutive failures per device, back off for 60 s, doubling up to 1 h.
  The counter resets on success. `tries_left` and `retry_after` are returned. Every failure is
  logged with `log_action("phone_gate", "fail <device_id>")`.
- Gate states and timers are in §6.2. The client shows GATE in the matching mode.
- **The secret long form of the brand name never appears in UI text, code, comments or docs.**
  It may appear only as sub-2-second animation flashes, following the deck rules. This plan
  uses no flash.

### 7.4 Content-Security-Policy
The policy is set as an `index.html` meta tag, because `tailscale serve` cannot add headers:
`default-src 'self'; connect-src 'self' wss://<origin-host>; img-src 'self' data:; font-src
'self'; style-src 'self'; script-src 'self'; worker-src 'self'; manifest-src 'self';
base-uri 'none'; form-action 'none'`.

Also:
- No inline scripts.
- Style writes go only through CSSOM (Preact `style` objects and `el.style`), so no
  `'unsafe-inline'` is needed.

### 7.5 CSRF and CORS
- Device-auth routes require `X-Jarvis-Phone: 1` and a same-origin `Origin`.
- The bridge's `Access-Control-Allow-Origin: *` without credentials means a cross-site request
  can't carry the SameSite=Strict cookies anyway.

### 7.6 Audit and visibility
- Every pair, revoke, gate open or fail, step-up and control is written to `actions.log`, so it
  appears in the HUD activity feed.

---

## 8. Voice transport in the browser

### Call flow
1. **Tap the reactor** and the call phase becomes `minting`.
   - Request: `POST /phone/v1/session {api_version: 1, mode: "new"}`.
   - Response: a call token and `roomName`.
2. **Open the socket:** `wss://<location.host>/voice?token=<token>`.
3. **Mic capture:**
   - `getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1}})`.
   - It feeds `new AudioContext({sampleRate: 16000})` and the mic worklet. Chrome resamples.
   - Every 512 samples (32 ms), the worklet sends a binary frame: `0x01` plus 1024 bytes of
     PCM16 LE.
   - While muted, no frames are sent.
4. **Playback:**
   - A separate `new AudioContext({sampleRate: 24000})` runs the playback worklet.
   - `0x02` payloads go into the ring buffer, which starts playing at 120 ms buffered.
   - The `flush` topic clears the buffer.
5. **Text topics:**
   - `state`: drives `call.agent`.
   - `caption`: drives the captions panel.
   - `hello`: confirms the identity.
   - `ping` / `pong`: answer pings; if nothing arrives for 15 s, treat the link as lost.
   - `jarvis-tasks`: ignored.
   - Typed text during a call goes to `lk.chat` (`{text}`) instead of `/phone/v1/command`.
6. **Keep the call alive:**
   - Request a Screen Wake Lock while the call is live and the page is visible.
   - Set Media Session metadata (`title: "LOCKE//LINK"`, `artist: "call live"`) and handle its
     `hangup`/`pause` actions as "end call".
7. **End:** tap the reactor, or the session ends. Close with code 4010, stop the tracks, and
   close both audio contexts.

### Limits to accept
- Output goes to the loudspeaker or a headset; the earpiece is not selectable on the web.
- If Android kills a backgrounded tab, the call ends. §9 handles this.

---

## 9. Offline and reconnect

### 9.1 Control plane
- **Poll failure:** `link.phase` becomes `offline`.
  - The strip greys every lamp and shows the last value's age (`LCK OPEN 2m`).
  - Tiles and the command line are disabled.
  - Polling backs off at 2, 4, 8, then 15 s (capped) and returns to 2 s on success.
- **No queuing:** POSTs are never queued or replayed.

### 9.2 Voice plane (`call.ts`)
| Event | Action |
|---|---|
| WS close 4001 or 4002 (bad, expired or replayed token) | Re-mint with `mode: "new"` and reconnect once. On a second failure, go to `error`. |
| 4003 (call already active) | `error`: "LAPTOP BUSY". No retry. |
| 4011 (room closed) | `idle` |
| 4012 (duplicate identity) | `error`, no retry |
| 4013 (owner revoked) | `idle`, and close the gate (morph to GATE) |
| Network loss (unlisted code or socket error) | `reconnecting`. Re-mint with `mode: "new"` (private rooms can't be rejoined) using backoff 0.5, 1, 2, 4 and 8 s; give up after 30 s total and go to `error`. On reconnect, add a caption line `── LINK RESET ──`; conversation context is not preserved. |
| Page hidden | The call keeps running. Polling stops. |
| Page shown | Polling resumes immediately, and the call state is checked. |
| `online` event | Resume polling right away. |

### 9.3 Service worker (`sw.ts`)
- **Precache:** the app shell (`index.html`, JS, CSS, fonts, icons, manifest), versioned as
  `link-<BUILD_ID>`.
- **Request handling:**
  - Navigation requests: network first with a 3 s timeout, then the cached `index.html`.
  - `/api/*` and `/voice`: **never cached** (network only).
  - Static assets: cache first.
- **Updates:**
  - Register with `updateViaCache: 'none'`.
  - A new SW installs and waits, and `UPD` shows in the strip.
  - Tapping `UPD`, or the next cold launch, sends `skipWaiting` and reloads.
  - Old caches are deleted on `activate`.

---

## 10. Accessibility
- **Semantics:**
  - Every actionable element is a real `<button>`. Toggles use `aria-pressed`.
  - Tiles have `aria-label`s in plain words ("Guest mode, off. Turning off needs fingerprint.").
  - ASCII glyphs are `aria-hidden`.
- **Reactor button label:** "Start call" or "End call". The long press is also reachable as a
  separate visually-hidden "Mute microphone" button for TalkBack.
- **Live regions:**
  - Captions: `aria-live="polite"`, final lines only.
  - Toasts: `role="status"`.
  - Gate errors: `role="alert"`.
- **Touch targets:** at least 48×48 CSS px; the `⌗` satellite and header ring have padded hit
  areas.
- **Contrast:** at least 4.5:1 for all text, and `dim` is used only for secondary text that
  meets 3:1 at 18 px or larger. The builder verifies with the Playwright axe check (§12).
- **Focus:** a visible ring (2 px cyan outline), and DOM order matches visual order. During a
  page morph, focus moves to the target page's first control once `p > 0.5`.
- **Reduced motion** per §4.3 and §4.4. Haptics are never the only signal.
- Dynamic type: layout holds up to 130% text zoom; captions wrap and tiles grow.

---

## 11. Performance budgets (all enforced in CI by §12 where marked †)
| Budget | Limit |
|---|---|
| Initial JS (gzip) † | ≤ 60 KB |
| CSS (gzip) † | ≤ 8 KB |
| Fonts (subset, total) † | ≤ 70 KB |
| First paint with a warm SW | ≤ 400 ms |
| Interactive with a warm SW | ≤ 1.2 s, measured on the user's phone |
| Morph frame script time † | ≤ 4 ms per frame under 4× CPU throttle |
| Long frames (> 16.7 ms) during any page morph † | 0 under 4× CPU throttle (Playwright trace) |
| Layout during a morph † | 0 forced reflows (no `Layout` events inside rAF in the trace) |
| Idle CPU | rAF stopped when settled; reactor canvas at 30 fps, and 0 while hidden |
| Mic frame to WS send | ≤ 40 ms |
| Playback start after the first `0x02` | ≤ 150 ms |
| Memory (JS heap) | ≤ 60 MB during a 10 min call |
| `/phone/v1/state` server time † | ≤ 20 ms p95 (cached probes; pytest benchmark with fakes) |

---

## 12. Test plan

### 12.1 Python (`uv run pytest`; every probe goes through injected `run` / clock fakes)
- **`test_phone_device.py`:**
  - A pairing code is single use, expires after 600 s, and is limited to 3 pending.
  - Pairing is limited to 5 attempts per minute.
  - Max 3 devices, then 409.
  - Cookie parsing; a bad secret is rejected.
  - A bearer token still works.
  - The Origin check and the missing-header check.
- **`test_phone_webauthn.py`:**
  - Use real P-256 keys from `cryptography`.
  - Registration with attestation none is parsed.
  - An assertion verifies.
  - Rejections: wrong origin, wrong rpId, challenge reused or expired, UV missing,
    `signCount` regression, bad signature, a non-ES256 alg, indefinite-length CBOR.
- **`test_phone_gate.py`:**
  - Scrypt verification against a fixture gate file.
  - Missing file returns 503.
  - Lockout backoff of 60 s doubling to 1 h, and reset on success.
  - Transitions between open, soft and cold, and a reopen with an assertion in the soft state.
  - Cold state after a simulated restart.
- **`test_phone_control.py`:**
  - The argv for each action (via fake `run`).
  - Step-up is required for `guest_off`, `unlock`, `restart` and `shutdown`.
  - **Guest exemption:** with `guest_required()` true, `lock`, `unlock` and `guest_off`
    succeed.
  - Readback statuses: verified, mismatched, unverified.
  - An audit line is written.
  - `run_phone_tool` is called outside the private context.
- **`test_phone_state.py`:** the response shape exactly matches §6.3; slow probes never block
  (a fake probe sleeping 5 s still gives a response in under 50 ms); TTLs; presence disabled by
  env.
- **`test_bridge_api_prefix.py`:** `/api/phone/v1/state` is the same as `/phone/v1/state`; the
  prefix is stripped before the guest gate.
- **Existing tests:** update the lock argv assertions to `lock-sessions`, and run the whole
  suite with `-m "not slow"` green.

### 12.2 Web unit tests (`pnpm test`, vitest)
- **`spring.test.ts`:**
  - Critical presets settle within the target time and never overshoot.
  - `setTarget` mid-flight keeps position and velocity continuous (no step larger than the
    velocity × dt bound).
  - Fixed-step determinism.
- **`page-morph.test.ts`:**
  - FLIP delta math: translate, scale and rotate the shortest way.
  - `lerpT` endpoints.
  - Stagger applies only from rest.
  - A third-page trigger queues correctly.
- **`mark.test.ts`:** every glyph in `mark-glyphs.json` has 16 core points and 4 satellites;
  interpolation is continuous; the state → glyph mapping, including the locked override and
  the guest modifier.
- **`mic-framing.test.ts`:** a frame is exactly 1025 bytes, little endian, and clamped.
- **`call.test.ts`:** a close-code table drives the transitions in §9.2.
- **`store.test.ts`:** reducers and the derived values.

### 12.3 E2E (`pnpm e2e`, Playwright Chromium, Pixel 7 emulation)
**Harness:**
- A mock bridge via route interception.
- A fake WS server (Node `ws`, dev dependency `ws@8.18.0`) that speaks the frames from
  `docs/voice-transport.md`.

**Flows:**
- Pair via `#pair=` → passkey registration (virtual authenticator through CDP
  `WebAuthn.addVirtualAuthenticator`) → passphrase → CORE.
- Soft lock → fingerprint → reopen.
- Lockout UI.
- Navigation: `⌗` tap, header tap, back button, typing "deck".
- **Interrupt test:**
  - Trigger CORE→DECK, then trigger DECK→CORE 120 ms later.
  - Sample `core.ring` `getComputedStyle().transform` every frame for 600 ms.
  - Assert no frame-to-frame jump larger than 2× the max per-frame delta seen during an
    uninterrupted morph.
  - Assert it ends at identity.
- **Mark interrupt:** switch the agent state from listening to speaking mid-morph; the same
  continuity check runs on the core path's vertex 0.
- Reduced motion (`emulateMedia({reducedMotion:'reduce'})`): only crossfades.
- **Controls:**
  - Each tile goes through the pending state to verified.
  - A failure shakes.
  - Step-up actions open WebAuthn (virtual authenticator).
  - POWER hold, and releasing early.
- **Offline:** route abort → OFF lamps and disabled controls → recovery.
- **Call:** connect, receive `state`/`caption`/`0x02`, `flush`, then the 4001 re-mint and the
  network-drop backoff.
- **axe-core** (`@axe-core/playwright@4.10.1`): no serious or critical violations on any page.
- **Perf trace** (CDP tracing with 4× CPU throttle): the budgets marked † in §11.

### 12.4 Manual on-device checklist (the user's phone, before R0 sign-off)
1. Install from Chrome (WebAPK).
   - The launcher icon shows correctly with round and squircle masks.
   - With themed icons enabled, the monochrome version shows, or a known Chrome limitation is
     recorded.
2. Pair via QR, register the passkey, enter the passphrase.
3. Run a voice call for 5 min, including 60 s with the screen off, then a reconnect after
   toggling airplane mode.
4. On the real laptop:
   - Guest ON: the HUD shows guest.
   - LOCK, then UNLOCK with the fingerprint.
   - Guest OFF with the fingerprint.
   - Screens off and on.
   - Mic mute.
   - Sleep (wake the laptop manually).
   - Restart, deferred to the end of the session.
5. Lock and unlock **while guest mode is on**: this must work.
6. Kill the bridge, and the app shows offline. Restart it, and the app returns to a cold gate.
7. Update path: deploy a new build, and `UPD` appears and applies.

---

## 13. Build, deploy and release

### Icons
`pnpm icons` (`phone-web/scripts/icons.mjs`) shells out to `inkscape`, which is installed at
`/usr/bin/inkscape`. Source: `docs/phone-app/icon/`.

| Output PNG | Size | Source |
|---|---|---|
| `icon-192.png` | 192 | `icon-squircle.svg`, purpose `any` |
| `icon-512.png` | 512 | `icon-squircle.svg`, purpose `any` |
| `icon-maskable-512.png` | 512 | `icon-maskable.svg`, purpose `maskable` |
| `icon-mono-512.png` | 512 | `ic_launcher_monochrome.svg` on transparent, purpose `monochrome` |

The script also copies `mark-glyphs.json` to `src/motion/`. The generated PNGs are committed.
If the icon design changes, re-run `python3 -I docs/phone-app/icon/gen_icons.py
docs/phone-app/icon` first.

### Manifest
- `name: "LOCKE//LINK"`, `short_name: "LINK"`, `id: "/"`, `start_url: "/?src=pwa"`,
  `scope: "/"`.
- `display: "standalone"`, `orientation: "portrait"`.
- `background_color` and `theme_color`: `"#04081a"`.
- `icons`: the four entries above.

### Build
- Command: `cd phone-web && pnpm install --frozen-lockfile && pnpm build`.
- Vite writes `dist/`.
- The build stamps `BUILD_ID` (the git short SHA plus a timestamp) into `sw.js` and
  `index.html`.
- `pnpm check` runs, in order: `tsc --noEmit`, `vitest run`, the size-limit check, and
  `playwright test`.

### Deploy (`scripts/phone_web_deploy.sh`)
1. Run `pnpm check && pnpm build`.
2. Copy `dist` to `~/.jarvis/phone-web/releases/<BUILD_ID>/`.
3. Swap atomically:
   `ln -sfn releases/<BUILD_ID> ~/.jarvis/phone-web/current.tmp && mv -T current.tmp current`.
4. Keep the newest 3 releases.
5. Make sure the mounts exist. Run these idempotently, and **verify the exact flag syntax
   against `tailscale serve --help` on the host**:
   `tailscale serve --bg --set-path / ~/.jarvis/phone-web/current`,
   `tailscale serve --bg --set-path /api http://127.0.0.1:4317`, and
   `tailscale serve --bg --set-path /voice http://127.0.0.1:7880/voice`.
6. If tailscaled does not follow the symlink, serve the release directory directly and re-run
   that mount on each deploy.
7. `tailscale serve status` must list all three mounts.
8. **Rollback:** point `current` at the previous release.

### Bridge environment
- Set `JARVIS_PHONE_ORIGIN=https://ripjk.tailea4e7f.ts.net` in the Jarvis service environment
  (where `JARVIS_BRIDGE_TOKEN` is set today; the builder locates it in the shell/Tauri manager
  or systemd unit).
- Restart the bridge **only when the user agrees**; the user's own rules govern restarts.

### No APK in v1
- The WebAPK is minted by Chrome on install.
- **Optional, unscheduled TWA:**
  - Bubblewrap `@bubblewrap/cli@1.22.x`, package `dev.jarvis.locke`.
  - Serve `/.well-known/assetlinks.json` through the static mount.
  - Native adaptive icon from the three layer SVGs, converted to VectorDrawable (they use no
    filters on purpose).
  - Android 12 splash `AnimatedVectorDrawable`: one ring → star ignition, ≤ 800 ms, built from
    `mark-glyphs.json` paths.
  - Release keystore stored outside the repo.
  - Only start this if the user asks.

---

## 14. Phased task list (for coding agents)

### Ownership (per the user's global rules)
- **[B]**: the builder agent does it itself: UI, visuals, motion, and security-critical code.
- **[O]**: delegable to opencode:
  - Command:
    `opencode run --auto -m opencode/muse-spark-1.3-contributor-free --variant high "<brief>"`.
  - Use it for non-UI plumbing and tests. The builder reviews every [O] diff.
  - Parallel runs are allowed only on files that don't overlap.

Each task has a done condition. **Don't start a phase until the previous phase's gate is
green.**

### P0: Prerequisites
| ID | Task | Owner | Done when |
|---|---|---|---|
| P0.1 | Confirm tailnet HTTPS certs and the `tailscale serve` syntax on the host; record the exact commands in `phone_web_deploy.sh` comments. | B | `tailscale serve status` works; any blocker is reported to the user. |
| P0.2 | Scaffold `phone-web/` with the pinned versions, the `@hud/ascii` alias, `theme.css` tokens and the empty pages. | O | `pnpm build` and `pnpm test` pass on an empty app. |
| P0.3 | Add `segno` to `pyproject.toml` and make `cryptography` explicit; `uv sync`. | O | The import works in `.venv`. |

**Gate P0:** both builds are green.

### P1: Bridge security core
| ID | Task | Owner | Done when |
|---|---|---|---|
| P1.1 | `/api` prefix normalisation plus its test. | O | `test_bridge_api_prefix` is green. |
| P1.2 | `phone_device.py`: device store, the pairing code flow, cookies, `authorize()`, gate records and state machine, scrypt gate and lockout. | B | `test_phone_device` and `test_phone_gate` are green. |
| P1.3 | `phone_webauthn.py`: CBOR, registration, assertion and challenges. | B | `test_phone_webauthn` is green. |
| P1.4 | Wire the routes from §6.1 into `bridge.py` before `_authorized()` and the guest gate. | B | The route table behaves per §6.1 (pytest with a handler harness). |
| P1.5 | `scripts/phone_pair.py` (QR, `--list`, `--revoke`). | O | A manual run prints a QR; unit test for code storage. |

**Gate P1:** all Python tests are green, `ruff format` and `ruff check` are clean, and a
security self-review of the P1 diff is done by the builder.

### P2: Bridge control and state
| ID | Task | Owner | Done when |
|---|---|---|---|
| P2.1 | The `lock-sessions` fix in both files, plus the test updates. | O | Green. |
| P2.2 | `power_actions.lock_state()` with a cache. | O | Unit tests are green. |
| P2.3 | `phone_control.py` state aggregator with background caches (lock, screens, mic, presence, power, voice). | O | `test_phone_state` is green, including the non-blocking test. |
| P2.4 | `/phone/v1/control` actions, step-up enforcement, readbacks and audit. | B | `test_phone_control` is green, including the guest exemption. |
| P2.5 | Rewrite `docs/PHONE_API.md`; update the README phone section. | O | Docs match §6. |

**Gate P2:** full `uv run pytest -m "not slow"` is green, and a manual `curl` with the bearer
token against a running bridge returns the state.

### P3: Web foundations
| ID | Task | Owner | Done when |
|---|---|---|---|
| P3.1 | `motion/spring.ts`, `loop.ts` and their tests. | B | `spring.test` is green. |
| P3.2 | `store.ts`, `api.ts`, `poll.ts`, `webauthn.ts`. | O | `store.test` is green; the API error typing is covered. |
| P3.3 | `sw.ts`, the manifest, the icons script and the PNGs. | O | Lighthouse PWA installability passes locally over `vite preview --https`. |

**Gate P3:** the unit tests are green.

### P4: Screens (static layout, no motion yet)
| ID | Task | Owner | Done when |
|---|---|---|---|
| P4.1 | `StatusStrip`, `Mark` (static glyphs), `Reactor` (canvas + segments + `⌗`). | B | Visual review on a 390×844 screenshot. |
| P4.2 | GATE page, all modes; the pairing and passphrase flow against the real API. | B | E2E gate flows pass. |
| P4.3 | CORE page: captions, command line, toast. | B | Command round trip against the mock and the real bridge. |
| P4.4 | DECK page: `Tile`, `PowerTile`, `HoldButton`, wired to `/control`. | B | E2E controls pass. |

**Gate P4:** E2E is green except the motion specs, and axe is clean.

### P5: Motion
| ID | Task | Owner | Done when |
|---|---|---|---|
| P5.1 | `flip.ts` and `page-morph.ts` with every map from §4.3, plus the triggers and back handling. | B | `page-morph.test` and the E2E interrupt test are green. |
| P5.2 | `mark.ts`: part springs, state mapping, modulation and the ignition cycle. | B | `mark.test` and the E2E mark interrupt are green. |
| P5.3 | Micro-interactions and haptics (§4.5, §4.6). | B | Visual review. |
| P5.4 | Reduced-motion paths. | B | The E2E reduced-motion test is green. |

**Gate P5:** the perf trace budgets marked † pass.

### P6: Voice
| ID | Task | Owner | Done when |
|---|---|---|---|
| P6.1 | Mic and playback worklets, plus `ws.ts` framing. | O | `mic-framing.test` is green; the fake WS round trip works. |
| P6.2 | `call.ts` state machine and reconnect table. | O | `call.test` is green. |
| P6.3 | Wire the call to the reactor, captions, mark modulation, wake lock and Media Session. | B | The E2E call flow is green. |
| P6.4 | Battery telemetry post (60 s while visible). | O | The HUD shows the phone battery. |

**Gate P6:** a real call from desktop Chrome against the real hub over `tailscale serve` works.

### P7: Hosting and on-device verification
| ID | Task | Owner | Done when |
|---|---|---|---|
| P7.1 | `scripts/phone_web_deploy.sh`, the CSP meta, the env var documented in the README. | B | Deploy, rollback and redeploy all work. |
| P7.2 | Install on the phone and run the §12.4 checklist **with the user**. | B and the user | Every item checked, or a recorded and accepted limitation. |

**Gate P7:** the user signs off. This is §15 R0.

---

## 15. Retire JarvisLink 3.0

### Rules for retirement
- Retirement is strictly ordered. **Nothing is removed before R0 passes.**
- Removal only through git-tracked deletes (`git rm`), in ordinary commits, on a branch.
- No `--force`, no history rewriting, and no `git clean`.
- **Never uninstall the app from the phone.** The user does that themselves if they want to.
- Commit or push only when the user asks; a commit needs the user's go-ahead in that session.

### R0: Verification gate (prerequisite)
- Gates P1–P7 are green.
- The §12.4 checklist is signed off by the user.
- The user has used the PWA as the only phone client for **7 consecutive days** with no
  blocker. The old APK stays installed as a fallback during this time.
- The user says "retire it" explicitly.

### R1: Archive (no deletions yet)
1. Copy every `~/JarvisLink-v*.apk` (v1.0 to v2.0 exist today) and the last built 3.x APK to
   `~/.jarvis/archive/jarvislink/`. The 3.x APK is `phone/app/build/outputs/apk/*/app-*.apk`;
   if none exists, build it once with the README's gradle command.
   - Name them `JarvisLink-<versionName>.apk`, with a `SHA256SUMS` file.
   - Copy only; leave the originals in `~` alone.
2. Create a local tag `jarvislink-3.6.1-final` on the commit before any removal. Do not push it
   unless the user asks.
3. Write `~/.jarvis/archive/jarvislink/README.txt` with the tag name, the restore command
   (`git checkout jarvislink-3.6.1-final -- phone`) and the APK checksums.

### R2: Remove the native app source (one commit)
- `git rm -r phone/`. This covers the 153 tracked files, including `phone/README.md` and
  `phone/PARITY.md`.
- Untracked build output (`phone/build`, `phone/app/build`, `phone/local.properties`, `.gradle`)
  is **not** deleted by the agent. List it in the final report so the user can delete it by
  hand.
- In the same commit, update references:
  - `README.md` (phone section → PWA)
  - `REMOTE_ACCESS_PLAN.md` and `src/remote/PROTOCOL.md`: add a one-line note that the
    JarvisLink `RemoteActivity` references are historical and the future phone client is the
    PWA.
  - Any `AGENTS.md`/`CLAUDE.md` line that mentions `phone/` or gradle.

### R3: Remove server code used only by the old app (one commit per row group)
Before deleting each item, run a **caller audit**:
`grep -rn "<route or symbol>" src frontend shell desktop scripts tests` (excluding `phone/`).
An item is removed only if its sole remaining callers are tests for that item. If anything else
calls it, **keep it** and record why.

| Item | Decision | Notes |
|---|---|---|
| `POST /camera/frame`, `GET /camera/latest` and their ingest helpers | Remove after audit | No HUD or agent caller found during planning. |
| `POST /type` | Remove after audit | Keyboard injection now goes through voice tools. |
| `POST /talk` | Audit; the old APK's push-to-talk path. Keep if the wake listener or shell uses it. | |
| `POST /summon` | **Keep** | Laptop wake listener and HUD semantics. |
| `src/mic_uplink.py`, plus its supervision in `shell/src-tauri/src/manager.rs` and `tests/test_mic_uplink_framing.py` | Remove after audit | It existed only for the APK's background mic. The Rust change must keep `cargo build` green. Remove `JARVIS_MIC_PORT`/`JARVIS_MIC_BIND` mentions from the docs. |
| `GET/POST /phone/v1/calls` (laptop rings the phone) | **Keep the code, dormant** | `phone_calls.py` is imported by `agent.py`, `proactive/engine.py`, `scheduled_tasks.py` and `mint_token.py`. Removing it is a separate decision for the user. Note in `docs/PHONE_API.md` that no current client polls it. |
| The `{"guest": true}` body stamp in `_effective_guest` and `phone_api` | **Keep** | It is harmless, and removing it changes bridge semantics. Mark it deprecated in a comment. |
| `/token`, `/route`, `/chat`, `/tool`, `/mic`, `/room`, `/sys`, `/captions`, `/phone/telemetry`, `/phone/v1/command`, `/session`, `/capabilities` | **Keep** | Used by the HUD or the PWA. |
| `phone_api.py`, `phone_session.py`, `mint_token.py` | **Keep** | Used by the PWA. |
| `tests/test_phone.py` and other tests that only exercise removed items | Remove together with their item | Run the full suite afterwards. |

### R4: Network tightening (config, not code)
- Set `JARVIS_BRIDGE_BIND=127.0.0.1` in the service environment, since the PWA reaches the
  bridge through `tailscale serve`.
- Make sure nothing else on the tailnet needed the tailnet bind (ask the user).
- Restart the bridge **with the user's OK**, and confirm the PWA still works and that
  `curl http://<tailnet-ip>:4317/health` now fails.

### R5: Final report to the user
List:
- the commits
- the archive path and checksums
- the untracked build directories left for manual deletion
- every R3 item kept, and why
- the rollback command:
  `git checkout jarvislink-3.6.1-final -- phone`, then reinstall the archived APK.
