# Jarvis decision-model benchmark — 28 October 2026

Completed 461 top-level measurements. Synthetic email fixtures only; no production configuration changes.

## Method

All decision models used OpenRouter's `/api/alpha/decisions` endpoint with the supplied benchmark key. Times are wall-clock request-to-complete-response latency from the execution host, including networking, TLS, routing and JSON validation; they are not provider-only inference times or microphone-to-speech latency. Each call uses a new urllib connection. One warm-up/preflight call per model is excluded from performance tables. Serial: 16 fixtures x 2 production prompt shapes x 1 repeat = 32/model. Concurrency: same fixtures and shapes x 2 repeats = 64/model, with three total requests in flight across models. Long inputs: six fixtures, roughly 3,000 added characters, three combined questions, serial. Order is shuffled with seed 20261028; payloads and thresholds are shared across models. Direct API errors are recorded without retries. Workflow runs use two concurrent jobs and the real urgency/extraction functions with a benchmark-only decision adapter; that adapter uses one key and one decision attempt. The repository client normally has two keys and can try both, so its failure delay can be longer. The ChatGPT-only control invokes the same backend and prompts directly. This is a short host-specific sample; p95 with only 4-6 workflow/long samples per task is especially unstable.

## Decision API latency

| Phase | Model | Successful / attempted | Median seconds | p95 seconds |
|---|---|---:|---:|---:|
| concurrent_3 | `inception/mercury-decide` | 64/64 | 0.770 | 1.131 |
| concurrent_3 | `perplexity/pplx-decider-flash-v1.2` | 64/64 | 0.753 | 0.797 |
| concurrent_3 | `perplexity/pplx-decider-v1.2` | 64/64 | 0.827 | 0.960 |
| concurrent_3 | `typesafe/jev-1.14` | 64/64 | 0.691 | 0.768 |
| long_serial | `inception/mercury-decide` | 6/6 | 1.053 | 1.619 |
| long_serial | `perplexity/pplx-decider-flash-v1.2` | 6/6 | 0.832 | 0.904 |
| long_serial | `perplexity/pplx-decider-v1.2` | 6/6 | 0.834 | 1.064 |
| long_serial | `typesafe/jev-1.14` | 6/6 | 0.705 | 0.774 |
| preflight | `inception/mercury-decide` | 1/1 | 0.800 | 0.800 |
| preflight | `inception/mercury-decide:free` | 0/1 | — | — |
| preflight | `perplexity/pplx-decider-flash-v1.2` | 1/1 | 0.792 | 0.792 |
| preflight | `perplexity/pplx-decider-v1.2` | 1/1 | 0.837 | 0.837 |
| preflight | `typesafe/jev-1.14` | 1/1 | 0.663 | 0.663 |
| serial | `inception/mercury-decide` | 32/32 | 0.814 | 1.203 |
| serial | `perplexity/pplx-decider-flash-v1.2` | 32/32 | 0.746 | 0.832 |
| serial | `perplexity/pplx-decider-v1.2` | 32/32 | 0.836 | 0.971 |
| serial | `typesafe/jev-1.14` | 32/32 | 0.696 | 0.909 |

## Quality at Jarvis's existing thresholds

Urgency threshold = 0.5; scam threshold = 0.8; scams override urgent. The event gate runs extraction whenever p(event) ≥ 0.05. These scores compare synthetic hand-labelled expectations and are a smoke test, not a production accuracy estimate. Repeated calls are not independent examples. The account fixture's event label was reviewed as true because the exam portal closes today, which meets the gate's dated-deadline criterion. See `label-review.json`; original local scoring is retained as `initial_correct` in affected rows. API outputs and latency were not changed. A cancelled meeting and relative-date cases require judgment; inspect individual rows before retuning thresholds.

| Model | Urgent correct | Scam correct | Event gate correct | Real events wrongly skipped | Non-events sent to extraction |
|---|---:|---:|---:|---:|---:|
| `inception/mercury-decide` | 48/48 (100.0%) | 48/48 (100.0%) | 48/48 (100.0%) | 0/18 | 0/30 |
| `perplexity/pplx-decider-flash-v1.2` | 45/48 (93.8%) | 45/48 (93.8%) | 33/48 (68.8%) | 0/18 | 15/30 |
| `perplexity/pplx-decider-v1.2` | 48/48 (100.0%) | 48/48 (100.0%) | 48/48 (100.0%) | 0/18 | 0/30 |
| `typesafe/jev-1.14` | 48/48 (100.0%) | 48/48 (100.0%) | 36/48 (75.0%) | 0/18 | 12/30 |

## Full workflow latency

