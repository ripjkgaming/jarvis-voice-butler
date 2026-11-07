# LOCKE//LINK runbook (what you run)

The builder could not do these steps, because they change network exposure, restart Jarvis
or need your phone. Do them in order. Each step says how to check it worked.

**Facts checked on 2026-11-07:**
- Host: `ripjk.tailea4e7f.ts.net`, tailscale 1.102.3.
- `tailscale serve status` shows one existing forward, `tcp://…:4317 → 127.0.0.1:4317`. The
  old JarvisLink APK uses it. Leave it alone until retirement (`RETIRE.md`, R4).
- The bridge binds loopback. `voice-butler.service` (user unit) runs the Jarvis shell, which
  loads `<repo>/.env.local` and starts the bridge.
- The deck gate file `~/.config/locke/gate` exists, so the passphrase works.

---

## 1. Enable HTTPS certificates for the tailnet (blocker)

`tailscale status --json` reports `"CertDomains": null`, which means HTTPS certificates are
off. `tailscale serve` cannot serve `https://ripjk.tailea4e7f.ts.net` until they are on.

1. Open https://login.tailscale.com/admin/dns.
2. Make sure **MagicDNS** is on.
3. Under **HTTPS Certificates**, click **Enable HTTPS**.

**Check:**

```console
tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["CertDomains"])'
# expect: ['ripjk.tailea4e7f.ts.net']
```

## 2. Tell the bridge its phone origin

Device auth stays off (pairing returns 503) until this is set. Append it to the env file the
shell loads:

```console
cd /mnt/data/jarvis-voice-butler/jarvis_new
printf '\n# LOCKE//LINK phone PWA (docs/phone-app/RUNBOOK.md)\nJARVIS_PHONE_ORIGIN=https://ripjk.tailea4e7f.ts.net\n' >> .env.local
# optional: keep the camera presence probe off (the PRS lamp then shows --)
# printf 'JARVIS_PHONE_PRESENCE=0\n' >> .env.local
```

About the presence probe: while a phone is polling, the bridge takes a camera snapshot at
most once every 60 s to fill the `PRS` lamp. If you don't want that, set
`JARVIS_PHONE_PRESENCE=0`.

## 3. Restart Jarvis (your call: it ends any live call)

```console
systemctl --user restart voice-butler.service
```

**Check:** the bridge has the new routes, and the bearer token still works.

```console
T=$(cat ~/.jarvis/bridge_token)
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:4317/phone/v1/capabilities | python3 -m json.tool | grep -E '"(control|state|webauthn)"'
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:4317/phone/v1/state | python3 -m json.tool
# expect real values for locked, screens, mic_muted and power
```

If `locked` stays `null`, the bridge has no `DBUS_SESSION_BUS_ADDRESS`. The user unit
normally has it. Check with `systemctl --user show-environment | grep DBUS`.

## 4. Build, deploy and mount (this step changes tailnet exposure)

```console
cd /mnt/data/jarvis-voice-butler/jarvis_new
scripts/phone_web_deploy.sh --serve
```

The script runs:
1. `pnpm check` (typecheck, unit tests, build, size budgets, Playwright).
2. A build into `~/.jarvis/phone-web/releases/<BUILD_ID>/`.
3. An atomic swap of the `current` symlink.
4. These three mounts:

```
tailscale serve --bg --set-path /      ~/.jarvis/phone-web/current
tailscale serve --bg --set-path /api   http://127.0.0.1:4317
tailscale serve --bg --set-path /voice http://127.0.0.1:7880/voice
```

Never use `tailscale funnel`. Everything stays tailnet-only.

**Check:**

```console
tailscale serve status            # lists /, /api, /voice on :443 AND the old tcp 4317 forward
curl -sI https://ripjk.tailea4e7f.ts.net/ | head -1                     # HTTP/2 200
curl -s https://ripjk.tailea4e7f.ts.net/api/phone/v1/state -H 'X-Jarvis-Phone: 1'
# {"ok": false, "error": "unauthorized", "auth": "pair"}
curl -sI https://ripjk.tailea4e7f.ts.net/manifest.webmanifest | grep -i content-type
```

If `/` returns 404 or 403, tailscaled is not following the `current` symlink. Re-run with
`scripts/phone_web_deploy.sh --serve-direct`, which serves the release directory itself and
must be re-run on every deploy.

## 5. Pair the phone (once)

On the phone, Tailscale must be connected.

```console
uv run python scripts/phone_pair.py
```

1. Scan the QR code with the phone's camera and open the link in **Chrome**. The code is valid
   for 10 minutes and works once. You can also type the printed URL.
2. Chrome asks to save a passkey. Confirm with your fingerprint.
3. Enter the deck passphrase. CORE opens.

## 6. Install the app

In Chrome, open the menu, then **Install app** (or **Add to Home screen**), then **Install**.
The launcher icon is the reactor star. Open it from the launcher. It runs fullscreen,
without the Chrome bar.

## 7. On-phone checklist (PLAN.md §12.4; this is the R0 sign-off)

Tick each item, or write down the limitation you accept.

