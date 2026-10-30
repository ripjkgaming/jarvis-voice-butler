# Voice session migration: livekit-client to `lib/voice-session`

Status: additive phase. The new core (`lib/voice-session.ts`) and hooks (`lib/voice-session-react.tsx`) exist. No component imports have been switched. Protocol: `docs/voice-transport.md`.

## Hook and call mapping

| LiveKit (today) | New | Notes |
| --- | --- | --- |
| `useSession(tokenSource, opts)` | `useVoiceSession()` → `{ isConnected, status, connect(url, token, {fetchToken?}), disconnect, sendChat, session }` | `connect` replaces the TokenSource. `fetchToken` is required for reconnect: the token nonce is single use. |
| `SessionProvider` / `useSessionContext()` | `VoiceSessionProvider` / `useVoiceSession()` | Provider needs `session` prop only to share an instance. |
| `session.end()` | `disconnect()` | Sends close 4010, no reconnect. |
| `session.isConnected` | `isConnected` | `status === 'connected'`, set on `hello`. |
| `useAgent().state` / `useVoiceAssistant().state` | `useAgentState()` | Values: `initializing`, `listening`, `thinking`, `speaking`. Server `idle` maps to `listening`. |
| `useAgent().failureReasons` | `snapshot.lastError` via `useVoiceSession().session.getSnapshot()` | Terminal close reasons are text in `lastError`. No list. |
| `useSessionMessages(session).messages` / `useChat().messages` | `useTranscript()` | Items are `{id, from: 'user' \| 'agent', text, final}`. |
| `useChat().send(text)` | `sendChat(text)` | Returns boolean, not a Promise. Adds the local user message. |
| `useTrackToggle({source: Microphone})` | `useMicToggle()` → `{enabled, toggle(next?)}` | `enabled` means mic on. Muting stops frames but keeps the socket. |
| `useTrackToggle` (Camera, ScreenShare) | none | Phone video (`0x03`) has no desktop capture path yet. Hide these controls. |
| `useLocalParticipantPermissions()` | derive from mode | Mic and data allowed only in `speaker` mode. No hook yet. Read `session.getSnapshot().mode`. |
| `usePersistentUserChoices()` / `useMediaDeviceSelect()` | none | Device persistence and selection are out of scope. |
| `useStartAudio()` / `useEnsureRoom()` | `session.resumeAudio()` | Call from a click handler. |
| `useTrackVolume(track)` / `useMultibandTrackVolume(track)` | `useAudioLevel('agent')` (single RMS) | Per-band values are not implemented. Pass the value as the existing `volume` override. |
| `useLocalTrack` mic level | `useAudioLevel('mic')` | |
| `room.on(RoomEvent.DataReceived, (bytes, p, k, topic))` | `useDataTopic(topic, (payload) => ...)` | Payload is already parsed JSON. |
| `useRoomContext()` | none | Use the session via `useVoiceSession().session`. |
| `setLogLevel`, `window.__lk_room` | none | Debug hook is removed with LiveKit. |
| `<RoomAudioRenderer muted volume>` | `session.setPlaybackMuted(bool)`, `session.setPlaybackVolume(0..1)` | Playback is in the core. The HUD webview passes `muted`. |
| `TokenSource.custom(mintToken)` / `TokenSource.endpoint` | `connect(url, token)` with a token fetched by the caller | Bridge must return the WebSocket URL and token. |

## Per-file map

