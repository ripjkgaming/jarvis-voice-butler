# Jarvis phone app: brief (phase 1, awaiting approval)

Codename **LOCKE//LINK**. This replaces JarvisLink 3.0 (`phone/`, native Kotlin, about 12 surfaces).
The detailed plan (`PLAN.md`) and the icon SVGs come after this brief is approved.

## Purpose
Jarvis already does nearly everything by voice. The phone app only handles what a phone has
to do itself:
1. Be a **pocket mic and speaker** for Jarvis when you are away from the desk.
2. Act as the **owner's key** for the computer: guest mode, lock, unlock and power. The phone
   always has admin rights, even when the laptop is in guest mode.
3. Show the **real state** of the laptop at a glance (call live, guest, locked, link).

## What stays
- A full-duplex voice call over the existing WS hub (`/phone/v1/session`, a private room,
  call token with a 900 s TTL).
- A single command line using `/phone/v1/command` (tries instant `/route` first, then chat).
  It covers anything you would rather type than say.
- Live captions for the current call only.
- Computer controls: guest on/off, lock, unlock, screens off/on, sleep/restart/shutdown,
  PC mic mute, and ending a remote session (kill switch).
- A status strip built only from real data: link RTT, `call_live`, `guest_mode`, lock state,
  laptop power, and remote session from `/sys` and `/room`.

## What is cut (one line each)
| Cut | Why |
|---|---|
| Chat tab and history | The command line plus voice covers it, and Jarvis keeps the context. |
| Activity / actions feed | Ask Jarvis "what did you do"; the HUD already shows it. |
| Volume, media, now playing | Voice routes are instant (`/route`, 1-60 ms). |
| Open app, remote type/keys | Voice can do it; heavier work belongs in a remote session. |
| Screenshots / Screens tab | Voice can do it. You can say "screens off". A viewer is out of scope. |
| Camera "see what I see" | Rarely used and needs a camera permission surface. It can come back later. |
| Approvals queue | Jarvis asks for confirmation aloud and a voice "yes" settles it. |
| SOS / alerts / location | Android has native Emergency SOS, so this is a duplicate with a liability. |
| Dialer / SMS / WhatsApp prefill | The phone's own apps do this better. |
| Hotword, background mic uplink, assistant role, recognizer | A web app can't do these reliably. They are open question 1. |
| Phone TTS settings, offline toggle, autostart, battery telemetry | Not needed: voice is call audio and the bridge sees the phone through tailnet presence. |
| Phone-side guest *stamp* (`{"guest":true}`) | Replaced by a real computer-side guest toggle. The phone itself is never a guest. |
| Remote coding UI (terminal, Claude chat, files, screen) | A separate product (`src/remote/PROTOCOL.md`). The app keeps only status and the kill switch. |
| Settings screen | Pairing is the only setting and happens once at the gate. |

## Screens (3 surfaces)
- **GATE**: passphrase decode screen in ASCII, plus one-time pairing (scan a QR code from the
  laptop). After that, a fingerprint is enough to re-enter.
- **CORE**: the main voice surface. The centre ASCII arc reactor shows voice state. Hold or tap
  it to talk. Live captions sit under it and the command line at the bottom, with the status
  strip on top.
- **DECK**: computer controls as a dense grid of real-state tiles: GUEST, LOCK, UNLOCK, SCREENS,
  POWER, MIC, REMOTE.

### Page changes: element morph (the headline transition)
Pages do not slide, swipe or push. Elements of the outgoing page **transform into** the
elements of the incoming page.

**Triggers** (no swipe gestures anywhere):
- **CORE to DECK**: tap the reactor's `⌗` satellite.
- **DECK to CORE**: tap the DECK header ring.
- **Voice**: "deck" / "core".
- **System back**: plays the same reverse morph, with no predictive-back drag.

**Technique** (the same part-based spring model as the animated mark, see the icon section):
- Both pages stay mounted. Each mapped pair of elements has its from and to rects measured
  once per layout (FLIP).
- Each element springs its own `translate`, `scale`, `rotate` and `opacity`. Stiffness is
  about 380 and the springs are critically damped, settling in about 350 ms. One
  `requestAnimationFrame` loop drives everything.
- **Interruptible**: a trigger mid-morph only swaps the targets, so every element reverses from
  its current in-between state with its velocity intact.
- **Text**: labels never stretch. They crossfade at the midpoint and re-decode in ASCII
  (opacity only).
- **Unmapped elements**: these only scale from 0.9 and fade.
- **Reduced motion**: a 120 ms crossfade.