| # | Check | OK / limitation |
|---|---|---|
| 1 | Installed from Chrome (WebAPK). Launcher icon correct with round and squircle masks. With themed icons on, the monochrome icon shows, or record that Chrome did not apply it. | |
| 2 | Pair with the QR, register the passkey, enter the passphrase. | |
| 3 | 5 min voice call, including 60 s with the screen off, then a reconnect after toggling airplane mode. You should see `── LINK RESET ──`. | |
| 4a | Guest ON: the HUD shows guest. | |
| 4b | LOCK, then UNLOCK with the fingerprint. | |
| 4c | Guest OFF with the fingerprint. | |
| 4d | Screens off, then on. | |
| 4e | PC mic mute, then unmute. | |
| 4f | Sleep (wake the laptop by hand). | |
| 4g | Restart: do this last; hold RESTART for 1.2 s, then the fingerprint. | |
| 5 | Lock and unlock **while guest mode is on**: both must work. | |
| 6 | Stop the bridge: the app shows `LNK OFF`, with grey lamps and disabled controls. Start it again: the app returns to a **cold** gate (passphrase). | |
| 7 | Update path: run `scripts/phone_web_deploy.sh` again. `UPD` appears in the strip; tap it and the new version loads. | |

Also check: battery telemetry. With the app open for 60 s, the HUD phone battery reading
updates (`sys.phone`).

## Everyday operations

| Task | Command |
|---|---|
| List devices | `uv run python scripts/phone_pair.py --list` |
| Revoke a lost phone (effective at once) | `uv run python scripts/phone_pair.py --revoke jl-xxxxxxxx` |
| Deploy a new build | `scripts/phone_web_deploy.sh` (add `--serve` only if mounts were changed) |
| Roll back the web app | `scripts/phone_web_deploy.sh --rollback` |
| Releases and mounts | `scripts/phone_web_deploy.sh --status` |
| Audit trail | `grep phone_ ~/.jarvis/actions.log` (pair, revoke, passkey, gate, control) |

**Removing the mounts (undo step 4):** `tailscale serve reset` would also delete the old APK's
`tcp 4317` forward, so do not use it. The per-path form is commonly
`tailscale serve --https=443 --set-path=/api off` (likewise for `/` and `/voice`). This
host's `tailscale serve --help` does not document `off`, so check the syntax there first.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Gate row `UNPAIRED`, pairing says `HOST NOT CONFIGURED` | `JARVIS_PHONE_ORIGIN` is not set in the bridge environment (step 2, then step 3). |
| `LINK DOWN // TAILSCALE ON? RETRYING` | The phone's Tailscale is off, or the `/api` mount is missing. |
| `GATE NOT CONFIGURED · RUN deck passwd` | `~/.config/locke/gate` is missing. Override the path with `JARVIS_PHONE_GATE`. |
| `LOCKOUT 60s` | 5 wrong passphrases. Each later failure doubles the wait, up to 1 h. A correct entry resets it. A bridge restart also clears it. |
| `NO FINGERPRINT ON FILE` | No passkey yet. Enter the passphrase; the app offers registration once per session. |
| `PC MIC --` on DECK | The wake listener (`wake.sock`) is not answering. |
| `PRS --` | Presence is disabled (`JARVIS_PHONE_PRESENCE=0`), or the camera is unreadable. |
| `LAPTOP BUSY` on call | Another call holds the voice hub (close code 4003). |
| Call drops while backgrounded | Android killed the page. The app re-mints and reconnects for up to 30 s; past that, tap the core. |

## Local test recipe (what the builder ran)

```console
cd phone-web && pnpm install --frozen-lockfile && pnpm check   # tsc, 71 unit, build, budgets, 26 e2e
uv run pytest tests/test_phone_*.py tests/test_bridge_api_prefix.py tests/test_power_lock_state.py
```

A real-bridge browser run used a throwaway bridge handler on port 4399:
- environment: a temp `JARVIS_HOME`, a temp gate file, `JARVIS_PHONE_ORIGIN=http://localhost:4174`;
- `LINK_BRIDGE=http://127.0.0.1:4399 npx vite preview --port 4174`;
- Chromium with a virtual authenticator.

It covered pairing, a real server-side passkey registration, a failed passphrase then a
correct one, and live state readbacks from this laptop.

## Connection diagnosis (2026-11-07, installed APK)

The APK displayed LINK DOWN although the phone's Tailscale was connected.
Host checks confirmed HTTPS on port 443 refused connections; the only Serve
mount was the legacy TCP 4317 forward. `CertDomains` was null. The running
bridge had no `JARVIS_PHONE_ORIGIN`; its bearer-authenticated phone state
endpoint returned 404 (old code). The bridge was a child of an autostarted
`jarvis-shell`, while `voice-butler.service` was inactive. Do not start a
second shell via systemd alongside it.

Prepared locally: the HTTPS origin in `.env.local` and the web release via
`phone_web_deploy.sh --skip-check` (no Serve changes). This does not activate
the endpoint or update a running process. The owner must enable HTTPS
certificates and approve a shell restart before the remaining steps proceed.
After restart, verify the phone endpoint returns device-auth JSON rather than
legacy unauthorized/unknown-route responses, mount `/`, `/api`, `/voice`
without resetting the legacy TCP forward, and verify HTTPS from the tailnet.
Only then create a fresh pairing code.
