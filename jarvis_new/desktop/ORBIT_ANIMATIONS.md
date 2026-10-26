# Cinematic window and taskbar animations — revision 1.6

This is the final edge-line design, superseding the intermediate travelling
window halves. Three independent visual lifecycles are active.

## Window close: red perimeter energy

The window compresses into two thin horizontal energy filaments, then the lines
reach the relevant monitor's left/right edges. No travelling window tiles remain.
Each line makes exactly one monitor-perimeter lap, follows the edge toward the
actual panel, bridges to the panel edge, makes exactly one panel-perimeter lap,
returns to the screen boundary and exits below the screen. The complete sequence
is 3000 ms before KDE scaling (2400 ms at factor 0.8).

Normalized phases: 0–.14 dematerialize/reach sides; .14–.52 one screen lap;
.52–.65 edge-only handoff; .65–.68 short panel connector; .68–.88 one panel lap;
.88–.90 connector back; .90–.97 boundary route to bottom; .97–1 below-screen exit.
A partial edge transit used to reach/leave the panel is not another full lap.
Non-bottom/floating panels use their nearest screen-edge projection and a short
axis-aligned connector, then return to the boundary for the below-screen exit.
Missing/fully hidden panel geometry uses the visible bottom edge fallback.

Each filament has one continuous narrow red core, a bounded eight-pixel analytic
glow and a leading directional arrowhead. Trail length is at most 240 logical
pixels. No particles, growing history, blur passes or whole-screen looping
searches. Work is two analytic line paths; texture sampling ends after the first
7% dematerialization stage. KWin retains only the deleted window's visual; no
surviving application is physically moved, closed or intercepted by the effect.

## Window open: energy-line gradient reconstruction

Original pixels progressively reveal from top to bottom behind a narrow bright
front, with smooth easing, finely spaced low-opacity lines, a 64-pixel fading
energy gradient and brief final corner-lock accents. No shutters or mechanical
plates. Duration is 560 ms before scaling (448 ms currently); input/focus is never
held. Original colors return cleanly at completion.

Content cannot appear before the client supplies it. Frozen test captures showed
the scan/gradient while some first-buffer client contents were blank. Source-filled
synthetic renders verify progressive reconstruction; identical early content on
every application is not claimed.

## Taskbar app opening: subtle code hand and strings

A separate first-running-task hook waits for published `iconGeometry` inside an
actual panel. A faint finite-glyph hand descends from the screen top, two thin
curved gradient strings become taut, and a temporary themed icon copy rises into
that slot and settles. Strings detach and retract, the hand retreats, and loose
strings swing to the nearest side edge and disappear. Existing task icons and
pinned launchers are never moved, hidden or removed. A second window belonging
to the same running app does not replay this sequence.

This refinement replaces the initial rigid-rail strings: gradients reduce the
middle-line intensity, tension changes the curves, icon lift has a small eased
settle, the hand is a faint code silhouette and release/recoil is continuous.
Duration 2300 ms before scaling (1840 ms currently). The copy fades into the
persistent real icon; the actual task remains visible throughout.

One 210-pixel-wide vertical surface is used during pull, then one 180-pixel-high
top strip during retreat. Both are transparent to input and cannot accept focus.
There are only two vector strings, finite glyphs, and a 30 fps timer while active.
Reduced motion produces a short static slot outline. Missing geometry/theme icon
skips safely; pending lookup expires after 2.2 seconds on the next panel event.
Taskbar move, screen/activity/desktop changes, app removal and lock cancel it.
Geometry is event-driven (panel damage plus available icon-change/activation
signals), not an invented generic window-opening hook.

Owned XWayland and native Wayland tests both observed this actual app-open hook
and the correct published task slots (coordinates are recorded per test). Universal native-app coverage is not
claimed: applications and panel configurations differ in geometry publication.
Other panel orientations use a bounded rise from below the slot, rather than
pretending every panel is at the bottom.

## Taskbar app close: cyan precision shot

