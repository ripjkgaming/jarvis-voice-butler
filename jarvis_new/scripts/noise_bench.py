"""Speech recognition accuracy in a loud canteen: babble + Jarvis's own echo.

Offline and local. User commands are the Piper fixtures from
tests/test_voice_fixtures.py (en_GB-alan). Noise is synthesized:

- babble: six "talkers" built from a second Piper voice (en_US-lessac),
  each pitch/tempo shifted by a seeded factor, staggered, reverberated
  (RT60 0.8 s) and summed, plus a little clatter. Level set by SNR against the command's active speech.
- echo: a Jarvis-style reply in the second voice through a synthetic room
  (RT60 ~0.3 s), standing in for what leaks past the echo canceller. Level
  set by SER (signal-to-echo ratio).

Real recordings beat synthesis: pass --babble-path / --echo-path WAVs.

Each mix runs through the real mic path at 48 kHz (src/mic_clean.py, so
resampling, model look-ahead and dry mix are all exercised), then local
faster-whisper. Whisper is a proxy for Gemini Live: relative gains carry
over, absolute WER does not.

Stages: raw, and one per denoiser in mic_clean.MODELS. Add more (speaker
gate, ...) to STAGES.

    .venv/bin/python scripts/noise_bench.py --limit 6 --stages raw,dpdfnet
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

import test_voice_fixtures as vf  # noqa: E402
from audio_dsp import Upsampler16to48, downsample_48k_to_16k  # noqa: E402

RATE = vf.TARGET_RATE
OTHER_VOICE = Path.home() / ".local/share/piper-voices/en_US-lessac-medium.onnx"
BABBLE_LINES = [
    "Did you finish the maths homework last night?",
    "Pass me the ketchup, would you?",
    "No way, she actually said that to him?",
    "We have double science after lunch again.",
    "I'm going to the match on Saturday with my cousin.",
    "This pasta is way better than yesterday's.",
    "Can I borrow your charger after class?",
    "The bus was twenty minutes late this morning.",
    "Who's sitting with us at the assembly?",
    "Honestly the test was easier than I expected.",
    "Are you coming to football practice later?",
    "I left my jumper in the music room.",
]
ECHO_LINES = [
    "Of course, sir. Turning that on now.",
    "Your next lesson is chemistry in room fourteen.",
    "I have set a timer for five minutes, sir.",
    "The weather today is cloudy with light rain.",
]
PAD_S = 0.5
INTENT_WER = 0.25


# ---- pure DSP --------------------------------------------------------------


def active_rms(pcm: np.ndarray, frame: int = 320) -> float:
    """RMS over the louder half of 20 ms frames (speech-active level). Pure."""
    x = pcm.astype(np.float64)
    n = len(x) // frame * frame
    if n == 0:
        return float(np.sqrt(np.mean(x**2))) if len(x) else 0.0
    per = np.sqrt(np.mean(x[:n].reshape(-1, frame) ** 2, axis=1))
    loud = per[per >= np.median(per)]
    return float(np.sqrt(np.mean(loud**2))) if len(loud) else 0.0


def scale_to(noise: np.ndarray, ref_rms: float, db: float) -> np.ndarray:
    """Scale noise so its RMS sits db below ref_rms. Pure."""
    rms = float(np.sqrt(np.mean(noise.astype(np.float64) ** 2))) or 1.0
    return noise.astype(np.float64) * (ref_rms * 10.0 ** (-db / 20.0) / rms)


def fit_length(x: np.ndarray, n: int, offset: int = 0) -> np.ndarray:
    """Loop/crop x to exactly n samples starting at offset. Pure."""
    if not len(x):
        return np.zeros(n)
    idx = (np.arange(n) + offset) % len(x)
    return x[idx]


def stretch(x: np.ndarray, factor: float) -> np.ndarray:
    """Resample by factor (>1 = higher, faster voice). Pure."""
    n = max(1, int(len(x) / factor))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)


def room_ir(seed: int, rt60: float = 0.3, rate: int = RATE) -> np.ndarray:
    """Synthetic room impulse response: direct path + decaying tail. Pure."""
    rng = np.random.default_rng(seed)
    n = int(rt60 * rate)
    t = np.arange(n) / rate
    tail = rng.standard_normal(n) * np.exp(-6.9 * t / rt60) * 0.3
    tail[0] = 1.0
    return tail / np.sqrt(np.sum(tail**2))


def make_babble(
    n: int, seed: int, lines: list[np.ndarray], talkers: int = 6, rt60: float = 0.8
) -> np.ndarray:
    """Sum of `talkers` shifted voices, each a seeded sentence sequence
    heard across a reverberant hall (canteens run ~0.8 s RT60), so other
    talkers arrive diffuse while the user, close to the laptop, is direct. Pure."""
    rng = np.random.default_rng(seed)
    out = np.zeros(n)
    for _ in range(talkers):
        factor = float(rng.uniform(0.82, 1.22))
        parts, total = [], 0
        while total < n + RATE:
            line = stretch(lines[int(rng.integers(len(lines)))].astype(np.float64), factor)
            gap = np.zeros(int(rng.uniform(0.05, 0.4) * RATE))
            parts += [line, gap]
            total += len(line) + len(gap)
        talker = np.concatenate(parts)
        start = int(rng.integers(0, RATE))
        talker = talker[start : start + n]
        if rt60:
            talker = np.convolve(talker, room_ir(int(rng.integers(1 << 30)), rt60))[: len(talker)]
        out[: len(talker)] += talker * rng.uniform(0.6, 1.0)
    clatter = rng.standard_normal(n) * (rng.random(n) < 0.002) * 3.0  # cutlery ticks
    clatter = np.convolve(clatter, np.exp(-np.arange(80) / 15.0), mode="same")
    out = out / (np.sqrt(np.mean(out**2)) or 1.0)
    return out + clatter * 0.18


def mix(
    speech: np.ndarray,
    babble: np.ndarray | None,
    snr_db: float | None,
    echo: np.ndarray | None,
    ser_db: float | None,
) -> np.ndarray:
    """Padded speech + babble at snr_db + echo at ser_db, int16. Pure."""
    pad = np.zeros(int(PAD_S * RATE))
    s = np.concatenate([pad, speech.astype(np.float64), pad])
    ref = active_rms(speech)
    out = s.copy()
    if babble is not None and snr_db is not None:
        out += scale_to(fit_length(babble, len(s)), ref, snr_db)
    if echo is not None and ser_db is not None:
        out += scale_to(fit_length(echo, len(s)), ref, ser_db)
    return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


# ---- rendering (cached) ----------------------------------------------------


def _render_other(text: str, length_scale: float = 1.0) -> np.ndarray:
    key = hashlib.sha1(f"babble-v1|lessac|{length_scale}|{text}".encode()).hexdigest()
    path = vf.CACHE_DIR / f"{key}.wav"
    if path.exists():
        return vf.read_wav_pcm(path)
    from local_voice import PiperTTS

    plugin = PiperTTS(model_path=OTHER_VOICE, length_scale=length_scale)
    raw = np.frombuffer(plugin._render_sentence(text), dtype=np.int16)
    pcm = vf.resample_to_16k(raw, plugin.sample_rate)
    vf.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(vf.wav_bytes(pcm))
    return pcm


def load_wav_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wh:
        ch, rate, width = wh.getnchannels(), wh.getframerate(), wh.getsampwidth()
        data = np.frombuffer(wh.readframes(wh.getnframes()), dtype=np.int16 if width == 2 else np.int32)
    x = data.reshape(-1, ch).mean(axis=1)
    if width == 4:
        x = x / 65536.0
    return vf.resample_to_16k(x.astype(np.int16), rate)


def noise_sources(args) -> tuple[np.ndarray, np.ndarray]:
    seconds = 60 * RATE
    if args.babble_path:
        babble = load_wav_16k(Path(args.babble_path)).astype(np.float64)
    else:
        lines = [_render_other(t, 1.0) for t in BABBLE_LINES]
        babble = make_babble(seconds, args.seed, lines)
    if args.echo_path:
        echo = load_wav_16k(Path(args.echo_path)).astype(np.float64)
    else:
        reply = np.concatenate(
            [np.concatenate([_render_other(t, 1.0), np.zeros(RATE // 4)]) for t in ECHO_LINES]
        ).astype(np.float64)
        echo = np.convolve(reply, room_ir(args.seed))[: len(reply)]
    return babble, echo


# ---- stages ----------------------------------------------------------------


def _through_mic_path(pcm16k: np.ndarray, name: str | None) -> np.ndarray:
    """16k -> 48k -> (cleaner) -> 16k, exactly like the wake client stream."""
    x48 = np.clip(Upsampler16to48().process(pcm16k.astype(np.float32)), -32768, 32767)
    x48 = np.concatenate([x48, np.zeros(48000 // 2, dtype=np.float32)]).astype(np.int16)
    if name is None:
        return downsample_48k_to_16k(x48)
    import mic_clean

    cleaner = mic_clean.MicCleaner(name, block=1536)
    out = []
    for i in range(0, len(x48) - 1535, 1536):
        out += cleaner.process(x48[i : i + 1536].tobytes())
    return downsample_48k_to_16k(np.frombuffer(b"".join(out), dtype=np.int16))


def _stages() -> dict:
    import mic_clean

    stages = {"raw": lambda pcm: _through_mic_path(pcm, None)}
    for name in mic_clean.MODELS:
        stages[name] = lambda pcm, n=name: _through_mic_path(pcm, n)
    return stages


STAGES = _stages()


# ---- run -------------------------------------------------------------------


def _parse_levels(text: str) -> list[float | None]:
    return [None if t.strip() == "none" else float(t) for t in text.split(",")]


async def run(args) -> list[dict]:
    from local_stt import FasterWhisperSTT

    fixtures = [f for f in vf.FIXTURES if f["kind"] in ("command", "number", "name_place")]
    fixtures = list({f["text"]: f for f in fixtures}.values())[: args.limit or None]
    babble, echo = noise_sources(args)
    stt = FasterWhisperSTT(model_size=args.model)
    rows = []
    for i, f in enumerate(fixtures):
        speech = await asyncio.to_thread(vf.render_fixture_cached, f["text"], f["kind"])
        for snr in _parse_levels(args.snr):
            for ser in _parse_levels(args.ser):
                offset = (i * 7919 + int((snr or 0) * 101)) % len(babble)
                mixed = mix(speech, np.roll(babble, -offset), snr, np.roll(echo, -i * 4001), ser)
                for stage in args.stages.split(","):
                    pcm = STAGES[stage](mixed)
                    event = await stt.recognize([vf._frame(pcm)])
                    hyp = event.alternatives[0].text if event.alternatives else ""
                    w = vf.wer_normalized(f["text"], hyp)
                    rows.append(
                        {"text": f["text"], "snr": snr, "ser": ser, "stage": stage,
                         "model": args.model, "hyp": hyp, "wer": w, "intent_ok": w <= INTENT_WER}
                    )
                    if args.save_wavs:
                        d = Path(args.save_wavs)
                        d.mkdir(parents=True, exist_ok=True)
                        name = f"{i:02d}_snr{snr}_ser{ser}_{stage}.wav"
                        (d / name).write_bytes(vf.wav_bytes(pcm))
                    if args.verbose:
                        print(f"{stage:>9} snr={snr} ser={ser} wer={w:.2f} | {hyp}", flush=True)
    return rows


def table(rows: list[dict]) -> str:
    """Per stage: rows = SNR, cols = SER, cells 'WER / intent%'. Pure."""
    out = []
    for stage in dict.fromkeys(r["stage"] for r in rows):
        sub = [r for r in rows if r["stage"] == stage]
        snrs = list(dict.fromkeys(r["snr"] for r in sub))
        sers = list(dict.fromkeys(r["ser"] for r in sub))
        out += [f"\n### {stage}", "| SNR \\ SER | " + " | ".join(str(s) for s in sers) + " |",
                "|---|" + "---|" * len(sers)]
        for snr in snrs:
            cells = []
            for ser in sers:
                c = [r for r in sub if r["snr"] == snr and r["ser"] == ser]
                cells.append(
                    f"WER {np.mean([r['wer'] for r in c]):.2f} / "
                    f"{100 * np.mean([r['intent_ok'] for r in c]):.0f}%"
                )
            out.append(f"| {snr} dB | " + " | ".join(cells) + " |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snr", default="10,5,0,-5", help="babble SNRs in dB; 'none' = no babble")
    ap.add_argument("--ser", default="none,0", help="echo SERs in dB; 'none' = no echo")
    ap.add_argument("--stages", default="raw,gtcrn,dpdfnet")
    ap.add_argument("--model", default="base")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--babble-path")
    ap.add_argument("--echo-path")
    ap.add_argument("--json")
    ap.add_argument("--save-wavs")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    rows = asyncio.run(run(args))
    print(table(rows))
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
