# Jarvis cinematic desktop

Installed design: electric-blue glass with translucent blue closing and brief horizontal slice faults,
broken scan lines and edge flickers. Circular ripple/ring accents were removed
from window and taskbar transitions after user feedback. Timing and glow were
reduced from the stronger intermediate revision. Earlier designs remain in
`desktop/kwin` and the original taskbar scripts.

The Jarvis window effect runs with `WindowMotionOnly=true` and the separate
`ParticleDragEnabled=true` opt-in. Minimise, restore and maximise/geometry
transitions remain enabled. The rebuilt drag/resize effect dissolves the window
into a display-wide cloud of irregular blue motes carried by broad, swirling
currents. The particles gather and curl toward the actual cursor, with a 90ms
exponential response that follows continuous pointer movement smoothly. Native
drag geometry events sample `effects.cursorPos`; mouse-change signals update it
when available. Cursor coordinates are translated to the owning output, including
negative origins and output crossing.

Drag particles v2 (shader `mode 4`, still a single pass with no history buffers):
motes are motion-blurred into streaks along the window's smoothed velocity,
which JS derives from frame-geometry events only (never the pointer), clamps to
6000px/s and the shader decays analytically (170ms) so a paused drag calms
down. Two extra single-lookup depth layers (fine far dust, large near bokeh)
parallax with the window's position on its output. A tapered comet wake and
bright filament trail the gathered core opposite the direction of travel; rare
white glints and a speed/heat colour shift (blue to cyan to white) add additive
bloom. A holographic ghost of the window (corner brackets, marching dashed rim,
scan line, faint lattice) marks the landing rectangle, lifting 2% while held and
settling with a damped overshoot. Sparks are thrown off the rim on pick-up and
drawn back into it on release. All loop terms use integer frequencies, so the
4200ms flow still loops seamlessly; every new term is zero at the release
endpoint, so source pixels return exactly. No ring/ripple accents. Synthetic
1080p GPU timing on the RTX 2050: about 1.1-1.3ms vs 0.65ms for v1 (same
harness). Storyboards: `validation/drag-particles-v{1,2}-storyboard.png`.

Drag particles v3 (2026-10-15): the field now lives in desktop coordinates
(fixed 1080px scale, `desktopOrigin` uniform) and the drag canvas grows over
every output the window or pointer touches (bounded to 4096px per axis and
nine million pixels, else the owning output). Crossing a monitor seam no longer
re-seeds, clips or jumps the field; the attractor stays on the same desktop
point. Root cause of "drag particles not playing": shader-slot ownership was
recorded on the KWin JS window wrapper and matched with `===`; a completion
or deletion arriving through another wrapper never released its slot, and once
both slots leaked every drag silently skipped its particles until the effect
was reloaded. Tracks are now keyed by `internalId` (WeakMap fallback), busy
slots without a live owner are reclaimed, every track has a deadline after
which it is reclaimed, and there are three slots.

Resize trace (shader `mode 11`, `ResizeTraceEnabled`, default on): interactive
resize no longer reuses the drag particles. The content stays crisp; the
dragged edges (detected from the geometry, sticky for the gesture) run hot with
a ruler counted from the pinned side, tension lines bunch against the moving
edge and stream outward while growing / inward while shrinking, pinned corners
hold brackets and the dragged corner carries a grip reticle. Release locks the
perimeter once (560ms) and ends on the exact source. Window-local canvas; never
triggered by moves, maximize or fullscreen.

Fullscreen deploy (shader `mode 12`, `FullscreenCinematic`, classes in
`FullscreenClasses`, default `brave`): F11 and HTML video fullscreen morph from
the old rectangle (any monitor) with a rectangular shock front deploying from
the centre, hologram glass ahead of it, light rails from the corners and a
corner lock; exit folds back in. 560ms in / 460ms out (KDE scaling applies),
both endpoints exact. Wayland (state first) and X11 (geometry first) orders and
follow-up configures are handled by retargeting from the visible rectangle.
Other applications get a size/position/cross-fade morph identical to KWin's
stock `fullscreen` effect, which `manage.py install` now disables so the two
never stack. The Brave extension and native host keep suppressing their tab
cues while fullscreen. Tests: `verify-resize-fullscreen.cjs` (logic, slot
self-healing, 4000-step stress) and `verify-resize-fullscreen-render.py` (GPU:
edge selectivity, exact endpoints, desktop-space continuity across canvases;
`JARVIS_RENDER_SHEET=1` writes `validation/resize-fullscreen-storyboard.png`).

