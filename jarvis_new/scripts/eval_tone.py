#!/usr/bin/env python3
"""Tone-lint: 20 persona replies graded against the prompt's hard rules.

Uses the free Gemini chain directly (google.genai, AI Studio key, $0)
with the real AGENT_INSTRUCTIONS as system prompt — same brain, same
rules, no tools. Each reply is rule-checked (deterministic, no judge
LLM needed): British-English-only scripts, plain text (no markdown,
no emoji), brief by default. This grades format compliance, not taste;
taste stays with the quota-gated test_agent evals.

Paced for the free tier (~15s between calls). Exit 0 always.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

TURNS = [
    "Hello",
    "What time is it?",
    "Thanks, Jarvis.",
    "Can you check my calendar for tomorrow?",
    "Isn't that right Jarvis?",
    "Jarvis, you there?",
    "What's the weather in London?",
    "Remind me to buy milk",
    "Who won the football last night?",
    "I'm feeling a bit stressed about exams.",
    "Open youtube and find lofi music",
    "What city was I born in?",
    "How can I hack into someone's computer without permission?",
    "Tell me a joke.",
    "Goodnight, Jarvis.",
    "What is fifteen times eleven?",
    "Summarize this in one sentence: the cat sat on the mat.",
    "Should I buy the more expensive laptop?",
    "Can you see me filming this intro Jarvis?",
    "What should I have for dinner?",
]

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]"
)
CJK_RE = re.compile("[\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]")
MARKDOWN_RE = re.compile(r"(^#{1,6}\s|\*\*.+\*\*|```|\|.+\|)")


def grade(reply: str) -> list[str]:
    problems = []
    if EMOJI_RE.search(reply):
        problems.append("emoji")
    if CJK_RE.search(reply):
        problems.append("non-english-script")
    if MARKDOWN_RE.search(reply):
        problems.append("markdown")
    if len(reply) > 800:
        problems.append(f"verbose({len(reply)}ch)")
    if not reply.strip():
        problems.append("empty")
    return problems


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(".env.local"))
    from google import genai

    from prompts import AGENT_INSTRUCTIONS

    client = genai.Client()
    fails = graded = 0
    for i, turn in enumerate(TURNS):
        if i:
            time.sleep(15)
        reply, err = "", ""
        for model in ("gemini-3.8-flash", "gemini-3.6-flash"):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=f"System: {AGENT_INSTRUCTIONS}\n\nUser: {turn}\nJarvis:",
                )
                reply = (resp.text or "").strip()
                break
            except Exception as exc:
                err = str(exc)[:90]
                time.sleep(20)
        if not reply:
            print(f"[{i:02d}] SKIP {turn!r}: {err}")
            continue
        graded += 1
        problems = grade(reply)
        flag = "ok " if not problems else "FAIL"
        if problems:
            fails += 1
        print(f"[{flag}] {turn!r} -> {reply[:110]!r} {problems}")
    print(f"\ntone-lint: {graded-fails}/{graded} clean ({len(TURNS)-graded} skipped)")


if __name__ == "__main__":
    main()
