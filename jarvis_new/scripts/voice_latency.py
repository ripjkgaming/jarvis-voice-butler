#!/usr/bin/env python3
"""Voice round-trip latency probe on the in-process voice runtime (jarvis_rt).

Stack per run: AgentServer + VoiceHub on loopback, one AgentSession with a
LiveBackend, and a caller VoiceClient that streams mic blocks in real time
(one 32 ms block per tick). The call token and nonce ledger live in a
temporary JARVIS_HOME, so the real ~/.jarvis secret is never read or written.

Default (offline): the Gemini Live client is ScriptedLive, a scripted fake
with no network and no key. Its server-side behaviour is modelled: end of
user turn after 500 ms of silence, 350 ms before a reply, 2x real-time
audio, barge-in (`interrupted`), and one tell_time tool call. Offline numbers
measure the in-process transport/session/backend overhead plus the scripted
delays. They say nothing about Gemini's latency.

--live: the real Gemini Live API through google-genai. It needs GOOGLE_API_KEY
in the environment (the value is never printed), spends quota, and questions
are rendered with the local Piper voice. Run:
    uv run python scripts/voice_latency.py --live [-n ROUNDS]

Metrics (all seconds, perf_counter, caller side):
- reply_latency_s: last voiced caller block sent -> first voiced agent audio frame.
- tool_roundtrip_s: LiveBackend received the tool call -> tool response handed
  to the model send queue (not wire time).
- interrupt_last_audio_s: barge-in onset -> last agent audio frame received.
  Only counted when the agent was speaking at onset.
- interrupt_flush_s: barge-in onset -> first `flush` control frame.

Results go to benchmarks/voice-runtime-<today>-jarvis_rt.json and are never
overwritten. Use --output for another new path, or --print-only to skip writing.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import sys
import tempfile
import time
from collections import deque
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from google.genai import types  # noqa: E402

from jarvis_rt import Agent, AgentServer, AgentSession, function_tool  # noqa: E402
from jarvis_rt.live import LiveBackend  # noqa: E402
from jarvis_rt.room_io import RoomOptions  # noqa: E402
from jarvis_rt.transport.client import VoiceClient  # noqa: E402
from jarvis_rt.transport.tokens import load_secret, mint_call_token  # noqa: E402

AGENT_NAME = "voice-latency"
ROOM_NAME = "latency-probe"
CALLER_IDENTITY = "latency-caller"
MIC_RATE = 16000
OUT_RATE = 24000
BLOCK_SAMPLES = 512  # 32 ms of 16 kHz mono PCM16
BLOCK_BYTES = BLOCK_SAMPLES * 2
BLOCK_S = BLOCK_SAMPLES / MIC_RATE
VOICED_RMS = 300.0
THINK_S = 0.35  # scripted fake: server think time before a reply
END_SILENCE_S = 0.5  # scripted fake: silence that ends a user turn
CHUNK_S = 0.04  # scripted fake: audio message length
TOOL_REPLY_S = 1.0
REPLY_TIMEOUT_S = 25.0
RESULT_DIR = ROOT / "benchmarks"

QUESTIONS = [
    "Jarvis, what is the capital of Australia?",
    "How many legs does a spider have?",
    "Give me one fun fact about the moon.",
    "What's the boiling point of water in Fahrenheit?",
    "Who wrote Romeo and Juliet?",
]
TOOL_QUESTION = "Jarvis, what time is it?"
LONG_REQUEST = "Jarvis, explain in detail how medieval castles were defended."
INTERRUPT_LINE = "Wait Jarvis, that's enough, stop there."


class HarnessAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="Answer briefly.")

    @function_tool()
    async def tell_time(self) -> str:
        """Tell the current local time (harmless; used by the latency probe)."""
        return time.strftime("%H:%M")


def tone(seconds: float, rate: int, freq: float = 220.0, amp: int = 4000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def resample_16k(audio: np.ndarray, rate: int) -> np.ndarray:
    if rate == MIC_RATE:
        return audio.astype(np.int16)
    n_out = round(len(audio) * MIC_RATE / rate)
    x_in = np.arange(len(audio)) / rate
    x_out = np.arange(n_out) / MIC_RATE
    return np.interp(x_out, x_in, audio.astype(np.float64)).round().astype(np.int16)


def render(text: str) -> tuple[np.ndarray, int]:
    """Piper-render `text` with the local voice (live mode only)."""
    from piper import PiperVoice

    from local_voice import DEFAULT_VOICE_PATH

    voice = PiperVoice.load(str(DEFAULT_VOICE_PATH))
    chunks = list(voice.synthesize(text))
    audio = np.concatenate(
        [np.frombuffer(c.audio_int16_bytes, np.int16) for c in chunks]
    )
    return audio, chunks[0].sample_rate


def _rms(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return 0.0
    x = pcm.astype(np.float64)
    return float(math.sqrt(float(np.mean(x * x))))


class ScriptedLive:
    """Scripted stand-in for google-genai's live client (no network, no key).

    ``expect()`` queues one step per caller turn: ("reply", seconds) speaks a
    tone, ("tool", seconds) emits tell_time and speaks after the response,
    ("silent", 0) ends the turn without audio.
    """

    def __init__(self) -> None:
        self.plan: deque[tuple[str, float]] = deque()
        self.conns: list[_ScriptedConn] = []
        self.aio = self
        self.live = self

    def expect(self, *steps: tuple[str, float]) -> None:
        self.plan.extend(steps)

    def next_step(self) -> tuple[str, float]:
        return self.plan.popleft() if self.plan else ("reply", 0.5)

    def connect(self, *, model: str, config: Any) -> _ScriptedConnCM:
        return _ScriptedConnCM(self)


class _ScriptedConnCM:
    def __init__(self, owner: ScriptedLive) -> None:
        self.owner = owner
        self._conn: _ScriptedConn | None = None

    async def __aenter__(self) -> _ScriptedConn:
        self._conn = _ScriptedConn(self.owner)
        self.owner.conns.append(self._conn)
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        if self._conn is not None:
            self._conn.close()


class _ScriptedConn:
    def __init__(self, owner: ScriptedLive) -> None:
        self.owner = owner
        self.inbox: asyncio.Queue = asyncio.Queue()
        self._in_turn = False
        self._silence_s = 0.0
        self._reply: asyncio.Task | None = None
        self._tool_reply_s: float | None = None

    def _speaking(self) -> bool:
        return self._reply is not None and not self._reply.done()

    def _push(self, **fields: Any) -> None:
        self.inbox.put_nowait(types.LiveServerMessage(**fields))

    async def send_realtime_input(self, audio=None, video=None, **_: Any) -> None:
        if audio is None:
            return
        pcm = np.frombuffer(audio.data, np.int16)
        if pcm.size == 0:
            return
        if _rms(pcm) > VOICED_RMS:
            if self._speaking():
                # Server-side barge-in: stop the reply and end its generation.
                self._reply.cancel()
                self._push(
                    server_content=types.LiveServerContent(
                        interrupted=True, turn_complete=True
                    )
                )
            self._in_turn = True
            self._silence_s = 0.0
        elif self._in_turn:
            self._silence_s += pcm.size / MIC_RATE
            if self._silence_s >= END_SILENCE_S:
                self._in_turn = False
                self._silence_s = 0.0
                self._start_reply(*self.owner.next_step())

    async def send_client_content(
        self, turns: list | None = None, turn_complete: bool = False, **_: Any
    ) -> None:
        # A completed client turn (generate_reply / say) is answered like Gemini does.
        if turn_complete and turns:
            self._start_reply(*self.owner.next_step())

    async def send_tool_response(self, function_responses=None, **_: Any) -> None:
        if self._tool_reply_s is not None:
            seconds, self._tool_reply_s = self._tool_reply_s, None
            self._reply = asyncio.get_running_loop().create_task(
                self._speak(seconds, delay=0.1)
            )

    def _start_reply(self, kind: str, seconds: float) -> None:
        if kind == "tool":
            self._tool_reply_s = seconds
            self._reply = asyncio.get_running_loop().create_task(self._call_tool())
        else:
            self._reply = asyncio.get_running_loop().create_task(
                self._speak(seconds, delay=THINK_S)
            )

    async def _call_tool(self) -> None:
        await asyncio.sleep(THINK_S)
        self._push(
            tool_call=types.LiveServerToolCall(
                function_calls=[
                    types.FunctionCall(id="latency-1", name="tell_time", args={})
                ]
            )
        )

    async def _speak(self, seconds: float, *, delay: float) -> None:
        await asyncio.sleep(delay)
        if seconds > 0:
            pcm = tone(seconds, OUT_RATE).tobytes()
            step = int(OUT_RATE * CHUNK_S) * 2
            for start in range(0, len(pcm), step):
                self._push(
                    server_content=types.LiveServerContent(
                        model_turn=types.Content(
                            parts=[
                                types.Part(
                                    inline_data=types.Blob(
                                        data=pcm[start : start + step],
                                        mime_type="audio/pcm;rate=24000",
                                    )
                                )
                            ]
                        )
                    )
                )
                await asyncio.sleep(CHUNK_S / 2)  # 2x real time
        self._push(server_content=types.LiveServerContent(generation_complete=True))
        self._push(server_content=types.LiveServerContent(turn_complete=True))

    async def receive(self):
        while True:
            item = await self.inbox.get()
            yield item

    def close(self) -> None:
        if self._reply is not None:
            self._reply.cancel()


class Mic:
    """The only mic sender: queued speech in real time, silence otherwise."""

    def __init__(self, client: VoiceClient) -> None:
        self._client = client
        self._jobs: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def speak(self, pcm16k: np.ndarray) -> tuple[float, float]:
        """Return (first block sent, last voiced block sent) as perf_counter times."""
        fut = asyncio.get_running_loop().create_future()
        await self._jobs.put((pcm16k, fut))
        return await fut

    async def _run(self) -> None:
        silence = bytes(BLOCK_BYTES)
        while True:
            if self._jobs.empty():
                await self._client.send_mic(silence)
                await asyncio.sleep(BLOCK_S)
                continue
            pcm, fut = self._jobs.get_nowait()
            blocks = [
                pcm[i : i + BLOCK_SAMPLES] for i in range(0, len(pcm), BLOCK_SAMPLES)
            ]
            if blocks and len(blocks[-1]) < BLOCK_SAMPLES:
                blocks[-1] = np.pad(blocks[-1], (0, BLOCK_SAMPLES - len(blocks[-1])))
            first = last = None
            try:
                for block in blocks:
                    if not await self._client.send_mic(
                        block.astype(np.int16).tobytes()
                    ):
                        raise RuntimeError("caller is not connected")
                    last = time.perf_counter()
                    first = first if first is not None else last
                    await asyncio.sleep(BLOCK_S)
            except Exception as exc:
                fut.set_exception(exc)
                continue
            fut.set_result((first, last))


class Caller:
    """Receives agent audio and control frames; records arrival times."""

    def __init__(self, client: VoiceClient) -> None:
        self.client = client
        self.audio: list[tuple[float, float]] = []  # (arrival, rms)
        self.flushes: list[float] = []
        self._task: asyncio.Task | None = None
        client.on_topic("flush", lambda _p: self.flushes.append(time.perf_counter()))

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._listen())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _listen(self) -> None:
        async for pcm in self.client.audio_frames():
            now = time.perf_counter()
            self.audio.append((now, _rms(np.frombuffer(pcm, np.int16))))

    async def first_voiced_after(self, t: float, timeout: float) -> float | None:
        end = time.perf_counter() + timeout
        seen = 0
        while time.perf_counter() < end:
            for at, level in self.audio[seen:]:
                if at >= t and level > VOICED_RMS:
                    return at
            seen = len(self.audio)
            await asyncio.sleep(0.02)
        return None

    async def quiet(self, quiet_s: float, timeout: float, session: Any = None) -> bool:
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            last = self.audio[-1][0] if self.audio else 0.0
            idle = session is None or session.agent_state == "listening"
            if idle and time.perf_counter() - last >= quiet_s:
                return True
            await asyncio.sleep(0.05)
        return False


class Stack:
    """AgentServer + VoiceHub on loopback, one session on the job, one caller."""

    def __init__(
        self,
        *,
        models: list[str],
        fake: ScriptedLive | None = None,
        http: Any = None,
        home: Path,
    ) -> None:
        self.models = models
        self.fake = fake
        self.http = http
        self.home = home
        self.server = AgentServer()
        self.ready = asyncio.Event()
        self.done = asyncio.Event()
        self.session: AgentSession | None = None
        self.backend: LiveBackend | None = None
        self.tool_calls_at: list[float] = []
        self.tool_responses_at: list[float] = []
        self.client: VoiceClient | None = None
        self._listener: Any = None
        self._old_home: str | None = None

    async def __aenter__(self) -> Stack:
        self._old_home = os.environ.get("JARVIS_HOME")
        os.environ["JARVIS_HOME"] = str(self.home)
        self.server.rtc_session(agent_name=AGENT_NAME)(self._entry)
        self._listener = await self.server.serve(host="127.0.0.1", port=0)
        port = self._listener.sockets[0].getsockname()[1]
        token = mint_call_token(
            ROOM_NAME, CALLER_IDENTITY, "speaker", 600, None, load_secret(self.home)
        )
        self.client = VoiceClient(f"ws://127.0.0.1:{port}/voice", token)
        self.client.start()
        await self.client.wait_connected(10)
        await asyncio.wait_for(self.ready.wait(), 30)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.done.set()
        if self.client is not None:
            await self.client.close()
        if self._listener is not None:
            self._listener.close()
            with contextlib.suppress(Exception):
                await self._listener.wait_closed()
        if self._old_home is None:
            os.environ.pop("JARVIS_HOME", None)
        else:
            os.environ["JARVIS_HOME"] = self._old_home

    async def _entry(self, ctx: Any) -> None:
        backend = LiveBackend(
            self.models, client=self.fake, http_options=self.http, watchdog=False
        )
        self.backend = backend
        self._instrument(backend)
        self.session = AgentSession(backend=backend)
        await self.session.start(
            HarnessAgent(), room=ctx.room, room_options=RoomOptions()
        )
        self.ready.set()
        await self.done.wait()
        await self.session.aclose()

    def _instrument(self, backend: LiveBackend) -> None:
        """Timestamp tool calls at the backend boundary (same code path live/offline)."""
        original_calls = backend._tool_calls
        original_send = backend.send_tool_results

        def traced_calls(tool_call: Any) -> None:
            self.tool_calls_at.append(time.perf_counter())
            return original_calls(tool_call)

        async def traced_send(results: Any, *, reply: bool) -> None:
            self.tool_responses_at.append(time.perf_counter())
            return await original_send(results, reply=reply)

        backend._tool_calls = traced_calls
        backend.send_tool_results = traced_send


def _pct(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


def summarize(values: list[float | None]) -> dict[str, Any]:
    got = [v for v in values if v is not None]
    return {
        "n": len(got),
        "missing": len(values) - len(got),
        "p50_s": _pct(got, 50),
        "p95_s": _pct(got, 95),
        "samples_s": [round(v, 4) for v in got],
    }


async def wait_len(items: list, n: int, timeout: float) -> bool:
    end = time.perf_counter() + timeout
    while len(items) < n and time.perf_counter() < end:
        await asyncio.sleep(0.02)
    return len(items) >= n


async def scenario(
    st: Stack, *, live: bool, rounds: int, fake: ScriptedLive | None, audio: dict
) -> dict[str, list]:
    mic = Mic(st.client)
    caller = Caller(st.client)
    mic.start()
    caller.start()
    out: dict[str, list] = {
        "reply_latency_s": [],
        "tool_reply_latency_s": [],
        "tool_roundtrip_s": [],
        "interrupt_last_audio_s": [],
        "interrupt_flush_s": [],
    }
    session = st.session
    try:
        await caller.quiet(1.0, 30, session)
        questions = audio["questions"]
        for index in range(rounds):
            if fake is not None:
                fake.expect(("reply", 1.2))
            _, end = await mic.speak(questions[index])
            first = await caller.first_voiced_after(end, REPLY_TIMEOUT_S)
            out["reply_latency_s"].append(None if first is None else first - end)
            await caller.quiet(1.0, 30, session)

        calls_before = len(st.tool_calls_at)
        responses_before = len(st.tool_responses_at)
        if fake is not None:
            fake.expect(("tool", TOOL_REPLY_S))
        _, end = await mic.speak(audio["tool"])
        first = await caller.first_voiced_after(end, REPLY_TIMEOUT_S)
        out["tool_reply_latency_s"].append(None if first is None else first - end)
        await wait_len(st.tool_responses_at, responses_before + 1, REPLY_TIMEOUT_S)
        if (
            len(st.tool_calls_at) > calls_before
            and len(st.tool_responses_at) > responses_before
        ):
            call_at = st.tool_calls_at[calls_before]
            response_at = st.tool_responses_at[responses_before]
            out["tool_roundtrip_s"].append(response_at - call_at)
        else:
            out["tool_roundtrip_s"].append(None)
        await caller.quiet(1.0, 30, session)

        if fake is not None:
            fake.expect(("reply", 8.0), ("silent", 0.0))
        _, end = await mic.speak(audio["long"])
        started = await caller.first_voiced_after(end, REPLY_TIMEOUT_S)
        if started is None:
            out["interrupt_last_audio_s"].append(None)
            out["interrupt_flush_s"].append(None)
        else:
            await asyncio.sleep(0.5)
            speaking_at_onset = session.agent_state == "speaking"
            onset, _ = await mic.speak(audio["interrupt"])
            await asyncio.sleep(1.5)
            after = [at for at, _ in caller.audio if at >= onset]
            if not speaking_at_onset:
                out["interrupt_last_audio_s"].append(None)
            else:
                out["interrupt_last_audio_s"].append(
                    (after[-1] - onset) if after else 0.0
                )
            flushed = [at for at in caller.flushes if at >= onset]
            out["interrupt_flush_s"].append(flushed[0] - onset if flushed else None)
        await caller.quiet(1.0, 30, session)
    finally:
        await mic.stop()
        await caller.stop()
    return out


def build_audio(live: bool, rounds: int) -> dict:
    """Mic audio (16 kHz int16) for every caller turn."""
    if not live:
        return {
            "questions": [tone(1.2, MIC_RATE) for _ in range(rounds)],
            "tool": tone(1.0, MIC_RATE, freq=260.0),
            "long": tone(1.0, MIC_RATE, freq=240.0),
            "interrupt": tone(0.8, MIC_RATE, freq=300.0),
        }
    rendered = {}
    for text in [*QUESTIONS[:rounds], TOOL_QUESTION, LONG_REQUEST, INTERRUPT_LINE]:
        audio, rate = render(text)
        rendered[text] = resample_16k(audio, rate)
    return {
        "questions": [rendered[q] for q in QUESTIONS[:rounds]],
        "tool": rendered[TOOL_QUESTION],
        "long": rendered[LONG_REQUEST],
        "interrupt": rendered[INTERRUPT_LINE],
    }


def build_result(out: dict, *, live: bool, models: list[str], rounds: int) -> dict:
    metrics = {name: summarize(values) for name, values in out.items()}
    return {
        "kind": "jarvis_rt in-process voice latency probe",
        "harness": "AgentServer + VoiceHub + VoiceClient + LiveBackend",
        "mode": "live" if live else "offline",
        "model": (
            "gemini live: " + ", ".join(models)
            if live
            else "scripted fake Gemini Live (no network): think 350 ms, end-of-turn 500 ms, 2x real-time audio"
        ),
        "date": date.today().isoformat(),
        "rounds": rounds,
        "metrics": metrics,
        "limitations": [
            "Offline model is scripted: offline numbers are harness overhead plus scripted delays, not Gemini latency.",
            "Sender boundary is the time the last voiced block was handed to the hub in real-time pacing, not remote receipt or physical playout.",
            "tool_roundtrip_s ends when the response is queued for the model, not when it hits the wire.",
            "No microphone, speaker, wake detector or answer correctness is measured.",
        ],
    }


def print_summary(result: dict) -> None:
    print(f"mode={result['mode']} rounds={result['rounds']} model={result['model']}")
    for name, row in result["metrics"].items():
        if row["n"]:
            print(
                f"{name}: p50={row['p50_s']:.3f}s p95={row['p95_s']:.3f}s "
                f"n={row['n']} missing={row['missing']}"
            )
        else:
            print(f"{name}: no samples; missing={row['missing']}")


def default_output() -> Path:
    return RESULT_DIR / f"voice-runtime-{date.today().isoformat()}-jarvis_rt.json"


def write_new(path: Path, payload: dict) -> None:
    """Create ``path`` exclusively; never overwrite an existing benchmark."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x") as fh:
            fh.write(json.dumps(payload, indent=2) + "\n")
    except FileExistsError:
        raise SystemExit(
            f"refusing to overwrite existing {path}; pass --output"
        ) from None


