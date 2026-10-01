# JARVIS HUD Revamp — Iron Man 1 & 2 Movie Look

## What changed (presentation only)

- **New `components/hud/arc-reactor.tsx`** — pure decorative SVG arc-reactor core:
  concentric 1px wireframe bezels, 72-tick index ring, three segmented power
  arcs rotating at different speeds/directions (64s / 22s / 11s-rev), 10-coil
  inner winding, and a state-tinted core (`var(--jarvis-state)`) with white
  hot spot. No hooks, no data, no props contract.
- **`components/hud/particle-orb.tsx`** — renders `<ArcReactor />` behind the
  existing baked particle canvas (now inset to 62% so the swarm lives inside
  the reactor iris). Baked-frame render logic, hooks, and props untouched.
- **`components/hud/hud-shell.tsx`** — added boot-flicker overlay div and a
  rolling telemetry ticker strip (ARC REACTOR OUTPUT / SUIT POWER / UPLINK /
  GRID SYNC / THREAT SCAN) between the state banner and the grid. All hooks,
  keyboard shortcuts, solo/dual branches, and existing copy untouched.
- **`components/hud/stark-dials.tsx`** — thinner movie-style strokes
  (shell 3→1px, arc 5→2px), denser 60-tick index rings, cyan retinted to
  `#5FE3FF`. Same four dials (DATE / CPU / RAM / TIME), same bridge polling.
- **`components/hud/sys-core.tsx`** — `CoreBar` gained a purely presentational
  `data-warn` flag (≥85% → amber striped fill + amber label). Telemetry,
  polling, and copy unchanged.
- **`styles/globals.css`** — appended `IRON MAN MARK I–II MOVIE HUD` section:
  `--im-*` tokens (`#5FE3FF` cyan, `#FFB347` amber), near-black + deep-blue
  vignette backdrop, hex micro-texture + blueprint grid + faint scanlines,
  4-corner `[ ]` brackets on panels, mono uppercase micro-labels with
  tabular numerals, ticker scroll, boot flicker, staggered panel slide/scale
  entrance, voice-state spin-up (speaking > thinking > listening), leader-line
  crosshair behind the reactor, amber warning treatment, and a full
  `prefers-reduced-motion` kill-switch. Idle keeps rings turning slowly
  (compositor transforms only); all JS canvas loops still park as before.
- Background SVG (`jarvis-background`) retinted via CSS only — JSX untouched.

No changes to `hooks/hud/*`, `lib/*`, props contracts, or any Python/Rust.
Every existing readout (status, dials, caption, activity log, workflow,
exec feed, CPU/RAM bars, footer) remains visible. No new dependencies;
SVG + CSS transforms/opacity only.

## Final build result

- `pnpm exec tsc --noEmit` → clean, exit 0.
- `pnpm build` → success (`Compiled successfully`, static export 5/5 pages,
  `/` 188 kB / 299 kB first load). Only pre-existing warnings in
  `agents-ui/*` files. `../shell/ui` refreshed as expected (build script
  `rm -rf ../shell/ui && cp -r out/. ../shell/ui/`).
- Two prettier formatting errors in the new/edited TSX were auto-fixed with
  `prettier --write` before the final build.
- Not committed, per instructions.

Note: the working tree already contained unrelated uncommitted changes
(e.g. deleted `session-pill.tsx`, edits in `hooks/`/`lib/`) before this
revamp started; those were left alone.
