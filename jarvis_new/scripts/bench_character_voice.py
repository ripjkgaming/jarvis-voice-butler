"""Benchmark character voices: cold load + warm per-sentence render (ms)."""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SENTENCES = [
    "Good evening, Sir.",
    "All systems are operating within normal parameters.",
    "I have finished the analysis you requested.",
    "There are no strings on me.",
    "Shall I proceed with the deployment?",
]


def main() -> None:
    import character_voice
    import characters

    chars = characters.load_all()
    results = {}
    for cid, char in chars.items():
        bank = character_voice.VoiceBank()
        t = time.perf_counter()
        bank.get_voice(char)
        cold_load = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        ok = bank.prewarm(char)
        first = (time.perf_counter() - t) * 1000
        per = []
        audio_ms = 0.0
        for s in SENTENCES:
            t = time.perf_counter()
            pcm, sr = bank.render_pcm(s, char)
            per.append((time.perf_counter() - t) * 1000)
            audio_ms += len(pcm) / sr * 1000
        results[cid] = {
            "model": char.voice["model"],
            "cold_load_ms": round(cold_load, 1),
            "prewarm_ms": round(first, 1),
            "prewarm_ok": ok,
            "warm_ms_per_sentence": [round(x, 1) for x in per],
            "warm_mean_ms": round(statistics.mean(per), 1),
            "warm_max_ms": round(max(per), 1),
            "rtf": round(sum(per) / audio_ms, 3) if audio_ms else None,
        }
    print(f"{'char':10}{'model':22}{'cold':>9}{'prewarm':>9}{'mean':>8}{'max':>8}{'rtf':>7}")
    for cid, r in results.items():
        print(f"{cid:10}{r['model']:22}{r['cold_load_ms']:9}{r['prewarm_ms']:9}"
              f"{r['warm_mean_ms']:8}{r['warm_max_ms']:8}{r['rtf']:7}")
    out = ROOT / "benchmarks" / "character-voices.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"sentences": SENTENCES, "results": results}, indent=2))


if __name__ == "__main__":
    main()
