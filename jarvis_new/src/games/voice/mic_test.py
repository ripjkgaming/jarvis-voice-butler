"""``chess --mic-test``: check the whole local voice chain and the wake client."""

from __future__ import annotations

import asyncio
import json
import sys
import time

import numpy as np

from games.voice.vad import Endpointer, make_vad
from games.voice.wake_watch import query_status, wake_sock_path


async def _record_utterance(capture_cls, ep: Endpointer, timeout: float = 8.0):
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def on_frame(frame: np.ndarray, t: float) -> None:
        utt = ep.feed(frame, t)
        if utt is not None and not fut.done():
            fut.set_result(utt)

    cap = capture_cls(on_frame)
    if not cap.start(loop):
        return None, cap.error
    try:
        return await asyncio.wait_for(fut, timeout), None
    except asyncio.TimeoutError:
        return None, "no speech heard"
    finally:
        cap.stop()


async def run_mic_test(rounds: int = 3, out=sys.stdout) -> int:
    from games.voice.capture import MicCapture
    from games.voice.stt import LocalSTT
    from games.voice.tts import PiperSpeaker

    def say(msg: str) -> None:
        print(msg, file=out, flush=True)

    rc = 0
    say("Chess voice check (all local: no LiveKit, no Gemini)")
    st = LocalSTT()
    t = await asyncio.to_thread(st.load)
    say(f"  whisper {st.model_name} int8 cpu loaded + warmed in {t:.1f}s")
    sp = PiperSpeaker()
    try:
        await asyncio.to_thread(sp.load)
        say(f"  piper voice loaded ({sp.rate} Hz)")
    except Exception as exc:
        say(f"  piper FAILED: {exc}")
        rc = 4
    status = await query_status(wake_sock_path())
    if status is None:
        say("  wake client: not running (fine; chess works without Jarvis)")
    else:
        say(
            f"  wake client: alive, muted={status.get('muted')}, "
            f"in_call={status.get('in_call')} — shares the mic; never muted by chess"
        )
    ep = Endpointer(make_vad())
    if sys.stdin.isatty() and rc == 0:
        say("Say a few moves, e.g. 'knight to f three'. (Ctrl+C to stop)")
        for i in range(rounds):
            say(f"  [{i + 1}/{rounds}] listening...")
            utt, err = await _record_utterance(MicCapture, ep)
            if utt is None:
                say(f"      {err}")
                rc = rc or 4
                continue
            t0 = time.monotonic()
            text = await st.transcribe(utt.audio)
            ms = (time.monotonic() - t0) * 1000
            import chess

            from games.chess import voice_moves

            pr = voice_moves.parse_move(text, chess.Board())
            parsed = (
                chess.Board().san(pr.moves[0])
                if pr.kind == "move" and pr.moves
                else pr.kind
            )
            say(
                f"      heard: {text!r}  ->  {parsed}   (speech {utt.duration:.1f}s, whisper {ms:.0f} ms)"
            )
        try:
            await sp.speak("That is all working, Sir.")
        except Exception as exc:
            say(f"  playback failed: {exc}")
            rc = 4
    else:
        say("  (not a terminal: skipping the live microphone rounds)")
    sp.close()
    say("OK" if rc == 0 else "Problems found — see above.")
    return rc


_ = json
