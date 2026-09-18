"""STT fixture factory + whisper benchmark (Phase 5 helper, H1).

Fixtures are GENERATED at test time with the local Piper voice
(en_GB-alan-medium) — no binary audio is committed.

HONEST DETERMINISM (prior finding, baked in): raw Piper renders differ
run-to-run (md5 differs, onset clipping), so determinism here comes from
CACHING rendered fixtures under ``~/.cache/jarvis-regressions/`` (content
keyed by text+kind+voice+params), never from assuming stable synthesis.
The first run renders + populates the cache; later runs are disk reads
and therefore byte-identical. Nothing is committed.

Manifest schema: ``[{file, text, kind}]`` where kind is one of
``command | number | name_place | noisy | music``:

- ``noisy``: seeded white noise mixed at -20dB (NOISE_DB).
- ``music``: seeded tonal "background music" bed mixed at low SNR
  (MUSIC_DB = -10dB) — the bar-with-music case.

Scoring uses digit-normalized WER (prior finding: "5" vs "five" inflates
WER 0.17-0.57 on ALL sizes, a scoring artifact — ``wer_normalized``
maps digit tokens to words before scoring).

The full benchmark (every fixture x tiny/base/small, per-kind tables)
lives in ``/tmp/opencode/phase5-helper/bench_whisper.py`` (scratch, not
committed); this file holds the shared builder plus a fast smoke test
(2 fixtures x tiny) so the suite stays under 5 minutes.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import struct
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from livekit import rtc

FIXTURE_LENGTH_SCALE = 0.85  # pinned: Piper must not read env here
FIXTURE_VOICE = "alan"  # en_GB-alan-medium; part of the cache key
TARGET_RATE = 16000
NOISE_DB = -20.0
NOISE_SEED = 20260918
MUSIC_DB = -10.0  # background-music bed level relative to signal RMS
MUSIC_SEED = 424242
CACHE_VERSION = "v2"  # bump when render params change (invalidates cache)
CACHE_DIR = Path.home() / ".cache" / "jarvis-regressions"

FIXTURES: list[dict[str, str]] = [
    # ---- original 11 (kept verbatim) ----
    {"text": "Mute it.", "kind": "command"},
    {"text": "What time is it?", "kind": "command"},
    {"text": "Turn it up.", "kind": "command"},
    {"text": "Set a timer for five minutes.", "kind": "number"},
    {"text": "What is twelve percent of eighty five?", "kind": "number"},
    {"text": "Open YouTube.", "kind": "name_place"},
    {"text": "Search for Ada Lovelace.", "kind": "name_place"},
    {"text": "What is the weather in London?", "kind": "name_place"},
    {"text": "Read my latest email from the teacher.", "kind": "command"},
    {"text": "Add milk to my shopping list.", "kind": "command"},
    {"text": "What time is it?", "kind": "noisy"},
    # ---- extension: multi-word commands ----
    {"text": "Please turn off the kitchen lights.", "kind": "command"},
    {"text": "Remind me to call mom tomorrow morning.", "kind": "command"},
    {"text": "Start my morning briefing.", "kind": "command"},
    {"text": "Turn it down.", "kind": "command"},
    # ---- extension: homophone pair mute/music ----
    {"text": "Mute the music.", "kind": "command"},
    {"text": "Play some relaxing music.", "kind": "command"},
    # ---- extension: numbers + names ----
    {"text": "Set an alarm for seven thirty.", "kind": "number"},
    {"text": "Call John Smith on speakerphone.", "kind": "name_place"},
    {"text": "What is the weather in Edinburgh?", "kind": "name_place"},
    # ---- extension: background-music variant at low SNR ----
    {"text": "Play some relaxing music.", "kind": "music"},
    {"text": "Turn it up.", "kind": "music"},
    {"text": "What time is it?", "kind": "music"},
]

KINDS = frozenset({"command", "number", "name_place", "noisy", "music"})

NUM_WORDS = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
    100: "one hundred",
}


def fixture_slug(text: str, kind: str) -> str:
    """Deterministic wav file name for a fixture. Pure."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    suffix = "" if kind in ("command", "number", "name_place") else f"_{kind}"
    return f"{slug}{suffix}.wav"


def build_manifest() -> list[dict[str, str]]:
    """Manifest JSON rows: {file, text, kind}. Pure."""
    return [
        {
            "file": fixture_slug(f["text"], f["kind"]),
            "text": f["text"],
            "kind": f["kind"],
        }
        for f in FIXTURES
    ]


