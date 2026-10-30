"""Reproducible OFFLINE before/after barge-in benchmark.

No real mic/speaker/cloud calls. The latency figures are simulated audio-time
using real frame boundaries, not physical acoustic timing or Gemini RTT.
Piper/faster-whisper provide an optional speech intelligibility check.
"""
import argparse
import copy
import json
import math
import statistics
import time
from pathlib import Path

import numpy as np

from fast_barge_in import MicBargeIn
from talk_gate import TalkOverGate

RATE = 48000
BLOCK = 1536
DT = BLOCK / RATE


def noise(rms, rng):
    x = rng.standard_normal(BLOCK)
    return np.clip(x * (rms / max(1e-8, np.sqrt(np.mean(x * x)))), -30000, 30000).astype(np.int16).tobytes()


def gate_with_ambient(rng):
    gate = TalkOverGate()
    for i in range(250):
        gate.process(np.frombuffer(noise(2400, rng), dtype=np.int16), i * DT)
    return gate


def synthetic_trials(n=100):
    rng = np.random.default_rng(20261030)
    result = {category: {"detected": 0, "onset_byte_exact": 0, "latencies_ms": [],
                         "baseline_exact_pct": [], "new_exact_pct": []}
              for category in ("loud", "moderate", "quiet", "very_quiet", "noise_click",
                               "loud_background_voice")}
    for category in result:
        for _trial in range(n):
            gate = gate_with_ambient(rng)
            old_gate = copy.deepcopy(gate)
            fired = []
            engine = MicBargeIn(gate, on_interrupt=lambda at, target=fired: target.append(at))
            start = 9.0
            seen = []
            incoming = []
            old_outgoing = []
            for i in range(30):
                t = start + i * DT
                gate.note_playback(8000, t)
                old_gate.note_playback(8000, t)
                level = {
                    "loud": 12000,
                    "moderate": 9400,
                    "quiet": 5500,
                    "very_quiet": 2800,
                    "loud_background_voice": 5500,
                    "noise_click": 12000 if i == 10 else 2600,
                }[category]
                if category not in {"noise_click", "loud_background_voice"} and i == 0:
                    level = 2600  # first weak 32 ms of a word
                raw = noise(level, rng)
                incoming.append(raw)
                old_gate_input = np.frombuffer(raw, dtype=np.int16)
                old_result = old_gate.process(old_gate_input, t)
                old_outgoing.append(raw if old_result is old_gate_input else old_result.tobytes())
                seen.extend(engine.feed(raw, t))
            # Simulate silence after Jarvis finishes, releasing withheld tail.
            seen.extend(engine.feed(noise(10, rng), start + 30*DT + 0.5))
            original_pcm = np.frombuffer(b"".join(incoming), dtype=np.int16)
            old_pcm = np.frombuffer(b"".join(old_outgoing), dtype=np.int16)
            new_pcm = np.frombuffer(b"".join(seen[:30]), dtype=np.int16)
            matched = np.array_equal(new_pcm, original_pcm)
            res = result[category]
            res["baseline_exact_pct"].append(100 * float(np.mean(old_pcm == original_pcm)))
            res["new_exact_pct"].append(100 * float(np.mean(new_pcm == original_pcm)))
            res["detected"] += bool(fired)
            res["onset_byte_exact"] += bool(matched)
            if fired:
                res["latencies_ms"].append(round((fired[0] - start)*1000, 2))
    for _category, r in result.items():
        values = r.pop("latencies_ms")
        r["detected_percent"] = round(100 * r["detected"] / n, 1)
        r["baseline_pcm_identical_percent_mean"] = round(statistics.mean(r.pop("baseline_exact_pct")), 2)
        r["new_pcm_identical_percent_mean"] = round(statistics.mean(r.pop("new_exact_pct")), 2)
        r["onset_byte_exact_percent"] = round(100 * r["onset_byte_exact"] / n, 1)
        r["onset_to_local_mute_ms_p50"] = statistics.median(values) if values else None
        r["onset_to_local_mute_ms_p95"] = sorted(values)[math.ceil(.95*len(values))-1] if values else None
    return result


