#!/usr/bin/env python3
"""Offline fixture benchmark. No model, network, browser, audio or account calls."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path

import system.inbox as inbox
import tools
from latency import VoiceLatencyObserver


def percentiles(values):
    ordered = sorted(values)
    return {
        "n": len(values),
        "p50_ms": ordered[math.ceil(len(values) * 0.5) - 1],
        "p95_ms": ordered[math.ceil(len(values) * 0.95) - 1],
    }


async def benchmark():
    async def slow(_):
        await asyncio.sleep(0.08)
        return None

    async def fast(_):
        await asyncio.sleep(0.005)
        return {
            "say": "fixture result",
            "url": "https://example.invalid",
            "fallback": "brave-api",
        }

    originals = {name: getattr(tools, name) for name in tools._API_PROVIDERS}
    tools.you_search, tools.brave_api_search, tools.ddg_lite_search = slow, fast, slow
    samples = []
    try:
        for _ in range(30):
            start = time.perf_counter()
            result = await tools.api_search_parallel("offline fixture")
            samples.append((time.perf_counter() - start) * 1000)
            assert result["fallback"] == "brave-api"
    finally:
        for name, fn in originals.items():
            setattr(tools, name, fn)
    observer = VoiceLatencyObserver()
    start = time.perf_counter()
    for i in range(10000):
        observer.user_state_changed("speaking", now=i * 10)
        observer.user_state_changed("listening", now=i * 10 + 1)
        observer.agent_state_changed("thinking", now=i * 10 + 2)
        observer.agent_state_changed("speaking", now=i * 10 + 3)
    observer_us = (time.perf_counter() - start) * 1e6 / 10000
    api, token, logger = inbox._gmail_api, inbox._gmail_access_token, inbox.log_action
    local = os.environ.get("JARVIS_LOCAL")
    os.environ["JARVIS_LOCAL"] = "1"
    active = peak = 0
    lock = threading.Lock()

    def metadata(path, *_):
        nonlocal active, peak
        if path == "/messages":
            return {"messages": [{"id": str(i)} for i in range(10)]}
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.005)
            return {"id": path.rsplit("/", 1)[1], "payload": {"headers": []}}
        finally:
            with lock:
                active -= 1

    inbox._gmail_api = metadata
    inbox._gmail_access_token = lambda: "fake-token"
    inbox.log_action = lambda *_: None
    mail_samples = []
    try:
        for _ in range(10):
            start = time.perf_counter()
            await inbox.InboxTools().gmail_inbox(None, n=10)
            mail_samples.append((time.perf_counter() - start) * 1000)
    finally:
        inbox._gmail_api, inbox._gmail_access_token, inbox.log_action = (
            api,
            token,
            logger,
        )
        if local is None:
            os.environ.pop("JARVIS_LOCAL", None)
        else:
            os.environ["JARVIS_LOCAL"] = local
    return {
        "search_slow_unused_provider": percentiles(samples),
        "observer_us_per_four_event_turn": observer_us,
        "ten_metadata_reads": {**percentiles(mail_samples), "peak_active_reads": peak},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "label": args.label,
        "scope": "offline synthetic I/O; not live voice latency",
        "fixture": {
            "slow_unused_provider_ms": 80,
            "valid_provider_ms": 5,
            "rounds": 30,
            "metadata_read_ms": 5,
            "metadata_rounds": 10,
        },
        "source_sha256": {
            name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for name, module in (("tools", tools), ("inbox", inbox))
        },
        **asyncio.run(benchmark()),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