The existing final-window app-identity/grouping and rotating per-icon scan remain.
After lock-on, a short cyan-white beam hits the copied icon, a localized thin shock
ring expands and four cyan code fragments fall. The previous red world-slash and
solid impact flash are replaced. No sound, large explosion or full-screen flash.
The cyan palette distinguishes this from the red window-edge circuits. The
original taskbar close preset is preserved exactly in the rollback snapshot.

## Preserved behavior and limits

Straight perpendicular minimize/reverse restore, maximize scan and the existing
`jarvis-compact-v3` 180 ms Alt-Tab are unchanged. Existing watch/security exclusions,
reduced motion, global zero duration, close opt-outs, cancellation, reversal and
HDR conversion remain. Two window shader slots and four million logical pixels /
4096 per source/canvas dimension bound work; oversized surfaces (including 4K
close), unavailable shaders and overflow use the existing short native fallback.

No desktop/session, audio, browser, Resolve, Jarvis speech or trading service was
restarted. Only the owned animation overlays/bridges were reloaded. No lock/unlock;
only disposable test windows were controlled. Temporary KWin main-thread stalls
were logged during checks. No sustained Resolve/GPU stress test was run.

## Evidence and checks

Under `kwin/effects/jarvis-energy/validation/`:

- `edge-live-contact-sheet.png`: actual frozen KWin captures of line formation,
  monitor edges, panel pass and exit; temporary exact-title freeze hooks removed.
- `edge-lifecycle-x11.json`, `edge-lifecycle-wayland.json`: real transition tracks,
  deleted-window retention, reverse minimize and settled cleanup.
- `puppet-live-x11.json`, `puppet-live-wayland.json`: real first-task trigger, actual
  slot and cleanup; `puppet-live-detail.png` is a live cropped capture.
- `precision-shot-live.json`: actual trigger/cleanup; `precision-shot-live.png`:
  actual compositor capture with the owned test impact temporarily held at 56.5%.
- `edge-final-state.json`: final source/runtime hashes, idle state and limitations.
- `orbit-synthetic.png`, `puppet-synthetic.png`, `taskbar-choreography.png`:
  synthetic renders, explicitly not desktop recordings.

Live captures are still frames, not motion-quality recordings. Visual window
proof covers the 1920×1080 monitor at (0,0), with actual panel (0,1024,1920,56).
Negative origins and other panel placements are harness checks. GPU-rendered
pixel-mask checks reject any line pixels away from the required monitor/panel
edges during their lap phases and require a transparent final frame.

```sh
node /mnt/data/jarvis-voice-butler/jarvis_new/desktop/verify-energy.cjs
node /mnt/data/jarvis-voice-butler/jarvis_new/desktop/verify-taskbar-puppet.cjs
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/verify-bridge-loader.py
uv run --python 3.11 --no-project --with moderngl --with pillow python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/verify-energy-render.py
QT_QPA_PLATFORM=offscreen python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/verify-puppet-render.py
QT_QPA_PLATFORM=offscreen python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/verify-taskbar-render.py
```

## Exact rollback

Restore the preceding shutter/bin window preset and red taskbar close, remove the
new puppet layer, and preserve the latest straight minimize / Alt-Tab:

```sh
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/cinematic-effects.py apply /home/ripjk/.local/share/jarvis-desktop-backups/cinematic-before-20261004
```

The full rollback was exercised, prior window/taskbar source bytes compared,
and the final preset reapplied successfully. Reapply the final revision:

```sh
python /mnt/data/jarvis-voice-butler/jarvis_new/desktop/cinematic-effects.py apply /home/ripjk/.local/share/jarvis-desktop-backups/cinematic-edge-20261004
```

The older Energy preset and original transition state remain untouched:
`energy-20261004-132544-569723` and `energy-20261004-123208-658576` under the same
backup directory. The older `energy-effects.py restore SNAPSHOT` commands restore
their broader recorded desktop-effect state; stop the puppet layer first with
`taskbar-puppet-effects.py stop` when using those historical restores.
