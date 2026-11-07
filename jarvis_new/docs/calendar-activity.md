# Calendar activity

Open **Insights → Calendar** from the Jarvis launcher. The drawer uses the existing native focus lease, traps keyboard focus, and closes with Escape. Its Calendar tab shows incoming suggestions, failed writes waiting to retry, recent additions and undo history, source status, account/calendar identity, and deferred reminders. The school clock menu also shows compact automation status.

The same dashboard is available at `/calendar` in the frontend dev server (`http://localhost:4000/calendar`). For a static export served with Python, use `/calendar.html`.

From `jarvis_new/frontend`:

```sh
pnpm dev
```

From `jarvis_new`, start the configured bridge/runtime using the normal project setup. Browser development uses the loopback bridge at port 4317; when the bridge requires authentication, supply `NEXT_PUBLIC_BRIDGE_TOKEN` to the development server. Never publish that token or a build containing it. Packaged native UI discovers its authenticated bridge through Tauri.

## Connect the sources

Use the existing Google OAuth setup for the selected calendar account, with Calendar event write permission. `JARVIS_CALENDAR_ACCOUNT` and `JARVIS_CALENDAR_ID` choose the account and calendar; `JARVIS_TZ` supplies the event timezone. The dashboard distinguishes configured credentials from verified connection. Calendar metadata permission is not required to write events: a metadata-only permission error does not prove event access failed.

Open and sign into WhatSie once:

```sh
flatpak run com.ktechpit.whatsie --remote-debugging-port=9223
```

Keep the client running on the same host as Jarvis. Jarvis reads WhatsApp through its existing local CDP connection. The watcher checks unread chats periodically; opening a chat marks its messages read. Source status reflects the backend scan metadata. `JARVIS_WHATSAPP_EVENTS=0` disables WhatsApp event ingestion; `JARVIS_EMAIL_EVENTS=0` disables email ingestion. `JARVIS_AUTO_CALENDAR=0` switches eligible events to suggestions. `JARVIS_WA_DATE_ORDER=DMY` is the default for WhatsApp timestamps; use `MDY` when the client's date order requires it.

Specific events are added automatically when enabled. Tentative plans, changes, and cancellations require review. Dismiss removes a pending suggestion without changing Google Calendar. Failed writes appear separately and retry automatically. “Undo the last auto event” uses the existing voice action and applies only to an event in Jarvis's automatic-add ledger.

Return reminders use sustained keyboard/mouse activity, so physically sitting down without interacting is not detected. Reminder queue and delivery details are described in [auto-calendar-reliability.md](auto-calendar-reliability.md).

## Inspect and verify

The dashboard reads `GET /auto-calendar/status` and dismisses a selected pending record with `POST /auto-calendar/review` (`{"id":"…","action":"dismiss"}`). It refreshes every five seconds while visible and reconnects automatically. During bridge failures it labels retained activity as stale and disables review actions. Google event IDs in the activity log provide provenance for successful writes; the log is not an exhaustive view of your calendar.

Production export and isolated browser checks:

```sh
cd frontend
pnpm exec next build
uv run --no-sync python -m http.server 4012 --bind 127.0.0.1 --directory out
# In a second terminal, from frontend:
uv run --no-sync python tests/calendar-smoke.py
uv run --no-sync python tests/insights-smoke.py --url http://127.0.0.1:4012
```

The browser fixtures do not contact WhatsApp or Google Calendar and never send messages. They exercise real React handlers with synthetic bridge responses, including narrow layouts, keyboard dismissal, stale-record errors, and reconnect behavior. Follow the normal frontend packaging workflow to embed the exported files into the native shell; a running old native binary retains its older embedded assets.

## Ingestion limits and recovery

The active watcher reads up to twenty unread conversations per pass, at most thirty recent messages per conversation. It pages the virtualized WhatsApp interface within a bounded history budget. A one-time verified import can inspect the first thirty listed conversations separately; neither scan promises complete account history. The default message lookback is seven days; `JARVIS_WHATSAPP_LOOKBACK_DAYS` accepts one to thirty days for a deliberate initial import. Unknown message timestamps are skipped. Only exact conversation names are opened, and duplicate exact names require manual identification. Group announcements addressed to all members can qualify; isolated third-party plans do not. A private phone-session read cannot create shared background calendar work.

Calendar times and duplicate comparisons use `JARVIS_TZ`, including events near midnight. The source message date anchors relative phrases such as “tomorrow.” Google Calendar descriptions retain the source chat and message date, without copying the conversation. A timed event without an explicit end defaults to one hour and says so in its description. Alternative dates, tentative plans, rescheduling and cancellation notices remain for review. They do not automatically move or delete existing events.

A failed extraction retains the private staged message and retries after sixty seconds, backing off to one hour. Failed calendar writes remain in the retry queue. Persistent provider event IDs prevent duplicate writes after a lost response or a lost local ledger update; successful provider conflicts require exact-ID readback. The ledger records destination account/calendar for new additions, so later configuration changes do not redirect their undo operation. Historical ledger records without destination metadata use the configured destination.

The calendar importer never sends WhatsApp messages. Its feature flag is independent of the existing reply automation. This installation explicitly disables `JARVIS_WA_AUTOREPLY` while enabling calendar ingestion, so automatic calendar setup cannot start unsolicited replies.

A fully isolated message-to-calendar-to-return rehearsal is available from `jarvis_new`:

```sh
uv run python scripts/simulate_auto_calendar.py
```

It uses temporary local state, synthetic extraction output, fake Calendar writes, and a fake speaker. Its JSON reports zero real messages read and zero real Calendar writes.

A one-time reconciliation request is consumed by the running WhatsApp watcher. It first persists up to thirty exact conversation names, then reads at most twenty per pass; the remaining names survive restart and resume on later passes. Failed chat reads remain in `read_jobs` with backoff even after WhatsApp has cleared an unread marker. A bounded `read_history` retains conversation identity and the actual read error, without message bodies. These records stay in the protected local WhatsApp calendar state. A successful listing/read is separate from successful event extraction or a confirmed calendar write.

Insights remains visible when presence has parked the underlying HUD: its entrance animation uses a static final state under the HUD's animation pause. The browser regression includes opening this parked state without reduced motion, in addition to the native verification.
