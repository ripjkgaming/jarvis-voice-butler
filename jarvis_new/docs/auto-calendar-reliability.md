# Auto-calendar delivery and verification

Calendar events extracted from WhatsApp use the shared email calendar ledger and Google Calendar account. A confirmed write, a queued write retry, a suggestion requiring review, and a delivered spoken reminder are separate states. The dashboard should expose pending review and write failures rather than label them added.

## Return to desk

The return detector uses keyboard/mouse activity, not physical seat or face detection. A pending reminder waits while input is idle or unknown and during quiet hours. Three consecutive recent-active input samples (five seconds apart) trigger the return queue. Interrupting input resets that streak. High-urgency calendar reminders follow the absence policy too; critical system safety signals retain their separate policy.

Pending calendar reminders are stored locally in `JARVIS_HOME/notify_queue.json` with mode 0600 and an atomic, fsynced replacement. Calendar stages coalesce to one pending reminder per event. Each drain dispatches one complete calendar reminder, retaining the rest for later return ticks; calendar details never disappear into a generic count-only summary. Calendar reminders survive long absences and pressure from general notifications; generic notifications retain their existing fifty-item / twenty-four-hour bounds. Calendar return speech includes an absolute date and time in the configured calendar timezone so a delayed announcement does not promise an obsolete relative countdown. Successful calendar undo clears pending reminders for that event.

Speech refusal or failure leaves a reminder pending. Calendar speech opts into a voice-required receipt: muted input, an active call, or school mode cannot acknowledge a toast-only fallback as spoken. The reminder retries after suppression ends even if keyboard/mouse activity has stayed active. Each accepted item is removed only after the speech interface accepts it; if a later item fails, previously accepted items remain acknowledged. A cross-process drain lock prevents two active consumers from draining the same queue. Successful notification receipts and pending fingerprints survive ordinary restarts.

The speech interface acknowledges dispatch, not proof that a person heard the sound. A process or power failure after audio dispatch but before removal can repeat the announcement on restart. Storage errors and full-disk conditions remain operational failures. There is no promise of exactly-once physical audio. No snooze control is implemented by the return queue.

Calendar cancellation/rescheduling must invalidate queued stages through `notify.cancel_calendar_reminders(event_id)` after the corresponding successful calendar mutation. A cancellation awaiting human review does not mutate the real calendar. Externally edited calendar events can remain in the source's four-minute fetch cache; updates cannot revoke audio already in flight.

## Repeatable local verification

Run with the repository's `uv` environment:

```sh
uv run pytest -q tests/test_calendar_return_reliability.py tests/test_notify.py tests/test_proactive_engine.py tests/test_audit_proactive_delivery.py tests/test_calendar_reconnect.py tests/test_whatsapp_calendar.py tests/test_event_auto_calendar.py
uv run ruff check src/notify.py src/proactive/engine.py src/proactive/sources/calendar_source.py tests/test_calendar_return_reliability.py
```

The return integration tests exercise the real policy, persisted engine state, queue, and announcer with a synthetic clock and output devices. They cover absence, unknown presence, restart, repeated returns, offline speech, partial delivery, same-ID rescheduling, cancellation invalidation, malformed/expired generic data, overlapping consumers, a three-day absence with sixty general notifications, and the actual speech suppression policy across mute/call/school transitions. WhatsApp tests inject messages, model output, and calendar transport so they do not send or create anything externally.
