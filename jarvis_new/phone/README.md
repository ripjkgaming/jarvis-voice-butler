# JarvisLink — Jarvis phone app (Android)

Companion app for the Jarvis PC voice butler. Talks to the PC over
**Tailscale** (tailnet IP + bearer token) — never expose the bridge to
the open internet.

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
