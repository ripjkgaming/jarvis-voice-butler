# HUD Layout Wiring Plan v3 — locked build

Locked in: new /api/sys sampler · LiveKit data-channel task events · typed + voice command field.

## Region 0 — Top Telemetry & System Header
- `frontend/app/api/sys/route.ts` — procfs (/proc/stat, /proc/meminfo, /proc/net/dev) →
  {cpu_pct, mem_used, mem_total, rx_kbps, tx_kbps}. Same-origin 1s poll, $0, no LiveKit, no LLM.
- Why new, not usage_stats: voice tool shells uptime/free/ps per call (heavy, spoken).
  Gauges need silent sub-second numbers; tool untouched.
- `components/hud/sys-gauges.tsx` — 3 compact gauges, pauses when tab hidden, hidden in solo.
- `components/hud/system-status.tsx` — [IDLE] JARVIS (Cyan) | ACTIVE + sub-state
  (Purple/Green/Orange) from useJarvisState(); profile Sir; display-mode toggle. Local only.

## Region 1 — Left: Core Interaction & Speech Engine
- `components/hud/particle-orb.tsx` — canvas 2D (KDE GPU-safe), 220 particles, state tint
  Orange > Green > Purple > Cyan, ~15% scale-up on Green/Orange.
- `components/hud/command-field.tsx` — NumpadEnter focuses HUD window + field; sends via
  session.sendText (supportsChatInput path); voice path unchanged. Ghost hint = last
  intent_echo ("hot rod red → crimson") from data channel.
- `components/hud/live-caption.tsx` — latest transcript single-line + EQ mini-strip.
- `components/hud/command-log.tsx` + `frontend/app/api/actions-log/route.ts` —
  tails ~/.jarvis/actions.log, last 50 lines, 2s poll. Solo: fullscreen.

## Region 2 — Right: Workflow & Automation Engine
- `src/hud_events.py` + agent.py `function_tools_executed` hook — publishes
  tool_start/tool_finish JSON on topic `jarvis-tasks`, mirrors to actions.log.
- `components/hud/node-graph.tsx` — 2D canvas, nodes from events, 10s idle fade,
  falls back to actions.log mining when silent.
- `components/hud/exec-feed.tsx` — max 5 live tasks with elapsed timers; done → log.

## Region 3 — Cross-Display & Adaptive
- `components/hud/radar-sweep.tsx` — conic sweep while read_screen_text/take_os_screenshot
  run; stealth (raw captures) = monochrome, no toast, log only. Cosmetic.
- `components/hud/edge-pulse.tsx` — border glow on tool_finish(success); dual = until acked
  (click); solo = subtle 4s auto-dismiss.
- `hooks/hud/use-display-mode.ts` — dual/solo persisted in localStorage; auto-collapses
  under 900px; solo = orb + caption + fullscreen log only. Second screen = manual drag.
- KDE Numpad Enter: System Settings → Shortcuts → Custom → focus HUD window
  (wmctrl -a HUD / xdotool), then in-HUD NumpadEnter handler focuses the field.

## Stages
- [x] Stage 0 — Tracker (this file) + HudShell 3-region grid + use-display-mode + persist
  - goal: shell + mode hook; files: hud-shell.tsx, use-display-mode.ts; tests: manual dual/solo toggle persists; demo: resize <900 collapses; cost: $0; status: done
- [x] Stage 1 — /api/sys sampler + sys-gauges + status readout
  - goal: silent gauges; files: api/sys/route.ts, sys-gauges.tsx, system-status.tsx, use-jarvis-state.ts; tests: curl /api/sys shape; demo: gauges tick 1s, pause on hidden; cost: $0; status: done
- [x] Stage 2 — orb tint/scale, command field, caption, terminal log + /api/actions-log
  - goal: left region; files: particle-orb.tsx, command-field.tsx, live-caption.tsx, command-log.tsx, api/actions-log/route.ts; tests: send text path, log tail; demo: NumpadEnter focuses, ghost hint shows; cost: $0; status: done
- [x] Stage 3 — data-channel publisher, node graph, exec feed
  - goal: right region live; files: src/hud_events.py, agent.py hook, use-hud-events.ts, node-graph.tsx, exec-feed.tsx, tests/test_hud_events.py; tests: pytest test_hud_events; demo: tool run lights node + feed row; cost: $0; status: done
- [x] Stage 4 — radar sweep + stealth, edge pulse, fallback collapse, KDE doc
  - goal: cross-display; files: radar-sweep.tsx, edge-pulse.tsx, HudShell solo branch, this doc section; tests: trigger screenshot tool → sweep; stealth mono; solo auto-dismiss 4s; demo: sweep + edge glow; cost: $0; status: done
- [x] Stage 5 — E2E + audit: climb→Orange→alert-pulse→log rehearsal, clean-HUD check, pnpm lint + pytest green
  - goal: green gates; files: none new; tests: uv run pytest, pnpm lint/tsc; demo: rehearsal; cost: $0 (spoken alerts pre-existing); status: done
  - result: pytest 220 passed; tsc clean; eslint clean (2 pre-existing warnings in aura/radial visualizers, untouched); ruff clean

Assumptions locked: gauges hidden in solo (orb-only wins); exec feed max 5; node graph canvas 2D.
Zero inference cost added anywhere except already-existing spoken alerts.
