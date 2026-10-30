"""DirectBackend turn behaviour, tested with fakes only.

No network, no real VAD, STT, TTS or Gemini client: the VAD emits scripted
events, STT returns scripted text, TTS returns tagged bytes and the genai
client is an in-process stream. Timings use short real sleeps.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from google.genai import types

from jarvis_rt.agent import Agent
from jarvis_rt.chat_context import ChatMessage, FunctionCallOutput
from jarvis_rt.pipeline_direct import DirectBackend, SentenceSplitter
from jarvis_rt.tools import function_tool

PCM = b"\x00\x00" * 160  # 10 ms of 16 kHz mono int16


@function_tool
def get_time(tz: str) -> str:
    """Return the current time in a timezone."""
    return "12:00"


class FakeEvent:
    def __init__(self, type_: str) -> None:
        self.type = type_


class FakeVADStream:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self.closed = False

    def push_frame(self, frame) -> None:
        # The real VAD takes an AudioFrame; keep the raw bytes for assertions.
        self.frames.append(bytes(frame.data))

    def start(self) -> None:
        self._queue.put_nowait(FakeEvent("start_of_speech"))

    def end(self) -> None:
        self._queue.put_nowait(FakeEvent("end_of_speech"))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def aclose(self) -> None:
        self.closed = True
        self._queue.put_nowait(None)


class FakeVAD:
    def __init__(self) -> None:
        self.stream_obj = FakeVADStream()

    def stream(self) -> FakeVADStream:
        return self.stream_obj


class FakeSTT:
    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls = 0

    async def transcribe(self, pcm: bytes) -> str:
        self.calls += 1
        return self.texts.pop(0) if self.texts else ""


class FakeTTS:
    """Bytes are ``<text>``. A sentence listed in ``gates`` blocks until set."""

    sample_rate = 24000

    def __init__(self, gates: dict[str, asyncio.Event] | None = None) -> None:
        self.gates = gates or {}
        self.started: list[str] = []
        self.rendered: list[str] = []
        self.cancelled: list[str] = []
        self.closed = False

    async def synthesize(self, text: str) -> bytes:
        self.started.append(text)
        gate = self.gates.get(text)
        try:
            if gate is not None:
                await gate.wait()
        except asyncio.CancelledError:
            self.cancelled.append(text)
            raise
        self.rendered.append(text)
        return f"<{text}>".encode()

    async def aclose(self) -> None:
        self.closed = True


def _chunk(text: str | None = None, call: tuple | None = None):
    parts = []
    if text is not None:
        parts.append(types.Part(text=text))
    if call is not None:
        name, args, call_id = call
        parts.append(
            types.Part(
                function_call=types.FunctionCall(id=call_id, name=name, args=args)
            )
        )
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


class FakeClient:
    """``client.aio.models.generate_content_stream`` with scripted replies.

    A script is a list of items: text strings, ``("call", name, args, id)``
    tuples, or an ``asyncio.Event`` the stream waits on before continuing.
    """

    def __init__(
        self,
        scripts: list[list] | None = None,
        fail_models: dict[str, BaseException] | None = None,
    ) -> None:
        self.scripts = list(scripts or [])
        self.fail_models = dict(fail_models or {})
        self.calls: list[SimpleNamespace] = []
        self.cancelled = 0
        self.completed = 0
        self.aio = SimpleNamespace(models=_FakeModels(self))

    async def _stream(self, script: list):
        try:
            for item in script:
                if isinstance(item, asyncio.Event):
                    await item.wait()
                elif isinstance(item, tuple):
                    _, name, args, call_id = item
                    yield _chunk(call=(name, args, call_id))
                else:
                    yield _chunk(text=item)
            self.completed += 1
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class _FakeModels:
    def __init__(self, owner: FakeClient) -> None:
        self.owner = owner

    async def generate_content_stream(self, *, model, contents, config):
        owner = self.owner
        owner.calls.append(
            SimpleNamespace(model=model, contents=contents, config=config)
        )
        if model in owner.fail_models:
            raise owner.fail_models[model]
        script = owner.scripts.pop(0) if owner.scripts else []
        return owner._stream(script)


class FakeSession:
    """Mirrors the parts of AgentSession that feed chat history."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.events: list[tuple[str, dict]] = []
        self._assistant: list[str] = []

    def backend_event(self, kind: str, **data) -> None:
        self.events.append((kind, data))
        items = self.agent.chat_ctx.items
        if kind == "user_transcript" and data["final"]:
            items.append(ChatMessage(role="user", content=[data["text"]]))
        elif kind == "agent_text":
            self._assistant.append(data["text"])
        elif kind == "tool_calls":
            self._flush_assistant()
            items.extend(data["calls"])
        elif kind == "turn_complete":
            self._flush_assistant()

    def _flush_assistant(self) -> None:
        if self._assistant:
            text = "".join(self._assistant)
            self._assistant.clear()
            self.agent.chat_ctx.items.append(
                ChatMessage(role="assistant", content=[text])
            )

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]

    def count(self, kind: str) -> int:
        return self.kinds().count(kind)

    def audio(self) -> list[bytes]:
        return [d["pcm24k"] for k, d in self.events if k == "agent_audio"]

    def agent_text(self) -> str:
        return "".join(d["text"] for k, d in self.events if k == "agent_text")


