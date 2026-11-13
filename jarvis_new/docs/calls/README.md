# Jarvis phone calls (WhatsApp)

Jarvis can answer and place WhatsApp voice calls through the WhatSie desktop
app: it listens to the caller (local VAD + whisper), thinks with an LLM and
speaks with a Piper voice. **Off by default.** Nothing here has run against a
real WhatsApp call; see UNVERIFIED.

Contract and module ownership: [CONTRACT.md](CONTRACT.md). HUD wiring:
[FRONTEND_WIRING.md](FRONTEND_WIRING.md).

## Architecture

```
WhatSie (CDP)  <->  CdpCallDriver (wa_call.py)      accept/decline/dial/hangup
WhatSie audio  <->  PipeWire null-sinks (audio_route.py) jarvis_call_in/out + jarvis_mic
sinks          <->  CallBridge (call_bridge.py)     parec ears / pacat mouth
ears: frames -> Silero VAD endpointer -> faster-whisper -> CallSession.on_caller_text
mouth: CallSession reply -> Piper (character_voice) -> CallBridge.play
CallSession (agent.py, policy.py, events.py)         state machine, guards, HUD events
store (contacts, rules, calllog, memory_bridge)      ~/.jarvis/calls/calls.db + vault Threads
CallRuntime (runtime.py)                             watcher, dial entry, per-call glue
```

`CallRuntime` runs on its own thread/event loop inside the agent process,
polls for ringing calls every second, runs `rules.decide()` through
`CallSession.start_inbound()`, and per call sets up the audio route, bridge and
ears, then tears them down.

## Setup

1. WhatSie installed, signed in, started with the CDP port the repo already
   uses for `system.whatsapp`.
2. PipeWire/PulseAudio with `pactl`, `parec`, `pacat`.
3. Local whisper + Piper models (`uv run python scripts/prefetch_models.py`),
   `~/.jarvis/models/silero_vad.onnx` (energy-gate fallback otherwise).
4. `GOOGLE_API_KEY` (free tier) and/or the signed-in `claude` CLI for the LLM.
5. Set `JARVIS_CALLS=1` and restart the agent (`uv run python src/agent.py ...`).

## Environment flags

| Variable | Default | Meaning |
| --- | --- | --- |
| `JARVIS_CALLS` | `0` | Master switch. Off: no watcher, no dialer, no scheduler thread. |
| `JARVIS_CALL_DISCLOSE_AI` | `1` | Say "this is Jarvis, an AI assistant" at call start. |
| `JARVIS_CALL_RECORD` | `0` | Record/transcribe notice spoken when on. |
| `JARVIS_CALL_PERSONA` | `jarvis` | `jarvis` or `owner` (owner voice). Per-contact `policy.persona` wins. |
| `JARVIS_CALL_MAX_S` | `600` | Hard call length limit. |
| `JARVIS_CALL_LLM` | `gemini,claude` | Backend order (free tier Gemini first). |
| `JARVIS_CALL_MODEL` | direct-pipeline model | Gemini model id. |
| `JARVIS_CALL_VOICE` | `jarvis` | Character id for the Jarvis voice. |
| `JARVIS_CALL_OWNER_VOICE` | unset | Character id whose Piper model is the owner voice; falls back to Jarvis. |
| `JARVIS_OWNER_NAME` | `the owner` | Name used in call prompts. |
| `JARVIS_CALLS_DIR` | `~/.jarvis/calls` | db, `events.jsonl`, `commands.jsonl`, `scheduled_calls.sqlite`. |
| `JARVIS_SCHEDULED_TASKS` | `1` | Existing scheduler switch; `0` also stops scheduled calls. |

Stored settings (`calls.db`, keys `disclose_ai`, `record_calls`, `persona`,
`max_call_s`) override the environment.

## Voice commands

Tools (registered in `agent.py`, prompt line in `prompts.py`): `call_contact`,
`schedule_call`, `list_scheduled_calls`, `cancel_scheduled_call`,
`set_auto_answer`, `cancel_auto_answer`, `list_call_rules`, `add_contact`,
`update_contact`, `find_contact`, `delete_contact`, `call_control`.

Examples: "Call Mum and ask what time dinner is", "call Dr Lee tomorrow at 9 and
confirm the appointment", "answer calls from unknown numbers for two hours",
"stop auto answering", "take over", "hold", "hang up", "take a message".

Rule precedence: deny list > allow list > blocked contact > contact policy >
newest live rule > ask the owner (call keeps ringing, HUD shows an escalation).

## Safety and limits

- AI disclosure on by default; recording notice when `record_calls` is on.
- Replies are checked: no passwords/PINs/card data, no payments, no
  commitments. Caller asks for money or sensitive data escalate to the owner.
- Takeover flushes audio first; the agent stays silent while the owner talks.
- Every decision, command, escalation and rule change is in the append-only
  audit log. Max one call at a time; a second ring while busy is ignored.
- Call transcripts are stored raw in `calls.db`; vault summaries are redacted.
- Owner commands from the HUD go through `commands.jsonl` (ignored when older
  than 30 s). `events.jsonl` is not rotated.
- Hub/bridge stay loopback; no new public port.

## Tests

```console
uv run pytest tests/calls -q
```

All simulated (FakeDriver, fake audio, scripted STT/LLM). The `pactl` smoke
test creates uniquely named sinks and removes them.

## UNVERIFIED (never run against a real call)

- Every WhatsApp selector/JS in `wa_call.SELECTORS`, incoming-call detection,
  and the dial flow (search -> first result -> voice call button).
- Moving WhatSie's call streams onto the null-sinks (`application.name`
  match), mic permission, and offscreen audio.
- `parec`/`pacat` latency under real streams; barge-in flush latency.
- Whisper accuracy on 8 kHz-origin phone audio; end-of-speech timing
  (600 ms silence) and Piper first-audio latency during a call.
- Resolving a WhatsApp display name to a number (falls back to contact-name
  lookup, otherwise the raw display name goes to `decide()`).
- Owner takeover speech path: the owner talks through the normal mic into
  WhatSie; the `jarvis_mic` routing for owner hand-back is not exercised.
- Gemini/Claude latency within a live turn; Contract gap: `Decision` has no
  `message` field, so the rule message comes from the matching rule.
- HUD polling UX (frontend wiring not done).
- Multi-process db write contention; Singapore-centric number normalisation.
