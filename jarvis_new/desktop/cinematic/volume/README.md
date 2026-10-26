# Animated desktop volume popup

The speaker-volume OSD uses a compact 264 × 72 HUD with a 480 ms arrival,
a smooth 180 ms level change and an 860 ms effects burst. Each range has its
own color and effect: blue sonar rings at 1–25%, cyan directional scans at
26–60%, green equalizer bursts at 61–100%, amber sparks at 101–125%, and red
hazard stripes, a warning triangle and heavy 800 ms breathing pulses above
125%. Crossing a range adds a bloom; lowering reverses the meter sweeps.
Mute closes a signal shutter, unmute blooms, and zero collapses to silence.

A repeated notification at the maximum (normally 150%) triggers a 1250 ms
impact with stronger shake, red illumination and a LIMIT REACHED label, even
when the percentage does not change. The warning clears on lowering or mute.
The OSD cannot distinguish an attempted increase from another notification of
the same maximum value; both receive the limit feedback. Red breathing stops
when the popup hides. Reduced motion preserves colors and warning signs
without motion. Rapid changes retarget the bar and restart finite effects.

It receives Plasma's existing volume-key/OSD events and does not change audio
settings. Changes made by programs that do not request a Plasma OSD continue
to be silent. The native outer borders are removed for this card, and it has
a brief fade before dismissal.

This uses the native `OsdWindow` module from Plasma 6.7. Brightness, microphone,
touchpad and other OSDs retain the installed KDE `OsdItem` implementation.
Only the current user's Plasma service receives the QML import override;
system packages are untouched. Native screen placement and focus behavior
remain owned by Plasma. KDE's zero-animation setting is honored live; the
Jarvis effects' ReducedMotion flags are read when installing.

From the repository root:

```sh
env -u DBUS_SESSION_BUS_ADDRESS QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
  uv run --offline --no-project --python /usr/bin/python3 python \
  jarvis_new/desktop/cinematic/volume/verify.py
uv run --offline --no-project --python /usr/bin/python3 python \
  jarvis_new/desktop/cinematic/volume/manage.py install
```

Installation snapshots the owned QML directory and service drop-in, then
restarts only Plasma's shell. Applications remain running. The installer prints
the exact rollback command. Rerun installation after a major Plasma upgrade to
refresh the matching native fallback. Each installation has its own snapshot.

The initial installed snapshot is
`~/.local/share/jarvis-desktop-backups/volume-20261014-132634-622883`.
To undo this installation:

```sh
uv run --offline --no-project --python /usr/bin/python3 python \
  jarvis_new/desktop/cinematic/volume/manage.py restore \
  ~/.local/share/jarvis-desktop-backups/volume-20261014-132634-622883
```

Verification covers actual QML rendering and interpolation, rapid retargeting,
all range boundaries and colors, raise/lower direction, mute/unmute, repeated
maximum notifications through the native wrapper, danger pulse, reduced motion,
animation cleanup, module resolution and native fallback routing. The expanded
VFX preview is `../validation/volume-storyboard.png`. Live 150% and repeated
150% notifications were also exercised through Plasma's D-Bus OSD service.
`../validation/volume-live.png` is the earlier blue-popup capture.

The expanded VFX installation snapshot is
`~/.local/share/jarvis-desktop-backups/volume-20261014-210439-168466`.
Use the same restore command above with this path to return to the preceding
volume popup.

## DeathStalker V2 media buttons

On this desktop, OpenRazer 3.12.2 driver mode caused the keyboard's media
button to emit letters instead of play/pause, and knob-click mute failed.
The user configuration at `~/.config/openrazer/razer.conf` now sets
`driver_mode = False` in this keyboard's existing-serial `Device:` section.
The keyboard was switched to device mode `0:0`, and OpenRazer restarted.
Mode `0:0`, wave lighting and 80% keyboard brightness were verified after
restart. Raw events then correctly reported `PLAY_PAUSE`, `MUTE`,
`VOLUME_UP` and `VOLUME_DOWN`; the user confirmed both buttons work without
extra text. No desktop shortcut remapping is needed.

The prior configuration and device state are backed up in
`~/.local/share/jarvis-desktop-backups/keyboard-media-20261014-211012/`.
To undo this workaround, restore that directory's `razer.conf` to
`~/.config/openrazer/razer.conf` and restart `openrazer-daemon.service` with
`systemctl --user restart openrazer-daemon.service`.
