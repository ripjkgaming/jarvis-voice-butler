Current window and taskbar choreography: [revision 1.6](ORBIT_ANIMATIONS.md). Alt-Tab remains unchanged. The taskbar red slash and shutter/bin descriptions
below are the preserved preceding presets.

# Jarvis robotic desktop animations — revision 1.4

Installed live on KDE/KWin 6.7.3. Two independent layers:

- Window opening: six alternating metal shutter plates assemble, lock along red seams, and clear into the application. 420 ms before the global animation factor (336 ms at the current 0.8 setting). A new application's first buffer may arrive after its mechanical assembly; plates cover that interval.
- Window close: the window compresses into a red orb and enters an upright receptacle anchored inside the relevant panel. Panel orientation and small panel height are accounted for; opening lids near a top screen boundary can be clipped. No Trash widget exists in the current panel configuration, so this is an animated representation, not a real trash operation.
- Minimize/restore: a full-width horizontal scan line, then perpendicular entry into the relevant panel at the same horizontal position; restore reverses the same path. Missing destinations use panel fallback. Protected surfaces, reduced motion, cancellation and two-shader concurrency bounds remain in place. Redirected canvases over four million logical pixels or 4096 per dimension use the native fallback.
- Separate taskbar close: after the final normal window of an application closes, a transient themed icon copy receives a rotating reticle scan, lock, long diagonal cutting stroke, one localized impact beat, and four falling shards that become code glyphs. Pinned launchers are never removed or modified.

- Actual maximize/unmaximize: KDE keeps responsibility for geometry; a 200 ms scan (160 ms at the current factor) adds the same visual language. This is distinct from restoring a minimized window.
- Alt-Tab: existing Compact layout copied as `jarvis-compact`, with an immediate selection and a 170 ms, 2-pixel selection-frame pulse plus 3-pixel scan. Global KDE zero-duration motion disables the pulse. The pulse stops on switcher closure; no ongoing animation while holding a settled selection.

## Taskbar timing and limits

At normal speed each known running-app task icon gets 170 ms travel and 260 ms dwell. Scan stops at the target and is bounded to six seconds; lock/cut/code fall adds 1100 ms. The global animation factor applies. For four positions with the current factor, the full sequence is about 2.26 seconds. Closure itself is never delayed.

The event bridge uses KWin's published icon rectangles, caching them on panel damage and activation/minimize events because KWin clears rectangles during close. No polling timer. The overlay repaints at 30 fps only while active, with one target and four fragments. Its large surface is restricted to the panel during scanning, a 640×360 maximum rectangle during the cut, and a narrow strip during falling. It accepts neither mouse input nor focus. Reopening the same app, desktop/activity changes, output changes and screen lock cancel it. Reduced motion uses a stationary 220 ms outline without sweep, cutting flash or falling.

KWin does not publish launcher-only pinned-icon rectangles through this interface: the scan includes known running-app task positions only. The copy uses the application's desktop-file theme icon, not a captured live badge. Missing geometry or theme icon skips safely. Native Wayland disposable-window tests did not reliably trigger the taskbar overlay; XWayland direct-close and grouped-window tests did. Native Wayland coverage is therefore not claimed. Multi-monitor/panel-edge math was checked in the window harness; visual desktop evidence covers the current 1920×1080 output only. Screenshots produced some KWin atomic-commit busy warnings; no sustained GPU stress test was run.

## Controls

```sh
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/taskbar-effects.py status
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/taskbar-effects.py stop
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/taskbar-effects.py start
```

The overlay has its own login autostart entry. Stop removes that entry and unloads only its own bridge. Configure `ScanDwellMs` (120–600), `ScanMoveMs` (80–400), or `Enabled` in `kwinrc` group `Taskbar-Slice`; these are reread on each close. Window reduced motion is `Effect-jarvis-energy/ReducedMotion`.

Exact rollback to the preceding Energy revision, including removal of this taskbar layer:

```sh
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/energy-effects.py restore /home/ripjk/.local/share/jarvis-desktop-backups/energy-20261004-132544-569723
```

Original pre-Energy transition state is also preserved:

