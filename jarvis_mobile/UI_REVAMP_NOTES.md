# Jarvis Mobile — Cinematic HUD UI Revamp

Premium Iron Man 1–2 style companion app. Presentation only: no controller,
networking, or behaviour changes.

## What changed

- **`lib/core/theme.dart`** — full design system. Near-black deep-navy base
  (`#04070D`), cyan primary `#5FE3FF` + bright/deep variants, amber warnings,
  danger red, glassy panels, mono uppercase telemetry labels (`JarvisTheme.label`),
  shared spacing scale (4–32), rounded-but-sharp radii, and complete
  `ThemeData.dark` (app bar, cards, inputs, buttons, chips, slider, dialogs,
  snackbars, nav bar).
- **`lib/widgets/orb.dart`** — arc-reactor hero orb (same `JarvisOrb(energy, size)`
  API). 60-tick outer ring, 3 counter-rotating segmented rings, radar sweep,
  10-dot coil, glow core that brightens with `energy`. Energy is smoothed
  (`lerp` per tick) so state flips never jank.
- **`lib/widgets/hud.dart`** (new) — shared chrome, all `CustomPainter` /
  core animations, zero new packages:
  `HudBackground` (navy gradient + grid + top glow), `HudPanel` (glass +
  hairline cyan border + bracket corners), `SectionLabel`, `StatusPill`,
  `Stagger` (timer-free entrance via `Interval`, so widget tests stay green),
  `HudEmpty` / `HudLoading` / `HudError`, `HudScanline` (shimmer divider),
  `PulseDot`.
- **`lib/main.dart`** — HUD background shell, 320 ms fade+slide tab
  transitions, custom glowing bottom command bar (mono labels + active pip +
  haptics). Tab order/logic unchanged.
- **`lib/screens/home_screen.dart`** — reactor hero, link `StatusPill`
  (`PROBING LINK` loading state), RESYNC / BRIDGE / ONLINE quick tiles,
  transmissions in a bracket panel with role labels. Same poll + link-settings
  pipeline; `JARVIS` title and empty-caption copy preserved.
- **`lib/screens/voice_screen.dart`** — `VOICE UPLINK` header with LIVE /
  JOINING / FAULT / IDLE pill, big reactor, mono status readout, glowing
  TALK / HANG UP button, mute tile, amber `HudError` with retry, transcript
  panel. Same `VoiceCtrl` join/hangup/mute flow.
- **`lib/screens/chat_screen.dart`** — `SECURE CHANNEL` header with message
  count + thinking light, role-labelled asymmetric bubbles (cyan user /
  glass Jarvis), animated thinking dots, long-press summon kept, glowing
  send button. `Ask Jarvis anything.` empty copy preserved.
- **`lib/screens/control_screen.dart`** — volume / media / screens / apps /
  remote-keys sections as bracket panels with staggered entrance, styled
  slider + mute tile, hero play/pause, bordered screenshot, mono CAPTURE /
  STATE / WAKE / SLEEP buttons (amber confirm dialog), uppercase app chips,
  type + enter row. Every action still calls the same `ControlCtrl` methods.
- **`lib/screens/soon_screen.dart`** — cinematic placeholder panel with
  reactor ring icon and `COMING ONLINE` loader.

## Constraints honoured

- `lib/core/*_ctrl.dart`, `link_api.dart`, `backend.dart`, `prefs.dart`
  untouched (`git diff` shows only `theme.dart` under `core/`).
- No new packages (`pubspec.yaml` unchanged).
- All features reachable: link settings, resync, talk/hangup/mute/retry,
  chat send + speak-seed, volume/mute/media/capture/screens/apps/type/enter.
- Not committed (working tree only).

## Verification (SDK: `~/.local/flutter/bin`, Flutter 3.47.5 / Dart 3.13.4)

- `flutter analyze` — **no errors**. 4 `info` lints remain, all pre-existing
  in untouched core files (`control_ctrl.dart` unnecessary import,
  `link_api.dart` missing `@override`s).
- `flutter test` — **11/11 pass** (`chat_ctrl`, `control_ctrl`, `voice_ctrl`,
  `widget_chat`, `widget` boot test).
- One test fix was needed mid-revamp: the first `Stagger` used
  `Future.delayed`, which trips Flutter's pending-timer assertion in the boot
  test — rewritten timer-free with `Interval`. No test files were modified.