async def run(args: argparse.Namespace) -> dict:
    models = ["gemini-3.8-live"]
    fake: ScriptedLive | None = None
    http = None
    if args.live:
        from jarvis_rt.live import http_options
        from voice_upgrade import DEFAULT_CHAIN, selected_chain

        models = selected_chain(DEFAULT_CHAIN)
        http = http_options()
    else:
        fake = ScriptedLive()
    audio = build_audio(args.live, args.rounds)
    with tempfile.TemporaryDirectory(prefix="jarvis-latency-") as home:
        async with Stack(models=models, fake=fake, http=http, home=Path(home)) as st:
            out = await scenario(
                st, live=args.live, rounds=args.rounds, fake=fake, audio=audio
            )
    return build_result(out, live=args.live, models=models, rounds=args.rounds)


def cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--live",
        action="store_true",
        help="use the real Gemini Live API (needs GOOGLE_API_KEY; spends quota)",
    )
    ap.add_argument("-n", "--rounds", type=int, default=len(QUESTIONS))
    ap.add_argument("--output", type=Path, help="new JSON path (must not exist)")
    ap.add_argument(
        "--print-only", action="store_true", help="print the summary, write nothing"
    )
    args = ap.parse_args(argv)
    if not 1 <= args.rounds <= len(QUESTIONS):
        ap.error(f"--rounds must be between 1 and {len(QUESTIONS)}")
    if args.live and not os.environ.get("GOOGLE_API_KEY"):
        ap.error("--live needs GOOGLE_API_KEY in the environment (value not printed)")
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        # Never echo SDK messages: they can contain request or key fragments.
        print(
            f"probe failed ({type(exc).__name__}); no complete measurement",
            file=sys.stderr,
        )
        return 1
    print_summary(result)
    if not args.print_only:
        path = args.output or default_output()
        write_new(path, result)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(cli())