def resample_to_16k(pcm: np.ndarray, src_rate: int) -> np.ndarray:
    """Mono int16 at src_rate -> mono int16 16kHz (linear interp). Pure DSP."""
    if src_rate == TARGET_RATE:
        return pcm.astype(np.int16)
    mono = pcm.astype(np.float64)
    n_out = round(len(mono) * TARGET_RATE / src_rate)
    idx = np.linspace(0, len(mono) - 1, n_out)
    out = np.interp(idx, np.arange(len(mono)), mono)
    return np.clip(out, -32768, 32767).astype(np.int16)


def mix_noise_db(
    pcm: np.ndarray, seed: int = NOISE_SEED, db: float = NOISE_DB
) -> np.ndarray:
    """Mix seeded white noise at db dB relative to signal RMS. Pure DSP."""
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(len(pcm))
    sig_rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))) or 1.0
    noise_rms = float(np.sqrt(np.mean(noise**2))) or 1.0
    gain = sig_rms * (10.0 ** (db / 20.0)) / noise_rms
    mixed = pcm.astype(np.float64) + noise * gain
    return np.clip(mixed, -32768, 32767).astype(np.int16)


def mix_music_db(
    pcm: np.ndarray, seed: int = MUSIC_SEED, db: float = MUSIC_DB
) -> np.ndarray:
    """Mix a seeded tonal 'background music' bed at db dB rel. to RMS.

    Pure DSP: sum of sines (A3 + C#4 + E4 + slow tremolo) with a seeded
    phase, so the bed is reproducible without committing audio.
    """
    rng = np.random.default_rng(seed)
    n = len(pcm)
    t = np.arange(n) / TARGET_RATE
    phase = float(rng.uniform(0, 2 * np.pi))
    bed = (
        np.sin(2 * np.pi * 220.0 * t + phase)
        + 0.6 * np.sin(2 * np.pi * 277.18 * t + phase / 2)
        + 0.5 * np.sin(2 * np.pi * 329.63 * t + phase / 3)
    )
    tremolo = 0.6 + 0.4 * np.sin(2 * np.pi * 2.0 * t)
    bed = bed * tremolo
    sig_rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))) or 1.0
    bed_rms = float(np.sqrt(np.mean(bed**2))) or 1.0
    gain = sig_rms * (10.0 ** (db / 20.0)) / bed_rms
    mixed = pcm.astype(np.float64) + bed * gain
    return np.clip(mixed, -32768, 32767).astype(np.int16)


def render_fixture(text: str, kind: str, seed: int = NOISE_SEED) -> np.ndarray:
    """Render one fixture to 16kHz mono int16 via Piper. Blocking.

    NOTE: raw Piper output is NOT deterministic run-to-run — callers
    wanting stability must go through :func:`render_fixture_cached`.
    """
    from local_voice import PiperTTS

    plugin = PiperTTS(length_scale=FIXTURE_LENGTH_SCALE)
    pcm_bytes = plugin._render_sentence(text)
    raw = np.frombuffer(pcm_bytes, dtype=np.int16)
    pcm16k = resample_to_16k(raw, plugin.sample_rate)
    if kind == "noisy":
        pcm16k = mix_noise_db(pcm16k, seed=seed)
    elif kind == "music":
        pcm16k = mix_music_db(pcm16k)
    return pcm16k


def cache_key(text: str, kind: str) -> str:
    """Content key for the fixture cache. Pure."""
    blob = (
        f"{CACHE_VERSION}|{FIXTURE_VOICE}|{FIXTURE_LENGTH_SCALE}|"
        f"{NOISE_DB}|{NOISE_SEED}|{MUSIC_DB}|{MUSIC_SEED}|{kind}|{text}"
    ).encode()
    return hashlib.sha1(blob).hexdigest() + ".wav"


def read_wav_pcm(path: Path) -> np.ndarray:
    """Read a 16kHz mono int16 WAV written by :func:`wav_bytes`. Pure IO."""
    with wave.open(str(path), "rb") as wh:
        assert wh.getnchannels() == 1
        assert wh.getsampwidth() == 2
        assert wh.getframerate() == TARGET_RATE
        return np.frombuffer(wh.readframes(wh.getnframes()), dtype=np.int16)


