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

## Two Tailscale accounts (cross-account)

Use this when a device belongs to someone else's Tailscale account (a
partner's computer) and you need Fleet to reach it. Your tailnet and the
partner's tailnet stay separate; the device is shared into yours.

### Node sharing steps

Run `python src/fleet.py share-info` (or `GET /fleet/share`) on the device
you want to share. It prints these steps with the partner account filled in:

1. Open https://login.tailscale.com/admin/machines in your own account.
2. Pick the device, open its menu, and choose **Share...**.
3. Invite the partner's account email (example: `partner@example.com`).
4. The partner accepts the invite link from their own Tailscale account.
   The device then shows up in their tailnet as a normal peer with a
   `100.x` address.
5. The partner pairs it as in Setup: `python src/fleet.py list` to confirm
   it is online, then `python src/fleet.py add <name> <url>` with the URL from
   the device's `pair-info` and its bridge token.

A shared device needs no special code. It is a normal peer with a `100.x`
address, so the bearer-token protocol works unchanged.

### Partner accounts in config

Fleet marks devices from partner accounts (`partner: true` in
`GET /fleet/devices`, `partner` in `devices()`). List the partner logins in
`~/.jarvis/fleet/config.json` (or `$JARVIS_HOME/fleet/config.json`):

```json
{"partner_accounts": ["partner@example.com"]}
```

Or set `JARVIS_FLEET_PARTNERS=partner@example.com` (comma separated). Both
are read; entries are lowercased and deduplicated. Keep real addresses out
of the repo; the config file lives under your Jarvis home.

### Requirements and limits

- **ACLs on both tailnets must allow port 4317** from your device to the
  shared device (and back, if you pair the other way). Otherwise the sharing
  works but the bridge stays unreachable.
- **Sharing is per device and one way.** Sharing a device from your account
  to the partner lets the partner control it. It does not give you access to
  the partner's devices. For two-way control, each side shares a device with
  the other side's account and pairs it.
- Shared devices show `shared: true` in `devices()` and `account` with the
  owning login (lowercased). `account` is empty when the login is unknown.

### Security notes

- A shared device runs commands with the same authority as a command typed
  on it. Share only devices you are willing to let that account drive.
- Revoke access by removing the share in the Tailscale admin console and
  removing the pairing (`python src/fleet.py remove <name>`).
- The bridge token travels only over a private channel. Never paste it into
  email, chat logs, or the repo.

### Peer tools

`python src/fleet.py tools NAME` lists the tools a peer offers (`GET
/fleet/tools`), and `python src/fleet.py tool NAME TOOL [JSON_ARGS]` runs one
(`POST /fleet/tool`). The peer must run a Jarvis build that serves these routes.

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

`GET /fleet/devices` (returns `self`, this device's `account`, the
`partners` count, and `devices` with `account`, `shared`, `partner`),
`GET /fleet/self`, `GET /fleet/share` (sharing steps, partner list),
`POST /fleet/ask {device,text}`, `POST /fleet/ping {device}`,
`POST /fleet/add {name,url,token}`, `POST /fleet/remove {name}`.
`GET /fleet/tools` and `POST /fleet/tool {tool,args}` are the peer side of
the CLI `tools` / `tool` commands.
