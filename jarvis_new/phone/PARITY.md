# JarvisLink 3.0: feature parity map

Native app (`jarvis_new/phone`, `dev.jarvis.link`) is the only phone app.
3.0 is a rebuild with a deliberately plain UI: standard Views, no styling.
Logic lives in testable classes (`net/`, `logic/`); fragments only render a
state class and forward clicks. Gate:
`./gradlew testDebugUnitTest lintDebug assembleDebug`.

Layers: `net/` bridge contract (BridgeClient, BridgeConfig, errors, limits),
`logic/` models (one immutable state class per screen), `device/` Android
adapters, `svc/` foreground services and receivers, `ui/` fragments.
`Graph` (process-wide) owns the models so rotation never loses state.
View ids follow `<screen>_<role>` (enforced by `LayoutIdsTest`).

## Feature -> class -> screen

| Feature | Logic class | Screen / ids |
|---|---|---|
| Voice call (LiveKit join/hangup/mute, token mint, retry, timeout, lost-call fault) | `logic/CallModel` (+ `device/LiveKitEngine`, `svc/VoiceCallService`) | Voice: `voice_btn_talk`, `voice_btn_mute`, `voice_state`, `voice_detail`, `voice_speaking` |
| Join the live laptop call (`GET /room`, dispatch=false) | `CallModel.joinLaptop` | `voice_btn_laptop` |
| Summon laptop (`/summon`) | `Messenger.summon` via `CallModel.summon` | `voice_btn_summon` |
| Captions (3 s poll, chronological, newest last) | `CallModel.startPolling` | `voice_captions`, `voice_btn_refresh` |
| Actions feed, captions, `/sys` stats (5 s poll) | `logic/ActivityModel` | Activity: `activity_actions`, `activity_captions`, `activity_system`, `activity_error`, `activity_btn_refresh` |
| Chat with history (last 10 exchanges, persisted) | `logic/ChatModel`, `Messenger`, `Conversation`, `ChatHistory` | Chat: `chat_log`, `chat_input`, `chat_btn_send`, `chat_btn_clear`, `chat_notice` |
| Speak a Jarvis reply on the laptop | `Messenger.speakOnLaptop` | `chat_btn_speak_laptop` |
| Instant commands (`/route`, chat fallback) | `Messenger.send(instant=true)` | Home: `home_btn_instant` |
| Prompt, push-to-talk `/talk` | `logic/HomeModel`, `TalkService`, `device/Audio16k` | Home: `home_input`, `home_btn_send`, `home_btn_talk`, `home_reply` |
| Telemetry (battery, 60 s, charging flip, ref-counted) | `logic/TelemetryReporter` | Home: `home_battery` |
| Connection status and config problems | `HomeModel.refreshLink`, `BridgeConfig.problem` | `home_link`, `home_config_problem`, `home_btn_refresh` |
| Lock / blackout / screens on / fingerprint unlock | `logic/PcActions`, `device/Biometric` | Home: `home_btn_lock/unlock/blackout/restore` |
| Volume (absolute `volume_set` 0..150 with read-back), mute | `logic/ControlModel` | Control: `control_volume`, `control_btn_vol_up/down`, `control_btn_mute` |
| Media + now playing | `ControlModel.media` | `control_btn_prev/play/next`, `control_now_playing` |
| Open app by name | `ControlModel.openApp` | `control_app_input`, `control_btn_open_app` |
| Type text (chunked at 500) + keys | `ControlModel.typeText/pressKey` | `control_type_input`, `control_btn_type`, `control_btn_enter/esc/tab/backspace/up/down/left/right` |
| PC mic mute | `PcActions.togglePcMic` | `control_btn_pc_mic`, `phone_btn_pc_mic` |
| Screenshot per display, display state, off/on | `logic/ScreensModel` | Screens: `screens_outputs`, `screens_btn_capture/refresh/off/on`, `screens_image` |
| Settings, validation, link test | `logic/SettingsModel`, `Prefs`, `BridgeConfig` | Settings: `settings_host/port/mic_port/token`, `settings_btn_save/test` |
| Offline / guest modes (guest locks PC first) | `SettingsModel`, `GuestPolicy`, bridge `guest` stamp | `settings_switch_offline`, `settings_switch_guest` |
| Assistant role | `svc/JarvisInteractionService`, `JarvisSessionService`, `JarvisSession` | system overlay (`session_*`) |
| Recognition service | `svc/JarvisRecognitionService` | n/a |
| Hotword (backoff, cooldown, mic gate) | `logic/HotwordController`, `HotwordGate`, `svc/HotwordService` | `settings_switch_hotword`, `settings_switch_pause_music` |
| Mic uplink + remote "hey Jarvis" | `logic/MicUplink`, `svc/LinkService` | `settings_switch_mic_uplink` |
| Wake routing (locked: one-shot, unlocked: app) | `logic/WakeRouting`, `svc/WakeRouter`, `ui/OneShotVoiceActivity` | `oneshot_*` |
| Approvals (local queue, shade Approve/Deny) | `logic/ApprovalStore`, `svc/ApprovalReceiver` | Approvals: `approvals_container`, `approvals_btn_demo` |
| Camera (send frame, view latest) | `logic/CameraModel`, `device/JpegShrinker` | Camera: `camera_preview`, `camera_btn_capture/latest`, `camera_image` |
| SOS + location (+ notify PC) | `logic/SosModel`, `svc/AndroidSosEffects` | Alerts: `alerts_btn_sos/cancel/location`, `alerts_status`, `alerts_root` |
| TTS | `device/TtsManager` | `settings_switch_tts`, `settings_switch_tts_male` |
| Boot start | `svc/BootReceiver`, `AndroidServiceControl.ensureRunning` | `settings_switch_autostart` |
| Dialer / SMS / WhatsApp / clipboard | `logic/PhoneModel`, `PhoneLinks` | Phone: `phone_number`, `phone_message`, `phone_btn_call/sms/whatsapp/copy/speak` |
| Remote desktop launch | `device/RemoteLauncher`, `PcActions.remoteStart` | `settings_btn_remote`, `settings_spinner_remote` |
| Permissions | `ui/MainActivity.requirePermissions` | `settings_btn_permissions` |

