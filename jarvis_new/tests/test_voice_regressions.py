"""Voice regression gate (Phase 5 slice 5.4): intent paraphrases + cached ears.

Two halves, no mic / server / keys needed:

(a) INTENT — every row of ``tests/voice_corpus.json`` (900+ paraphrases
    + abstain negatives) through ``resolve_intent`` with Needle pinned
    off (deterministic layers 1-3). Asserts expected action + tier
    (act >=0.8 / confirm 0.5-0.8 / ask <0.5) + expected params subset.

(b) EARS — 10 Piper fixture wavs through ``FasterWhisperSTT`` (pinned
    ``base`` reference ear; the size sweep lives in
    ``tests/test_direct_perf.py``). Fixtures render ONCE into
    ``~/.cache/jarvis-regressions/`` and are reused verbatim afterwards
    (Piper renders are nondeterministic run-to-run, so nothing
    re-renders when the cache sidecar matches; no audio is committed).
    WER is scored AFTER expanding digits to words on both sides
    ("5" vs "five" inflates WER ~0.17-0.57 on every model size —
    a scoring artifact, not deafness).

Adding cases: append ``{text, action, params, tier}`` rows to
``tests/voice_corpus.json`` (params may be ``{}``, tier a guess), then
run ``JARVIS_REGRESSION_UPDATE=1 pytest tests/test_voice_regressions.py``
— the hatch fills params/tier from the resolver and FAILS LOUDLY.
Re-run clean to verify. CI must never set the flag.

Full run: intent half is instant (pure functions); ears half is ~30s
CPU (one shared model load + 10 short transcriptions) — budget <5 min.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import numpy as np
import pytest
from livekit import rtc
from test_voice_fixtures import (
    FIXTURE_LENGTH_SCALE,
    FIXTURES,
    fixture_slug,
    render_fixture,
    wav_bytes,
    wer,
)

from intent import needle_router
from intent.resolver import resolve_intent
from local_stt import FasterWhisperSTT

CORPUS_PATH = Path(__file__).with_name("voice_corpus.json")
CACHE_DIR = Path.home() / ".cache" / "jarvis-regressions"
REFERENCE_STT_MODEL = "base"  # stable gate ear; sweep lives in test_direct_perf
PER_FIXTURE_WER_CAP = 0.4
MEAN_WER_CAP = 0.2
EARS_BUDGET_S = 300.0

# 10 cached wavs: 9 unique utterances (command/number/name_place) + the
# seeded -20dB noisy variant. ("Add milk to my shopping list." stays in
# the intent corpus instead — one mouth fewer to feed.)
EARS_IDX = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10]

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}


def _small_num(n: int) -> str:
    """0-999 in words. Pure (fixtures never need more)."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        ten, rest = n // 10 * 10, n % 10
        return _TENS[ten] if rest == 0 else f"{_TENS[ten]} {_ONES[rest]}"
    return f"{_ONES[n // 100]} hundred" + (
        "" if n % 100 == 0 else f" {_small_num(n % 100)}"
    )


def expand_numbers(text: str) -> str:
    """Digits/%/hyphens -> words so WER scores hearing, not formatting. Pure."""
    t = (text or "").lower().replace("%", " percent ").replace("-", " ")

    def _sub(match: re.Match[str]) -> str:
        n = int(match.group(0))
        return f" {_small_num(n)} " if n < 1000 else match.group(0)

    return re.sub(r"\d+", _sub, t)


def tier_of(confidence: float) -> str:
    if confidence >= 0.8:
        return "act"
    if confidence >= 0.5:
        return "confirm"
    return "ask"


def _load_corpus() -> dict:
    with CORPUS_PATH.open() as fh:
        return json.load(fh)


@pytest.fixture(autouse=True)
def needle_disabled(monkeypatch):
    """Pin layers 1-3: the stochastic model stays out of the gate."""
    monkeypatch.setenv("JARVIS_NEEDLE", "0")
    needle_router.reset_agent()
    yield
    needle_router.reset_agent()


def test_update_hatch_rewrites_then_fails_loudly() -> None:
    """Escape hatch: with JARVIS_REGRESSION_UPDATE=1, refill every row's
    action/params/tier from the live resolver, save the corpus, then
    FAIL so CI can never silently pass on rewritten expectations."""
    if os.environ.get("JARVIS_REGRESSION_UPDATE", "") != "1":
        return
    payload = _load_corpus()
    for case in payload["cases"]:
        result = resolve_intent(case["text"])
        case["action"] = result.action
        case["params"] = dict(result.params)
        case["tier"] = tier_of(result.confidence)
    with CORPUS_PATH.open("w") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    pytest.fail(
        f"JARVIS_REGRESSION_UPDATE=1 rewrote {len(payload['cases'])} rows in "
        "voice_corpus.json — re-run WITHOUT the flag to verify."
    )


