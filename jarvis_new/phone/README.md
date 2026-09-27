# JarvisLink — Jarvis phone app (Android)

Companion app for the Jarvis PC voice butler. Talks to the PC over
**Tailscale** (tailnet IP + bearer token) — never expose the bridge to
the open internet.

## Features (v1.9)

- **No-approval actions** — Lock/Unlock/Blackout/Screens-off fire
  instantly, no confirm dialogs. Unlock stays fingerprint-gated
  (biometrics IS the confirmation). Home tab: Lock PC replaced by
  Unlock PC.
- **Per-screen screenshots** — Screens tab lists All + each display
  (HDMI-A-2, DP-1, eDP-1); captures crop server-side to that output.
- **Absolute volume** — bridge `volume_set` 0..150 with honest
  read-back; "set volume to 80/100/120" routes instantly.
  `~/JarvisLink-v1.9.apk` (vCode 10).

## Features (v1.8)

- Release vehicle for everything below (v1.7 never left the bench).
  Install this one. `~/JarvisLink-v1.8.apk` (vCode 9).

## Features (v1.7)

- **⚡ insta-commands** — new `POST /route` on the bridge: text →
  executed tool in milliseconds, no LLM, no TTS render. Layers: regex
  (µs) → layered intent resolver (exact/keyword/fuzzy + Needle-2
  semantic, offline) → canned reply. Time, math, volume, media, apps,
  lock/unlock, screenshots, screens all instant; anything else honestly
  no-routes and the app falls back to chat. Home prompt bar has a ⚡
  button showing the round-trip ms. Measured: 1–60ms vs ~10s chat.
- **Render diet, same pixels** — reactor + backdrop pre-render static
  layers once (no live blur, zero per-frame allocation), hardware
  layers, 30fps idle / 60fps hot, loops fully stop when hidden or
  screen-off. `~/JarvisLink-v1.7.apk` (vCode 8).

## Features (v1.6)

- **Full HUD reskin** — every element translucent over a living animated
  backdrop (drifting grid, rising motes, breathing edge glow). Outlined
  cyan buttons with ripple, glass text fields, monospace HUD text,
  dark translucent dialogs, styled tab bar, fade/rise screen transitions,
  staggered list entrances, glass chat/activity/approval rows, framed
  screenshot imagers, breathing status pulse + red SOS button.
  `~/JarvisLink-v1.6.apk` (vCode 7).

## Features (v1.5)

- **Arc-reactor HUD Home** — Iron Man style hero: segmented shell ring
  with node circles, glowing cyan ring, counter-rotating gauge ticks,
  dotted ring, pulsing core, orbiting sparks (+ comet streak when hot).
  Red mini-core with live phone battery % + charging state, HUD clock /
  date header, cyan-on-black chrome. Talk tab uses the reactor too.
  `~/JarvisLink-v1.5.apk` (vCode 6).

## Features (v1.4)

- **Voice commands that act** — Talk/Chat text is routed server-side to
  real PC actions (`lock`, `unlock`, volume, media, screenshot, screens,
  `open_app`, notify, `type`). The reply reports the true outcome —
  failures say so instead of claiming success. Needs the updated PC
  bridge (voice routing lives there; old bridges just chat back).
- **Guest mode** — Settings toggle. Jarvis turns cold and mean, allows
  Q&A + media/volume only; lock/unlock/type/apps/screens are refused
  with frost. Enforced on the PC bridge (`{"guest": true}`), so no
  client can bypass it. Lint-clean → `~/JarvisLink-v1.4.apk` (vCode 5).

## Features (v1.3)

Everything in v1.2, plus (all client-side — zero desktop-app changes):

- **On-device hotword** — "Jarvis" wake-word loop (`HotwordService`,
  platform SpeechRecognizer, partial results, no extra deps). Exponential
  backoff on busy/server errors; stands down while PTT/sessions hold the
  mic and while music plays (toggleable). Boot-starts when enabled.
- **Context-aware wake** — locked screen: stays closed, says "recording",
  one-shot voice command (10s timeout), speaks the first reply line.
  Unlocked: opens the app and auto-starts talking.
- **Tap-to-talk + PTT** — Home mic button (6s auto exchange); PTT pauses
  the hotword and sends text.
- **TTS replies** — Jarvis speaks the first reply line (Talk, one-shot,
  assistant fallback); toggle + male/focus-friendly voice setting.
- **Memory continuity** — stable per-install session id + event cursor in
  prefs (survive restarts); offline-mode toggle holds bridge calls behind
  an input-lock banner; autostart toggle gates boot services.
- **7-tab layout** — Home (orb + mic + prompt + quick actions + persisted
  live stream), Activity (PC `/actions` log; spend line notes the missing
  `/costs` endpoint), Screens (screenshot + display + lock; tap-to-click
  noted as needing a desktop endpoint), Approvals (local queue + one-tap
  Approve/Deny shade actions with confirmation notifications), Phone
  (dial/SMS/WhatsApp prefill, clipboard, TTS, PC mic, laptop audio,
  fingerprint-gated PC unlock), Alerts (local SOS: red flash, vibration,
  announcement, last-known location, persistent notification), Settings
  (all v1.3 toggles + session display).