**Morph map**
| Pair | Outgoing element → incoming element |
|---|---|
| GATE → CORE | The looping mark retargets to the star and scales up into the **reactor core**. The passphrase decode line becomes the **command line** (same rect family, translate plus scaleX). The pairing/hint glyph row becomes the **status strip**. Captions grow out of the reactor (scaleY from 0). |
| CORE → DECK | The reactor ring shrinks and translates into the **DECK header ring**, with the voice-state mark riding inside it. Seven outer ring segments detach and each one flies out and grows into a **tile** (GUEST, LOCK, UNLOCK, SCREENS, POWER, MIC, REMOTE), starting at the segment's position and angle and staggered 18 ms by angle. The command line becomes the wide **MIC tile**. Captions collapse into a one-line ticker under the header. The status strip is shared and does not move. |
| DECK → CORE, CORE → GATE | The exact reverse on the same springs. CORE goes back to GATE on app lock or idle timeout. |

The touch-first UI is an explicit exception to the voice-only HUD rule. Every DECK action also
keeps its existing voice route.

## Delivery form: PWA served over the tailnet, no APK in v1
- **Hosting**: `tailscale serve` (HTTPS with a `*.ts.net` certificate, tailnet only) maps `/` to
  the static PWA build, `/api/` to the bridge, and `/voice` to the loopback WS hub. Everything
  is one origin, so CORS is gone. The bridge can go back to binding loopback only, with no
  public port.
- **Install and updates**: Chrome's "Install app" builds a WebAPK with a real launcher icon and
  a fullscreen window. A service worker caches the app shell. Updates reach the phone on the
  next launch without a reinstall.
- **Mic**: `getUserMedia` (HTTPS is a secure context) feeds an AudioWorklet that produces
  PCM16 at 16 kHz in 32 ms frames, matching the existing `0x01` frames. Agent audio at 24 kHz
  plays through Web Audio.
- **Background**: a call stays up while it is foregrounded or the screen is on. Media Session
  helps keep it alive when backgrounded, but this is best effort. If Android kills the page,
  the app reconnects with a fresh token. No hotword.
- **Push**: none. Web Push goes through Google FCM, which breaks the "no external service"
  rule, and there are no laptop-initiated rings in v1.
- **Security**: the tailnet is the network boundary. The phone gets a device-scoped credential
  bound to a WebAuthn passkey (the fingerprint). The raw bridge token is never stored in
  browser storage.
- **Why no wrapper**: a TWA (Bubblewrap) only adds a guaranteed themed icon and a store
  identity. Its Digital Asset Links check fails whenever Tailscale is off at launch. Capacitor
  adds a native build and APK updates for features that are all cut. A TWA is an optional
  last phase, and only if the WebAPK icon disappoints.

## Computer-side controls: reuse vs missing
| Control | Exists | Missing / to fix |
|---|---|---|
| Guest ON | `watch_mode.enter_guest()` (file flag plus `revoke_security`), and the voice `watch_mode` route (which also arms camera GUARD) | An owner endpoint that turns guest on *without* the camera or card. |
| Guest OFF | `POST /watch/owner {"mode":"owner"}` → `restore_owner()` | Nothing; the passkey gate goes in front of it. |
| Lock / Unlock | Bridge tools `lock` (`loginctl lock-sessions`) and `unlock` (`unlock-sessions`) | A lock-state read (`ScreenSaver.GetActive` exists in `power_actions` but no endpoint exposes it). **Bug:** `/power lock` uses singular `lock-session`, which per the bridge's own comment misses the graphical seat. |
| Phone is always admin | none | **Blocker:** while in guest mode the bridge returns 403 on `/power` and most routes, so the phone could not lock or unlock. A new `/phone/v1/control` route is needed that is device-credential and passkey authenticated, is exempt from the guest gate, and is audited to `actions.log`. |
| Screens, sleep/restart/shutdown, PC mic | `screen_off/on` tools, `/power` with confirmation tickets, `/mic` | Nothing new. Restart and shutdown use the existing ticket via a hold-to-confirm. |
| Remote session | `/remote/status`, `/remote/stop` | Status only. |
| Presence (at desk) | `presence.py` (camera, tri-state) | Not exposed over HTTP. It could become one status-strip glyph. |

## Icon and mark: "reactor glass"
Style follows the reference: a dark navy glass tile, a thin glowing teal/cyan neon ring around
one glyph, a soft outer glow and small 4-point sparkles near the corners. A launcher icon cannot
animate, so this ships as two pieces.