## Bugs fixed in the rewrite

- Enter key sent `Enter`; wtype only knows `Return` (always failed). Now `Keys.ENTER`.
- Sleep used tool `screens_off`; the bridge only has `screen_off`.
- Guest toggle sent `lock` while already guest, so the bridge refused it. Lock is sent first.
- `GET /room` null became the string "null" and was joined as a room.
- Captions were reversed (the bridge returns chronological, newest last).
- Volume was step-walked; now `volume_set` with real read-back (0..150).
- `/type` over 500 chars was rejected; now chunked. `/talk` under 0.1 s was a 400; now explained locally.
- Notification id 3 was shared by hotword and remote launch.
- Mic foreground services started without checking RECORD_AUDIO (crash on 14+); boot start of mic services is blocked on 14/15 (tap-notification fallback); voice call service was sticky (stale notification after process death); wake lock expired after 12 h; telemetry only ran with the mic uplink.
- A service cannot start an activity in the background; wake now also posts a full-screen notification.
- 401 / unreachable / timeout / wrong port / missing token each have a specific message.
- Defaults: mic uplink and hotword now start off (opt-in after permissions).

## Contract tests

`net/ContractTest` reads bridge.py for every tool name, the guest list and
size limits. `net/LiveBridgeTest` (opt-in via `JARVIS_LIVE_BRIDGE`,
`JARVIS_LIVE_TOKEN`) runs the client against a real bridge.py.

## Design (3.0 restyle)

The look follows the desktop HUD (`jarvis_new/frontend/styles/globals.css`):
near-black navy backdrop, pale-cyan Commit Mono text, Everett Light status
lines, cyan hairline glass panels, a live link pill in the app bar, and
state colours (green ok, amber linking, red fault, purple speaking).

- Static styling lives in `res/values/themes.xml` (theme defaults for every
  widget) plus `res/drawable/hud_*` and `res/color/hud_*`. Layouts stay
  free of `style=` and hex colours (`LayoutIdsTest`); they only add panel
  containers, label text appearances and button-row gaps.
- Live styling (state colours, primary/danger button emphasis, the link
  pill) is `ui/Hud.kt`. Empty status labels collapse after each render
  (`ui/UiUtil.kt` `collapseEmptyLabels`).
- No orb: the owner removed it from Jarvis.
