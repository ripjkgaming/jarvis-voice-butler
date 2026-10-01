# Jarvis Mobile — build plan

Fresh Flutter app (not a fork): remote voice + PC control over Tailscale.
UI language ported from the native phone app (OrbView particle recipe).
Backend contract: `jarvis_new/src/bridge.py` (LinkApi parity) + LiveKit Cloud.

Transport: bridge `http://100.77.6.93:4317` + bearer token
(`~/.jarvis/bridge_token` on the laptop). Voice rooms join LiveKit
Cloud with tokens from bridge `POST /token`.

Build note: no system JDK here — build with
`JAVA_HOME=~/.local/jdk21 flutter build apk --debug`.

## Phase 1 — Link + captions + orb ✅ SHIPPED
Theme (HUD dark), Prefs (host/token + tailnet default), LinkApi (full
bridge surface incl. `/token`), animated JarvisOrb (120-particle
CustomPainter port, energy-driven), home with live status dot + caption
tail + settings sheet. `flutter analyze` clean, widget test green,
debug APK builds.

## Phase 2 — Voice (remote Jarvis access) ✅ SHIPPED
VoiceCtrl (token via POST /token, Room join, mic publish, speaker
auto-play, speaking-driven orb energy), voice tab with Hero orb,
mute/hangup/retry, live captions. Mic/camera permissions in manifest.
Unit + widget tests green.
`livekit_client` + `record` deps. Voice tab: fetch `/token`, join room,
mic publish, speaker output, orb energy ← audio level, captions → live
transcript. Hero orb transition home ↔ voice. Accept: round-trip
"what time is it" over tailnet+cloud.

## Phase 3 — Chat ✅ SHIPPED
Chat tab with history (last 20 trimmed pairs), typing indicator,
long-press voice-seed summon, degrade lines on outage. Unit + widget
tests green.
Chat tab: `/chat` with history, TTS playback toggle, voice-seed
(`"voice": true`) handoff to Phase 2 room. Typing indicator, retry.

## Phase 4 — Remote control ✅ SHIPPED
Volume walk-to-target, media keys + now-playing, screens viewer +
state/wake (confirm-gated sleep), app chips from the server allowlist,
type/enter field. Backend surface via ControlBackend interface (fake
in tests). Unit + widget tests green.
Control tab: volume/media via `/tool`, screens viewer (`tool
"screenshot"` + `screens_state/off/restore`), type/press, quick app
launcher (`open_app` allowlist). Confirm sheets for destructive actions.

## Phase 5 — Polish + release ✅ SHIPPED
Pulsing link dot, haptic Talk button, play-once staggered control
sections, generated launcher icon + native splash, release keystore
(~/.local, never committed), signed split-per-abi APKs
(arm64 ~32MB).
Shared-element orb across tabs, staggered list entrances, haptic ticks
on state flips, animated connection dot, dark-glassmorphism pass,
release APK split-per-abi + icon/splash.
