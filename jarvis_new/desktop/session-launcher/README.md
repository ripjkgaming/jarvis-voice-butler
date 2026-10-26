# Ordered login and unlock startup

Installed for this KDE Plasma Wayland desktop: **Brave → ChatGPT → Jarvis**.
The launcher runs eight seconds after login and 1.5 seconds after an unlock.
It skips existing windows (including minimized ones), uses the existing application
launchers, and never closes applications. An app with only a hidden/tray window is
opened through its launcher. Jarvis matching excludes its Drafts/Projects windows.

With one active screen, new windows are maximized to its work area. With multiple
screens, new windows have independent random positions and ordinary window sizes
on the output with KDE's highest priority. The work area excludes panels. Existing
windows are left where they are. Monitor count and primary output are read afresh
for each sequence; a missing primary during placement stops that sequence.

KWin opening, maximize, and screen-transition effects plus the cinematic taskbar
helper must become idle before the next app launches. A short quiet interval
allows late animation starts. These are desktop animations, not browser page-load
or arbitrary animations within applications. Launch timeout, invalid placement,
or an animation that never settles stops the sequence and records an error.
Locking cancels pending work; the next unlock retries only the missing apps.
The KWin script is unloaded after each run; idle service work is a lightweight
watchdog, without desktop polling.

## Files and controls

- Settings: `~/.config/jarvis/session-launcher.json`
- Autostart: `~/.config/autostart/jarvis-session-launcher.desktop`
- Log: `~/.local/state/jarvis-session-launcher.log`
- Service: `org.jarvis.SessionLauncher`, object `/Launcher`
- The competing `~/.config/autostart/Jarvis.desktop` is disabled with `Hidden=true`.
- Installation snapshots are in `~/.local/share/jarvis-desktop-backups/session-launcher-*`.

From this directory:

```sh
/usr/bin/python launcher.py status   # Results, errors and latest window snapshot
/usr/bin/python launcher.py inspect  # Refresh snapshot without moving/opening windows
/usr/bin/python launcher.py run      # Run the same sequence now
/usr/bin/python launcher.py stop     # Stop listener until next login/start
/usr/bin/python launcher.py start    # Listen for unlocks without an immediate launch
```

To disable persistently, stop the listener and remove only
`~/.config/autostart/jarvis-session-launcher.desktop`. To restore the old independent
Jarvis startup, copy `Jarvis.desktop` from the installation backup back into
`~/.config/autostart/`. Do not restore an entire desktop settings directory.

Requires the system Python with dbus-python/GLib, `gtk-launch`, `qdbus`, and
`kscreen-doctor`, all present on the target machine. The helper uses the repository's
existing KWin bridge loader. Window placement follows the
[KDE KWin scripting API](https://develop.kde.org/docs/plasma/kwin/api/).

## Validation

```sh
uv run --project ../.. --no-sync pytest ../../tests/test_session_launcher.py -q
node verify.cjs
```

Validated 2026-10-14: six state-machine/matching tests; 3,000 random geometry checks
including negative monitor coordinates and small work areas; both monitor-count
branches in the JavaScript harness; live three-window sequence with owned temporary
Qt windows on the primary monitor. All three appeared in order, were unmaximized,
and fit within its 1920×1040 usable area. The real Brave/ChatGPT/Jarvis run skipped
all three existing windows. The login/unlock hooks are installed; validation did
not reboot or lock the user's laptop, or disconnect their monitors.
