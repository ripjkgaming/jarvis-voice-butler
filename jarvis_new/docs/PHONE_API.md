# Phone API (LOCKE//LINK)

The phone client is **LOCKE//LINK**, an installable web app (PWA) in `phone-web/`.
Design and build contract: [`docs/phone-app/PLAN.md`](phone-app/PLAN.md). Operator steps:
[`docs/phone-app/RUNBOOK.md`](phone-app/RUNBOOK.md).

## Topology

`tailscale serve` (tailnet only, HTTPS, one origin) maps:

| Path | Target |
|---|---|
| `/` | `~/.jarvis/phone-web/current` (static PWA build) |
| `/api` | `http://127.0.0.1:4317` (the bridge) |
| `/voice` | `http://127.0.0.1:7880/voice` (voice hub WebSocket, [voice-transport.md](voice-transport.md)) |

The bridge strips a leading `/api` before routing, so the mount works whether or not
Tailscale strips the mount path. The bridge bearer token never reaches the browser.

## Credentials

| Credential | Who | How |
|---|---|---|
| Bearer | owner CLI, tests, legacy APK | `Authorization: Bearer <JARVIS_BRIDGE_TOKEN>`. Treated as an open gate; no step-up. |
| Device cookie `jl_dev` | the PWA | `<device_id>.<secret>`; `HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=15552000`. Only `sha256(secret)` is stored (`~/.jarvis/phone/devices.json`, 0600). |
| Gate cookie `jl_gate` | the PWA | Random 32-byte id, session cookie. Records live in bridge memory only. |

Every device-cookie request must also send `X-Jarvis-Phone: 1`. A POST must carry
`Origin` equal to `JARVIS_PHONE_ORIGIN`; a GET may omit `Origin`, but if present it must
match. Device auth is off (pairing returns 503) until `JARVIS_PHONE_ORIGIN` is set in the
bridge environment. The WebAuthn RP ID is that origin's hostname.

### Gate states

| State | Rule | Accepts |
|---|---|---|
| `open` | under 600 s since the last authorised request **and** under 43200 s since the passphrase | everything |
| `soft` | 600 s or more idle, still under 43200 s since the passphrase | passphrase or passkey assertion |
| `cold` | no record, over 43200 s, a different device, or the bridge restarted | passphrase only |

Each authorised request with an open gate refreshes `last_seen`. A soft gate is not
refreshed by requests.

**Passphrase:** the deck gate file `~/.config/locke/gate` (`salt_hex:hash_hex`,
`scrypt(n=16384, r=8, p=1)`), path overridable with `JARVIS_PHONE_GATE`. A missing file
gives **503** `gate not configured; run deck passwd`.

**Lockout (per device, memory only):** failures 1 to 4 report `tries_left`; the 5th failure
locks for 60 s and every further failure doubles the wait up to 3600 s. While locked every
attempt (even a correct one) gets **429** with `retry_after`. A success resets the counter.
A rejected passkey assertion counts as a failure.

## Routes

All responses are JSON with `ok`; errors add `error`. Bodies are capped at 4 KiB (16 KiB for
`/phone/v1/passkey`). Phone-route responses send `Cache-Control: no-store`.

| Route | Method | Auth | Needs open gate | Body → response |
|---|---|---|---|---|
| `/phone/v1/pair` | POST | pairing code + same-origin headers | no | `{code, name}` → sets `jl_dev`; `{ok, device_id, registration}` |
| `/phone/v1/passkey` | POST | device | within 600 s of pairing, or open gate | `{credential}` (registration response JSON) → `{ok}` |
| `/phone/v1/challenge` | POST | device | only for step-up purposes | `{purpose}` → request options (below) |
| `/phone/v1/gate` | POST | device | no | `{passphrase}` or `{assertion}` → sets `jl_gate`; `{ok, gate:"open", expires_in_s, soft_after_s:600}` |
| `/phone/v1/gate/close` | POST | device | no | `{}` → clears `jl_gate` (`Max-Age=0`) |
| `/phone/v1/state` | GET | device or bearer | yes | see below |
| `/phone/v1/control` | POST | device or bearer | yes | see below |
| `/phone/v1/command` | POST | device or bearer | yes | unchanged (legacy section) |
| `/phone/v1/session` | POST | device or bearer | yes | unchanged; the PWA dials `wss://<origin>/voice` and ignores `serverUrl` |
| `/phone/v1/capabilities` | GET | device or bearer | no | adds `features.control`, `features.state`, `features.webauthn` = true |
| `/phone/telemetry` | POST | device or bearer | yes | unchanged `{battery: 0-100, charging: bool}` |

`/phone/v1/challenge` purposes:

- `"gate"` and the step-up actions (`guest_off`, `unlock`, `restart`, `shutdown`) return
  `{ok, challenge, rpId, allowCredentials, timeout: 60000, userVerification: "required"}`.
  Step-up purposes need an open gate; `409` if the device has no passkey yet.
- `"register"` (an addition to the plan, so a cancelled passkey prompt can be retried)
  returns `{ok, registration}` under the same rule as `/phone/v1/passkey`.

Challenges are single use, bound to (device, purpose) and expire after 60 s.

| Status | Meaning |
|---|---|
| 401 `{"error":"unauthorized","auth":"pair"}` | no valid credential: pair again |
| 401 `{"error":"gate","gate":"cold"\|"soft"}` | the route needs an open gate |
| 401 `{"error":"denied","tries_left","retry_after"}` | wrong passphrase / passkey |
| 403 `{"error":"stepup","stepup":action}` | missing or invalid step-up assertion |
| 409 | 4th device, or no passkey registered |
| 429 | pairing rate limit, or gate lockout (`retry_after`) |
| 503 | `JARVIS_PHONE_ORIGIN` unset, or no gate file |