async def until(pred, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


class Env(SimpleNamespace):
    def hear(self, text: str | None = None) -> None:
        """A complete user utterance, the STT text already scripted."""
        self.backend.push_audio(PCM)
        self.vad.start()
        self.backend.push_audio(PCM)
        self.vad.end()


async def make_env(
    *,
    stt_texts=(),
    tts=None,
    client=None,
    models=("m1", "m2"),
    min_duration: float = 0.05,
    endpoint: float = 0.02,
    preemptive: bool = True,
    resume: bool = True,
    clock=time.monotonic,
) -> Env:
    vad = FakeVAD()
    client = client or FakeClient()
    tts = tts or FakeTTS()
    backend = DirectBackend(
        vad=vad,
        stt=FakeSTT(list(stt_texts)),
        tts=tts,
        llm_models=list(models),
        barge_in={
            "mode": "adaptive",
            "min_duration": min_duration,
            "resume_false_interruption": resume,
        },
        endpointing_delay=endpoint,
        preemptive_generation=preemptive,
        client=client,
        clock=clock,
    )
    agent = Agent(instructions="You are Jarvis.", tools=[get_time])
    session = FakeSession(agent)
    await backend.start(session, agent)
    return Env(
        backend=backend,
        vad=vad.stream_obj,
        tts=tts,
        client=client,
        session=session,
        agent=agent,
    )


def _user_texts(env: Env) -> list[str]:
    return [d["text"] for k, d in env.session.events if k == "user_transcript"]


async def test_normal_turn_event_order_and_request_shape():
    client = FakeClient(scripts=[["It is ", "noon. ", "Enjoy."]])
    env = await make_env(stt_texts=["What time is it?"], client=client)
    env.hear()
    await until(lambda: env.session.count("turn_complete") == 1)

    kinds = env.session.kinds()
    assert kinds[:3] == [
        "user_speech_started",
        "user_transcript",
        "user_speech_stopped",
    ]
    assert kinds[-1] == "turn_complete"
    assert env.session.audio() == [b"<It is noon.>", b"<Enjoy.>"]
    assert env.session.agent_text() == "It is noon. Enjoy."
    assert env.vad.frames[0] == PCM

    call = client.calls[0]
    assert call.model == "m1"
    assert call.config.system_instruction == "You are Jarvis."
    assert call.contents[-1].parts[0].text == "What time is it?"
    names = [d.name for d in call.config.tools[0].function_declarations]
    assert names == ["get_time"]
    await env.backend.aclose()


async def test_barge_in_after_min_duration_cancels_audio_and_emits_interrupted():
    release = asyncio.Event()
    client = FakeClient(scripts=[["One. ", release, "Two. ", "Three."], ["Yes, Sir."]])
    env = await make_env(stt_texts=["Tell me a story.", "Stop, Jarvis."], client=client)
    env.hear()
    await until(lambda: env.session.audio() == [b"<One.>"])

    # Sir talks over the reply: nothing is emitted before min_duration.
    env.vad.start()
    await asyncio.sleep(0.01)
    assert "interrupted" not in env.session.kinds()
    await until(lambda: "interrupted" in env.session.kinds())
    kinds = env.session.kinds()
    assert kinds.index("user_speech_started") < kinds.index("interrupted")

    # The cut reply keeps producing text, but none of it is spoken.
    release.set()
    await asyncio.sleep(0.02)
    interrupted_at = env.session.kinds().index("interrupted")
    assert all(k != "agent_audio" for k in env.session.kinds()[interrupted_at + 1 :])

    env.backend.push_audio(PCM)
    env.vad.end()
    # The cut reply ends at "interrupted"; only the new reply completes.
    await until(lambda: env.session.count("turn_complete") == 1)
    # True interruption: the cut reply was discarded, only the new one spoke.
    assert env.session.audio() == [b"<One.>", b"<Yes, Sir.>"]
    assert _user_texts(env) == ["Tell me a story.", "Stop, Jarvis."]
    assert client.calls[1].contents[-1].parts[0].text == "Stop, Jarvis."
    await env.backend.aclose()


async def test_short_blip_while_speaking_is_not_an_interruption():
    release = asyncio.Event()
    client = FakeClient(scripts=[["One. ", release]])
    env = await make_env(stt_texts=["Tell me."], client=client, min_duration=0.2)
    env.hear()
    await until(lambda: env.session.audio() == [b"<One.>"])
    env.vad.start()
    await asyncio.sleep(0.01)
    env.vad.end()
    await asyncio.sleep(0.3)
    assert "interrupted" not in env.session.kinds()
    assert "user_speech_started" not in env.session.kinds()[1:]
    release.set()
    await until(lambda: env.session.count("turn_complete") == 1)
    await env.backend.aclose()


async def test_false_interruption_resumes_cut_reply_without_loss():
    gate = asyncio.Event()
    tts = FakeTTS(gates={"Two.": gate})
    client = FakeClient(scripts=[["One. Two. Three."]])
    env = await make_env(stt_texts=["Tell me more.", ""], client=client, tts=tts)
    env.hear()
    await until(lambda: "Two." in tts.started and env.session.audio() == [b"<One.>"])

    env.vad.start()
    await until(lambda: "interrupted" in env.session.kinds())
    assert tts.cancelled == ["Two."]
    assert env.session.audio() == [b"<One.>"]

    # Noise: no transcript when the user stops. The reply resumes at the cut sentence.
    gate.set()
    env.backend.push_audio(PCM)
    env.vad.end()
    await until(lambda: env.session.count("turn_complete") == 1)

    assert env.session.audio() == [b"<One.>", b"<Two.>", b"<Three.>"]
    assert _user_texts(env) == ["Tell me more."]
    assert env.session.kinds().count("user_speech_stopped") == 2
    assert env.session.kinds().index("interrupted") < env.session.kinds().index(
        "turn_complete"
    )
    await env.backend.aclose()


async def test_preemptive_reply_discarded_when_user_keeps_talking():
    never = asyncio.Event()
    client = FakeClient(
        scripts=[[never], ["Tomorrow it rains."]],
    )
    env = await make_env(
        stt_texts=["What's the weather?", "What's the weather and tomorrow?"],
        client=client,
        endpoint=0.3,
    )
    env.hear()
    # Transcript is in, so the LLM starts before the endpoint delay is over.
    await until(lambda: len(client.calls) == 1)
    assert "user_transcript" not in env.session.kinds()
    assert client.calls[0].contents[-1].parts[0].text == "What's the weather?"

    env.vad.start()  # the user keeps talking: the speculative reply goes
    await until(lambda: client.cancelled == 1)
    assert env.session.audio() == []

    env.hear()
    await until(lambda: env.session.count("turn_complete") == 1)
    assert env.session.audio() == [b"<Tomorrow it rains.>"]
    assert _user_texts(env) == ["What's the weather and tomorrow?"]
    assert (
        client.calls[1].contents[-1].parts[0].text == "What's the weather and tomorrow?"
    )
    await env.backend.aclose()


async def test_preemptive_reply_released_on_matching_commit():
    client = FakeClient(scripts=[["Sunny, Sir."]])
    env = await make_env(stt_texts=["Weather?"], client=client, endpoint=0.05)
    env.hear()
    await until(lambda: env.session.count("turn_complete") == 1)
    kinds = env.session.kinds()
    assert len(client.calls) == 1
    assert kinds.index("user_transcript") < kinds.index("agent_audio")
    assert env.session.audio() == [b"<Sunny, Sir.>"]
    await env.backend.aclose()


async def test_tool_call_roundtrip_runs_follow_up_generation():
    client = FakeClient(
        scripts=[
            ["Checking. ", ("call", "get_time", {"tz": "Asia/Kolkata"}, "c1")],
            ["It is noon."],
        ]
    )
    env = await make_env(stt_texts=["Time in Kolkata?"], client=client)
    env.hear()
    await until(lambda: env.session.count("tool_calls") == 1)
    await until(lambda: env.session.count("turn_complete") == 1)
    call = env.session.events[env.session.kinds().index("tool_calls")][1]["calls"][0]
    assert call.name == "get_time"
    assert call.arguments == '{"tz": "Asia/Kolkata"}'

    result = FunctionCallOutput(
        call_id=call.call_id,
        name="get_time",
        output="12:00",
        is_error=False,
        reply_required=True,
    )
    # The session appends the call and its output before sending results back.
    env.agent.chat_ctx.items.append(result)
    await env.backend.send_tool_results([result], reply=True)
    await until(lambda: env.session.count("turn_complete") == 2)

    assert env.session.audio() == [b"<Checking.>", b"<It is noon.>"]
    follow_up = client.calls[1].contents
    assert follow_up[-1].parts[0].function_response.name == "get_time"
    assert follow_up[-1].parts[0].function_response.response == {"output": "12:00"}
    assert any(p.function_call is not None for c in follow_up for p in c.parts)
    await env.backend.aclose()


async def test_tool_choice_none_disables_tools_for_that_turn():
    client = FakeClient(scripts=[["Just talking."]])
    env = await make_env(client=client)
    await env.backend.generate_reply("Greet Sir.", "none")
    await until(lambda: env.session.count("turn_complete") == 1)
    config = client.calls[0].config
    assert config.tools is None
    assert "Greet Sir." in config.system_instruction
    await env.backend.aclose()


async def test_say_speaks_exact_text_without_llm():
    client = FakeClient()
    env = await make_env(client=client)
    await env.backend.say("Good morning, Sir. Shall I open the news?")
    await until(lambda: env.session.count("turn_complete") == 1)
    assert client.calls == []
    assert env.session.audio() == [b"<Good morning, Sir.>", b"<Shall I open the news?>"]
    assert env.session.agent_text() == "Good morning, Sir. Shall I open the news?"
    await env.backend.aclose()


async def test_typed_text_starts_a_spoken_reply():
    client = FakeClient(scripts=[["Typed answer."]])
    env = await make_env(client=client)
    env.backend.push_text("what is the time")
    await until(lambda: env.session.count("turn_complete") == 1)
    assert len(client.calls) == 1
    assert env.session.agent_text() == "Typed answer."
    await env.backend.aclose()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_model_failover_then_cooldown_then_primary_again():
    clock = _Clock()
    client = FakeClient(
        scripts=[["First."], ["Second."], ["Third."]],
        fail_models={"m1": RuntimeError("429 quota")},
    )
    env = await make_env(client=client, models=("m1", "m2"), clock=clock)

    await env.backend.generate_reply(None, None)
    await until(lambda: env.session.count("turn_complete") == 1)
    await env.backend.generate_reply(None, None)
    await until(lambda: env.session.count("turn_complete") == 2)
    assert "error" not in env.session.kinds()
    # m1 sits out during its cooldown
    assert [c.model for c in client.calls] == ["m1", "m2", "m2"]

    clock.now += 61
    await env.backend.generate_reply(None, None)
    await until(lambda: env.session.count("turn_complete") == 3)
    assert [c.model for c in client.calls][3:] == ["m1", "m2"]
    assert env.session.audio() == [b"<First.>", b"<Second.>", b"<Third.>"]
    await env.backend.aclose()


async def test_all_models_failing_reports_error_and_ends_turn():
    client = FakeClient(
        fail_models={"m1": RuntimeError("down"), "m2": RuntimeError("down")}
    )
    env = await make_env(client=client)
    await env.backend.generate_reply(None, None)
    await until(lambda: env.session.count("turn_complete") == 1)
    assert env.session.count("error") == 1
    assert env.session.audio() == []
    await env.backend.aclose()


async def test_interrupt_cancels_between_sentences():
    gate = asyncio.Event()
    tts = FakeTTS(gates={"Two.": gate})
    client = FakeClient(scripts=[["One. Two. Three."]])
    env = await make_env(client=client, tts=tts)
    await env.backend.generate_reply(None, None)
    await until(lambda: env.session.audio() == [b"<One.>"] and "Two." in tts.started)

    env.backend.interrupt()
    await asyncio.sleep(0.02)
    assert tts.cancelled == ["Two."]
    assert "Three." not in tts.started
    assert env.session.audio() == [b"<One.>"]
    assert "turn_complete" not in env.session.kinds()
    assert env.backend._reply is None
    await env.backend.aclose()


async def test_aclose_cancels_work_and_closes_tts():
    gate = asyncio.Event()
    client = FakeClient(scripts=[[gate]])
    env = await make_env(client=client)
    await env.backend.generate_reply(None, None)
    await until(lambda: len(client.calls) == 1)
    await env.backend.aclose()
    assert env.tts.closed
    assert env.vad.closed
    assert client.cancelled == 1
    assert env.backend._reply is None


class _ChunkedStream:
    """ChunkedStream-shaped TTS output: async iterable of frames, with aclose."""

    def __init__(self, pcm: bytes, rate: int) -> None:
        self._items = [
            SimpleNamespace(frame=SimpleNamespace(data=pcm, sample_rate=rate))
        ]
        self.closed = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for item in self._items:
            yield item

    async def aclose(self) -> None:
        self.closed = True


async def test_render_resamples_chunked_output_to_24k():
    stream = _ChunkedStream(b"\x01\x00" * 2205, 22050)

    class _Tts:
        sample_rate = 22050

        def synthesize(self, text):
            return stream

    env = await make_env(tts=_Tts())
    pcm = await env.backend._render("Hello.")
    assert len(pcm) == 2 * 2400
    assert stream.closed
    await env.backend.aclose()


def test_sentence_splitter_keeps_numbers_and_flushes_tail():
    splitter = SentenceSplitter()
    out = splitter.feed("It is 3.14 today. Also")
    out += splitter.feed(" fine")
    out += splitter.flush()
    assert out == ["It is 3.14 today.", "Also fine"]


async def test_push_text_records_typed_turn_in_chat():
    env = await make_env()
    env.backend.push_text("Hello there")
    env.backend.push_text("Hello there")
    users = [i for i in env.agent.chat_ctx.items if isinstance(i, ChatMessage)]
    assert [u.raw_text_content for u in users] == ["Hello there"]
    await env.backend.aclose()