- **Deferred (need desktop endpoints, documented in-app):** `/history`,
  `/costs`, `/phone` action queue + results, `/phone/sos` dispatch,
  laptop→phone TTS push, tap-to-click, fingerprint elsewhere.
- Lint-clean, 11 unit tests green → `~/JarvisLink-v1.3.apk` (vCode 4).

## Features (v1.2)

Everything in v1.1, plus:

- **System assistant** — JarvisLink registers as a full digital assistant
  (`VoiceInteractionService` + session + recognizer, `BIND_VOICE_INTERACTION`):
  pick it under Settings > Apps > Default apps > Digital assistant app.
  The assistant button / gesture then opens the Jarvis voice plate (orb +
  transcript + Listen/Dismiss) instead of Google Assistant. Utterances are
  transcribed via PC bridge /talk; replies show and play back.
- **Speech recognizer** — `JarvisRecognitionService` transcribes through
  the standard `SpeechRecognizer` API (required for the assistant role).
- **Main UI that launches** — the 6-tab bar exceeded
  `BottomNavigationView`'s hard limit of 5 and crashed MainActivity on
  start; navigation is a scrollable TabLayout now.
- Lint-clean (`lintDebug`), 4 unit tests green.

Setup after install: open the app once (grants mic/camera), enter PC
tailnet IP + bridge token in the Link tab, then set it as assistant
above. Full flow (role grant, bind-on-boot, session show/exchange/hide,
recognizer protocol) verified on an API-35 emulator against the live PC
bridge; see `~/JarvisLink-v1.2.apk` (debug-signed, vCode 3).

## Features (v1.1)

- **Talk** — hold-to-talk voice with a live particle orb (idle/listening/
  thinking/speaking energy states); screen stays awake while talking.
- **Chat** — on-phone text conversation with Jarvis (history-aware,
  screen stays awake while chatting).
- **Type** — remote keyboard (full text + Enter/Esc/Tab/arrows via wtype).
- **PC** — volume, media keys, app launcher, live screenshots, ping,
  lock/unlock (confirm-gated in-app), screen blackout/restore.
- **Camera** — see-what-I-see: capture uploads a frame to the PC;
  view the latest frame back.
- **Link** — settings (host/token/ports), connection test, background
  mic toggle.
- **Background service** — foreground mic uplink streams 16k PCM to the
  PC `mic_uplink` server for remote "hey Jarvis" detection; on WAKE the
  phone buzzes and opens Talk. Autostarts on boot (if enabled). Talk +
  Chat hold a wake lock so the phone stays always-on with you.

## Install

1. On the phone, install Tailscale (Play Store/F-Droid) and join the
   same tailnet as the PC. Note the PC's tailnet IP
   (`tailscale ip -4` on the PC — e.g. `100.77.6.93`).
2. Copy `JarvisLink-v1.0.apk` (ask the owner) to the phone and install
   (allow "install unknown apps" once). Debug-signed; release signing
   is post-v1.
3. Open JarvisLink → Link tab → enter PC tailnet IP + bridge token
   (owner: `cat ~/.jarvis/bridge_token`) → Save → Test connection
   (expect `OK: bridge …`).
4. Grant microphone + camera + notification permissions when asked.
5. Flip "Background mic" on for hands-free wake. Keep Tailscale
   connected (persistent VPN) or the link drops.

## PC side requirements

- Bridge with `JARVIS_BRIDGE_BIND=<pc-tailnet-ip>` (or `0.0.0.0`) AND
  `JARVIS_BRIDGE_TOKEN` set — it refuses public binds without a token.
- `mic_uplink.py` reachable on `JARVIS_MIC_PORT` (default 4318, same
  bind rule via `JARVIS_MIC_BIND`). The Tauri shell supervises both.
- Voice reply needs `GOOGLE_API_KEY` on the PC (Gemini-direct) and the
  local whisper/Piper stack (present in `.venv`).

## Build (PC, no sudo)

Needs JDK 21 + Android SDK (see repo: `~/Android/Sdk`,
`~/.local/share/jdk-21*`, Gradle 8.10.2):

```bash
export JAVA_HOME=$HOME/.local/share/jdk-21.0.12.1+1
~/.local/share/gradle-8.10.2/bin/gradle :app:assembleDebug
# APK: phone/app/build/outputs/apk/debug/app-debug.apk (15MB, debug-signed)
```

`phone/local.properties` (sdk.dir) is gitignored — recreate per machine.
`minSdk 28`, `target/compile 35`, Kotlin 1.9.24, no Compose (Views +
Material) to keep headless builds boring.
