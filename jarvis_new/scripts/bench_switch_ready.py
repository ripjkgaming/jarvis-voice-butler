"""Trigger-to-ready (ms) for character switches, cold vs warm voice bank.

Models the real path: exit line rendered in the OLD voice concurrently with the
target prewarm (two threads, one bank, as the wake client does). Real models.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PAIRS = [("jarvis", "ultron"), ("jarvis", "friday"), ("ultron", "friday")]


def trial(cv, chars, a, b, bank, exit_line="Standing down, Sir."):
    t = time.perf_counter()
    exit_t = threading.Thread(target=bank.render_pcm, args=(exit_line, chars[a]))
    exit_t.start()
    ok = bank.prewarm(chars[b])
    ready = (time.perf_counter() - t) * 1000
    exit_t.join()
    return ready, ok, (time.perf_counter() - t) * 1000


def main() -> None:
    import character_voice as cv
    import characters

    chars = characters.load_all()
    motion = characters.motion()
    out = {"ready_gate_ms": motion["ready_gate_ms"],
           "prewarm_timeout_ms": motion["prewarm_timeout_ms"], "pairs": {}}
    for a, b in PAIRS:
        row = {}
        bank = cv.VoiceBank()
        ready, ok, both = trial(cv, chars, a, b, bank)  # cold: nothing loaded
        row["cold"] = {"ready_ms": round(ready), "ok": ok, "exit_and_ready_ms": round(both)}
        ready, ok, both = trial(cv, chars, a, b, bank)  # warm: same bank again
        row["warm"] = {"ready_ms": round(ready), "ok": ok, "exit_and_ready_ms": round(both)}
        row["cold_within_gate"] = row["cold"]["ready_ms"] <= motion["ready_gate_ms"]
        row["cold_within_timeout"] = row["cold"]["ready_ms"] <= motion["prewarm_timeout_ms"]
        out["pairs"][f"{a}->{b}"] = row
        print(f"{a}->{b}: cold {row['cold']['ready_ms']} ms, warm {row['warm']['ready_ms']} ms")
    dest = ROOT / "benchmarks" / "switch-ready.json"
    dest.write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
