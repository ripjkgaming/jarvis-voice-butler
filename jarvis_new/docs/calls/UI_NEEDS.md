# Calls HUD: data the backend does not send yet

The HUD has a CALENDAR panel and inline transcript tags that show slot checks,
bookings, reminder lookups and change requests made during a call. The call
event stream (`CONTRACT.md`) has no calendar event, so today the HUD reads the
template sentences that `src/calls/calendar_hook.py::_run` speaks, and takes
the booking title and time for held or pending bookings from the escalation
reasons in `calendar_tools.py`. See `frontend/components/calls/calendar.ts`.

This works, but it is fragile:

- Any change to the wording in `calendar_hook._run` or to the escalation
  reasons breaks the panel without breaking a test. The tests in
  `frontend/tests/call-calendar.test.cjs` copy the current sentences.
- The spoken result does not include the conflicting event. For a clash, the
  HUD shows "EXISTING EVENT · NO DOUBLE-BOOKING" instead of the event's title
  and time. The owner cannot see which event blocks the slot.
- The requested slot for a clash is not spoken either, so the day strip shows
  the offered alternatives but not the requested time.
- A reminder lookup's query is not shown. The HUD shows the caller's last line
  instead.
- The audit records (`calendar_check`, `booking_*`,
  `calendar_reminder_lookup`) have this data, but they go to `calllog`
  (SQLite), not to `events.jsonl`, so the bridge does not serve them.

## Requested event

Emit one event after each calendar tool call, through `events.emit`, in the
same call stream:

```json
{
  "type": "call",
  "event": "calendar",
  "call_id": "...",
  "action": "check | book | remind | change",
  "status": "free | busy | confirmed | proposed | escalated | hold_pending_owner | denied | found | ambiguous | near | missing | withheld | change_escalated",
  "title": "Dinner",
  "start": "2026-11-14T19:00:00+08:00",
  "end": "2026-11-14T20:00:00+08:00",
  "who": "Mum",
  "query": "flight to Tokyo",
  "reason": "clash: would double-book",
  "match": { "title": "Dentist appointment", "start": "...", "end": "..." },
  "clashes": [{ "title": "Team dinner", "start": "...", "end": "..." }],
  "suggestions": [{ "start": "...", "end": "..." }],
  "nearby": [{ "title": "...", "start": "...", "end": "..." }]
}
```

Only `action` and `status` are required; send the other fields when they are
known. Times are ISO strings in the owner's timezone; the HUD reads the
wall-clock part as sent.

Privacy: the HUD is owner-only, so it may show event titles to the owner.
This event must not change what Jarvis says to the caller (free/busy only for
unknown callers).

The UI already accepts this event (`parseCallEvent` in `call-reducer.ts`).
When a call has any `calendar` events, the panel uses them and ignores the
spoken sentences. No UI change is needed when the backend adds it. The
event also needs a line in the "Call event stream" section of `CONTRACT.md`
(parent-owned).

## Smaller items

- `sim.py` runs a scenario on the scenario's `now` clock, so `started_at` and
  `ts` can be hours away from wall time. The bridge poller shifts a call's
  times when they are more than 15 minutes off (`makeRetimer` in
  `bridge-feed.ts`). Real calls use `time.time()` and are not shifted.
- `events.jsonl` is never rotated. If the runtime crashes during a call, the
  last `state` is not `ended`, and `/calls/state` reports that call as active.
  The HUD overlay then stays up until a later call replaces it. An `ended`
  state on runtime start (or an age limit in `hud_feed.snapshot`) would fix it.
