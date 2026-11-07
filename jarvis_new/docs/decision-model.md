# Email and WhatsApp decision model

As of 28 October 2026, the default is the larger
`perplexity/pplx-decider-v1.2`, served through OpenRouter's Decisions API.
It replaces Mercury for email urgency/phishing and the calendar extraction
gate. There is no automatic selection of Jev, Flash or free Mercury.

The existing voice pipeline is unchanged. Urgency failures/incomplete answers
fall back to the existing headless ChatGPT backend. The calendar gate skips
extraction only when p(event) < 0.05; uncertain/failed decisions still go to
ChatGPT extraction. Urgency uses 0.5 and phishing uses 0.8; phishing overrides
urgency. These existing thresholds passed the small synthetic benchmark;
they are not a guarantee of production accuracy.

## Configuration

- `JARVIS_DECIDE_MODEL` overrides the default model at process startup.
- `JARVIS_DECIDE_API_KEY` takes a dedicated **OpenRouter** key from the
  environment, then `$JARVIS_HOME/keys.env` (default `~/.jarvis/keys.env`).
  When present, decision calls use that key exclusively. It is never logged.
- Without a dedicated key, existing `OPENROUTER_API_KEY` and
  `OPENROUTER_API_KEY_2` handling remains available.
- `JARVIS_MERCURY=0` is the legacy kill switch for decision calls. The legacy
  `mercury_decide.py` module and helper names remain for caller compatibility.

The supplied key was saved outside the repository in the private `keys.env`
with mode 0600. Only the supervised bridge was restarted for activation;
the agent, wake listener and microphone uplink were not restarted.

## WhatsApp reply-mode routing

WhatsApp now uses the same larger Perplexity model before the existing reply
writers. `wa_reply_router.py` selects among application-permitted modes:

- `jarvis`: the polite assistant writer.
- `owner_style`: the existing owner-style writer, only for contacts on the
  approved mimic list. It retains the style analyst and sensitive-topic /
  bot-identity handoff checks. Explicit requests addressed to the Jarvis
  assistant can select `jarvis` even in an approved mimic chat.
- `rude_jarvis`: the existing cutting butler writer, only when
  `JARVIS_WA_RUDE=1` and the contact is not on the trusted list. The selector
  distinguishes clear hostility toward Jarvis from casual swearing, ordinary
  impatience, teasing and emergencies.

Code removes disallowed choices from the model request and rejects any
disallowed response. Failed/malformed decisions or a selected probability
below 0.8 use polite Jarvis. An explicitly polite route suppresses a writer
response marked rude. Per-chat writer instructions are isolated with a
ContextVar so parallel chats cannot change one another's persona.

Blocked/self chats, group mentions, owner activity, muted chats, reply limits
and duplicates retain their existing checks before selection. Only the latest
16 messages are considered, with at most 10,000 text characters; new messages
are marked separately from history. The route is recorded in the existing
per-chat state as `reply_route`, including model and probability or fallback
reason. The model does not write or send the reply.

`JARVIS_WA_DECIDER=0` explicitly restores the previous list/prompt routing.
The legacy global `JARVIS_MERCURY=0` also disables this decision call.
Default-enabled routing adds one decision request per eligible chat, using
the existing private decision key. Existing contact lists and rude-mode
settings are not changed. Reply generation and the owner-style analyst retain
their existing models and fallbacks.

Live synthetic checks passed all ten routing cases after clarifying an initial
prompt ambiguity around approved owner-style chats. Four full-handler dry
runs then verified normal, owner-style, rude and trusted-contact dispatch with
fake writers and zero WhatsApp send calls. Evidence is stored in
`benchmarks/decisions-2026-10-28/main/whatsapp-router-smoke.json` and
`whatsapp-handler-smoke.json`; the initial failed prompt check is retained as
`whatsapp-router-smoke-initial.json`. Four additional cases with all three
choices permitted passed in `whatsapp-three-mode-smoke.json`.

## Request intent and background reasoning

`route_request` is an advisory tool for ambiguous owner requests. Perplexity
classifies `direct`, `backend`, `research` or `code`, and identifies an explicit
Claude/ChatGPT preference. It does not execute any of those routes. Obvious
conversation, quick facts and local controls keep their existing direct paths.
The realtime/STT/TTS pipelines are unchanged.

