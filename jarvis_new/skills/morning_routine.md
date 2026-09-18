# Skill: Morning Routine

One-turn morning bundle: briefing + calendar + weather + open todos.
Read-only. No destructive actions. No confirmation needed.

## Trigger phrases

- "morning routine"
- "start my morning"
- "run my morning briefing"
- "good morning"
- "kick off the day"
- "daily startup"

## Step sequence (real tool names)

1. `morning_briefing` (InboxTools, no args) — spoken bundle: weather +
   headlines + school today + todos. This is the backbone; speak it first.
2. `school_day` (DailyTools, `when="today"`) — calendar detail the bundle
   compresses. If it reports no calendar loaded, say so once and move on.
3. `weather_now` (InboxTools, `city=""` for local) — only if the bundle's
   weather section came back empty (network was down); otherwise skip.
4. `manage_todo` (SystemTools, `action="list"`) — only if the bundle's todo
   section came back empty; otherwise skip. Never add/done/clear anything
   unasked.

Speak a short closing line: what is next on the calendar and in one
sentence the weather. Keep the whole turn under ~45 seconds of speech.

## Abort / confirm rules

- All four tools are read-only: run without asking.
- If every source is empty (no calendar, no weather, no todos, no
  headlines): say "Quiet morning, Sir — nothing on the calendar and no
  headlines yet." and stop. Do not pad with trivia.
- Never open browsers, play media, or change system state in this routine.
- If any tool raises ToolError, skip that section and mention it in one
  clause ("weather is unreachable just now") — never abort the whole
  routine over one section.

## Success criteria

- User hears: today's classes/events, one-line weather, open todos.
- No tool errors surfaced raw; no section repeated twice (bundle contents
  are not re-read verbatim by steps 2–4).
- Wall time under ~60s even with the weather fallback path.
