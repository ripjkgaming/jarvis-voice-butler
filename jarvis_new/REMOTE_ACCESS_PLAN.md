# Remote Access Plan — phone → laptop, on demand (PLAN ONLY, not built)

Goal: from JarvisLink on the phone, start a full coding session on the
laptop (terminal, Claude Code / opencode, files, screen, git) **without
touching the laptop** and **without any external service**. Today Claude
Code remote control needs `claude rc` typed on the laptop; this removes that.

## What already exists (reuse, don't rebuild)
| Piece | Status | Role in this plan |
|---|---|---|
| Tailscale tailnet (laptop `ripjk` + phone) | running | Private encrypted network; works on any Wi-Fi / mobile data. No port forwarding. |
| Jarvis bridge (`src/bridge.py`, bearer-token auth) | running, autostarted by the Jarvis service | The single always-on entry point. Gains the session broker. |
| Self-hosted LiveKit server (`~/.jarvis/livekit.yaml`) | running | Screen video + data channels for the "see the desktop" mode. |
| Desktop tools (screenshot / OCR / click / type, confirm-gated) | built, sandbox-tested 7/7 | Fallback remote control when video is overkill. |
| Biometric unlock in JarvisLink | built | Gate for starting any remote session. |

Nothing new has to be installed on the phone; on the laptop only `tmux`
(one package) is added.

## Architecture
```
 JarvisLink (phone)                         Laptop (always on via Jarvis service)
 ┌───────────────────────┐   tailnet only   ┌──────────────────────────────────────┐
 │ CODE tab              │  (WireGuard,     │ bridge.py  ── /remote/* (new)        │
 │  • terminal (xterm)   │◄──────────────── │   session broker                     │
 │  • Claude chat view   │   WebSocket +    │    ├─ tmux server  "jarvis-remote"   │
 │  • file/diff viewer   │   HTTP, bearer   │    │    ├─ win: shell  (bash, cwd)   │
 │  • screen (LiveKit)   │   + session key  │    │    ├─ win: claude (Claude Code) │
 │ biometric to start    │                  │    │    └─ win: opencode            │
 └───────────────────────┘                  │    ├─ PTY ⇄ WebSocket pump           │
                                            │    ├─ claude -p stream-json runner   │
                                            │    └─ LiveKit screen publisher       │
                                            │ HUD: "REMOTE SESSION ACTIVE" banner  │
                                            └──────────────────────────────────────┘
```

### 1. Session broker (bridge, new `/remote/*` routes)
- `POST /remote/session {mode, cwd}` → creates or re-attaches the tmux
  session `jarvis-remote` (survives phone disconnects; reattach anywhere).
  Returns a one-time **session key** (random, 15-min idle TTL).
- `GET  /remote/pty?key=…` → WebSocket. Bridge opens a PTY running
  `tmux attach -t jarvis-remote`, pumps bytes both ways, handles resize
  messages. This is the "real terminal" mode: vim, git, anything.
- `POST /remote/end` → detaches; `?kill=1` also kills the tmux session.
- `GET  /remote/status` → active sessions, idle timers, what's running.
- Stdlib-only like the rest of the bridge (`pty`, `os`, `select`); the
  WebSocket framing is ~150 lines (or `websockets` in the agent venv).

### 2. Coding agents without typing a laptop command
- **Claude Code, chat mode**: bridge runs
  `claude -p --output-format stream-json --input-format stream-json`
  in the chosen repo and relays JSON events to the phone → a native chat
  view (messages, tool calls, diffs, permission prompts as Approve/Deny
  buttons — reusing the existing approvals UI). Sessions resume with
  `--resume <id>`, so a conversation started on the phone continues later.
- **Claude Code, terminal mode**: the `claude` window inside tmux, driven
  through the PTY view — identical to sitting at the laptop.
- **opencode**: same two modes (`opencode run --format json` for chat,
  interactive TUI in its tmux window), using the Muse Spark 1.3 model rule.
