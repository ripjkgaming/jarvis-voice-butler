# Jarvis voice transport (WebSocket)

Replaces the former LiveKit room. One authenticated WebSocket per call, served by the agent worker process (`src/agent.py`, implemented in `src/jarvis_rt/transport/`). The bridge connects to it; it does not serve it.

## Endpoint
`ws://127.0.0.1:<JARVIS_VOICE_PORT>/voice?token=<call token>` (port default 7880, bound to loopback). Remote access goes through a tunnel to this loopback endpoint; never open a public port. `JARVIS_VOICE_URL` (ws:// or wss://) overrides only the URL that clients use.

## Call token
`<b64url(json claims)>.<b64url(hmac_sha256(secret, claims_b64))>`; claims: `{room, identity, mode, issued, expires, nonce, attrs}`.
`mode`: `speaker` (mic + control) or `observer` (receive-only HUD: server drops its binary and control frames). TTL 900 s for phone, 120 s otherwise. Secret: `~/.jarvis/voice_secret` (0600, created on first use). Phone tokens keep the `phone_session` HMAC authority; the nonce is single use (`.claimed` ledger).

## Frames
Binary: `[1-byte kind][payload]`
- `0x01` mic: PCM16 LE mono 16 kHz, 32 ms (1024 B) blocks, client -> server.
- `0x02` agent audio: PCM16 LE mono 24 kHz, server -> client, any chunk size, paced by the server.
- `0x03` video: `[8-byte BE ms timestamp][JPEG]`, client -> server (phone camera/screen).

Text frames are JSON `{"topic": str, "payload": any}`; topics keep their former LiveKit names:
- `jarvis-tasks` (HUD events, server -> all), `jarvis.voice.barge.v1` (<= 256 B payload, validated as today), `lk.chat` (typed text, client -> server as `{"text": str}`), `state` (`{"agent": "...", "user": "..."}`), `caption` (`{"text": str, "final": bool}`), `flush` (server -> client: drop queued playback now, sent on interrupt), `hello` (server -> client on accept: `{identity, room, mode}`), `ping`/`pong`.

## Close codes
4001 bad/expired token, 4002 replayed token, 4003 call already active, 4004 observer sent forbidden frame, 4010 client requested close, 4011 room closed/deleted, 4012 duplicate identity, 4013 owner revoked. Unlisted = lost connection (client reconnect/backoff policy applies).

## Limits
Text frame <= 64 KiB, binary <= 64 KiB, oversize or malformed frames are dropped and counted. Outbound queues are bounded; telemetry topics drop oldest, mic audio is never dropped. One speaker per room; observers unlimited (16 max).

## Running

```console
uv run python src/agent.py dev       # debug logging
uv run python src/agent.py console   # info logging
uv run python src/agent.py start     # info logging (production / Docker)
```

The worker writes `$JARVIS_HOME/voice_active.json` (`{"active": true, ...}` while a call is live) so other processes can detect a call without the hub.
