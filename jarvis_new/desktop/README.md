# STARK OS desktop

The KDE Plasma 6 theme shares the HUD's cyan blueprint palette, reactor
rings, typography and fine borders. The visual specification is
[`../design/STARK_OS.md`](../design/STARK_OS.md). Fonts and their SIL Open Font
License notices are in `../design/fonts/`.

Run these commands from `jarvis_new/`.

## Generate assets without opening windows

```sh
uv run python desktop/build.py
env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen \
  uv run python desktop/build.py --wallpaper
bash -n desktop/install.sh desktop/revert.sh
```

The first command generates the SVG theme, colour schemes, window decoration,
Konsole profile, splash rings, GTK themes, icon theme and cursors. It runs the
supplemental Plasma/application generators as part of the same command.
`--wallpaper` also exports both 1920 × 1080
PNG wallpapers with Inkscape. Generation does not apply the theme or start a
desktop session. The generated assets are included so installation does not
need Inkscape.

## Install and apply

From a logged-in KDE Plasma 6 session:

```sh
desktop/install.sh
# Or preserve the current wallpaper on every desktop:
desktop/install.sh --no-wallpaper
```

Installation uses the current user's XDG data and config directories, needs no
sudo, and checks the Plasma connection before changing settings. It installs
fonts, the expanded Plasma theme, Aurorae decoration, splash, Konsole profile,
wallpapers, GTK 2/3/4 themes, icons and cursors. It selects native Breeze Qt
controls, compact Alt+Tab, and the STARK wallpaper for the native lock screen.
It does not rearrange panels or restart KWin. Open applications may need normal
reopening to pick up fonts, cursors or cached icons. `--no-wallpaper` preserves
desktop wallpapers; the lock-screen wallpaper is still integrated.

Every installation creates a new private backup under
`~/.jarvis/desktop-backup/`. It saves the affected config files and each
desktop's wallpaper plugin, image and fill mode. Expanded installations also
save every replaced theme asset directory and the previous values of owned
GTK/icon/cursor/switcher/lock-wallpaper settings in `integration.json` and
`gtk-integration.json`. GTK2 uses its active `GTK2_RC_FILES` path; when no
alternate path exists, the fallback receives a removable managed block.
Existing unrelated rules remain intact. The installer
prints the exact backup path, including if installation fails.

## Restore saved settings

```sh
desktop/revert.sh
# Restore a particular snapshot instead of the newest one:
desktop/revert.sh ~/.jarvis/desktop-backup/<snapshot-directory>
```

Run restoration from the logged-in Plasma session too. The newest snapshot
may already contain STARK OS if the installer has been run more than once;
choose the first pre-install snapshot to return to the original appearance.
Restoration supports the original tab-separated wallpaper backups as well as
the newer JSON format. For an expanded snapshot, restoration also puts back
previous asset versions and removes newly introduced asset directories. Older
snapshots have no asset manifest and retain installed assets for manual
selection. The saved full config files are retained for manual recovery; the
script restores theme settings and wallpapers without replacing the running
desktop's panel layout. Expanded setting restoration preserves unrelated GTK
and lock-screen preferences changed after installation.

The original pre-STARK snapshot on this machine is
`~/.jarvis/desktop-backup/20261001-193439`. The 21:15 and subsequent snapshots
already contain STARK styling. Restore the latest expansion snapshot, then the
first expansion snapshot `20261001-213328-uw5ZjG`, then the original snapshot
to recover the older desktop (newest to oldest).

## Headless verification

On this system `/usr/bin/python3` provides PyQt6 and GTK introspection; these
desktop checks do not need the agent application's Python environment.

```sh
env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
  /usr/bin/python3 desktop/verify_plasma_extras.py
env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
  /usr/bin/python3 desktop/verify_app_extras.py
env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
  /usr/bin/python3 desktop/verify_integration.py
env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
  /usr/bin/python3 desktop/verify_gtk_integration.py
env -u DISPLAY -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS \
  /usr/bin/python3 desktop/verify_app_extras.py --installed
python3 desktop/integration.py verify
```

The rollback verifier uses a temporary XDG tree and never changes the running
desktop. The final command checks live owned settings and installed application
assets without launching a window. Frontend export/fixture verification is
described in [FRONTEND_COVERAGE.md](FRONTEND_COVERAGE.md).

## Coverage and boundaries

See [PLASMA_COVERAGE.md](PLASMA_COVERAGE.md),
[APPLICATION_COVERAGE.md](APPLICATION_COVERAGE.md), and
[FRONTEND_COVERAGE.md](FRONTEND_COVERAGE.md) for the complete surface inventory.
The visual source of truth remains `design/STARK_OS.md`.

The finished icon set contains **64 original designs, 161 icon names, and
483 SVGs** with separate 16px/22px versions and detailed scalable masters.
See the [detail preview](previews/icons-stark-os-detail.png),
[full contact sheet](previews/icons-stark-os.png), and
[icon coverage](app-extras/icons/stark-os/README.md).
The applied installation and verification record is [STATUS.md](STATUS.md).

For later artwork-only revisions, use an unused snapshot directory:

```sh
python3 desktop/integration.py update-icons ~/.jarvis/desktop-backup/<new-icon-snapshot>
```

This replaces only the STARK icon artwork and refreshes icon caches; it does
not reapply fonts, GTK settings, wallpapers, or launch commands. The same
`desktop/revert.sh <snapshot>` command recognizes icon-only backups and restores
just their artwork. Always restore snapshots from newest to oldest if undoing
multiple installations.

Native authentication, password masking, PAM, polkit, lock timeouts and session
controls remain intact. SDDM login, firmware, bootloader, disk-unlock and early
boot screens are outside this per-user installation. Libadwaita, sandboxed apps,
websites, custom Electron chrome and application-supplied tray graphics may
control their own appearance. Uncovered icons/cursors inherit complete Breeze
fallbacks. These surfaces are not claimed as fully reskinned.