Each model/workload has four synthetic cases: room change, promotion, phishing and dental appointment. Calendar outputs are checked for event presence only, not the accuracy of every extracted date/time. `inception/mercury-decide:free` represents the checked-in architecture with its now-unavailable gate; `chatgpt_only` removes the failed gate call. Other models replace only the decision call in this isolated benchmark process. Event-positive cases still need the existing headless ChatGPT extractor.

| Model | Task | Success / attempted | Median seconds | p95 seconds | ChatGPT calls | Mean gate seconds | Mean ChatGPT seconds |
|---|---|---:|---:|---:|---:|---:|---:|
| `chatgpt_only` | event | 4/4 | 4.346 | 5.161 | 4 | 0.000 | 4.454 |
| `chatgpt_only` | urgency | 4/4 | 4.335 | 8.186 | 4 | 0.000 | 5.166 |
| `inception/mercury-decide` | event | 4/4 | 3.287 | 5.881 | 2 | 0.860 | 2.433 |
| `inception/mercury-decide` | urgency | 4/4 | 1.009 | 1.163 | 0 | 0.999 | 0.000 |
| `inception/mercury-decide:free` | event | 4/4 | 5.215 | 5.752 | 4 | 0.306 | 4.843 |
| `inception/mercury-decide:free` | urgency | 4/4 | 4.532 | 6.065 | 4 | 0.326 | 4.467 |
| `perplexity/pplx-decider-flash-v1.2` | event | 4/4 | 2.761 | 5.003 | 2 | 0.769 | 2.045 |
| `perplexity/pplx-decider-flash-v1.2` | urgency | 4/4 | 0.762 | 0.772 | 0 | 0.762 | 0.000 |
| `perplexity/pplx-decider-v1.2` | event | 4/4 | 3.037 | 5.774 | 2 | 0.880 | 2.283 |
| `perplexity/pplx-decider-v1.2` | urgency | 4/4 | 0.827 | 0.836 | 0 | 0.826 | 0.000 |
| `typesafe/jev-1.14` | event | 4/4 | 4.976 | 5.649 | 3 | 0.698 | 3.376 |
| `typesafe/jev-1.14` | urgency | 4/4 | 0.690 | 0.714 | 0 | 0.692 | 0.000 |

## Errors and disagreements

- ('preflight', 'inception/mercury-decide:free', 'HTTP_404'): 1
- `perplexity/pplx-decider-flash-v1.2` / `account` / `scam`: expected False, observed probability 0.8811.
- `perplexity/pplx-decider-flash-v1.2` / `account` / `urgent`: expected True, observed probability 0.9786.
- `perplexity/pplx-decider-flash-v1.2` / `past` / `has_event`: expected False, observed probability 0.0785.
- `typesafe/jev-1.14` / `past` / `has_event`: expected False, observed probability 0.1900.
- `perplexity/pplx-decider-flash-v1.2` / `injection` / `has_event`: expected False, observed probability 0.0549.
- `typesafe/jev-1.14` / `medical` / `has_event`: expected False, observed probability 0.1000.
- `typesafe/jev-1.14` / `cancelled` / `has_event`: expected False, observed probability 0.6800.
- `typesafe/jev-1.14` / `sale` / `has_event`: expected False, observed probability 0.1500.
- `perplexity/pplx-decider-flash-v1.2` / `routine` / `has_event`: expected False, observed probability 0.0973.
- `perplexity/pplx-decider-flash-v1.2` / `medical` / `has_event`: expected False, observed probability 0.1105.
- `perplexity/pplx-decider-flash-v1.2` / `cancelled` / `has_event`: expected False, observed probability 0.7020.

## Repeat consistency

Same-prompt prediction consistency across the serial call and two concurrent repetitions (3 calls per fixture/task).

| Model | Fixture/task groups with any label change | Maximum probability spread |
|---|---:|---:|
| `inception/mercury-decide` | 0/32 | 0.010051 |
| `perplexity/pplx-decider-flash-v1.2` | 0/32 | 0.000000 |
| `perplexity/pplx-decider-v1.2` | 0/32 | 0.000000 |
| `typesafe/jev-1.14` | 0/32 | 0.040000 |

## Cost and artifacts

Sum of reported OpenRouter `usage.cost` for standalone API samples: **$0.003929**. Workflow API calls, the interrupted exploratory run and ChatGPT subscription usage are excluded from this sum. No key is present in the artifacts.

- `calls.jsonl`: checkpointed per-call latency, probabilities, labels, usage and workflow counters.
- `fixtures.json`: synthetic emails and expected labels.
- `summary.json`: machine-readable grouped results and backend configuration.
