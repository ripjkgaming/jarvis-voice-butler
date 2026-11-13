# Call simulation report (W8)

Real free OpenRouter chain, no `--scripted`, all 35 scenarios, fixed clock Fri 2026-11-13 10:00 Asia/Singapore.

## Results

| pass | run id | result |
|---|---|---|
| 1 | 20261113-222827 | 34/35 |
| 2 | 20261113-223530 | 34/35 |

Per scenario: 34 scenarios passed in both passes (2/2). `in_work_answer` failed in both passes (0/2), and passed on a rerun after the expectation fix (below). The rerun is a single run, not a full pass.

Failure detail: the expectation required the literal substring `pass your note`, which is a phrase from the scripted reply. The real models answered correctly ("Sure, I can pass a note along", "What would you like the message to say?"). The expectation was wrong, not the agent. Change: `must_contain` now empty, `must_not_contain` is `["I promise","I guarantee"]`, `events` adds `audit:decision`. This still checks the call is answered and nothing is promised. It no longer checks wording the model is free to choose. It is weaker than before; the note-taking itself is covered by unit tests and the scripted replay, not the live run.

Second expectation change: `book_timezone_conversion` caller said "3pm my time, I'm in London" with no day. The old scripted model silently assumed tomorrow. The code now asks "Which day do you mean?", which is correct behaviour for a missing day. The caller line now says "3pm tomorrow my time". A unit test covers the no-day case.

## Latency and models

- Calendar scenarios (book / check / remind / cancel / withdraw) now resolve in code: no LLM call for the reply (llm_ms 0). Guard-blocked scenarios also skip the LLM. 54 of 70 scenario-runs passed with zero LLM reply time.
- Remaining LLM turns (16 across both passes): mean 5.4 s, max 12.4 s per turn on free tiers. Piper TTS about 1.7 to 3.2 s per line.
- Models that answered (scenario-run count, includes summaries): particle-ai/particle-1:free 70, baidu/ernie-5.5-flash-preview:free 50, google/gemma-4-31b-it:free 13, nvidia/nemotron-3.5-super-vl:free 8.
- Pass 2 hit rate limits: several summary calls failed with all free models 429/empty/404 and fell back to the built-in caller-text summary. This did not fail any scenario.

## What changed (all inside src/calls, scripts, tests/calls)

- `src/calls/intent.py` (new): code parses book / check / remind / cancel-change / withdraw intent, times ("6pm", "seven thirty", "Friday at 3", "from 3 to 5pm", "noon"), dates (today, tomorrow, weekday, "20 November", ISO), durations, caller timezone (London, New York, etc.) converted to Asia/Singapore. Vague or incomplete input returns a clarifying question: no time ("What time exactly tomorrow evening?"), bare 7 with no daypart, no day, "my time" with no zone. Pending state carries over turns ("Seven thirty then").
- `calendar_hook.handle_caller_text`: runs the tool and returns the sentence. The tool result is spoken as a template; the LLM is not asked to phrase it. Past times are refused by `propose_booking`. Withdraw cancels open proposals; withdraw or cancel of a confirmed booking, and cancel / move of existing events, go through `request_change` (escalation + audit `booking_change_escalated`). Parser errors fall back to the LLM path.
- `calendar_hook.false_confirmation`: if the model says "booked", "done", "all set" and no tool ran, the reply is replaced and escalated.
- `agent.py`: runs the code intent for inbound calls with a calendar, after the caller guard and before the LLM. Code-generated text goes through `check_outgoing`. Prompt-injection guard replies without escalating, and audits `prompt_injection_blocked`.
- `policy.py`: caller guard adds prompt-injection patterns and more credential words (passcode, verification code, CVV, credentials, login details). Guard checks run first, so a booking request mixed with a payment ask or password ask never reaches the tools.
- `sim.py`: every event `call_id` is prefixed `sim-` so sim calls cannot collide with real ids in the live HUD feed (unit test with a temp live events file).
- Tests: `tests/calls/test_intent_harden.py` (22 tests); W6 tests that fed a booking phrase to the token path now send a neutral phrase, since a booking phrase is handled in code (the token path remains the fallback).

## Known limits and honest failures

- Intent is English regex. Unusual phrasings fall through to the LLM, which can emit tokens (still handled) or free text (false confirmations blocked).
- Code intent applies to inbound calls only. Outbound calls still rely on the LLM and the goal.
- Free chat of non-calendar topics is still LLM-quality: a weak model may be vague or rambling. Only the calendar and guard paths are deterministic.
- Zone table is small (about 30 names). An unknown city is not recognised and the code asks again.
- Bare "7" without daypart asks morning or evening; bare 1 to 6 means pm and 8 to 11 means am (assumption, documented in `parse_time`).
- Live run used the in-memory calendar only. `RealCalendarAdapter` still untested against Google.
- `--hud` live replay in the real HUD was not run by W8.
- `--play`: ran `--scripted --play` for `book_clash_alternatives` once (paplay present); the run completed and passed, but audible output was not heard by me.
