# Jarvis Fleet: control your other computers

Run Jarvis on every computer you own, join them to one Tailscale tailnet,
and talk to any of them from any other:

- "list my devices"
- "ping office"
- "ask office to open spotify" / "tell the laptop to lock the screen"

**Separate API keys.** Each device runs its own Jarvis with its own `.env`
keys. Fleet only forwards the plain text of your command (`POST /chat` on
the peer's bridge); the peer answers with its own models and keys. No key is
ever sent, shared or pooled, so one device cannot burn another's quota.

## Setup (once per device)

1. Install Tailscale on every device and sign in to the same account.
2. On each device, make the bridge listen on its tailnet IP. It refuses to
   start on a non-loopback address without a token (`~/.jarvis/bridge_token`):
   - Linux/macOS: `JARVIS_BRIDGE_BIND=$(tailscale ip -4) python src/bridge.py`
   - Windows: run `scripts/fleet_setup.ps1` (sets the user env var, prints
     this device's pairing info).
3. On the device you control FROM, pair each target:
   `python src/fleet.py add office http://100.x.y.z:4317` and paste the
   target's bridge token when asked (`python src/fleet.py pair-info` on the
   target shows its URL and the token file path).
4. Check: `python src/fleet.py list` and `python src/fleet.py ping office`.

Pairing is one-way: pair A→B to control B from A. Pair B→A too for the
reverse.

## Safety

- Stored URLs must be tailnet addresses (100.64.0.0/10, `fd7a:115c:a1e0::/48`
  or `*.ts.net`); anything else is rejected, so the registry cannot be used to
  reach arbitrary hosts.
- Registry `~/.jarvis/fleet/devices.json` is mode 0600 (tokens inside); the
  API and voice never print tokens.
- Commands received from a peer (`client_id` `fleet:*`) are never forwarded
  again, so devices cannot loop.
- `/fleet/*` routes need the bridge bearer token and refuse guest sessions.
- A peer runs the command with the same authority as a command typed on it.
  Only pair devices you own.

## Bridge API

`GET /fleet/devices`, `GET /fleet/self`, `POST /fleet/ask {device,text}`,
`POST /fleet/ping {device}`, `POST /fleet/add {name,url,token}`,
`POST /fleet/remove {name}`.