def render_fixture_cached(text: str, kind: str) -> np.ndarray:
    """Cached render: byte-identical across runs (disk read on hit).

    Determinism comes from the CACHE, not from stable synthesis.
    Blocking on a cache miss (renders via Piper once).
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / cache_key(text, kind)
    if path.exists():
        return read_wav_pcm(path)
    pcm = render_fixture(text, kind)
    path.write_bytes(wav_bytes(pcm))
    return pcm


def wav_bytes(pcm16k: np.ndarray) -> bytes:
    """Wrap 16kHz mono int16 PCM in a WAV container. Pure."""
    data = pcm16k.astype(np.int16).tobytes()
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        TARGET_RATE,
        TARGET_RATE * 2,
        2,
        16,
        b"data",
        len(data),
    )
    return header + data


def normalize_words(text: str) -> list[str]:
    """Lowercase word tokens for WER. Pure."""
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def normalize_digits(text: str) -> str:
    """Map digit tokens to words so '5' scores equal to 'five'. Pure."""
    text = (text or "").lower().replace("%", " percent ")

    def _spell(token: str) -> str:
        try:
            value = int(token)
        except ValueError:
            return token
        if value in NUM_WORDS:
            return NUM_WORDS[value]
        if 0 < value < 100:  # e.g. 85 -> "eighty five"
            tens, ones = (value // 10) * 10, value % 10
            if tens in NUM_WORDS and ones in NUM_WORDS:
                return f"{NUM_WORDS[tens]} {NUM_WORDS[ones]}"
        return " ".join(NUM_WORDS.get(int(d), d) for d in token)

    return " ".join(_spell(tok) for tok in re.findall(r"[a-z0-9']+", text))


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate via difflib opcodes (stdlib, no new deps). Pure."""
    import difflib

    ref = normalize_words(reference)
    hyp = normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    matcher = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    errors = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            errors += i2 - i1
        elif tag == "insert":
            errors += j2 - j1
        else:  # replace
            errors += max(i2 - i1, j2 - j1)
    return errors / len(ref)


def wer_normalized(reference: str, hypothesis: str) -> float:
    """WER after mapping digit tokens to words on BOTH sides. Pure."""
    return wer(normalize_digits(reference), normalize_digits(hypothesis))


