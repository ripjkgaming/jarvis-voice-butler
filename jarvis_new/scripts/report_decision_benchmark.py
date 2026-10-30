#!/usr/bin/env python3
"""Build an auditable Markdown report from bench_decision_models checkpoints."""

import argparse
import collections
import json
import math
import statistics
from pathlib import Path


def latency(items):
    values = sorted(r["elapsed_ms"] for r in items if r["ok"])
    if not values:
        return "—", "—"
    return (
        f"{statistics.median(values) / 1000:.3f}",
        f"{values[math.ceil(0.95 * len(values)) - 1] / 1000:.3f}",
    )


def render(root):
    rows = [json.loads(s) for s in (root / "calls.jsonl").read_text().splitlines()]
    fixtures = {c[0]: c for c in json.loads((root / "fixtures.json").read_text())}
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["phase"], r["model"])].append(r)
    lines = [
        "# Jarvis decision-model benchmark — 28 October 2026",
        "",
        f"Completed {len(rows)} top-level measurements. Synthetic email fixtures only; no production configuration changes.",
        "",
        "## Method",
        "",
        "All decision models used OpenRouter's `/api/alpha/decisions` endpoint with the supplied benchmark key. "
        "Times are wall-clock request-to-complete-response latency from the execution host, including networking, TLS, routing and JSON validation; "
        "they are not provider-only inference times or microphone-to-speech latency. Each call uses a new urllib connection. "
        "One warm-up/preflight call per model is excluded from performance tables. "
        "Serial: 16 fixtures x 2 production prompt shapes x 1 repeat = 32/model. "
        "Concurrency: same fixtures and shapes x 2 repeats = 64/model, with three total requests in flight across models. "
        "Long inputs: six fixtures, roughly 3,000 added characters, three combined questions, serial. "
        "Order is shuffled with seed 20261028; payloads and thresholds are shared across models. "
        "Direct API errors are recorded without retries. Workflow runs use two concurrent jobs and the real urgency/extraction functions with "
        "a benchmark-only decision adapter; that adapter uses one key and one decision attempt. "
        "The repository client normally has two keys and can try both, so its failure delay can be longer. "
        "The ChatGPT-only control invokes the same backend and prompts directly. "
        "This is a short host-specific sample; p95 with only 4-6 workflow/long samples per task is especially unstable.",
        "",
        "## Decision API latency",
        "",
        "| Phase | Model | Successful / attempted | Median seconds | p95 seconds |",
        "|---|---|---:|---:|---:|",
    ]
    for (phase, model), items in sorted(groups.items()):
        if phase == "workflow":
            continue
        p50, p95 = latency(items)
        lines.append(
            f"| {phase} | `{model}` | {sum(r['ok'] for r in items)}/{len(items)} | {p50} | {p95} |"
        )
    lines += [
        "",
        "## Quality at Jarvis's existing thresholds",
        "",
        "Urgency threshold = 0.5; scam threshold = 0.8; scams override urgent. "
        "The event gate runs extraction whenever p(event) ≥ 0.05. "
        "These scores compare synthetic hand-labelled expectations and are a smoke test, not a production accuracy estimate. "
        "Repeated calls are not independent examples. The account fixture's event label was reviewed as true because the exam portal closes today, which meets the gate's dated-deadline criterion. "
        "See `label-review.json`; original local scoring is retained as `initial_correct` in affected rows. API outputs and latency were not changed. "
        "A cancelled meeting and relative-date cases require judgment; inspect individual rows before retuning thresholds.",
        "",
        "| Model | Urgent correct | Scam correct | Event gate correct | Real events wrongly skipped | Non-events sent to extraction |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    models = sorted({r["model"] for r in rows if r["phase"] == "serial"})
    for model in models:
        sample = [
            r
            for r in rows
            if r["phase"] in ("serial", "concurrent_3")
            and r["model"] == model
            and r["ok"]
        ]

        def metric(label, sample=sample):
            flags = [
                r["correct"][label] for r in sample if label in r.get("correct", {})
            ]
            return (
                f"{sum(flags)}/{len(flags)} ({sum(flags) / len(flags):.1%})"
                if flags
                else "—"
            )

        event_rows = [r for r in sample if r["task"] == "event"]
        fn = sum(
            fixtures[r["case"]][6] and not r["predictions"]["has_event"]
            for r in event_rows
        )
        positives = sum(fixtures[r["case"]][6] for r in event_rows)
        fp = sum(
            not fixtures[r["case"]][6] and r["predictions"]["has_event"]
            for r in event_rows
        )
        negatives = len(event_rows) - positives
        lines.append(
            f"| `{model}` | {metric('urgent')} | {metric('scam')} | {metric('has_event')} | {fn}/{positives} | {fp}/{negatives} |"
        )
    lines += [
        "",
        "## Full workflow latency",
        "",
        "Each model/workload has four synthetic cases: room change, promotion, phishing and dental appointment. "
        "Calendar outputs are checked for event presence only, not the accuracy of every extracted date/time. "
        "`inception/mercury-decide:free` represents the checked-in architecture with its now-unavailable gate; "
        "`chatgpt_only` removes the failed gate call. Other models replace only the decision call in this isolated benchmark process. "
        "Event-positive cases still need the existing headless ChatGPT extractor.",
        "",
        "| Model | Task | Success / attempted | Median seconds | p95 seconds | ChatGPT calls | Mean gate seconds | Mean ChatGPT seconds |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    wg = collections.defaultdict(list)
    for r in rows:
        if r["phase"] == "workflow":
            wg[(r["model"], r["task"])].append(r)
    for (model, task), items in sorted(wg.items()):
        p50, p95 = latency(items)
        lines.append(
            f"| `{model}` | {task} | {sum(r['ok'] for r in items)}/{len(items)} | {p50} | {p95} | {sum(r.get('chatgpt', 0) for r in items)} | {statistics.mean(r.get('decision_ms', 0) for r in items) / 1000:.3f} | {statistics.mean(r.get('chatgpt_ms', 0) for r in items) / 1000:.3f} |"
        )
    lines += ["", "## Errors and disagreements", ""]
    errors = collections.Counter(
        (r["phase"], r["model"], r.get("error")) for r in rows if not r["ok"]
    )
    for key, n in sorted(errors.items()):
        lines.append(f"- {key}: {n}")
    seen = set()
    for r in rows:
        if r["phase"] not in ("serial", "concurrent_3") or not r["ok"]:
            continue
        for label, correct in r.get("correct", {}).items():
            k = (r["model"], r["case"], label)
            if not correct and k not in seen:
                seen.add(k)
                lines.append(
                    f"- `{r['model']}` / `{r['case']}` / `{label}`: expected {dict(zip(('urgent', 'scam', 'has_event'), fixtures[r['case']][4:]))[label]}, observed probability {r['probabilities'][label]:.4f}."
                )
    lines += [
        "",
        "## Repeat consistency",
        "",
        "Same-prompt prediction consistency across the serial call and two concurrent repetitions (3 calls per fixture/task).",
        "",
        "| Model | Fixture/task groups with any label change | Maximum probability spread |",
        "|---|---:|---:|",
    ]
    for model in models:
        repeats = collections.defaultdict(list)
        for r in rows:
            if (
                r["model"] == model
                and r["phase"] in ("serial", "concurrent_3")
                and r["ok"]
            ):
                repeats[(r["case"], r["task"])].append(r)
        changed = sum(
            len({json.dumps(r["predictions"], sort_keys=True) for r in rr}) > 1
            for rr in repeats.values()
        )
        spread = max(
            (
                max(r["probabilities"][label] for r in rr)
                - min(r["probabilities"][label] for r in rr)
                for rr in repeats.values()
                for label in rr[0]["probabilities"]
            ),
            default=0,
        )
        lines.append(f"| `{model}` | {changed}/{len(repeats)} | {spread:.6f} |")
    cost = sum((r.get("usage", {}).get("cost") or 0) for r in rows)
    lines += [
        "",
        "## Cost and artifacts",
        "",
        f"Sum of reported OpenRouter `usage.cost` for standalone API samples: **${cost:.6f}**. "
        "Workflow API calls, the interrupted exploratory run and ChatGPT subscription usage are excluded from this sum. "
        "No key is present in the artifacts.",
        "",
        "- `calls.jsonl`: checkpointed per-call latency, probabilities, labels, usage and workflow counters.",
        "- `fixtures.json`: synthetic emails and expected labels.",
        "- `summary.json`: machine-readable grouped results and backend configuration.",
        "",
    ]
    (root / "REPORT.md").write_text("\n".join(lines))
    print(root / "REPORT.md")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    render(ap.parse_args().root)
