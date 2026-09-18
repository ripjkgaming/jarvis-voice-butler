# Skill: Meeting Prep

Next calendar event + attendee web lookup + prep notes file. Read-mostly;
writes exactly one notes file and opens nothing without asking.

## Trigger phrases

- "meeting prep"
- "prep my meeting"
- "brief me for my next meeting"
- "who am i meeting next"
- "get ready for my call"
- "what is my next event"

## Step sequence (real tool names)

1. `school_day` (DailyTools, `when="today"`, then `when="next"` if today
   is empty) — establish the next event: title, time, attendees if the
   calendar names them. If no event exists, say so and stop (do not
   invent one).
2. `search_the_web` (BrowserTools, `query="<attendee or topic> background"`)
   — one lookup per named attendee/topic, max two lookups. Read-only web
   search; no browser windows.
3. `build_document` (DailyTools, `topic="<event title> prep"`,
   `kind="notes"`) — writes the prep sheet (event, attendees, lookup
   highlights, 3 suggested questions) to `~/Documents/Jarvis/`. Report
   the file path when done.
4. Speak a 30-second brief: when, who/what, one line per lookup, where
   the notes landed.

## Abort / confirm rules

- `open_url` is available but gated: open the event link / attendee page
  ONLY if the user says "open it". Looking something up
  (`search_the_web`) never opens a browser by itself.
- If the calendar has no events: stop after step 1 with "Nothing on the
  calendar, Sir." Do not search, do not write notes.
- If the lookup returns nothing usable: still write the notes file with
  event details + questions, and say the lookup came up dry.
- Never send mail, message attendees, or post anything. This skill reads
  the web and writes one local file — nothing leaves the laptop.

## Success criteria

- User hears: next event time/title, who is involved, one useful line per
  lookup.
- Exactly one notes file written per prep; path reported.
- No browser opened unasked; no message sent; no event invented.