`ask_backend` also checks the request before scheduling a reasoning job. Direct
requests return guidance with `backend_started=false`; research/code decisions
point back to their dedicated existing tools. A background acknowledgement is
returned only when a reasoning job is scheduled. Uncertain/failed classification
defaults to direct, except anchored explicit backend instructions are retained.

Reasoning jobs use `backend_pool.py`: ChatGPT first by default, or the requested
provider first. Recognised usage/rate-limit errors put that provider in a
five-minute **process-local** cooldown and try the other provider within the
same overall timeout. Missing/disabled providers can also fall back. Ambiguous
timeouts/errors do not cause a blind replay. This reacts to real call failures;
it does not query or invent remaining quota percentages.

Claude is a text-only subscription-authenticated CLI worker with empty tools
and MCP configuration, disabled slash commands/hooks, a neutral temporary
working directory and no saved session. Its successful result must include
initialization evidence of empty tools/MCP, and any tool-use record is refused.
The existing extraction, research, coding/Assigner and WhatsApp adapters are
unchanged by this provider pool. In particular, this does not add general Claude
code execution, browser access, automatic patch application or sending.

Configuration: `JARVIS_REQUEST_DECIDER=0` restores the old ask_backend dispatch;
`JARVIS_BACKEND_PROVIDER` chooses the default first provider (`chatgpt` or
`claude`); `JARVIS_CLAUDE=0` disables Claude; `JARVIS_CLAUDE_MODEL` defaults to
`sonnet`. Explicit provider-only handling is available internally through the
pool's `allow_fallback=false` option.

Validation: nine live synthetic intent requests selected the expected routes
without dispatching jobs. The real isolated Claude worker returned the exact
smoke-test answer successfully. Limit fallback/cooldown is covered with injected
errors rather than intentionally exhausting a subscription. The agent was
reloaded only after the LiveKit admin API confirmed zero room participants;
bridge, wake and microphone process identities stayed unchanged.

## Estimated running cost

The published larger-model rate is **US$0.02 per million input tokens**, with
free output. Benchmark OpenRouter usage agrees with this rate. Sources:
[Perplexity model and pricing documentation](https://docs.perplexity.ai/docs/decisions/quickstart#models).

The serial/repeated short-email samples averaged:

| Call | Input tokens | Cost |
|---|---:|---:|
| Urgency + phishing (one request, two questions) | 303.5 | $0.00000607 |
| Calendar gate | 221.5 | $0.00000443 |
| Both calls per email | 525 | $0.00001050 |

Assuming both calls for every email, and 30 days per month:

| Emails per day | Estimated daily cost | Estimated monthly cost |
|---:|---:|---:|
| 100 | $0.00105 | $0.0315 |
| 1,000 | $0.0105 | $0.315 |
| 10,000 | $0.105 | $3.15 |

This assumption counts two calls for every email, although existing mail
filters and the per-run calendar-check cap can reduce calls. Actual charges
depend on the length and volume of new messages and any repeated requests.
For a more conservative scenario of **1,000 input tokens per request** and
two requests per email, 1,000 emails/day costs **$0.04/day or $1.20/month**.

There is no idle inference charge: the watcher can poll every minute without
calling the decision model when no new qualifying mail needs processing.
These estimates cover the Perplexity decisions only; they exclude existing
ChatGPT fallback/extraction usage and other Jarvis services. No new recurring
decision polling or model heartbeat was added.

WhatsApp routing adds one call per eligible chat. Its ten synthetic routing
checks averaged 347.8 input tokens, approximately **$0.00000696 per decision**
or **$0.007 per 1,000 decisions** at the same rate. At 1,000 eligible chats/day
that is about $0.21/month in additional routing cost; longer histories cost
more. This excludes the existing reply writer and owner-style analyst.

## Validation

Five integration regression tests were added after first confirming the new
default, key selection and reason labeling tests failed against the old code.
All 45 Mercury-client, email urgency and calendar-extractor tests then passed.
Live checks using the saved dedicated key confirmed the larger model recognized
the legitimate urgent school-account alert and skipped extraction for a sales
newsletter. The bridge health check passed after its restart; voice sidecar
process identities were unchanged.

Evidence:

- `benchmarks/decisions-2026-10-28/main/REPORT.md`: earlier 461-measurement comparison.
- `benchmarks/decisions-2026-10-28/main/integration-smoke.json`: live default-client checks.
- `benchmarks/decisions-2026-10-28/main/activation.json`: bridge health and process checks.
