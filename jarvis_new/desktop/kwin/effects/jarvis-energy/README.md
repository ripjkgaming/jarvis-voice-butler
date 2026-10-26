# Current revision 1.6

See [window orbit and reconstruction](../../../ORBIT_ANIMATIONS.md) for the live
close/open choreography, evidence, limits and exact scoped rollback.

The following records the historical Energy revision, preserved for rollback.

# Jarvis Energy Retrieval

Live KWin 6.7.3 effect, revised from user visual feedback on 2026-10-04.

- **Minimize / restore:** 460 ms before global scaling. Smoothly contract the
  window into the center of its actual taskbar icon; restore reverses the same
  geometry. No trash can or invented dock capsule. Supports icons on any panel
  edge. If no valid icon exists on the window's output, project onto an actual
  panel on that output, then fall back to its bottom edge if no panel exists.
- **Close:** 680 ms. The rounded window contracts into a red energy core, travels
  downward and is absorbed by an upright, opening metal bin. This is a transient
  graphic, not the filesystem trash and never deletes files.
- **Open:** 260 ms. Native eased scale/opacity reveal. No offscreen shader needed;
  original application colors are preserved.

Current global factor is 0.8: 208 / 368 / 544 ms for open / retrieve / close.
Animations are compositor-clock-driven, with no timer process, particle loops,
background rendering, audio, or application actions. Two reusable shader slots
bound concurrent offscreen windows; overflow and unavailable shaders use a short
native fade. Each shader uses one source lookup and analytic shapes. KWin's
HDR/color-management conversion is retained.

## Install, status, rollback

```sh
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/energy-effects.py install
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/energy-effects.py status
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/energy-effects.py restore
```

The last command restores the latest installation snapshot. To restore the
original exact transition configuration from before this work explicitly:

```sh
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/energy-effects.py restore /home/ripjk/.local/share/jarvis-desktop-backups/energy-20261004-123208-658576
```

Rollback was exercised live, restoring the original Signal Aperture package,
Magic Lamp and Squash loaded states and their exact persisted values (including
absent keys), before re-enabling the revised effect. Full `kwinrc` and original
custom packages are saved in each backup. Restore changes only affected keys,
so unrelated newer configuration edits are preserved. The original
`locke-tech-fade` source/package was never overwritten. No KWin restart required.

## Reduced motion and exclusions

```sh
kwriteconfig6 --file kwinrc --group Effect-jarvis-energy --key ReducedMotion true
qdbus org.kde.KWin /KWin reconfigure
qdbus org.kde.KWin /Effects org.kde.kwin.Effects.unloadEffect jarvis-energy
qdbus org.kde.KWin /Effects org.kde.kwin.Effects.loadEffect jarvis-energy
```

Set false and repeat the reload to resume. The targeted reload is needed to refresh
KWin 6.7's cached effect configuration reliably. The installer inherits Signal Aperture's reduced-motion
preference when the new effect has none. Global animation scaling, including
zero, is honored. Roles are claimed even with motion disabled.

Lock, authentication, watch, screenshot, desktop, panel, popup, notification,
fullscreen, splash and unmanaged surfaces are excluded. Current desktop/activity
and close-animation opt-outs are honored. Cancels on geometry changes, screen
lock, fullscreen effects, desktop/activity/output changes, or takeover. Rapid
minimize/restore reverses in flight. Closing cancels other tracks. Native opening
has no shader allocation. All completed/cancelled tracks release their slots.
The blue suit, activation UI, speech, audio, Resolve and trading were not edited.

## Root cause and evidence

The first design drew screen shapes using raw texture coordinates. KWin's
redirected framebuffer texture is bottom-left-oriented; logical desktop geometry
is top-left-oriented. The bin and travel destination were therefore mirrored
vertically. Actual compositor capture confirmed this. The corrected shader
converts screen coordinates with `1 - texcoord0.y` and separately flips the
source sampling coordinate. This fixes both orientation and trajectory.

`validation/live-contact-sheet.png` contains **actual compositor captures** of
owned disposable windows, frozen at representative phases against an owned dark
backdrop. The temporary freeze hook was removed and the effect reloaded after
each capture. The bin is upright. The minimize target was verified against the
live task icon `(436,1040,48,40)`, giving center `(460,1060)`.

`live-wayland.json` records all four transitions and clean completion on the
Wayland session. The test unminimizes only its own exact PID/title via a temporary
KWin script because Wayland clients cannot directly request unminimizing.
`live-x11.json` also records XWayland execution. `reduced-motion.json` confirms no
animations during an owned open/close test. `final-state.json` records active
effects, source/runtime hashes and limits. The focused harness also checks
security exclusions, zero motion, reversal, takeover, slot limits, negative
monitor origins and icons on top/left/right panels.

```sh
node /mnt/data/jarvis-voice-butler/jarvis_new/desktop/verify-energy.cjs
```

Only one physical output was available; other monitor geometries are harness
checks. Captures are still frames, not a motion-quality recording. Intermittent
KWin main-thread stall warnings occurred during screenshot/reload testing; no
sustained GPU/Resolve load test was run. Checks were kept short and sequential.
`close-preview.gif` and `shader-storyboard.png` are synthetic shader renders,
not desktop recordings. The native opening is shown only in the live evidence.