**(a) Static adaptive icon (108 dp canvas, launcher picks the mask)**
- **Background layer**: full-bleed navy glass, a `#0a1a3a` to `#04081a` radial gradient with a
  faint top-left specular sheen. Sparkles sit just inside the 72 dp visible area so a round
  mask does not clip them.
- **Foreground layer**: a cyan `#5fe3ff` ring (about 2.5 dp stroke with a blur glow) around the
  compass/reactor star, which is the arc-reactor identity. The star has 4 long and 4 short
  points, an ice core and an amber `#ffb347` pinpoint. Ring, glow and star all stay inside the
  66 dp safe circle.
- **Monochrome layer**: the ring and star as one flat path with no glow, for themed icons.
- **PWA manifest**: the same art as a `maskable` icon, plus an `any` icon with the squircle tile
  baked in.
- **Files**: `icon/` foreground, background and monochrome SVGs, the maskable SVG, and a
  48 dp legibility check.

**(b) Animated mark, one shared in-app component**
- **Role**: a state indicator. The page morph above is the headline transition. The mark uses
  the same spring engine and travels inside the reactor/header ring during page morphs.
- **Where it is used**: the splash, the GATE screen, and the CORE voice-state indicator.
- **The four glyphs**: each is a morph target, and they map to app states:
  - *Orbit-dot ring* means **listening**. The dot radius follows the mic level.
  - *Amber triangle/flame* means **thinking**, with an amber flicker.
  - *Play triangle* means **speaking**. It pulses with the agent's audio level.
  - *Compass/reactor star* means **idle**, with a slow ring spin.
- **Locked and guest**: these are modifiers, not extra glyphs. **Locked** collapses the core to
  a sealed dot inside a closed ice ring. **Guest** turns the ring amber and dashed over
  whichever glyph is showing.
- **Splash and GATE motion**: the splash plays one ignition cycle: orbit, triangle, play, then
  the star. GATE loops that cycle slowly, and a correct passphrase retargets it to the star.
- **Shared topology**: every glyph is built from the same parts:
  - 1 ring
  - 4 satellites (dots, sparks or star tips, which shrink to 0 when unused)
  - 1 core polygon with a fixed 16 vertices (triangles put 6/5/5 samples on their edges
    with every corner a sample; the star alternates tips and valleys)

  Because the parts never change, a morph only interpolates numbers. That needs no runtime
  path matching (flubber is tween-based and awkward to retarget, so it is not used).
- **Interruptible**: each parameter (vertex x/y, satellite transform and opacity, ring
  colour and dash) is a critically damped spring holding a value and a velocity. A tap or state
  change mid-morph only swaps the target, so motion continues from the current in-between shape
  with its velocity intact. There are no restarts and no jumps.
- **One animation loop**: a single `requestAnimationFrame` loop drives everything and sleeps
  when all springs settle.
- **Rendering cost**: the ring, satellites, sparkles and glow use only `transform` and `opacity`
  on their own layers. The only repaint is the core path, under 128 px and cheaper than 0.3 ms
  per frame.
- **Reduced motion**: when `prefers-reduced-motion` is set, it snaps to the target glyph with a
  120 ms opacity crossfade.
- **Android 12 animated splash** (an AnimatedVectorDrawable under 1 s): not worth it in v1.
  An installed PWA (WebAPK) shows Chrome's static splash built from the manifest icon, so an
  animated vector splash only matters if the optional TWA phase happens. If it does, one
  ring-to-star ignition under 800 ms reuses the same SVG paths.

## Open questions (recommended default in **bold**)
1. **Retire native JarvisLink 3.0** once CORE voice reaches parity, accepting the loss of
   hotword, assistant role, background mic and incoming calls. **Yes; keep the old APK
   sideloaded until then.**
2. Should guest ON also lock the screen? **No. Guest means Jarvis is restricted and the desktop
   stays usable. Lock is a separate tile.**
3. Gate strength. **Passphrase on cold launch, then fingerprint (passkey) for each unlock and
   each guest OFF. Lock and guest ON are instant.**
4. Power set on DECK. **Sleep, restart and shutdown, with restart and shutdown on hold-to-confirm.
   Logout is cut.**
5. Navigation triggers and the mark's states. **Yes as specified:**
   - **Navigation**: tap the reactor's `⌗` satellite or the DECK header ring, voice
     "deck" / "core", or system back. No swipe.
   - **Mark states**: orbit = listening, flame = thinking, play = speaking, star = idle.
     The star is also the launcher icon.
   - **Push**: none in v1.