- `components/app/app.tsx`: `TokenSource` → token fetch via `mintToken(getHudRoom())`. `useSession` → `useVoiceSession`. `muted` prop on `AgentSessionProvider` → `setPlaybackMuted(true)` in an effect. Tauri stays receive-only (`observer` hello).
- `components/agents-ui/agent-session-provider.tsx`: `SessionProvider` and `RoomAudioRenderer` → `VoiceSessionProvider`. `Room` type removed.
- `components/agents-ui/start-audio-button.tsx`: `useEnsureRoom` / `useStartAudio` → `session.resumeAudio()`.
- `components/agents-ui/agent-control-bar.tsx`: `useChat().send` → `sendChat`. `Track` enum → string keys. Camera and screen-share toggles removed.
- `hooks/agents-ui/use-agent-control-bar.ts`: `useTrackToggle` (mic) → `useMicToggle`. `useLocalParticipantPermissions` → mode check. `useSessionContext().local.microphoneTrack` → none. `usePersistentUserChoices` → removed.
- `components/agents-ui/agent-track-toggle.tsx`: `Track.Source` switch → string keys. UI unchanged.
- `components/agents-ui/agent-track-control.tsx`: `useMediaDeviceSelect`, `useMaybeRoomContext` → removed (no device select).
- `components/agents-ui/agent-disconnect-button.tsx`: `useSessionContext().end` → `disconnect`.
- `components/agents-ui/agent-chat-transcript.tsx`: `ReceivedMessage` → `TranscriptMessage`. `message` → `text`. `from?.isLocal` → `from === 'user'`. `AgentState` type → from `voice-session`.
- `components/agents-ui/agent-audio-visualizer-{bar,grid,radial,wave,aura}.tsx` and `hooks/agents-ui/use-agent-audio-visualizer-{bar,grid,radial,wave,aura}.ts`: `audioTrack` prop and `useMultibandTrackVolume` / `useTrackVolume` → `volume` (wave, aura) or `volumeBands` (bar, grid, radial, using one value repeated) fed from `useAudioLevel('agent')`. `AgentState` type import → `voice-session`.
- `components/agents-ui/blocks/agent-session-view-01/components/audio-visualizer.tsx`: `useVoiceAssistant` → `useAgentState()` + `useAudioLevel('agent')`. `videoTrack` (agent avatar) has no equivalent.
- `components/agents-ui/blocks/agent-session-view-01/components/tile-view.tsx`: `useLocalParticipant`, `useTracks`, `VideoTrack` removed. Camera and screen share have no desktop path.
- `components/agents-ui/blocks/agent-session-view-01/components/agent-session-block.tsx`: `useAgent().state` → `useAgentState()`. `useSessionContext` + `useSessionMessages` → `useVoiceSession` + `useTranscript()`. `session.isConnected` / `session.end` → `isConnected` / `disconnect`. `from.isLocal` → `from === 'user'`.
- `hooks/useAgentErrors.tsx`: `useAgent` + `useSessionContext` → `useVoiceSession()`. `state === 'failed'` → `closeCode` 4001-4013 (terminal) with `lastError`. Ends with `disconnect()`.
- `hooks/useDebug.ts`: `setLogLevel`, `useRoomContext`, `window.__lk_room` → removed.
- `hooks/hud/use-jarvis-state.ts`: `useAgent().state` → `useAgentState()`. `agentStateToJarvis` already maps `initializing` to idle, so the mapping is unchanged. Bridge-based logic is untouched.
- `hooks/hud/use-hud-events.ts`: `room.on(RoomEvent.DataReceived)` with topic filter `jarvis-tasks` / `jarvis` → two `useDataTopic` calls. `parsePayload` byte decoding is no longer needed on this path.
- `lib/hud-room.ts`: no LiveKit import. Unchanged. `getHudRoom()` is still read when the token is fetched.
- `lib/voice-link.ts`: no LiveKit import. Consumes bridge `boot` stages. Unchanged. Optionally, the `online` stage can come from `isConnected` once the bridge hands off.
- `lib/utils.ts`: `getSandboxTokenSource` returns `TokenSource`. Replace with a token-fetch function.
- `lib/tauri.ts`: `mintToken` comment references `TokenSource.custom`. Response shape must include `url` and `token` for the WebSocket.

## Behavior decisions in the new core

- `hello` sets mode and identity. `speaker` starts mic capture. `observer` never opens the mic.
- Observer mode suppresses every outbound frame: chat, publish, mic, and pong. The server drops these, and 4004 is the consequence of sending forbidden frames.
- Captions default to `from: 'agent'` unless the payload sets `from: 'user'`. Interim captions update in place until a `final` arrives.
- `flush` on the `flush` topic clears pending chunks and stops scheduled sources. Playback starts after 120 ms of queued audio.
- Reconnect: any non-4xxx close, up to 6 attempts, delays 500 ms doubling to 8 s. Any 4xxx close is terminal. Reconnect uses `fetchToken` when supplied.
- Malformed, oversize (over 64 KiB), or shape-less frames are dropped and counted in `session.dropped`.

## Gaps before the switch

- No per-band audio levels. Bar, grid and radial visualizers show one shared value.
- No `useVoiceMode()` hook. Mode is read from the snapshot.
- No camera, screen, device selection, or agent video.
- The bridge must return the WebSocket URL and token from `mintToken`.
