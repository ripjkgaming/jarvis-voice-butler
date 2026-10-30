# Jarvis voice transport (WebSocket)

Replaces the LiveKit room. One authenticated WebSocket per call, served by the bridge process.

## Endpoint
`ws://127.0.0.1:<bridge-port>/voice?token=<call token>` (loopback by default; remote access goes through the tunnel in REMOTE_ACCESS_PLAN.md).

## Call token
`<b64url(json claims)>.<b64url(hmac_sha256(secret, claims_b64))>`; claims: `{room, identity, mode, issued, expires, nonce, attrs}`.
`mode`: `speaker` (mic + control) or `observer` (receive-only HUD: server drops its binary and control frames). TTL 900 s for phone, 120 s otherwise. Secret: `~/.jarvis/voice_secret` (0600, created on first use). Phone tokens keep the `phone_session` HMAC authority; the nonce is single use (`.claimed` ledger).

## Frames
Binary: `[1-byte kind][payload]`
- `0x01` mic: PCM16 LE mono 16 kHz, 32 ms (1024 B) blocks, client -> server.
- `0x02` agent audio: PCM16 LE mono 24 kHz, server -> client, any chunk size, paced by the server.
- `0x03` video: `[8-byte BE ms timestamp][JPEG]`, client -> server (phone camera/screen).

Text frames are JSON `{"topic": str, "payload": any}`; topics keep their LiveKit names:
- `jarvis-tasks` (HUD events, server -> all), `jarvis.voice.barge.v1` (<= 256 B payload, validated as today), `lk.chat` (typed text, client -> server as `{"text": str}`), `state` (`{"agent": "...", "user": "..."}`), `caption` (`{"text": str, "final": bool}`), `flush` (server -> client: drop queued playback now, sent on interrupt), `hello` (server -> client on accept: `{identity, room, mode}`), `ping`/`pong`.

## Close codes
4001 bad/expired token, 4002 replayed token, 4003 call already active, 4004 observer sent forbidden frame, 4010 client requested close, 4011 room closed/deleted, 4012 duplicate identity, 4013 owner revoked. Unlisted = lost connection (client reconnect/backoff policy applies).

## Limits
Text frame <= 64 KiB, binary <= 64 KiB, oversize or malformed frames are dropped and counted. Outbound queues are bounded; telemetry topics drop oldest, mic audio is never dropped. One speaker per room; observers unlimited (16 max).
