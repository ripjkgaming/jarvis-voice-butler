#!/usr/bin/env python3
"""Opt-in live smoke test: jarvis_rt voice path against the real Gemini Live API.

Not a pytest test. It calls the live API, spends quota, and needs network.
It refuses to run unless GOOGLE_API_KEY is set in the environment (this file
does not read .env.local). The key value is never printed.

Stack: AgentServer + VoiceHub on loopback, AgentSession with LiveBackend
(the production model chain), and a caller VoiceClient. The JARVIS_HOME is a
temporary directory, so the real voice secret is never touched.

Behaviours, each printed as PASS or FAIL:
  reply      speaking the input gets spoken agent audio back
  tool       the tell_time tool is executed and its response goes back to the model
  interrupt  speaking over a long reply stops it (the hub sends a flush)

Input: --wav PATH (16-bit PCM WAV, any rate, mono or stereo) is spoken for the
reply and interrupt behaviours. Without it a 1.2 s 220 Hz tone is used. A tone
is not speech, so reply and interrupt will usually FAIL in that mode: it only
shows that the plumbing runs. Supply real speech for a meaningful result.

Usage:
    GOOGLE_API_KEY=... uv run python scripts/rt_live_smoke.py --wav question.wav
Exit status: 0 when every behaviour passes, 1 otherwise, 2 when refused.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from voice_latency import (  # noqa: E402
    MIC_RATE,
    Caller,
    Mic,
    Stack,
    resample_16k,
    tone,
    wait_len,
)

from jarvis_rt.live import http_options  # noqa: E402
from voice_upgrade import DEFAULT_CHAIN, selected_chain  # noqa: E402

TIMEOUT_S = 25.0
REPLY_REQUEST = "Count slowly from one to thirty, one number per line."
TOOL_REQUEST = (
    "Call the tell_time tool now, then tell Sir the time in one short sentence."
)


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("WAV must be 16-bit PCM")
        channels = wav.getnchannels()
        rate = wav.getframerate()
        raw = np.frombuffer(wav.readframes(wav.getnframes()), np.int16)
    if channels > 1:
        raw = raw.reshape(-1, channels)[:, 0]
    return resample_16k(raw, rate)


def speech_clip(wav: Path | None) -> np.ndarray:
    # No trailing silence here: the caller's mic supplies it after the speech,
    # so the speech-end timestamp marks the last voiced block.
    return load_wav(wav) if wav is not None else tone(1.2, MIC_RATE, freq=220.0)


def report(name: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)
    return ok


async def smoke(wav: Path | None) -> bool:
    if "pytest" in sys.modules:
        raise SystemExit("rt_live_smoke.py is an opt-in live check, not a pytest test")
    speech = speech_clip(wav)
    results: list[bool] = []
    with tempfile.TemporaryDirectory(prefix="jarvis-rt-smoke-") as home:
        async with Stack(
            models=selected_chain(DEFAULT_CHAIN),
            fake=None,
            http=http_options(),
            home=Path(home),
        ) as st:
            session = st.session
            mic = Mic(st.client)
            caller = Caller(st.client)
            mic.start()
            caller.start()
            try:
                await caller.quiet(1.0, TIMEOUT_S, session)

                _, end = await mic.speak(speech)
                first = await caller.first_voiced_after(end, TIMEOUT_S)
                if first is None:
                    results.append(
                        report(
                            "reply", False, f"no agent audio within {TIMEOUT_S:.0f}s"
                        )
                    )
                else:
                    results.append(
                        report(
                            "reply",
                            True,
                            f"agent audio {first - end:.2f}s after speech end",
                        )
                    )
                await caller.quiet(1.0, TIMEOUT_S, session)

                calls_before = len(st.tool_calls_at)
                responses_before = len(st.tool_responses_at)
                session.generate_reply(instructions=TOOL_REQUEST)
                answered = await wait_len(
                    st.tool_responses_at, responses_before + 1, TIMEOUT_S
                )
                ran = len(st.tool_calls_at) > calls_before
                results.append(
                    report(
                        "tool",
                        answered and ran,
                        "tell_time executed and its response was sent"
                        if answered and ran
                        else f"no tell_time response within {TIMEOUT_S:.0f}s",
                    )
                )
                await caller.quiet(1.0, TIMEOUT_S, session)

                asked = time.perf_counter()
                session.generate_reply(instructions=REPLY_REQUEST)
                started = await caller.first_voiced_after(asked, TIMEOUT_S)
                if started is None:
                    results.append(
                        report("interrupt", False, "no long reply to interrupt")
                    )
                else:
                    speaking = session.agent_state == "speaking"
                    onset, _ = await mic.speak(speech)
                    flushed = [at for at in caller.flushes if at >= onset]
                    if not speaking:
                        results.append(
                            report(
                                "interrupt",
                                False,
                                "reply ended before the interruption; not a test",
                            )
                        )
                    elif flushed:
                        results.append(
                            report(
                                "interrupt",
                                True,
                                f"playback flushed {flushed[0] - onset:.2f}s after barge-in",
                            )
                        )
                    else:
                        results.append(
                            report(
                                "interrupt", False, "no playback flush after barge-in"
                            )
                        )
            finally:
                await mic.stop()
                await caller.stop()
    return all(results)


def cli(argv: list[str] | None = None) -> int:
    if "pytest" in sys.modules:
        raise SystemExit("rt_live_smoke.py is an opt-in live check, not a pytest test")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--wav", type=Path, help="speech to speak (16-bit PCM WAV)")
    args = ap.parse_args(argv)
    if not os.environ.get("GOOGLE_API_KEY"):
        print(
            "refusing to run: GOOGLE_API_KEY is not set in the environment "
            "(this calls the live Gemini API)",
            file=sys.stderr,
        )
        return 2
    if args.wav is not None and not args.wav.is_file():
        ap.error(f"no such file: {args.wav}")
    try:
        ok = asyncio.run(smoke(args.wav))
    except Exception as exc:
        # Never echo SDK messages: they can carry request or key fragments.
        print(f"FAIL harness: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(cli())
