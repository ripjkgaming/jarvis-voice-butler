# Dedicated Jarvis window capture identity

The helper is the compiled `jarvis-watch-capture` ELF, with one owner-local
desktop entry `dev.jarvis.watchcapture.desktop`. The desktop entry's exact
`Exec` path identifies the executable for KWin's restricted-interface check.
The permitted interface is only `org.kde.KWin.ScreenShot2`. Neither Python nor
the Jarvis shell receives that entitlement; the helper does not impersonate
Spectacle or provide full-desktop capture commands.

KWin 6.7 checks the authenticated D-Bus caller PID, resolves `/proc/PID/exe`, and
matches its canonical path against the first executable token in a registered
desktop service. Setting an app name or desktop-file environment variable alone
does not establish permission. Keep exactly one desktop service for this helper
executable. The source mechanism is documented in KDE's
[serviceutils.h](https://raw.githubusercontent.com/KDE/kwin/Plasma/6.7/src/utils/serviceutils.h)
and [executable path implementation](https://raw.githubusercontent.com/KDE/kwin/Plasma/6.7/src/utils/executable_path_proc.cpp).

## Review and owner-local installation

These are explicit deployment commands, not actions performed by reading this
document or importing the installer. Review the dedicated native helper and
compiled binary before installation. Installation adds three owner files and
refuses existing files or symlink paths. Removal verifies the recorded hashes
and package identity before deleting them. No service, KWin, guard or desktop
restart is part of installation or removal.

From `jarvis_new`, build and install the reviewed binary:

```bash
cargo build --release --manifest-path native/window-capture/Cargo.toml
uv run python src/watch_capture_package.py install --binary native/window-capture/target/release/jarvis-watch-capture
kbuildsycoca6 --noincremental
```

The installer writes `~/.local/libexec/jarvis-watch-capture`,
`~/.local/share/applications/dev.jarvis.watchcapture.desktop` and a private
hash manifest beside the helper. The cache refresh makes the ordinary
owner-local desktop entry visible to KDE; it does not restart the compositor.
The next owner-process startup discovers this exact package automatically only
when its manifest identity, binary hash and exact desktop-entry content match.
Installation does not change environment files or restart the running Jarvis.
For a reviewed test deployment outside the default owner-local package path,
an optional explicit absolute-path override is available:

```bash
export JARVIS_WATCH_CAPTURE_HELPER="$HOME/.local/libexec/jarvis-watch-capture"
```

The verified default package or that override selects the new **pinned-window**
capture policy. A fresh owner
start request verifies the candidate window through native `getWindowInfo`,
names its current title/application in the Gemini disclosure, and requires
fresh confirmation. The pinned window remains the only target while the user
switches applications. Minimized, excluded-from-capture or closed windows pause
capture. A previous foreground-only disclosure cannot authorize this policy.
An unavailable candidate produces a selection instruction; it never selects
the whole desktop or a replacement window automatically.

For removal, first stop Watch Mode and remove the helper setting from the owner
process configuration, then run:

```bash
uv run python src/watch_capture_package.py remove
kbuildsycoca6 --noincremental
```

If files were changed after installation, removal refuses to delete them.
Inspect those files manually instead of treating an unknown executable or
desktop entry as this package. The installer does not automatically modify
agent/bridge environment files, installed services or the running Jarvis.
