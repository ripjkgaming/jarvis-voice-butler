# Calls: frontend wiring (backend is done; frontend not edited)

## How events reach the HUD

The Tauri webview has no direct access to the agent process. Call events go
`calls.events.emit` -> `$JARVIS_CALLS_DIR/events.jsonl` (default
`~/.jarvis/calls/events.jsonl`) -> bridge HTTP -> HUD polling, the same way
`/captions` works. (`events.set_room` / the `jarvis-tasks` data topic is not
used: the hub has no room object for this path. `useHudEvents` filters on
`kind`, so call events would be ignored there anyway.)

Event shapes are the contract in `CONTRACT.md`; every event is
`{"type":"call","event":"state|transcript|speaking|escalation|summary", ...}`.

## Bridge endpoints (src/bridge.py, owner only; guests get 403)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/calls/state` | Snapshot for a HUD that just opened: `{ok, active, transcript[], escalation, last_summary, next}`. `active` is the latest `state` event of the live call, or `null`. |
| GET | `/calls/events?since=<offset>` | Events after byte offset `since` -> `{ok, events[], next}`. Omit `since` to get only the current cursor (`events: []`). Poll with `since=next` every 500 ms during a call, 2 s when idle. |
| POST | `/calls/command` body `{"command": "takeover"}` | Queue an owner command. Accepted: `takeover hold resume hangup accept decline take_message stop_all` (`take over`, `hang up`, `stop` aliases). Returns `{ok, detail, id}`; the call runtime executes it within ~250 ms. |

Same `Authorization` / token handling as every other bridge route; use the
existing `lib/bridge.ts` `getJson` / post helper.

## What to add (UI worker / parent)

1. `lib/bridge.ts`: three helpers.
   ```ts
   export type CallEvent =
     | { type: 'call'; event: 'state'; call_id: number | string; state: 'ringing' | 'handling' | 'hold' | 'owner' | 'ended'; direction: 'in' | 'out'; caller: { name: string; number: string; trust: string; relationship: string; notes: string } | null; goal: string; started_at: number; mode: 'jarvis' | 'owner_voice'; ai_disclosed: boolean; recording: boolean }
     | { type: 'call'; event: 'transcript'; call_id: number | string; speaker: 'caller' | 'jarvis' | 'owner'; text: string; partial: boolean; ts: number }
     | { type: 'call'; event: 'speaking'; call_id: number | string; level: number }
     | { type: 'call'; event: 'escalation'; call_id: number | string; reason: string }
     | { type: 'call'; event: 'summary'; call_id: number | string; summary: string; outcome: string };
   export const bridgeCallState = () => getJson<{ ok: boolean; active: CallEvent | null; transcript: CallEvent[]; escalation: CallEvent | null; last_summary: CallEvent | null; next: number }>('/calls/state');
   export const bridgeCallEvents = (since?: number) => getJson<{ ok: boolean; events: CallEvent[]; next: number }>(`/calls/events${since === undefined ? '' : `?since=${since}`}`);
   export const bridgeCallCommand = (command: string) => postJson('/calls/command', { command });
   ```
   (use whichever POST helper `bridge.ts` already has).
2. `hooks/hud/use-call-events.ts`: on mount call `bridgeCallState()`, seed the
   reducer, then poll `bridgeCallEvents(next)` (500 ms while `state` is not
   `ended`/null, 2000 ms otherwise). Reduce: `state` replaces the call;
   `transcript` appends when `partial` is false and replaces a trailing partial
   line of the same speaker when true; `speaking.level` drives the visualiser;
   `escalation` sets a pending-approval banner; `summary` shows the post-call
   card. Ignore events whose `call_id` differs from the current call after an
   `ended` state.
3. Mount the existing `frontend/components/calls/*` panel in `hud-shell.tsx`
   when `active !== null` (or briefly for the summary card). Buttons call
   `bridgeCallCommand`: Take over = `takeover`, Hold = `hold`, Resume =
   `resume`, Hang up = `hangup`, ringing: Accept = `accept`, Decline =
   `decline`, Take message = `take_message`.
4. Disclosure/recording badges come straight from `ai_disclosed` / `recording`
   on the `state` event. `mode: "owner_voice"` means the persona is the
   owner's voice.

Phone web app (`phone-web/`): same endpoints through the `/api` prefix; no
extra work needed on the backend.

## Not wired on purpose

- No SSE/WebSocket: polling keeps the bridge dependency-free.
- `events.jsonl` grows without bound; the runtime does not rotate it yet.