Release keeps the particle field in screen coordinates and gently fades it while
the window reappears at its native position. The former rectangular compression
and rearrangement are removed: grid spacing and the field's bounds stay fixed.
The flow period is 4200ms and reconstruction takes 900ms before KDE scaling
(3360ms / 720ms at factor 0.8). Opening, closing and focus tracks remain under
the existing recovery-mode restriction.

The previous full-screen version caused a KWin crash at 21:17:50 SGT on
2026-10-14. Its completion handler cancelled persistent scale/translation tracks
while KWin traversed its animation list, the suspected crash trigger. The rebuild
uses only finite, equal-duration paint tracks. Completion frees JS bookkeeping
and may append the next finite batch; it never cancels compositor tracks.
Geometry refresh and interruption cancel only from external event handlers.
Drag tracks use `keepAlive=false`, and the full-screen canvas is limited to the
owning output, nine million logical pixels and 4096 pixels per axis. Other modes
retain their original limits. Reduced motion and zero animation scaling apply.

Validation: `verify-windows.cjs` exercises 100 release/regrab sequences and rejects
any cancellation during completion or any persistent `set()` track. The recovery
harness checks the restricted modes independently. `verify-drag-isolated.py`
creates a private D-Bus session, runtime/config/data directories and two virtual
KWin outputs. It passed held-clock renewal, 36 release/resize cycles, eight output
crossings during drag/reconstruction, closing, explicit cancellation and shader
slot contention. Its test-only command hook exists only in the temporary package.
The isolated harness also checks the native cursor coordinates against the
gravity target for each refreshed window. Results and compositor log:
`validation/drag-fluid-isolated.json` / `.log`.

GPU checks confirm complete dissolution, particles across all screen quadrants,
seamless flow loops and exact source RGB after reconstruction. Rendered density
near the pointer must exceed the density with a distant pointer by at least 40%.
The synthetic
`validation/drag-fluid-preview.gif` shows the new motion. A 60-frame GPU-only
1080p measurement with cursor gravity averaged 2.67ms (max 3.45ms) on the RTX 2050; it is not an
end-to-end desktop frame-rate guarantee. The live keyboard-driven test lost
focus to another window and was aborted; it is not claimed as a successful
owned-window validation. The final installation was checked without simulated
input: enabled flag, loaded effect, identical source/runtime files and unchanged
compositor PID. `validation/drag-fluid-install.json` records the final state and
rollback package. This is stronger lifecycle evidence than the original one-shot
check, but does not establish safety under every desktop/GPU workload.

The prior crashing package and trace remain identified in
`validation/particle-crash-rollback.txt`; screen-particle screenshots from that
version are historical evidence. The final scoped backup is
`/home/ripjk/.local/share/jarvis-desktop-backups/cursor-gravity-20261014-215452`.
To disable just particles, set `Effect-jarvis-energy/ParticleDragEnabled=false`
and reconfigure `jarvis-energy` through `/Effects`; finite minimise/maximise
motion remains available. Restoring the backup's `jarvis-energy` package and its
saved particle flag rolls back this rebuild.

The compact Alt+Tab switcher has a dark 73–86% translucent backing, outlined pale
labels, blue edges and a close button per closable window. Its footprint remains
20% screen width with 27px rows at the current settings. Native QML and the new
close controls were rendered over a white owned test window. The close handler
uses KWin's native `ClientModel.close(index)` (normal application save prompts
remain under application control).

The Plasma theme uses 19–24% blue-tinted backgrounds and native blur strength 4.
The switcher adds a stronger translucent layer to maintain contrast on bright
content. Individual third-party app content is not globally forced transparent.