- Repos picker: bridge lists git repos under allow-listed roots
  (`/mnt/data`, `~/`) with branch + dirty state.

### 3. Files, diffs, git (read-mostly, fast on mobile)
- `GET /remote/fs?path=` list/read (text ≤ 1 MB, images as thumbnails),
  confined to allow-listed roots (same path rules as system tools).
- `GET /remote/git?repo=` status, log, and per-file diff for review on the phone.
- Writes go through the terminal or the agent, never a raw upload endpoint
  (keeps the attack surface small).

### 4. Screen on demand (see and drive the desktop)
- `POST /remote/screen` → the bridge starts a LiveKit publisher that
  captures the desktop (PipeWire / xdg portal screencast on KDE Wayland)
  and joins a private room. The phone joins with a `/token` grant for
  that room (subscribe only).
- Control input from the phone goes over the LiveKit data channel to the
  existing desktop tools (click / type / key / scroll), which keep their
  confirm gate. Low-bandwidth fallback: 1 fps screenshots + OCR taps.
- Wayland note: KDE shows a one-time "allow screen share" portal prompt.
  Grant it once with "remember" so later sessions start unattended.

## Security model (on-demand, but never silent)
1. **Network**: bind the remote routes to the tailnet IP only, never
   0.0.0.0. Tailscale ACL: only the phone's node may reach port 4317.
2. **Two keys**: the existing bridge bearer token, plus a per-session key
   issued only after **biometric confirmation on the phone** (existing
   BiometricPrompt, which also covers the approvals flow).
3. **Visible**: while any remote session is live, the laptop HUD shows an
   amber "REMOTE SESSION ACTIVE · <mode>" banner, the tray icon changes,
   and a KDE notification offers "End session" (a local kill switch).
4. **Tiers** (the phone asks for one and the bridge enforces it):
   `view` (fs / git / screen read) → `agent` (Claude / opencode chat) →
   `shell` (full PTY) → `control` (desktop input). Higher tiers require
   a fresh biometric check.
5. **Timeouts + audit**: 15-min idle / 4-h hard cap. Every session
   start, end, command and tier change is written to `~/.jarvis/actions.log`
   (visible in the HUD activity feed). PTY bytes themselves are not logged
   (passwords).
6. **Guest mode**: remote access is always refused for guests.

## Phone UX (JarvisLink → new CODE tab)
- Home tile "REMOTE" → biometric → pick: Terminal · Claude · opencode · Screen · Files.
- Terminal: xterm-style view (e.g. Termux terminal-view library), with an
  extra key row (Esc, Tab, Ctrl, arrows, `|`, `~`) and landscape mode.
- Claude chat: streaming bubbles, collapsible tool calls, inline diffs,
  Approve/Deny for permission prompts, a "continue in terminal" hand-off.
- Voice: "Jarvis, open a Claude session on voice-butler" → the same flow
  (the intent router maps it to `/remote/session`).
- Reconnect: the tmux session keeps running when the phone sleeps or the
  network changes; the app reattaches automatically.

## Build phases (when approved)
| # | Scope | Size | Owner |
|---|---|---|---|
| 1 | Broker + tmux + PTY WebSocket + tailnet bind + session keys + HUD banner | M | Claude (security-critical) |
| 2 | Phone terminal view + biometric gate + reconnect | M | opencode plumbing, Claude does the UI |
| 3 | Claude Code / opencode stream-json chat relay + approvals | M | opencode relay, Claude reviews |
| 4 | Files / git read APIs + phone viewers | S | opencode, Claude does the UI |
| 5 | LiveKit screen share + input over data channel | L | Claude (portal / Wayland specifics) |
| 6 | Voice intents, audit polish, sandbox tests (reuse the Xephyr harness) | S | opencode + Claude verification |

## Open decisions for you
1. Allow-listed roots for files and repos: just `/mnt/data`, or also `~`?
2. Should `shell`/`control` tiers need a fresh fingerprint every time, or once per session?
3. Screen share: accept the one-time KDE portal grant, or keep screenshot-only control?
