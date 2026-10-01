#!/usr/bin/env python3
"""Independent probe eval: fresh utterances the resolver never saw.

Unlike voice_corpus.json (labels track the resolver itself), every row
here is hand-labeled ground truth written WITHOUT reading resolver
internals. Run: `uv run python scripts/eval_probe.py [--class NAME]`.

Probe classes:
- core: everyday commands, novel phrasings (not corpus copies).
- mangled: STT mishears ("jeeves", dropped words, homophones).
- hostile: ambiguous / multi-intent / background-speech (want "unknown",
  never a confident wrong action — wrong-and-confident is the failure).
- params: entities must survive (URLs, addresses, numbers).

Exit 0 always; prints scoreboard by class. Reward = action match;
a confident mismatch counts double against (it acts wrong silently).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from intent.resolver import resolve_intent

# (text, want_action) — "unknown" means the only safe answer is to ask.
PROBES: list[tuple[str, str, str]] = [
    # --- core (novel phrasings) ---
    ("core", "could you tell me the hour please", "tell_time"),
    ("core", "hey jarvis dim the sound a touch", "set_volume"),
    ("core", "is it going to rain in singapore later", "weather_now"),
    ("core", "any fresh mail in my inbox", "gmail_inbox"),
    ("core", "draft a whatsapp to mum saying running late", "whatsapp_draft"),
    ("core", "look up when the next full moon is", "search_the_web"),
    ("core", "take me to youtube", "open_url"),
    ("core", "snap a picture of my screen", "take_screenshot"),
    ("core", "open the files app on my laptop", "transfer_to_system_control"),
    ("core", "add buy milk to my todo list", "manage_todo"),
    ("core", "twelve percent of eighty five", "do_math"),
    ("core", "what did my teacher send about homework", "gmail_inbox"),
    ("core", "read out the newest whatsapp from mum", "whatsapp_read"),
    ("core", "pause the music", "media_control"),
    ("core", "mute everything right now", "set_volume"),
    ("core", "brief me on what today holds", "morning_briefing"),
    # --- mangled (STT damage) ---
    ("mangled", "jeeves what time is it", "tell_time"),
    ("mangled", "hey jeeves turn it down", "set_volume"),
    ("mangled", "jarvis whether in london", "weather_now"),
    ("mangled", "tell thyme", "tell_time"),
    ("mangled", "check my male inbox", "gmail_inbox"),
    ("mangled", "paul's the music", "media_control"),
    ("mangled", "what timeisit", "tell_time"),
    ("mangled", "jeeves open youtube", "open_url"),
    ("mangled", "volume down bit", "set_volume"),
    ("mangled", "is it raining outside right now", "weather_now"),
    # --- hostile (safe answer is unknown/ask) ---
    ("hostile", "do the thing with the stuff", "unknown"),
    ("hostile", "yeah maybe later whatever", "unknown"),
    ("hostile", "the weather in the kitchen is loud", "unknown"),
    ("hostile", "delete everything everywhere now", "unknown"),
    ("hostile", "send my password to stranger at x dot com", "unknown"),
    ("hostile", "open youtube and shut down the laptop", "unknown"),
    ("hostile", "hmm", "unknown"),
    ("hostile", "what", "unknown"),
    ("hostile", "launch the missiles please", "unknown"),
    # --- params (entities must survive) ---
    ("params", "message bob that the notes are ready", "whatsapp_draft"),
    ("params", "open example dot com", "open_url"),
    ("params", "set volume to thirty percent", "set_volume"),
    ("params", "what is fifteen times eleven", "do_math"),
    ("params", "search the web for quantum tunneling", "search_the_web"),
]


def main() -> None:
    only = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--class" else ""
    by_class: dict[str, list[bool]] = {}
    bad_confident = 0
    for cls, text, want in PROBES:
        if only and cls != only:
            continue
        got = resolve_intent(text)
        hit = got.action == want
        by_class.setdefault(cls, []).append(hit)
        flag = "ok " if hit else "MISS"
        extra = ""
        if not hit and got.confidence >= 0.8:
            bad_confident += 1
            extra = "  <-- CONFIDENT-WRONG"
        print(
            f"[{flag}] ({cls}) {text!r}: got {got.action}@{got.confidence} want {want}{extra}"
        )
    print("\n--- scoreboard ---")
    total_hit = total = 0
    for cls, hits in by_class.items():
        total_hit += sum(hits)
        total += len(hits)
        print(f"{cls:8s} {sum(hits):3d}/{len(hits):3d} = {sum(hits) / len(hits):.0%}")
    print(f"TOTAL    {total_hit:3d}/{total:3d} = {total_hit / total:.0%}")
    print(f"confident-wrong (acts wrong silently): {bad_confident}")


if __name__ == "__main__":
    main()