def test_corpus_schema() -> None:
    payload = _load_corpus()
    assert "meta" in payload and "cases" in payload
    cases = payload["cases"]
    assert len(cases) >= 200, f"corpus too small: {len(cases)}"
    seen: set[str] = set()
    for case in cases:
        assert set(case) >= {"text", "action", "params", "tier"}, case
        assert case["text"].strip() and case["action"].strip()
        assert isinstance(case["params"], dict)
        assert case["tier"] in ("act", "confirm", "ask"), case
        assert case["text"] not in seen, f"duplicate: {case['text']!r}"
        seen.add(case["text"])


def test_resolve_intent_regressions() -> None:
    """Every corpus row resolves to its recorded action+tier+params."""
    mismatches: list[str] = []
    for case in _load_corpus()["cases"]:
        result = resolve_intent(case["text"])
        problems: list[str] = []
        if result.action != case["action"]:
            problems.append(f"action {result.action!r} != {case['action']!r}")
        if tier_of(result.confidence) != case["tier"]:
            problems.append(
                f"tier {tier_of(result.confidence)}({result.confidence}) "
                f"!= {case['tier']}"
            )
        for key, want in case["params"].items():
            if result.params.get(key) != want:
                problems.append(f"param {key}={result.params.get(key)!r} != {want!r}")
        if problems:
            mismatches.append(f"{case['text']!r}: " + "; ".join(problems))
    assert not mismatches, (
        f"{len(mismatches)} corpus mismatches "
        f"(showing {min(20, len(mismatches))}):\n" + "\n".join(mismatches[:20])
    )


def _needs_ears() -> str | None:
    """None when the ears half can run, else the skip reason."""
    from local_voice import DEFAULT_VOICE_PATH

    if not DEFAULT_VOICE_PATH.exists():
        return "voice model not downloaded"
    hub = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / f"models--Systran--faster-whisper-{REFERENCE_STT_MODEL}"
    )
    if not hub.exists():
        return f"whisper {REFERENCE_STT_MODEL} weights not cached (offline box?)"
    return None


def cached_fixture_wav(text: str, kind: str) -> bytes:
    """Render-once wav bytes: cache hit when the sidecar matches. Blocking."""
    slug = fixture_slug(text, kind)
    wav_path = CACHE_DIR / slug
    meta_path = wav_path.with_suffix(".json")
    meta = {"text": text, "kind": kind, "length_scale": FIXTURE_LENGTH_SCALE}
    if wav_path.exists() and meta_path.exists():
        try:
            if json.loads(meta_path.read_text()) == meta:
                return wav_path.read_bytes()
        except Exception:
            pass
    raw = wav_bytes(render_fixture(text, kind))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(raw)
    meta_path.write_text(json.dumps(meta))
    return raw


def test_expand_numbers_unit() -> None:
    assert "five" in expand_numbers("Set a timer for 5 minutes.")
    assert "percent" in expand_numbers("12% of 85")
    assert wer(expand_numbers("five"), expand_numbers("5")) == 0.0
    assert wer("hello world", "hello world") == 0.0


async def test_cached_ears_regressions() -> None:
    """10 cached Piper wavs -> reference ear, digit-blind WER caps."""
    skip = _needs_ears()
    if skip is not None:
        pytest.skip(skip)
    import time

    started = time.monotonic()
    stt = FasterWhisperSTT(model_size=REFERENCE_STT_MODEL)
    wers: list[float] = []
    detail: list[str] = []
    for idx in EARS_IDX:
        fix = FIXTURES[idx]
        raw = await asyncio.to_thread(cached_fixture_wav, fix["text"], fix["kind"])
        pcm = np.frombuffer(raw[44:], dtype=np.int16)
        frame = rtc.AudioFrame(
            data=pcm.tobytes(),
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=len(pcm),
        )
        event = await stt.recognize([frame])
        assert event.type.name == "FINAL_TRANSCRIPT"
        hyp = event.alternatives[0].text
        score = wer(expand_numbers(fix["text"]), expand_numbers(hyp))
        wers.append(score)
        detail.append(f"{fixture_slug(fix['text'], fix['kind'])}: WER {score:.2f}")
        assert score <= PER_FIXTURE_WER_CAP, (
            f"{fix['text']!r} heard as {hyp!r} (WER {score:.2f} "
            f"> cap {PER_FIXTURE_WER_CAP})"
        )
    mean_wer = sum(wers) / len(wers)
    elapsed = time.monotonic() - started
    print(f"\nears detail (model={REFERENCE_STT_MODEL}):\n" + "\n".join(detail))
    print(f"mean WER {mean_wer:.3f}, wall {elapsed:.1f}s")
    assert mean_wer <= MEAN_WER_CAP, f"mean WER {mean_wer:.3f} > {MEAN_WER_CAP}"
    assert elapsed < EARS_BUDGET_S, f"ears took {elapsed:.1f}s > {EARS_BUDGET_S}s"