def speech_quality():
    from faster_whisper import WhisperModel

    from audio_dsp import downsample_48k_to_16k
    from audio_shared import load_piper_voice, render_sentence_pcm

    phrase = "Wait, actually compare the Pi four and the Pi five. Which one is cheaper?"
    model_path = Path.home() / ".jarvis/voices/en_GB-alan-medium.onnx"
    voice = load_piper_voice(model_path)
    pcm = render_sentence_pcm(voice, phrase, length_scale=.85)
    source_rate = int(voice.config.sample_rate)
    native = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    source_t = np.arange(len(native)) / source_rate
    target_t = np.arange(round(len(native) * RATE / source_rate)) / RATE
    signal = np.interp(target_t, source_t, native)
    # Moderate foreground level; source remains fully intelligible with
    # small ambient noise and aligned 32 ms real-time mic frames.
    signal = signal * (11500.0 / max(1.0, np.sqrt(np.mean(signal**2))))
    rng = np.random.default_rng(3091)
    pad = (-len(signal)) % BLOCK
    signal = np.pad(signal, (0, pad))
    original = []
    old = []
    new = []
    gate_old, gate_new = gate_with_ambient(np.random.default_rng(881)), gate_with_ambient(np.random.default_rng(881))
    fired = []
    engine = MicBargeIn(gate_new, on_interrupt=lambda t: fired.append(t))
    start = 9.0
    for i in range(len(signal)//BLOCK):
        t = start + i * DT
        ambient = np.frombuffer(noise(750, rng), dtype=np.int16)
        raw = np.clip(signal[i*BLOCK:(i+1)*BLOCK] + ambient, -32768, 32767).astype(np.int16)
        gate_old.note_playback(8000, t)
        gate_new.note_playback(8000, t)
        old_out = gate_old.process(raw, t)
        original.append(raw)
        old.append(old_out)
        new.extend([np.frombuffer(x, dtype=np.int16) for x in engine.feed(raw.tobytes(), t)])
    # Make sure a pending tail is forwarded after the old speaker stops.
    new.extend([np.frombuffer(x, dtype=np.int16) for x in engine.feed(np.zeros(BLOCK, dtype=np.int16).tobytes(), start + len(signal)/RATE + .5)])
    original_wave = np.concatenate(original).astype(np.int16)
    old_wave = np.concatenate(old).astype(np.int16)
    new_wave = np.concatenate(new).astype(np.int16)
    # Trim artificial trailing 32ms flush block for parity.
    new_wave = new_wave[:len(original_wave)]
    m = WhisperModel("base.en", device="cpu", compute_type="int8", local_files_only=True)
    def transcribe(x):
        pcm16 = downsample_48k_to_16k(x)
        parts, _ = m.transcribe(pcm16.astype(np.float32) / 32768, language="en", beam_size=1,
                                vad_filter=False, condition_on_previous_text=False)
        return " ".join(part.text.strip() for part in parts)
    return {
        "prompt": phrase,
        "clean_transcription": transcribe(original_wave),
        "current_gate_transcription": transcribe(old_wave),
        "fast_barge_transcription": transcribe(new_wave),
        "fast_barge_fired": bool(fired),
        "microphone_audio_duration_s": round(len(original_wave)/RATE, 2),
        "sample_exact_fraction": round(float(np.mean(new_wave == original_wave)), 5),
        "baseline_exact_fraction": round(float(np.mean(old_wave == original_wave)), 5),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=80)
    parser.add_argument("--with-stt", action="store_true")
    parser.add_argument("--output", default="benchmarks/barge-in-offline-2026-10-30.json")
    args = parser.parse_args()
    t0 = time.monotonic()
    out = {
        "kind": "offline_deterministic_mic_and_audio_frame_simulation",
        "real_world_mic_to_speaker_latency_measured": False,
        "audio_frame_duration_ms": 32,
        "playback_callback_target_ms": 10,
        "new_pcm_queue_max_ms": 250,
        "baseline_independent_local_mute": False,
        "baseline_native_provider_delay_ms": "unknown/not measured",
        "trials_each": args.trials,
        "cases": synthetic_trials(args.trials),
    }
    if args.with_stt:
        out["tts_whisper"] = speech_quality()
    out["elapsed_wall_s"] = round(time.monotonic() - t0, 2)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, indent=2)+"\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
