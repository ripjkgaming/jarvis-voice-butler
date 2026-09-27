# JarvisLink ↔ jarvis_mobile feature parity

Native app (`jarvis_new/phone`, `dev.jarvis.link`) is the only phone app.
Every behaviour that existed only in the Flutter app (`jarvis_mobile/`,
read-only reference — do not modify) now lives below. Gate:
`./gradlew testDebugUnitTest lintDebug assembleDebug` (green).

## Flutter → Kotlin map

| Flutter source | Behaviour | Kotlin provider | ✅/❌ |
|---|---|---|---|
| `core/voice_ctrl.dart` `connectParams` | Shape POST /token payload into connect params | `VoiceCall.kt` `connectParams` (accepts `{serverUrl,participantToken}` + `{url,token}`) | ✅ |
| `core/voice_ctrl.dart` `CallState` | idle/joining/live/error | `VoiceCall.kt` `CallState` (IDLE/JOINING/LIVE/ERROR) | ✅ |
| `core/voice_ctrl.dart` `join/hangup/setMuted` | Join LiveKit room, publish mic, remote audio plays, mute, hangup | `VoiceCallManager.kt` (`LiveKit.create` + `room.connect` + `setMicrophoneEnabled`) | ✅ |
| `core/voice_ctrl.dart` speaker energy | Agent-speaking drives orb energy (1.0 → 0.45 idle) | `VoiceCallManager.kt` `ActiveSpeakersChanged` + 2s idle decay → `VoiceFragment` orb | ✅ |
| `screens/voice_screen.dart` talk button | TALK/HANG UP + JOINING state + fault + retry text | `VoiceFragment.kt` + `fragment_voice.xml` (`btn_voice_talk`, `voice_state`, `voice_detail`) | ✅ |
| `screens/voice_screen.dart` mute | In-call mic toggle | `VoiceFragment.kt` `btn_voice_mute` (MUTE/UNMUTE) | ✅ |
| `screens/voice_screen.dart` transcript | Live captions below orb, 3s poll | `VoiceFragment.kt` `pullCaptions()` (GET /captions limit 8, newest last) + `voice_swipe` pull-to-refresh | ✅ |
| `core/link_api.dart` `livekitToken` | POST /token `{room, dispatch}` | `LinkApi.kt` `livekitToken(room, dispatch)` | ✅ |
| `core/link_api.dart` `room()` | GET /room — join the live laptop call | `LinkApi.kt` `room()` + `VoiceFragment.kt` JOIN LAPTOP CALL (`btn_voice_room`) | ✅ |
| `core/link_api.dart` `summon` + `chat_ctrl.dart` `summonSpoken` | POST /summon, optional spoken-text seed | `LinkApi.kt` `summon(text)`; `VoiceFragment.kt` SUMMON LAPTOP + `ChatFragment.kt` long-press Jarvis bubble | ✅ |
| `core/link_api.dart` `captions` | GET /captions?limit=N | `LinkApi.kt` `captions(limit)`; feeds in `VoiceFragment.kt` + `ActivityFragment.kt` | ✅ |
| `core/link_api.dart` `actions` | GET /actions?limit=N | `LinkApi.kt` `actions(limit)` (pre-existing); feed in `ActivityFragment.kt` | ✅ |
| feeds auto-refresh + pull-to-refresh | 5s poll + swipe refresh | `ActivityFragment.kt` 5s `poll` + `activity_swipe`; `VoiceFragment.kt` 3s live poll + `voice_swipe`; `ControlFragment.kt` `ctrl_swipe` → volume re-read | ✅ |
| `core/chat_ctrl.dart` `historyOf` | Last 10 exchanges as `[role,text]` pairs | `ChatHistory.kt` `historyOf`/`toJson` (last 20 msgs, role-normalised) | ✅ |
| `core/chat_ctrl.dart` `send`/typing | /chat threads + thinking indicator | `ChatFragment.kt` (history-aware send) — kept, history now via `ChatHistory` | ✅ |
| `screens/chat_screen.dart` long-press | Speak a Jarvis reply via Voice | `ChatFragment.kt` long-press → POST /summon `{text}` + toast | ✅ |
| `control_ctrl.dart` `refreshVolume`/`setVolume` | Volume slider with bridge read-back (step walk + re-read) | `ControlFragment.kt` `ctrl_volume` SeekBar + `setVolume` walk + `refreshVolume` | ✅ |
| `control_ctrl.dart` `toggleMute` | Mute toggle | `ControlFragment.kt` `btn_vol_mute` (state-synced MUTE/UNMUTE) | ✅ |
| `control_ctrl.dart` `media` | play/pause/next/prev + now-playing | `ControlFragment.kt` `btn_media*` + `ctrl_now_playing` | ✅ |
| `control_ctrl.dart` `capture` | Screenshot + preview | `ControlFragment.kt` `capture()` + `img_shot` preview + `ctrl_note` | ✅ |
| `control_ctrl.dart` `screens` | screens state/off/restore (+confirm) | `ControlFragment.kt` STATE/WAKE/SLEEP (`screens_state`/`screens_restore`/`screens_off`, SLEEP confirms) | ✅ |
| `control_ctrl.dart` `openApp` | Open ANY app by free-text name (`open_app` tool) | `ControlFragment.kt` `edit_open_app` + `btn_open_app_go` (presets kept) | ✅ |
| `control_ctrl.dart` `typeText`/`pressEnter` | Type text + Enter | `ControlFragment.kt` `edit_type_text` + `btn_type_send` + `btn_type_enter` | ✅ |
| `core/theme.dart` palette | Near-black navy, cyan #5FE3FF, amber, glass 1px cyan panels, mono uppercase labels | `colors.xml` `jarvis_cyan #5FE3FF` + `jarvis_amber #FFB648`, `themes.xml` primary, `hud_panel` drawable; existing ArcReactorView/HudBackdropView reused | ✅ |
| `widgets/hud.dart` panels/pills/scanline | Glass panels, status pills, section labels | `hud_panel`/`hud_field` drawables, HUD button/field/text styles, `fragment_voice.xml` mono labels | ✅ |
| `widgets/orb.dart` breathing orb | Energy-driven orb hero | `ArcReactorView` (`voice_orb`, `energy`) + speaking indicator | ✅ |
| `screens/home_screen.dart` captions tail | Latest transmissions panel | `HomeFragment.kt` stream (pre-existing) + `ActivityFragment.kt` captions feed | ✅ |
| `screens/home_screen.dart` suit power | Phone battery pill + bridge telemetry | `HomeFragment.kt` power-core readout + `LinkApi.postTelemetry` (pre-existing) | ✅ |
| `core/phone_telemetry.dart` | Payload/clamp/label/60s push, fail-silent | `LinkApi.telemetryPayload` + `postTelemetry` (pre-existing; unit-tested) | ✅ |
| `core/prefs.dart` host/token | Bridge URL + bearer token persistence | `Prefs.kt` host/httpPort/token (+ session/cursor/modes) + `SettingsFragment.kt` | ✅ |
| `main.dart` tabs | Home/Voice/Chat/Control | `Tabs.kt` + `MainActivity.kt`: Home, Voice, Chat, Control (+ all v1.3 tabs kept) | ✅ |
| `screens/soon_screen.dart` | Unused placeholder (not in tab bar) | n/a — no behaviour to port | ✅ |
| RECORD_AUDIO + foreground mic in call | Mic permission + foreground-service-microphone while live | `VoiceCallService.kt` (microphone type, join→hangup) + `VoiceFragment.kt` runtime RECORD_AUDIO gate; manifest perms pre-existing | ✅ |

