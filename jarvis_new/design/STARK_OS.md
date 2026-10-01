# STARK OS — one look for the HUD and the desktop

Jarvis's HUD (`frontend/`, built into `shell/ui/`) and the KDE Plasma
desktop around it (`desktop/`) share this one visual language, so the whole
machine reads as a single Iron Man workshop console. Change a token here
first, then in both places.

## Character

Holographic instrument glass over a dark blueprint. Thin cyan wireframes,
precise mono read-outs, concentric arc instruments, corner brackets and
chamfered cuts. Calm by default; colour is information, not decoration.
Amber means attention, red means danger, nothing else is warm.

## Colour

| Token            | Value                     | Use |
|------------------|---------------------------|-----|
| `void`           | `#010509`                 | deepest background, vignette edge |
| `ink`            | `#03101a`                 | base surface (desktop, HUD canvas, views) |
| `deep`           | `#061826`                 | panels, title bars, menus |
| `raise`          | `#0b2436`                 | hover / raised surfaces, buttons |
| `blueprint`      | `#0a2a44`                 | radial glow at the centre of large surfaces |
| `line`           | `rgba(95,227,255,0.18)`   | hairline borders, grid lines (`#16414f` solid on `deep`) |
| `line-strong`    | `rgba(95,227,255,0.42)`   | focused borders, section rules (`#2b7f95` solid) |
| `cyan`           | `#5fe3ff`                 | primary accent, wireframes, focus, brackets |
| `cyan-hot`       | `#d8f8ff`                 | key numbers, active titles, bloom core |
| `cyan-dim`       | `#2a8fb0`                 | inactive accents, disabled wireframes |
| `select`         | `#12546a`                 | solid selection fill (text on it: `cyan-hot`) |
| `text`           | `#d9f4ff`                 | primary text |
| `text-dim`       | `#7fa6b8`                 | secondary text, labels |
| `text-faint`     | `#46677a`                 | disabled, tertiary |
| `amber`          | `#ffb347`                 | warnings, attention, "speaking" |
| `red`            | `#ff4d5e`                 | errors, destructive (close button hover) |
| `green`          | `#6dffb3`                 | success, healthy |

Voice states (shared with the school taskbar, `use-jarvis-state.ts`):
idle `#22d3ee`, listening `#a855f7`, thinking `#22c55e`, speaking `#fb923c`,
muted `#6b7280`.

## Type

Fonts live in `design/fonts/` (all SIL OFL) and install to
`~/.local/share/fonts/stark-os/`.

- **Orbitron** 600–700 — the J.A.R.V.I.S. wordmark and splash only.
- **Rajdhani** SemiBold/Medium — every UI label and title. Labels are
  UPPERCASE with 0.14–0.22em tracking; body text stays sentence case.
- **Share Tech Mono** — numbers, clocks, logs, terminals. Tabular.

HUD scale (px): 10 micro · 11 label · 13 body · 15 emphasis · 20 value ·
28 big value · 44 clock. Desktop: general *Rajdhani Medium 11pt*, fixed
*Share Tech Mono 10pt*, window titles *Rajdhani SemiBold 11pt*.

## Shape

- **Hairline**: 1px `line` borders. Never thicker except the 2px active
  indicator (taskbar, tabs, selected list rows).
- **Chamfer**: panels cut 10px at top-left and bottom-right (HUD
  `clip-path`, desktop SVG paths). Small controls cut 4px.
- **Brackets**: 12px L-shaped corner brackets in `cyan` at 85% on framed
  instruments, dialogs and focused frames.
- **Section header**: `◢ LABEL` in Rajdhani, a hairline running to the
  right edge, a two-digit index in Share Tech Mono at the end.
- **Glow**: only on `cyan-hot` key values and active wireframes
  (`0 0 8px rgba(95,227,255,.45)`). No blur on body text.
- **Background**: `ink` with a `blueprint` radial glow, a 40px grid at 4%
  cyan, vignette to `void` at the edges.

## Motion

Rotations 20–90 s per turn; state changes 240 ms ease-out; entrances rise
8px and fade, staggered 40 ms. No bounce, no flashing. Everything stops
under `prefers-reduced-motion` and while the HUD is hidden.

## Desktop icons

Icons use a brighter light-blue hologram treatment so their silhouettes remain
legible against the dark desktop. The panel/HUD palette above stays unchanged.
Detailed scalable artwork retains reactor rings, etched circuits, bevels and
distinct application/folder/device shapes. Dedicated 16px/22px drawings simplify
the detail without reducing the silhouette to faint outlines.

- Ice-blue outline `#9aeaff`; highlight `#edfcff`; etched detail `#6fccea`.
- Blue glass body `#317da2`–`#52aacb`, with translucent `#b4eeff` highlights.
- Dark inset `#183e56` and a thin `ink` keyline separate details and preserve
  edges on light surfaces.
- Amber/red/green retain the semantic meanings defined above.
- Transparent SVGs, native vector gradients and static geometry; no blur
  filters, flashing or animated icons.

## Voice-only rule

HUD surfaces never depend on the mouse. Controls render as status
displays with the spoken command beside them ("SAY 'JARVIS, MUTE'").
Only the school-mode taskbar is clickable.
