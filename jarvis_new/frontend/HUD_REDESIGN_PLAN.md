# JARVIS Voice HUD — Redesign Plan

## Product intent

Make the desktop overlay feel like a calm, capable Stark Industries command surface while keeping voice as the only way to issue commands. The screen should make three things obvious at a glance: whether JARVIS is available, whether a conversation is active, and what the system is doing.

## Visual system

- **Canvas:** near-black graphite with a subtle blueprint grid and restrained radial light; keep the existing ambient SVG as atmosphere, not the focal point.
- **Materials:** translucent instrument panels, hairline cyan borders, modest corner radii, and a small set of typography sizes. Avoid heavy blur and large shadow stacks.
- **State color:** cyan for standby, violet for listening, green for thinking, amber for speaking, and neutral gray for muted microphone. State color accents the orb, microphone, and a few indicators rather than tinting every panel.
- **Hierarchy:** identity/status masthead → voice interaction hero → live caption → supporting workflow and system panels.

## Layout

### Masthead

- JARVIS mark, product descriptor, and a compact state label.
- Existing load/RAM/home telemetry at desktop widths.
- Existing dual/solo display toggle and hide action.
- Drag behavior remains on the non-interactive header surface; controls stop drag propagation.

### Voice console

- Large state-reactive particle orb as the main visual anchor.
- One prominent `Talk to JARVIS` action, with a visible hotword/keyboard hint.
- Separate, clearly labeled microphone mute control; no typed command field or text-submit affordance.
- Live caption directly below the voice control, with speaker identity and a low-key waveform strip.
- Actions history presented as a compact activity log.

### Intelligence console

- Workflow graph for recent tool activity.
- Active execution feed with a purposeful quiet state and elapsed times.
- System core telemetry panel, retaining existing bridge values and eye animation.
- Panels collapse into a single-column composition at narrow sizes; solo mode remains centered on orb, voice action, caption, and activity log.

## Interaction and motion

- The voice action invokes the same shell summon chain as the hotword; `NumpadEnter` remains a keyboard summon shortcut. `Super+J` and tray controls continue to work through the native shell.
- `M` toggles the existing shell microphone mute; `D` toggles persisted dual/solo mode; `Escape` hides the overlay.
- Idle has a slow breathing halo; listening gets a focused ring and waveform; thinking increases orb energy; speaking uses a warm amber response. A successful task finish continues to use the existing edge pulse.
- Keep ambient and panel motion subtle. Use CSS transform/opacity for new effects, retain pre-baked canvas rendering for the particle orb/eye, and keep the graph's throttled 4 Hz repaint.
- Pause periodic polling/animation while the document is hidden. Honor `prefers-reduced-motion` by removing non-essential motion.

## Accessibility and performance

- Semantic buttons with descriptive accessible names and pressed/disabled state; live state and captions use polite announcements.
- Visible keyboard focus and minimum 44 px interactive targets.
- No new animation framework, audio meter, continuous DOM loop, or inference request. Keep existing bridge cadence and rendering strategy.
- Respect responsive display mode and `prefers-reduced-motion`; do not make color the only state cue.

## Build sequence

1. Replace the typed command affordance with a reusable voice summon/mute dock and preserve existing summon/mute integrations.
2. Refine the shell hierarchy and status/caption/activity labels without changing telemetry or task-event data paths.
3. Apply the visual system and responsive/motion rules in the HUD stylesheet.
4. Type-check/lint/build the static frontend, inspect the diff, and correct regressions.
