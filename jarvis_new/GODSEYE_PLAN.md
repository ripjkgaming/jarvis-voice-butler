# God's-Eye-View + Speedometer UI Integration Prep

Source: `/home/ripjk/gods-eye-view` (vanilla JS + Cesium, `vite build` → static `dist/`).
Target: Jarvis overlay free space (Tauri shell `shell/ui`) + HUD web frontend.

## Data hooks (ready, no code needed)
- `GET /sys` (bridge :4317, Bearer token) → `{load_1_5_15, cpu_count, mem_bytes{MemTotal,MemAvailable}, home_free_bytes}`. Poll 1s for gauges. NEW: `cpu_count` added for load-per-core normalization.
- `GET /status` → pipeline/in_call/agent state for HUD state tint.
- `GET /captions?limit=N` → transcript strip.
- `POST /summon {text?}` / `POST /chat {voice:true}` → text→voice triggers.
- `wake.sock {"status":true}` → `{muted, threshold, in_call}` for local UI.

## Speedometer gauges (RAM/CPU/load/disk)
- Do NOT shell `usage_stats` per frame (spawns processes, spoken-oriented). Poll `/sys`.
- Gauge math: `mem_pct = 1 - MemAvailable/MemTotal`; `cpu_pressure = load_1min / cpu_count`; `disk_pct` needs total → extend `_sys_stats` with `home_total_bytes` (1 line, same statvfs call) when building.
- Visuals: reuse gods-eye tape-gauge pattern (`src/ui/templates/cockpit.html` `#cockpit-speed-rim` + `cockpitInstruments.js:updateHud()`) — rim + needle + tick ruler. Port the rim/tick CSS+SVG to `shell/ui` or `frontend/components/hud/sys-gauges.tsx` (HUD_PLAN Region 0 already reserves this file).
- New controller modeled on gods-eye `frameRateMonitor.js`: `setInterval(1000)` → fetch `/sys` → update needles; pause when overlay hidden.

## God's-Eye globe in the free space (WIRED 2026-09-20, display-only)
- Built: `vite build -- --base=./` → `shell/ui/globe/` (31MB, relative paths).
- gods-eye pack loader REQUIRES http(s) asset URLs (`director/packs/source.js`
  throws on `tauri://`), so the window does NOT use the Tauri custom protocol:
- `globe-assets.service` (systemd user, enabled): `python3 -m http.server 4001
  --bind 127.0.0.1 --directory .../shell/ui/globe`. Window url =
  `http://127.0.0.1:4001/index.html` (port 4001: 4000 belongs to the shell's ui sidecar). CSP widened for that origin
  (script/style/font/img/media) + pre-existing https allowances for tiles,
  fonts, and data layers.
- Window: label `globe`, 1280×800, normal (not always-on-top), display-only —
  page runs `?ambient=1` (slow auto-spin + `pointer-events:none` chrome kill,
  patched in `src/app/viewer.js:enableAmbientMode`, upstream tests 4144 pass).
- Control surface: `jarvis globe` (toggle, manual) / `jarvis globeshow`
  (show-only) / `jarvis globestate` (visibility report). Voice
  `launch_gods_eye` tool (SystemTools, Assistant-direct) shells `globeshow`,
  so "launch gods eye view" can never hide it; prompt-routed in prompts.py.
- Live layers need the dev proxies (`server/providers/*`): still TODO
  (sidecar or accept keyless fallbacks). No Cesium ion/Google keys baked,
  so ion-dependent layers go dark; Esri/OSM/keyless sources render.

## Build order
1. `home_total_bytes` in `_sys_stats` (1 line).
2. Needle/rim gauge component in `shell/ui` fed by `/sys` poll.
3. `vite build` gods-eye → Tauri second window, tray toggle.
4. Proxy sidecar for live layers (only the layers Sir actually watches).

## JARVIS system UI integration

Integrated into God’s-Eye’s existing ambient command dock (`?ambient=1`): the
globe remains the full-screen system backdrop while the dock owns the single
interactive Talk/Mic surface, current voice caption, recent action, and live
load/RAM/home-space telemetry. The GEV Realtime controller is not started in
this mode; the native JARVIS wake listener remains the sole microphone owner.

The integrated orb uses a deterministic 48-frame sprite sheet per state color,
played at 24 fps by image blits. It pauses while hidden and honors reduced
motion. JARVIS bridge discovery uses the existing Tauri `bridge_info` command;
credentials are memory-only and bridge failures degrade to standby/offline.
The ambient mode’s map credit keep-out is covered in God’s-Eye’s attribution
tests. Static output is built with `vite build --base=./` and installed into
`shell/ui/globe/` for the existing local asset server.