`/phone/v1/pair`, `/passkey`, `/challenge`, `/gate`, `/gate/close`, `/state` and `/control`
are all served **before** the bridge's bearer check and its guest gate. For the existing
routes, a valid device cookie with an open gate only marks the request as authorised; the
request then takes the normal path (bearer requests are untouched).

## `GET /phone/v1/state`

```json
{
  "ok": true, "api_version": 1, "ts": 1762500000.123,
  "laptop": {"host": "ripjk", "guest": false, "locked": false, "screens": "on",
             "mic_muted": false, "presence": "present",
             "power": {"percent": 87, "charging": true}},
  "voice": {"call_live": false, "voice_owner": null, "agent_state": null, "waking": false},
  "gate": {"state": "open", "expires_in_s": 40000, "soft_in_s": 600},
  "caps": {"remote": false, "presence": true, "webauthn": true},
  "ages": {"locked": 0.4, "screens": 3.1, "mic_muted": 0.2, "presence": 22.0}
}
```

A probe is never awaited. A stale value triggers one background refresh and the request
returns the cached value, or `null` (age `null`) before the first reading.

| Field | Source | TTL |
|---|---|---|
| `guest` | `watch_mode.guest_required()` | live |
| `locked` | `power_actions.lock_state()` (`ScreenSaver.GetActive`, 2 s timeout); `null` without `DBUS_SESSION_BUS_ADDRESS` (logged once) | 2 s |
| `screens` | `screens_state` tool: `off` when every output is disabled | 10 s |
| `mic_muted` | wake listener `{"status": true}` → `muted` | 2 s |
| `presence` | `presence.check_presence()["status"]`; `unknown` and `caps.presence=false` when `JARVIS_PHONE_PRESENCE=0` | 60 s, only while polled |
| `power` | `/sys/class/power_supply` (`charging` = AC online, else status `Charging`) | live |
| `voice.*` | same as `GET /room` | live |

For a bearer principal `gate` is `{"state":"open","expires_in_s":null,"soft_in_s":null}`.

## `POST /phone/v1/control`

Request `{"action": "<action>", "assertion"?: {...}}`. The phone is always admin: this route
ignores guest mode and calls the primitives directly (never `taskbar.dispatch_power`). Tools
run in a fresh context, outside any private phone call.

| Action | Step-up | Implementation | Readback |
|---|---|---|---|
| `guest_on` | | `watch_mode.enter_guest("explicit")` (no camera, no card) | `guest_required()` true |
| `guest_off` | yes | `request_stop()` then `restore_owner()` | `guest_required()` false |
| `lock` | | `power_actions.dispatch_sync("lock")` = `loginctl lock-sessions` | lock state true, 3 reads 300 ms apart |
| `unlock` | yes | `unlock` tool (`loginctl unlock-sessions`) | lock state false, 3 reads |
| `screens_off` | | `screen_off` tool (all outputs) | `screens_state` |
| `screens_on` | | `screens_restore`; if nothing was recorded as off, `screen_on` (all outputs) | `screens_state` |
| `sleep` | | `systemctl suspend` | none |
| `restart` | yes | `systemctl reboot` | none |
| `shutdown` | yes | `systemctl poweroff` | none |
| `mic_mute` / `mic_unmute` | | wake listener `{"mute": bool}` | the reply's `muted` |

Step-up: `POST /phone/v1/challenge {"purpose": "<action>"}`, run
`navigator.credentials.get`, then send the assertion with the action. Missing or invalid →
**403** `{"ok":false,"error":"stepup","stepup":"<action>"}`.

Response:

```json
{"ok": true, "action": "lock", "status": "verified", "readback": {"locked": true},
 "state": {"host": "ripjk", "guest": false, "locked": true, "...": "..."}}
```

`status` is `verified`, `dispatched_unverified` (no readback possible; the client shows a
warn lamp), `mismatched` (readback disagrees; the client treats it as a failure) or `failed`
(`ok: false`).

## Audit

Written to `actions.log` (the HUD activity feed): `phone_pair`, `phone_revoke`,
`phone_passkey`, `phone_gate open|fail|close <device>`, `phone_control <action> <status>`
(and `<action> stepup` on a refused step-up).

## Pairing CLI

```console
uv run python scripts/phone_pair.py            # QR + code + URL (https://<origin>/#pair=<code>)
uv run python scripts/phone_pair.py --list     # devices
uv run python scripts/phone_pair.py --revoke jl-1a2b3c4d
```

Codes are 10 Crockford base32 characters (dashes, case, `O`/`I`/`L` tolerated), valid for
600 s, single use, at most 3 pending; only their sha256 is stored
(`~/.jarvis/phone/pair.json`, 0600). At most 3 devices; pairing attempts are limited to 5 per
minute across all clients. Revocation takes effect on the next request (no restart).

## Legacy APK (JarvisLink 3.x)

Kept until the retirement steps in [`phone-app/RETIRE.md`](phone-app/RETIRE.md) run. The APK
uses the bearer token on the bridge port:

- `/phone/v1/command` sends text plus a bounded history. Laptop policy picks instant routing
  (`instant: true`, ≤ 500 chars) with an explicit no-route fallback to chat; the reply has
  `via_route`.
- `/phone/v1/session` mints a call token for a new private room (`mode: "new"`) or the live
  laptop room (`mode: "laptop"`, refused in guest mode).
- `/phone/v1/capabilities` negotiates API v1.
- `config/phone_policy.json` (override with `JARVIS_PHONE_POLICY`) accepts only
  `instant_routing` (bool) and `history_messages` (1–20); it is re-read on every command.
- `GET/POST /phone/v1/calls` (laptop rings the phone) stays in the code but is dormant: no
  current client polls it.

POSTs are never replayed by either client.
