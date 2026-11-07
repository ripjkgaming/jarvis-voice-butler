# Live WhatsApp calendar audit — 6 November 2026

One real event was created after exact chat-header and timestamp verification, then independently read back from Google Calendar.

**Latest status, 7 November:** subsequent runtime verification reports this provider event as `cancelled`, and it no longer appears in the active November 7 event list. The initial creation/readback below is historical evidence, not a claim that the event is currently active. It has not been recreated. The cancellation origin has not been established.

| Field | Verified value |
| --- | --- |
| Event | Badminton training |
| Start | 7 November 2026, 13:00 Asia/Singapore (UTC+08:00) |
| End | 7 November 2026, 14:00 Asia/Singapore (UTC+08:00) |
| Duration | End was not supplied in the message. The app's one-hour default was used and recorded in the calendar description. |
| Venue | Not supplied; left blank. |
| Source | WhatsApp group invitation: Saturday Badminton 12-1pm |
| Source timestamp | 2026-11-06T17:48:00+08:00 |
| Calendar account | ripjkgaming@gmail.com |
| Calendar | Primary (`primary`) |
| Event ID | `jarvisaf4f39617c1375b07f694dd85416c7179da9e8e8137a5783f90a4a81fe14f7ae` |
| Provider link | [Open Badminton training in Google Calendar](https://www.google.com/calendar/event?eid=amFydmlzYWY0ZjM5NjE3YzEzNzViMDdmNjk0ZGQ4NTQxNmM3MTc5ZGE5ZThlODEzN2E1NzgzZjkwYTRhODFmZTE0ZjdhZSByaXBqa2dhbWluZ0Bt) |

The invitation was addressed to all group participants. No attendance acceptance or WhatsApp message was sent. Subsequent retrieved group messages contained no cancellation/reschedule of this training invitation. The group name includes an older time range; the event uses the explicit time in the dated announcement.

The shared app ledger and source record contain this addition, including source, sender/group, date, time, provider event ID, and status `added`. The Google event description preserves the group source/message date and the assumed duration. A repeated insertion was deduplicated, and the live day query contained exactly one copy of this provider event ID.

## Scan scope and unresolved items

The final scan is being repeated with strict requested/returned/header identity matching after repairing virtualized chat selection. Initial scans had incomplete history and fuzzy name substitutions; those results were used only to identify candidates, never to justify calendar writes. Exact verification preceded the single addition above.

A proposed tutor appointment offering Monday around 14:00 **or** Tuesday is ambiguous and was persisted as a dashboard review item, with no calendar entry. Past group announcements were not imported as future events. Final strict scan attempted all 30 listed chats: 28 passed requested-name/returned-name/header identity verification; 2 could not be read and were skipped. It retrieved 337 message records, including 158 incoming messages timestamped within the preceding 30 days. The bounded request was at most the latest 30 retrievable messages per chat; this is not a complete historical archive. Fifteen scheduling-keyword candidates were reviewed. One future training invitation was added; one ambiguous tutor choice was persisted as review-required; the remaining candidates were past, vague/availability-only, or lacked a definite future commitment. No other calendar entries were created. No raw message contents, unrelated contacts, access tokens, or refresh tokens appear in this audit.
