# Manual Calendar actions

A spoken request to add a single event to Google Calendar uses `calendar_add`.
The owner must supply a title, full date including year, and exact start/end
times, or explicitly request an all-day event. An incomplete “in the morning”
request asks for clarification. `exam_schedule` manages the local exam list;
its success does not mean anything was written to Google Calendar.

The action checks the configured account/calendar for a matching event, writes
with a deterministic provider event ID, and reads that exact ID back. Title,
start, end and location must match before the tool returns `ok: true` and a
`calendar.create` outcome with state `verified`. A failed write, unavailable
readback, mismatch, pending job or local schedule result cannot authorize a
spoken Google Calendar success claim. Verified manual action receipts live in
`JARVIS_HOME/manual_calendar_receipts.jsonl` with owner-only permissions.

`calendar_events_on` reads an exact date from Google Calendar and returns both
the provider events and a verified read receipt. A read can support an existing
named event's state; it cannot prove that Jarvis created or changed it.

The Direct backend checks complete sentences before emitting captions or TTS,
including speculative replies after the final user transcript. The native
Gemini realtime backend can deliver audio before text, so its response audio
and captions are held until a complete response can be checked. This adds the
response-generation interval to first playback. Native buffers are bounded to
sixty seconds of PCM; missing response text and interrupted unverified output
are discarded. A rejected success claim never enters conversation history.
Ordinary questions and requests for clarification still pass. Provider proof
is required even when final user transcription is missing or delayed.

The bridge's existing authenticated `POST /chat` with `voice: true` seeds a
normal Jarvis voice turn; the agent selects and executes its own tools.
Creating an event through a standalone helper is a separate action and is not
proof that the live assistant can carry out the request. Owner-mode policy
remains enforced at the Calendar tool boundary.