```sh
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/energy-effects.py restore /home/ripjk/.local/share/jarvis-desktop-backups/energy-20261004-123208-658576
```

Full rollback (including restoration of Compact and removal of taskbar autostart) was exercised live, its bridge/overlay/autostart absence verified, then revision 1.4 reactivated. No desktop, audio, Jarvis, browser or Resolve restart was used; only disposable test windows were controlled. Existing blue suit and activation UI were not edited.

## Evidence

`kwin/effects/jarvis-energy/validation/` contains:

- `robotic-live-x11.json`: KWin's live animation records for opening, closing, minimizing, restoring, reversal and settled cleanup.
- `live-open.png`, `live-minimize.png`, `robotic-live-close.png`: frozen owned-window compositor captures.
- `taskbar-live-capture.png`: live reticle and transient task-icon copy on the actual panel.
- `taskbar-direct-close.json`: first-of-two close skipped, final close active, settled overlay stopped.
- `taskbar-live-wayland.json`: retained negative coverage evidence.
- `taskbar-choreography.png`: synthetic frames from the actual overlay painter, not desktop screenshots.
- `maximize-switch-live.json`: actual maximize/unmaximize shader activity and idle cleanup. Current-application Alt-Tab was exercised using only disposable windows, including repeat, reverse, Escape and modifier release; the KWin journal confirmed the custom Compact QML loaded.
- `robotic-final-state.json`: runtime/source hashes, rollback snapshot and limitations.

`node desktop/verify-energy.cjs` checks transition lifecycles, exclusions, reversal, concurrency, panel/icon geometry, bin placement and canvas budget. `verify-taskbar-render.py` checks rendering, static reduced-motion cleanup, and generates the synthetic storyboard with Qt's offscreen backend.

Implementation uses KDE's documented [KWin scripting API](https://develop.kde.org/docs/plasma/kwin/api/) for window lifecycle, iconGeometry, and asynchronous DBus calls.

## Alt-Tab initial-show correction

The original pulse only reacted to a changed selection index and could miss the initial highlight creation. It now starts on highlight creation, switcher visibility and selection changes, with a more visible 170 ms frame/scan. Installed and KWin reconfigured while session 3 was already locked; visual verification of this correction is pending unlock. The previous QML is preserved in `/home/ripjk/.local/share/jarvis-desktop-backups/alt-tab-pulse-20261004-134803/main.qml`. Very quick Alt-Tab taps may bypass the switcher UI according to KDE’s existing delay behavior.

## Confirmed Alt-Tab and direct panel entry correction (revision 1.4)

The old switcher component remained cached inside KWin after same-path QML edits and reconfigure. A fresh package identity, `jarvis-compact-v3`, forces the revised component to load without restarting KWin. The pulse is now a top-level overlay independent of ListView highlight creation. `TabBox/DelayTime=0` removes the stock 90 ms display delay. Final pulse duration is 180 ms and switching remains immediate.

Actual Alt+Tab was invoked with two disposable windows as the most recent pair; Escape cancelled before activating another application. Native screenshot `alt-tab-native-proof.png` shows the corrected row overlay with diagnostic timing temporarily stretched to 1800 ms for capture. That diagnostic package is not active. The final fresh package emitted `Jarvis AltTab runtime pulse 180 ms, row 39` during a separate real Alt+Tab test. Source and installed final package match. No visual-confirmation claim relies solely on configured layout names.

Minimize no longer follows an icon sideways. On horizontal panels the source window center X remains fixed, the line travels directly to panel center Y, and only fades after entering the panel. Restore reverses that path. Vertical panels use perpendicular entry. `minimize-panel-entry.png` shows the final line inside the real panel at 92% of the animation. The observed source center and target share global X=780; target Y=1052 lies inside the live panel. Unit coverage checks negative-origin top/bottom panels and the no-horizontal-travel invariant; live reversal and cleanup passed.

Session remained unlocked (`LockedHint=no`); no new lock was requested or performed. Before this change the full prior sources and configuration were saved under `/home/ripjk/.local/share/jarvis-desktop-backups/direct-panel-alt-tab-20261004-143012`. The full original rollback command above remains available; it also restores the original switcher delay from the saved configuration.