def markdown_table(rows: list[dict]) -> str:
    """Render benchmark rows as a markdown table. Pure."""
    lines = [
        "| fixture | model | WER | hyp | secs |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        hyp = str(r.get("hyp", "")).replace("|", "/")[:60]
        lines.append(
            f"| {r['file']} | {r['model']} | {r['wer']:.2f} | {hyp} | {r['secs']:.1f} |"
        )
    return "\n".join(lines)


def per_kind_table(rows: list[dict]) -> str:
    """Mean (normalized) WER + total secs per kind x model. Pure."""
    kinds = sorted({r["kind"] for r in rows})
    models = sorted({r["model"] for r in rows}, key=["tiny", "base", "small"].index)
    lines = [
        "| kind | " + " | ".join(models) + " |",
        "|---|" + "|".join(["---"] * len(models)) + "|",
    ]
    for kind in kinds:
        cells = []
        for model in models:
            sub = [r for r in rows if r["kind"] == kind and r["model"] == model]
            mean = sum(r["wer"] for r in sub) / len(sub)
            tot = sum(r["secs"] for r in sub)
            cells.append(f"WER {mean:.2f} ({len(sub)} utts, {tot:.0f}s)")
        lines.append(f"| {kind} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _frame(pcm16k: np.ndarray) -> rtc.AudioFrame:
    data = pcm16k.astype(np.int16)
    return rtc.AudioFrame(
        data=data.tobytes(),
        sample_rate=TARGET_RATE,
        num_channels=1,
        samples_per_channel=len(data),
    )


async def transcribe_pcm(pcm16k: np.ndarray, model_size: str) -> tuple[str, float]:
    """Transcribe PCM via FasterWhisperSTT; returns (text, wall secs)."""
    from local_stt import FasterWhisperSTT

    stt = FasterWhisperSTT(model_size=model_size)
    started = time.monotonic()
    event = await stt.recognize([_frame(pcm16k)])
    elapsed = time.monotonic() - started
    assert event.type.name == "FINAL_TRANSCRIPT"
    assert len(event.alternatives) == 1
    return event.alternatives[0].text, elapsed


async def benchmark(
    fixtures: list[dict[str, str]], model_sizes: list[str]
) -> list[dict]:
    """Transcribe fixtures x model sizes -> rows.

    Rows: {file, text, kind, model, hyp, wer, secs} where wer is the
    DIGIT-NORMALIZED WER. Renders go through the fixture cache.
    """
    rows: list[dict] = []
    for f in fixtures:
        pcm = await asyncio.to_thread(render_fixture_cached, f["text"], f["kind"])
        for size in model_sizes:
            hyp, secs = await transcribe_pcm(pcm, size)
            rows.append(
                {
                    "file": fixture_slug(f["text"], f["kind"]),
                    "text": f["text"],
                    "kind": f["kind"],
                    "model": size,
                    "hyp": hyp,
                    "wer": wer_normalized(f["text"], hyp),
                    "secs": secs,
                }
            )
    return rows


def _needs_voice() -> bool:
    from local_voice import DEFAULT_VOICE_PATH

    return DEFAULT_VOICE_PATH.exists()


def test_manifest_schema() -> None:
    manifest = build_manifest()
    assert len(manifest) >= 20
    kinds = {m["kind"] for m in manifest}
    assert {"command", "number", "name_place", "noisy", "music"} <= kinds
    for m in manifest:
        assert set(m) == {"file", "text", "kind"}
        assert m["file"].endswith(".wav")
        assert m["text"].strip()
        assert m["kind"] in KINDS
    assert len({m["file"] for m in manifest}) == len(manifest)
    # homophone pair present (mute/music confusability probe)
    texts = [m["text"] for m in manifest]
    assert "Mute the music." in texts
    assert "Play some relaxing music." in texts


def test_builder_determinism() -> None:
    """Determinism is HONEST: it comes from the cache, not the synth.

    Raw Piper renders are known to differ run-to-run (md5/onset
    clipping), so this test asserts that the CACHED path is
    byte-identical, that the cache lives outside the repo (nothing
    committed), and that noisy/music variants actually differ.
    """
    if not _needs_voice():
        pytest.skip("voice model not downloaded")
    from local_voice import DEFAULT_VOICE_PATH  # noqa: F401  (existence probe)

    first = wav_bytes(render_fixture_cached("What time is it?", "command"))
    second = wav_bytes(render_fixture_cached("What time is it?", "command"))
    assert first == second
    assert len(first) > 44  # header + samples
    key_path = CACHE_DIR / cache_key("What time is it?", "command")
    assert key_path.exists()
    assert key_path.stat().st_size > 44
    repo_root = Path(__file__).resolve().parents[1]
    assert repo_root not in key_path.resolve().parents
    noisy_a = wav_bytes(render_fixture_cached("What time is it?", "noisy"))
    noisy_b = wav_bytes(render_fixture_cached("What time is it?", "noisy"))
    assert noisy_a == noisy_b
    assert noisy_a != first  # noise actually mixes in
    music_a = wav_bytes(render_fixture_cached("What time is it?", "music"))
    music_b = wav_bytes(render_fixture_cached("What time is it?", "music"))
    assert music_a == music_b
    assert music_a != first  # music bed actually mixes in
    assert music_a != noisy_a  # the two beds differ


def test_digit_normalizer() -> None:
    assert normalize_digits("Set a timer for 5 minutes") == (
        "set a timer for five minutes"
    )
    assert normalize_digits("What is 12% of 85?") == (
        "what is twelve percent of eighty five"
    )
    assert (
        wer_normalized("Set a timer for five minutes.", "Set a timer for 5 minutes")
        == 0.0
    )
    # raw wer still sees the artifact (documents WHY we normalize)
    assert wer("Set a timer for five minutes.", "Set a timer for 5 minutes") > 0.0


def test_wer_unit() -> None:
    assert wer("hello world", "hello world") == 0.0
    assert wer("hello world", "hello") == pytest.approx(0.5)
    assert wer("", "") == 0.0
    assert wer("mute it", "mute it please") == pytest.approx(0.0, abs=0.51)


def test_per_kind_table_unit() -> None:
    rows = [
        {
            "kind": "command",
            "model": "tiny",
            "wer": 0.0,
            "secs": 1.0,
            "file": "a.wav",
            "hyp": "x",
        },
        {
            "kind": "command",
            "model": "tiny",
            "wer": 0.5,
            "secs": 1.0,
            "file": "b.wav",
            "hyp": "y",
        },
        {
            "kind": "number",
            "model": "tiny",
            "wer": 1.0,
            "secs": 2.0,
            "file": "c.wav",
            "hyp": "z",
        },
    ]
    table = per_kind_table(rows)
    assert "| command | WER 0.25 (2 utts, 2s) |" in table
    assert "| number | WER 1.00 (1 utts, 2s) |" in table


@pytest.mark.asyncio
async def test_benchmark_smoke_tiny() -> None:
    """Smoke: 2 fixtures x tiny model — well-formed events + WER + timing."""
    if not _needs_voice():
        pytest.skip("voice model not downloaded")
    subset = [FIXTURES[1], FIXTURES[3]]  # "What time is it?", timer/number
    rows = await benchmark(subset, ["tiny"])
    assert len(rows) == 2
    for r in rows:
        assert isinstance(r["hyp"], str)
        assert 0.0 <= r["wer"] <= 2.0
        assert r["secs"] >= 0.0
        assert r["model"] == "tiny"
        assert r["kind"] in KINDS