The animated `jarvis-electric-cursors` theme has 12 pointer roles, four sizes,
24 frames at 50ms each and fixed hotspots. It includes arrow, link, text, busy,
resize, move and forbidden states. Unsupported cursor names inherit Breeze.

## Jarvis command sequences

`src/system/cinematic.py` marks resolved window actions just before dispatch in
`kwin_windows.py` and application launches in `core.py`. The effect consumes a
short-lived, single-use ticket scoped to a PID/caption or application class.
Unrelated apps keep ordinary timing. Commands add extended perimeter locks and
segmented data rails. Closing uses a shallow retreat and staggered glass-strip
dissolve with blue accents capped at 28% opacity for manual closes and 46% for Jarvis. Focus uses a fine blue edge
without sweeping, dimming or tinting the content; both timing sets retain the
exact original interior pixels. Red accents remain in other danger styling.

- Open: 1350ms.
- Close: 1450ms, keeping the closed window texture alive until completion.
- Focus, minimize and restore: 1200ms when tagged.
- Maximize, snap, arrange and layout undo: 1250ms geometry travel. Layout requests
  tag their exact windows through the desktop bridge before moving them; a 180ms
  fail-open gate preserves the action when the visual service is unavailable.
- Reduced-motion and global zero-animation settings still suppress effects.
- Missing compositor support, an unmatched class, an ambiguous window or an
  expired ticket falls back to ordinary behavior. This is visual-only and never
  dispatches a window command itself. Native fallback under shader contention
  preserves commanded timing; ordinary fallback remains short.
- Live owned-window tests verified actual 1350/1200/1450ms compositor tracks and
  cleanup. The running voice agent was not restarted; updated routes load on
  its next start.

## Brave

Two local unpacked extensions are installed in the existing Brave profile:
`brave/extension` (new tab and finite webpage transition overlays) and
`brave/theme` (native browser colors). The new tab has glass panels, local time,
search and real current-window tab controls. Version 1.2 adds a 620ms row arrival,
600ms blue-glass closing dissolve, 340ms row repositioning, and a brief departure
when switching from a row. Pin/unpin, mute/unmute and reload controls have finite
feedback; search focus and shortcut hover/press also animate. Closing through
these controls waits for the dissolve, and errors restore the row's controls.
Native closes animate the removed row afterward when the manager is visible.
A shared **Motion off** setting uses local extension storage across new-tab
pages and website cues. OS reduced motion is also respected; disabling motion
cancels active animations and removes overlays immediately.

The native Brave tab strip now also gets blue rail and corner accents through
`brave/native-host.py`. This is a transparent, input-free desktop overlay over
the horizontal tab bar: open expands the rail, close retracts it, and switching,
pinning, muting, loading, moving and zooming produce short cues. At the current
KDE factor, open/close last 920ms and other cues last 608ms. These accents do
not change individual tabs' native shape, layout, or size animation. The theme
changes Brave's supported colors. Ordinary HTTP(S) pages get a bounded,
input-transparent edge cue on document entry, closing, switching, loading,
load completion, pinning, muting, moving the active tab, and zoom changes.
Cues use blue corner locks, retracting rails and short edge fragments; they
finish within 1100ms and never animate website content. Overlapping events are
coalesced, background-tab updates cannot animate a different active page, and
hidden pages remove their cue. Installation updates supported already-open
HTTP(S) tabs without reloading them; new documents receive the same script.
The scripting and HTTP(S) host permissions enable this update. Native/internal
browser pages are excluded. No
website text is read, modified or transmitted by the overlay. The tabs permission
is used locally for the new-tab manager. There are no analytics or remote assets.

Rollback: remove **Jarvis — Electric Glass** and **Jarvis — Blue Glass / Crimson**
in `brave://extensions` (or use `brave/restore.py` while the existing local DevTools
endpoint is available). Existing extensions remain untouched. Prior theme
metadata is saved in `brave/backup.json`.

For this motion update, the previous extension files are saved under
`~/.local/share/jarvis-desktop-backups/brave-motion-20261013-205159/extension`.
Restoring those files and reloading this extension restores version 1.0.

