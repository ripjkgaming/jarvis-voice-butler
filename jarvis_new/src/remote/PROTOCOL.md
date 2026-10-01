# Jarvis Remote — wire contract (v1)

Phone ⇄ laptop remote sessions. ONE transport: a private LiveKit room on
the self-hosted server (reached over Tailscale). The bridge only brokers.

## Actors
- **bridge** (`src/bridge.py`, stdlib): auth, session lifecycle, tokens, audit.
- **host** (`src/remote/host.py`, venv, livekit rtc): joins the room as
  identity `laptop-remote`; publishes the screen track; serves data topics.
- **screencast helper** (`src/remote/screencast.py`, SYSTEM `/usr/bin/python3`
  with PyGObject/GStreamer): XDG portal ScreenCast → PipeWire → raw I420
  frames on stdout. Spawned and owned by host.
- **phone** (JarvisLink `RemoteActivity`): joins as identity `phone-remote`.

## Bridge HTTP (bearer auth as today; guest => 403)
- `POST /remote/start {"tier": "view"|"agent"|"shell"|"control", "origin": "phone"|"laptop"}`
  → `{"ok":true,"session":"<id>","room":"remote-<id>","url":"<livekit ws url>","token":"<phone JWT, 4h>","tier":"…"}`.
  Idempotent: an active session is returned (tier upgraded if higher).
  Spawns host (`.venv/bin/python src/remote/host.py --room … --token <laptop JWT> --tier …`)
  detached in its own process group; pid kept in memory + `~/.jarvis/remote/session.json`.
- `POST /remote/stop` → kills host process group, clears state. `{"ok":true}`.
- `GET /remote/status` → `{"active":bool,"session","tier","origin","started_ts","idle_s","room"}`.
- `GET /remote/pending` → `{"pending": null | {start payload}}` — set when the
  LAPTOP voice agent starts a session (origin "laptop"); the phone's LinkService
  polls it every 5s and auto-opens RemoteActivity; consumed on first read.
- `/sys` gains `"remote": {"active","tier","origin"} | null` (HUD banner).
- Every start/stop/tier change → `log_action("remote", …)` (actions.log).
- Limits: 4h hard cap; host exits after 15 min with no phone participant.

## Data topics (LiveKit reliable data, UTF-8 JSON unless noted)
Every message: `{"t": "<type>", ...}`. Host rejects types above the session tier.

### topic `input` (tier control) — phone → host
- `{"t":"move","x":0-1,"y":0-1}` normalized to the captured screen.
- `{"t":"click","x","y","button":"left|right|middle","count":1|2}`
- `{"t":"down"|"up","x","y","button"}` (drag)
- `{"t":"scroll","dx":int,"dy":int}` (notches)
- `{"t":"text","text":"…"}` (≤500 chars) · `{"t":"key","key":"Return|Escape|Tab|BackSpace|Delete|Up|Down|Left|Right|Home|End|Page_Up|Page_Down|F1..F12","mods":["ctrl","alt","shift","super"]}`
Host injects via the existing uinput device (system/desktop.py UInputMouse +
keyboard); mods allowed here (unlike the voice tool) because the tier is
fingerprint-gated.

### topic `pty` (tier shell)
- phone→host `{"t":"open","id":"<tab>","cols","rows","cwd"?}` · `{"t":"in","id","data":"<base64>"}` · `{"t":"resize","id","cols","rows"}` · `{"t":"close","id"}`
- host→phone `{"t":"out","id","data":"<base64>"}` · `{"t":"exit","id","code"}`
Shells: user's login shell via `pty.fork`, persist across phone reconnects
while the host lives (re-`open` of an existing id re-attaches + replays the
last 64 KB scrollback).

### topic `agent` (tier agent)
- phone→host `{"t":"start","id","tool":"claude"|"opencode","repo":"<path>","prompt":"…","resume"?:"<session id>"}`
  · `{"t":"say","id","prompt":"…"}` · `{"t":"approve"|"deny","id","req":"<request id>"}` · `{"t":"stop","id"}`
- host→phone `{"t":"event","id","ev":{…}}` — raw stream-json events relayed
  1:1 (claude: `claude -p --output-format stream-json --input-format stream-json --verbose`;
  opencode: `opencode run --format json -m opencode/muse-spark-1.3-contributor-free --variant high`),
  `{"t":"done","id","session":"<resume id>"}`, `{"t":"error","id","msg"}`.

### topic `fs` (tier view) — request/response, `rid` echoes
- `{"t":"ls","rid","path"}` → `{"t":"ls","rid","entries":[{"name","dir":bool,"size","mtime"}]}`
- `{"t":"read","rid","path"}` → `{"t":"read","rid","text"| "b64","truncated":bool}` (≤1 MB)
- `{"t":"repos","rid"}` → `{"t":"repos","rid","repos":[{"path","branch","dirty":bool}]}`
- `{"t":"git","rid","repo","op":"status"|"log"|"diff","file"?}` → `{"t":"git","rid","text"}`
Roots allow-list: `/mnt/data` and `$HOME`; resolve symlinks, reject escapes.

### topic `ctl` — both ways
- host→phone `{"t":"hello","tier","screen":{"w","h"},"shell":"/bin/bash"}` on join.
- phone→host `{"t":"tier","tier"}` (upgrade; bridge-issued only — host re-validates
  with bridge `/remote/status`). `{"t":"bye"}` ends the session.

## Screen track
Host publishes one video track `screen` (VideoSource from helper frames),
target 1280 px wide, 15 fps idle / 30 fps while input active, H.264/VP8 by
LiveKit default. Portal persist_mode=2; restore token stored at
`~/.jarvis/remote/portal_token` so only the FIRST session ever prompts.

## Voice
- Phone: "start a remote session" (also "remote session", "remote into my
  laptop") → bridge `/route` voice tool `remote_start` → reply action
  `{"tool":"remote_start","session":{…start payload}}` → phone opens RemoteActivity.
- Laptop agent: function tool `start_remote_session(tier="control")` → bridge
  `/remote/start` with origin "laptop" → sets pending → phone auto-opens.
