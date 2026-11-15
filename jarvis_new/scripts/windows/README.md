# Jarvis Fleet on Windows

Makes this PC linkable by your other Jarvis devices over Tailscale. No tokens to copy.

## Install (once per PC)

```powershell
git pull
powershell -ExecutionPolicy Bypass -File scripts\windows\install_fleet.ps1
```

`-DryRun` prints the plan and changes nothing. The script:

- checks Tailscale is `Running` and prints the `tailscale up` fix if not;
- creates a local loopback token in `%USERPROFILE%\.jarvis` (never sent to peers);
- starts the fleet listener at logon (Scheduled Task `JarvisFleet`, hidden);
- adds a firewall rule `Jarvis Fleet`: TCP 4318 from `100.64.0.0/10` only.
  This step needs Administrator; it raises one UAC prompt, or prints the command.

The main bridge (port 4317) stays on loopback. Nothing listens on a public address.

## Linking

Devices on the same Tailscale account link by themselves. Say "list my devices".

## Another Tailscale account (shared device)

1. Owner of the other account: in the Tailscale admin console, share the PC
   (for example to `partner@example.com`).
2. Your Jarvis shows the other account's devices as pending.
3. Accept with the voice command `approve partner@example.com`, or run
   `python src\fleet.py approve partner@example.com`. Approval is per account.
4. To pre-approve an account, list it in `%USERPROFILE%\.jarvis\fleet\config.json`:
   `{"partner_accounts": ["partner@example.com"]}`.

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\uninstall_fleet.ps1 [-DryRun]
```

Removes the task and the firewall rule. The local token is kept; delete it by hand if you want.
Run `python scripts\fleet_bootstrap.py --help` for the bootstrap flags.
