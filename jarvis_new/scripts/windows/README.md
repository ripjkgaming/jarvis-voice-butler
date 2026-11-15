# Jarvis Fleet on Windows

Makes this PC reachable by your other Jarvis devices over Tailscale.

## Install (once, from the repo root)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install_fleet.ps1
```

`-DryRun` changes nothing. An Administrator PowerShell adds the firewall rule
(TCP 4317 from `100.64.0.0/10` only). The script prints the URL, token path
and pairing command. `scripts\windows\jarvis-fleet.cmd` wraps `src\fleet.py`.

On the controller: `python src\fleet.py add <name> http://<tailnet-ip>:4317`,
then paste the token.

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\uninstall_fleet.ps1
```

Keeps the token unless you add `-RemoveToken`.

## Partner devices on another Tailscale account

Partner devices sit on a different account. Use node sharing; no account merge.

1. Partner: in the Tailscale admin console, share the device to your account
   (for example `owner@example.com`).
2. You: accept the share. The device appears in `tailscale status` with a
   `100.x` address and pairs like any other device.
3. Partner: run `install_fleet.ps1` on their PC. Send you the URL and token
   over a private channel.
4. You: list the partner account in `%USERPROFILE%\.jarvis\fleet\config.json`
   as `{"partner_accounts": ["partner@example.com"]}`, or set
   `JARVIS_FLEET_PARTNERS=partner@example.com`.

Partner emails are private: never commit them.