## Kept Kotlin-only features (still working, untouched paths)

Assistant role (`JarvisInteractionService`, `JarvisSession`), hotword
(`HotwordService`, `Hotword`), recognition service
(`JarvisRecognitionService`), approvals (`ApprovalsFragment`),
biometric-gated unlock (Control/Phone/Home), camera
(`CameraFragment`), SOS + location (`AlertsFragment`), screens
per-output viewer (`ScreensFragment`), activity spend line,
push-to-talk `/talk` (`TalkFragment`, `HomeFragment`), TTS
(`TtsManager`, `Tts`), boot (`BootReceiver`), battery telemetry,
offline/guest modes, dialer/SMS/WhatsApp/clipboard (`PhoneFragment`).

## Deltas vs Flutter worth knowing

- Token shapes: Flutter reads `{serverUrl, participantToken}`; the
  documented shape is `{url, token}`. `VoiceCall.connectParams`
  accepts both (unit-tested).
- `GET /room` is defined but unwired in Flutter; here it powers
  JOIN LAPTOP CALL (joins that room with `dispatch=false`).
- Sleep uses the existing `screens_off` tool (Flutter `control_ctrl`
  names it `screens_off` too; Screens tab uses `screen_off` — both kept).
- New deps: `io.livekit:livekit-android:2.28.1` (matches Flutter
  `livekit_client ^2.13.0` protocol family; needs JitPack for its
  `audioswitch` fork — see `settings.gradle`),
  `androidx.swiperefreshlayout:1.1.0`,
  `org.json:json` (unit tests only — android.jar stubs throw on JVM).
- New tests: `VoiceCallTest` (token parsing), `ChatHistoryTest`
  (history builder incl. bridge JSON shape). `TabsTest` updated to the
  10-tab order. 24 tests, 0 failures.
- `gradlew` wrapper (Gradle 8.10.2) generated so the gate command runs;
  run with JDK 21 (`JAVA_HOME=…/jdk21`).
