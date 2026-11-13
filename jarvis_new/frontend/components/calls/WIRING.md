# Calls HUD wiring

The Calls HUD is self-contained in `frontend/components/calls/`. It is wired into
the live HUD with one import and one line in `frontend/components/app/app.tsx`
(added 2026-11-13). `lib/bridge.ts` and `hooks/hud/*` are not changed; the bridge
client for calls is `bridge-feed.ts`, which uses the exported `bridgeGet` / `bridgePost`.

## The one line

In `frontend/components/app/app.tsx`, inside `<AgentSessionProvider>` (next to `<HudShell>`).
`useDataTopic` needs that provider:

```tsx
import { CallHudLive } from '@/components/calls/call-hud-live';
// ...
<AgentSessionProvider session={voice} muted>
  <AppSetup />
  <CallHudLive />          {/* ← add */}
  <HudShell>…</HudShell>
```

`CallHudLive` renders `null` until a `{type:"call"}` event arrives. Then it shows a
fixed, full-screen overlay (`z-index: 80`, `pointer-events: none`) over the stark HUD.
The overlay clears itself 45 s after the call ends (`ENDED_LINGER_MS`).

## Event sources it listens to

The HUD consumes the events defined in `docs/calls/CONTRACT.md` → "Call event stream". It
listens on four sources:

- The bridge feed (`bridge-feed.ts`, the path the backend uses today): `GET /calls/state`
  seeds the call, then `GET /calls/events?since=<byte offset>` is polled every 500 ms
  during a call and every 2 s otherwise. Speaking levels from each poll are replayed
  at 70 ms steps. Calls on a clock more than 15 min from ours (the simulator) are
  shifted onto our clock. `<CallHudLive bridge={false} />` turns the poll off.
- The data-channel topics `jarvis-tasks` and `jarvis`, the same topics as
  `use-hud-events.ts`. `events.py` should publish `{"type":"call", ...}` on
  `jarvis-tasks`. `useHudEvents` ignores these payloads because they have no `kind`.
- A `window` `CustomEvent('jarvis-call', { detail: <event json> })`. A shell or bridge relay can
  inject events this way without the voice socket.
- Any other source: `useCallEvents()` returns `{ call, level, onEvent, clear }`. Pass any
  payload to `onEvent` and render `<CallHud call={call} level={level} />` yourself.

The `speaking` level is stored in a ref, not in React state, so 20 Hz level packets do not re-render the
tree. Timestamps can be epoch seconds (`time.time()`) or epoch milliseconds.

## Optional props (not in the contract)

`CallHud` / `CallHudLive` accept `rules`, `nextCall`, `contacts` and `disposition`. The event
stream does not carry them. Without them, the rules strip shows "NO RULES", the contacts panel is
hidden, and the ringing protocol is derived from the caller's trust level. To fill them, add a bridge endpoint
(for example, `GET /calls/overview` that returns `rules.active_rules()`, the next scheduler item and
`contacts.list_contacts()`), poll it, and pass the result:

```tsx
<CallHudLive rules={overview?.rules} nextCall={overview?.next} contacts={overview?.contacts} />
```

## Voice phrases

The HUD sends no commands. It only shows spoken hints. The phrases are in `VOICE` in
`call-hud.tsx` and map to `events.py: handle_command(name)` (`accept`, `decline`,
`take_message`, `takeover`, `hold`, `resume`, `hangup`, `stop_all`). Keep them in sync with
the phrases that `src/calls/voice_commands.py` matches.

## Calendar panel

During a call, the left column shows a CALENDAR panel for slot checks, bookings,
reminder lookups and change requests, and Jarvis's transcript lines get a small
status tag. The stream has no calendar event yet, so `calendar.ts` reads the fixed
sentences from `src/calls/calendar_hook.py` (and booking details from escalation
reasons). A `{type:"call", event:"calendar"}` event, if the backend adds it, replaces
the parsing. See `docs/calls/UI_NEEDS.md`.

## Demo and tests

- `/calls-demo` plays the scripted inbound call on a loop. Use `?state=ringing|dialing|handling|escalation|hold|owner|ended`
  to freeze one state.
- `/calls-demo?source=bridge` shows live calls from the bridge feed. With a plain
  browser, set `NEXT_PUBLIC_BRIDGE_PORT` (and `NEXT_PUBLIC_BRIDGE_TOKEN` for the real
  bridge) for `next dev`, then run
  `uv run python scripts/calls_sim.py --scenario <id> --scripted --hud` with the same
  `JARVIS_CALLS_DIR` as the bridge.
- `node --test tests/call-events.test.cjs tests/call-calendar.test.cjs` runs the reducer,
  calendar and bridge-feed tests.