Verification: `node desktop/cinematic/brave/verify-events.cjs` checks event
coalescing and routing. `uv run python desktop/cinematic/brave/verify-live.py`
checks the installed extension through the existing local DevTools endpoint and
exercises temporary tabs in a separate owned window, starting at the real
`brave://newtab/` override. It does not change extension installation. It restores the motion preference
and closes its test tabs. Live results and 390px/desktop screenshots are in
`validation/brave-motion-*`. This update passed the live control, delayed close,
overlay cleanup, input transparency and reduced-motion checks with no captured
runtime exceptions. API boundaries follow the Chromium
[Tabs API](https://developer.chrome.com/docs/extensions/reference/api/tabs) and
[content script model](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts).

The native helper uses an extension-restricted Native Messaging host. It receives
only event kinds and window geometry. A read-only KWin script supplies the actual
active Brave client rectangle, since Brave reports incorrect global positions
under Wayland. Missing geometry, another focused application, fullscreen,
minimize, lock, global zero animation, and reduced motion suppress the native
overlay. Moving the window cancels an active cue. Repaint timers stop after each
cue; the helper performs no screenshot capture, input simulation, tab operations
or website-content reads. This implementation is for the existing horizontal
tab-bar configuration; it does not track individual tab slots or vertical tabs.

Both extensions are now installed persistently in the existing Brave profile
through the normal **Load unpacked** file picker. Their saved installation
location is `4` (unpacked), rather than `8` (command line); evidence is in
`validation/brave-profile-installation.json`. This fixes sessions that were
launched without the earlier command-line flags. No special launch command is
needed. `brave/install-native.py` installs the native host registration and
removes obsolete Jarvis load flags, preserving other extension flags. For a new
profile, load both `brave/extension` and `brave/theme` through **Load unpacked**
in `brave://extensions/` with Developer mode enabled. Avoid using the DevTools
`Extensions.loadUnpacked` command as a persistent installer or in verification.
The original native integration snapshot is
`~/.local/share/jarvis-desktop-backups/brave-native-20261013-211008`.
`uv run python desktop/cinematic/brave/restore-native.py <snapshot>` restores the
host registration and launcher and stops the overlay. Use `brave/restore.py`
as well to unload the extension/theme from a running browser.

Persistence repair: the custom drag/drop installation API failed, and a
`loadDirectory` attempt caused Brave to exit. The session was restored; the
supported file picker installed both packages successfully. Those experimental
installation methods are not included in the shipped installer or verifier.

`uv run --no-project --python /usr/bin/python3 desktop/cinematic/brave/verify-native.py`
checks real Qt paint output, transparent endpoints, negative screen origins,
and invalid/fullscreen/minimized geometry. Native close and pin cues, compositor
placement, retained browser focus, shared motion-off and webpage controls were
exercised live in a disposable Brave profile. Set `JARVIS_BRAVE_DEBUG_PORT` to
point `verify-live.py` at that profile's existing endpoint. An earlier attempt to
enable native accessibility coincided with Brave exiting; session restore was
launched and that approach was abandoned. The shipped native helper does not
use accessibility or DevTools, and no accessibility settings were persisted.

## Wallpapers

The installed pair is `../wallpapers/jarvis-glass-signal-core.png` and
`../wallpapers/jarvis-glass-signal-drift.png`, generated/edited using built-in
ImageGen. Exact prompts and modes are in `../wallpapers/glass-generation-prompts.json`.
They are 1672×941 raster assets. `wallpapers.py restore` restores the previous
per-desktop image settings independently of desktop-effect rollback.

## Motion

| Interaction | Behavior | Base duration | At current KDE factor 0.8 |
| --- | --- | --- | --- |
| Open | Interrupted reconstruction beams, horizontal slice glitches and corner locks | 620 ms | 496 ms |
| Close | Glass strips dissolve with shallow retreat and thin blue fragments | 480 ms | 384 ms |
| Minimize / restore | Smooth tapered collapse into the taskbar centre, with a fine blue edge and late fade; restore reverses | 600 ms | 480 ms |
| Drag / interactive resize | Moving blue scan lines, flowing edges and brief horizontal glitch slices, then a smooth release | 140 ms entry / 1000 ms loop / 300 ms release | 112 / 800 / 240 ms |
| Maximize / snap / resize geometry | Smooth expansion from the visible rectangle, including rapid reversals, with a fine blue edge | 420 ms | 336 ms |
| Focus | Gentle blue edge, unchanged content | 240 ms | 192 ms |
| Taskbar first launch | Icon lift, brief slice offsets, broken signal lines and braces | 580 ms | 464 ms |
| Taskbar final close | Icon slices contract with interrupted lines and retracting braces | 440 ms | 352 ms |
| Menu | Short unfold | 180 ms | 144 ms |
| Notification arrival / departure | Blue scan reconstruction, corner locks, moving rails and fragmented dissolve with pronounced edge travel | 900 / 620 ms | 720 / 496 ms |
| Notification click / open | Smooth full-monitor expansion, blue glass, then a circular reveal from the click point | 440 ms expansion / 780 ms total | 352 / 624 ms |
| Launcher / tray | Native clipped panel slide with coordinated timing | 220 / 160 ms | KDE-scaled |
| Alt+Tab | Original compact list, high-contrast translucent blue glass, close buttons, electric glide and scan | KDE-scaled | Theme-controlled |
| Session entrance | Reactor, depth entrance, progress sweep and systems-online state | Stage-driven | Next login |

The native overview uses its organized grid. Native taskbar hover and window
highlighting remain available. There is one configured virtual desktop; the
slide preset is ready if the user later adds desktops. This installer does not
create additional workspaces. Menu items and notification text stay native;
these are surface transitions, not per-item content animations. Notifications use a dedicated GPU shader with a moving blue scan front,
illuminated corner brackets, segmented rails, brief horizontal slice displacement,
and staggered dissolution on dismissal. Travel is bounded to 48–120 px and follows
the nearest side of the notification’s own monitor. Six independent shader slots
support overlapping notifications; excess cards or unavailable shaders retain
native movement and fading. Critical alerts stay immediate, and reduced motion
or zero animation disables the transition. Shader slots release on completion,
cancellation, lock, configuration changes and deletion.

Notification body clicks use `notification-click.py`, a small D-Bus listener for
Plasma's `ActionInvoked(default)` signal. The action is never delayed. Only a
popup beneath the pointer (or its retained closing texture, up to 600 ms old)
can animate. History entries, reply/archive buttons, and unrelated cards are
excluded. The card expands to the exact bounds of its own monitor in 440 ms, including
outputs with negative coordinates. Geometry stays at full size through the end
of the 780 ms shader clock. Text fades within the first 19% of the transition, before it becomes stretched.
A fine outline and translucent blue gradient carry the expansion. After the
card fills the monitor, a circular aperture grows from the actual click point
to reveal the destination. Fresh clicks and late action signals both use
InOutCubic geometry easing. The timing follows KDE’s animation
factor. A blue ripple replaces the fragmented dismissal. If the action
arrives after closure, existing tracks are retargeted without releasing the
closing pixmap. Reduced and zero motion remain respected.
The expanding card is temporarily elevated above the other notifications, so
cards higher in the stack cannot cut through the animation. Its normal painting
order returns on completion or cancellation; ordinary arrivals retain their order.

Install this addition independently, without restarting Plasma or changing the
other desktop effects:

```sh
/usr/bin/python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/cinematic/notification-click.py install
```

The installer prints its snapshot directory. Use `notification-click.py restore
SNAPSHOT` to restore that effect and the helper's previous autostart state. The
helper starts at login and has no idle polling. `verify-notification-click.py`
checks the genuine action signal with owned test windows, including closure
before the signal; `--before-close` checks the opposite ordering and `--during-arrival` exercises
a click before the arrival animation finishes. Add `--stacked` to keep two owned
notifications above the clicked card during these checks. This exercises
the bridge, compositor tracks, and cleanup, rather than clicking a third-party
app notification. Live records are in `validation/notification-click*-live.json`.

`verify-surfaces.cjs` covers lifecycle, pool exhaustion, shader failure, click
ordering, stale events, pointer targeting, and motion settings. `verify-notification-render.py` compiles the shader with EGL and checks
exact source/transparent endpoints in both directions; its synthetic storyboard is
`validation/notification-click-storyboard.png` (arrival, dismissal, and click). `verify-notification-live.py`
checks real arrival/dismissal tracks and cleanup; the current desktop run is saved
in `validation/notification-heavy-live.json`. Third-party
in-window menus cannot always be animated independently by the compositor.

The native session splash remains session-stage-driven, never a fake loading
wait. The login manager, firmware, disk unlock and authentication are unchanged.

## Install and rollback

```sh
/usr/bin/python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/cinematic/manage.py install
/usr/bin/python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/cinematic/manage.py status
/usr/bin/python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/cinematic/manage.py restore
```

`restore` without a path uses the latest installation snapshot. To restore the
exact state before this chat's changes:

```sh
/usr/bin/python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/cinematic/manage.py restore /home/ripjk/.local/share/jarvis-desktop-backups/cinematic-v2-20261013-144210-792480
```

Snapshots preserve the replaced effect, new package destinations, exact affected
configuration keys (including absent keys), original taskbar service activity,
and autostart file bytes. Unrelated configuration is not restored wholesale.
Live rollback was exercised and checked before the revised effects were applied.
No desktop or application restart is required. The new splash takes effect at
next login. Both taskbar accent processes start at login; they repaint only for
a finite animation and never accept input or focus. Startup explicitly loads
missing enabled Jarvis effects, and the taskbar helper reconnects after KWin
returns on D-Bus. The missing-effect recovery entry point was exercised live;
a full compositor restart was not used as a test.

## Verification and boundaries

- `verify-windows.cjs`: lifecycle, reversal, cancellation, protected surfaces,
  taskbar-centre destinations on all panel edges, negative monitor origins, shader
  concurrency limits and oversized-window fallback.
- `verify-surfaces.cjs`: popup/notification lifecycle, protected surfaces,
  ownership, reduced motion and cancellation.
- `verify-render.py`: real GPU shader compilation and rendered frames; exact
  source-pixel endpoints and transparent completion. `cinematic-storyboard.png`
  is a synthetic render, not a desktop screenshot.
- Live owned-window checks recorded native Wayland and XWayland execution,
  menu, maximize and taskbar first/last-window grouping behavior. Latest Wayland
  records are refreshed for the final glitch revision; the XWayland record is from the initial pass.
- Native Alt+Tab was exercised with two owned windows. QML loaded and its scan
  ran without reported QML errors. A real notification exercised the surface
  effect. The session splash compiled and instantiated offscreen; no logout or
  lock was invoked for testing.
- Two window shader slots, four million logical pixels and 4096 pixels per
  source dimension bound offscreen work. Large surfaces and unavailable slots
  use a native fallback (short for manual actions, full duration for tagged
  commands). The close canvas is local to the window.
- Taskbar accents use at most 128 × 128 logical pixels per surface; a second
  window of the same app does not replay its launch animation. Missing icon or
  panel geometry skips the accent.
- Global zero animation duration is honored. The existing window ReducedMotion
  preference is preserved and copied into the surface effect on installation;
  taskbar accents read it at each event. Alt+Tab and session motion follow KDE.
- No sustained gaming or video-rendering stress test was run. Existing KWin
  main-thread stall messages also appeared during reload/testing; these checks
  do not establish their cause or promise a frame-rate improvement.

Native integration references: [KWin scripting API](https://develop.kde.org/docs/plasma/kwin/api/),
[KDE popup lifecycle](https://github.com/KDE/kwin/blob/master/src/plugins/fadingpopups/package/contents/code/main.js),
[panel slide configuration](https://github.com/KDE/kwin/blob/master/src/plugins/slidingpopups/slidingpopups.kcfg).

## Latest validation

Targeted Python tests: 35 passed. Shader lifecycle and GPU checks passed,
including exact endpoints with command accents enabled. Native cursor files have
96 image chunks each (24 frames × 4 sizes) with fixed hotspots. Brave's live
new-tab controls kept the selected test tab alive during the dissolve, removed
only that tab afterward, and passed 780px responsive and motion-toggle checks.
A broader pre-existing `test_play_media_returns_focus_to_previous_window` failed
because it expects the old “Playing” response while the current media route says
“Requested a new tab … not visually verified”; the media route was not changed
for this animation work. No claim is made that the entire repository test suite
passes.

Live HTTP-page inspection confirmed that the browser cue creates an input-transparent fixed overlay and removes it after its finite animation.

Minimize/drag repair: `verify-minimize.py` recorded all four native minimize and
restore transitions (manual 480ms; Jarvis 1200ms). `verify-drag.py` recorded a
held drag shader, 240ms release and a 1250ms Jarvis arrangement.
`verify-layout-gate.cjs` checks exactly-once layout dispatch with successful,
missing, throwing and late D-Bus replies. The GPU check covers all five shader
modes including exact original pixels at both drag endpoints.

Drag motion revision (2026-10-13): replaced the static held uniform with a
finite animation clock that renews during interactive move/resize. Sparse
horizontal slices shift by at most 4.5 logical pixels; blue scan lines and
perimeter rails continue moving while held. Release returns to exact source
pixels over 240 ms at the current KDE factor. The real rectangle and input
tracking remain native. Reduced motion, cancellation, oversized surfaces and
shader-slot cleanup are covered by `verify-windows.cjs`. GPU checks confirm
changing held frames, a seamless clock loop and clean endpoints. Native Wayland
readback in `validation/drag-live.json` confirms the renewed 1-to-2 clock,
release and no active animation after the owned window closes. The previous
installed effect is saved under
`~/.local/share/jarvis-desktop-backups/drag-glitch-20261013-214602-165328/jarvis-energy/`.

Closing/focus revision: removed red from window and task-icon closing effects.
GPU regression checks isolate generated closing accents on a transparent source
and confirm bounded alpha (28% manual, 46% Jarvis) with blue-dominant pixels. Focus and maximize interior pixels
remain exact throughout manual and commanded effects. Minimize/restore have no red faults. Their tapered blue outline follows the window into the taskbar centre, with a small landing glint. Shader and native fallback use the same target. The live command probe
checks long focus/close tracks and cleanup after reloading the installed shader.

Jarvis closing now has distinct staged shader choreography: a longer acquisition,
twin fine split beams and staggered glass-strip dissolution over 1450ms. Manual
closing stays at 384ms. The final taskbar icon accent matches the exact
PID/caption and unexpired single-use close token, and also lasts 1450ms for
Jarvis; ordinary taskbar closing stays at 352ms. Native readback confirmed both
window and taskbar command durations together. Minimize remains 480ms manual /
1200ms Jarvis, targeting the centre of the actual panel on the same output. The app icon identifies which panel to use when multiple panels exist.


Centre-collapse revision (2026-10-14): minimise keeps the window content visible
while it narrows and tapers into the centre of its taskbar, fading only at the
landing. Restore reverses this same shader path, including mid-flight toggles.
Horizontal and vertical panels, floating panels and offset outputs use the
visible panel centre; without a panel surface, icon geometry identifies an edge
and the landing uses that edge's centre. With neither available, the destination
is the output's bottom centre. Native fallback shares the destination and 480ms
manual timing. The previous slice faults and trailing outline echoes were removed
from minimise. Maximise/unmaximise use 336ms cubic ease-in/out at factor 0.8 and
retarget from the current visible rectangle when interrupted. Jarvis command
timing remains 1200ms for docking and 1250ms for geometry.

Validation: `verify-windows.cjs` passed centre targeting, fallback, reversal,
retargeting, reduced motion and lifecycle checks. `verify-render.py` compiled on
the NVIDIA GPU and passed pixel endpoints and transparent completion. Native
Wayland probes confirmed manual/Jarvis minimise and restore, maximise/unmaximise,
rapid reversal and settled animation cleanup. Records are in
`validation/minimize-live.json` and `validation/center-maximize-live.json`.
The installed effect was backed up at the path in
`validation/center-collapse-backup.txt`; restore its `jarvis-energy` directory to
`~/.local/share/kwin/effects/jarvis-energy` and reload that effect for scoped rollback.


Recovery verification: `node desktop/cinematic/verify-window-motion-only.cjs`
checks the restricted lifecycle, panel-centre targets, reversal, zero/reduced
motion and cleanup. The restricted mode is declared in the KWin configuration
schema and defaults to true, so a missing configuration key cannot enable drag.
The installer persists this setting. Native Wayland checks cover minimise,
restore, maximise/unmaximise, rapid toggles, and absence of opening animations.
The separate full-particle drag harness is undergoing changes and is not a
validation claim for this restricted mode. Snapshot location:
`validation/window-motion-only-backup.txt`.

## Cinematic transitions (opt-in, 2026-10-15)

`Effect-jarvis-energy/CinematicTransitions=true` (schema default false; only a
real boolean enables it) adds shader modes 6-10 and a drag-release reassembly.
All share one shard engine: the window is cut into square shards that leave or
return along analytic paths behind a scan front; a pixel finds its shard by
inverting the path and testing 3x3 neighbours (one texture read, no buffers).
Shards at home tile the window exactly, so every endpoint is the source.

| Mode | Event | Look | Base ms |
| --- | --- | --- | --- |
| 6 | Open | Corner locks, blueprint grid; dust rises into place under a lock-in scan line; rows hold a flickering hologram, then resolve exactly | 760 |
| 7 | Minimize / restore | Scan line peels the window from the taskbar side; shards spiral into the panel centre with a lens flare. Restore is the same track reversed (mid-flight redirect) | 680 |
| 8 | Close | Window turns hologram, a broken derez line descends, glass lifts away as fading dust | 640 |
| 9 | Focus | Dimensional slide: bands kick sideways with RGB split and depth push; twin perimeter tracers lock the corners | 460 |
| 10 | Maximize / unmaximize | Diagonal energy surge (reversed for unmaximize), charged wake, crackling edges, corner lock | 520 |
| 4 | Drag release | Held drag v2 is byte-identical; release materialises the window with mode 6's scan/dust | 900 (unchanged) |

Jarvis command timings are unchanged. Every track is a finite batch. Only
close uses keepAlive=true, and completion never cancels or appends (drag
renewal excepted). Reduced/zero motion suppresses all of it. Desktop switching
still stops tracks: KWin's slide effect owns that transition.
Checks: `verify-windows.cjs` (lifecycle plus a 3000-step stress), `verify-window-motion-only.cjs`
and `verify-render.py` (exact endpoints, seam, GPU cost). The previews are
synthetic: `validation/cinematic-transitions-{storyboard.png,preview.gif}`
and `validation/cinematic-minimize-taskbar.png`.

## Drag particles (out of process)

`ParticleDragEnabled` in the `jarvis-energy` KWin effect stays **false**: the effect's
JS `animate()`/window-wrapper API crashed `kwin_wayland` twice (Oct 14 in
`AnimationEffect::postPaintScreen`, Oct 15 in Qt's `Object::insertMember`), which also
killed `plasmashell` (no taskbar/wallpaper). Drag particles now live in `dragfx/`:
`drag-kwin.js` is a read-only KWin script (sends only the dragged window's rect and the
cursor over D-Bus, like the Brave bridge) and `dragfx.py` is a PyQt6 helper that draws
input-transparent overlays on every screen with particles kept in global desktop
coordinates, so they flow across monitor seams. A fault there only ends the helper.
Run: `python3 dragfx/dragfx.py` (autostart entry `~/.config/autostart/jarvis-dragfx.desktop`);
stop: `qdbus org.jarvis.DragFx /Drag org.jarvis.DragFx.Quit`. The agent-built resize trace and
Brave fullscreen modes in `jarvis-energy` are not installed (see `verify-resize-fullscreen.cjs`).
