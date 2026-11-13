"""Call runtime: glue between WhatsApp, audio, STT/TTS and the call agent.

One ``CallRuntime`` owns:
  * the incoming-call watcher (polls ``WhatsAppCallDriver.detect_incoming``),
  * the dial entry used by voice commands and the scheduler,
  * per-call audio route + ``CallBridge`` + ears (VAD -> STT) + mouth (TTS),
  * the owner command queue written by the bridge/HUD (``commands.jsonl``).

Everything external is injectable, so tests run with fakes. Nothing starts
unless ``JARVIS_CALLS=1`` (see ``maybe_start``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from calls import events
from calls.agent import CallSession, default_settings, load_stores

log = logging.getLogger("calls.runtime")

POLL_S = 1.0
MISS_POLLS = 3  # consecutive empty polls before a ringing call counts as missed
COMMAND_MAX_AGE_S = 30.0
FRAME_SAMPLES = 512  # 32 ms at 16 kHz, what Silero and the bridge both use
TTS_RATE = 24_000

SETTING_KEYS = ("disclose_ai", "record_calls", "persona", "max_call_s")


def enabled() -> bool:
    return os.environ.get("JARVIS_CALLS", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# ---------------------------------------------------------------- settings
def runtime_settings(name: str, default: Any = None) -> Any:
    """Stored setting (calls.db) wins, then env, then default."""
    if name in SETTING_KEYS:
        try:
            from calls import store

            raw = store.get_setting(name)
            if raw is not None:
                if name in ("disclose_ai", "record_calls"):
                    return raw.strip().lower() in ("1", "true", "yes", "on")
                if name == "max_call_s":
                    return float(raw)
                return raw
        except Exception:
            log.debug("setting %s lookup failed", name, exc_info=True)
    return default_settings(name, default)


# --------------------------------------------------------------------- LLM
def make_llm(
    *,
    gemini: Callable[[str, list[dict], str], str] | None = None,
    claude: Callable[[str, str], tuple[str, str | None]] | None = None,
    order: str | None = None,
) -> Callable:
    """Build ``llm(system, history) -> str`` (async).

    Free tier first: Gemini on the owner's AI Studio key (JARVIS_CALL_MODEL,
    default the repo's direct-pipeline model), then the signed-in Claude CLI
    worker. ``JARVIS_CALL_LLM`` ("gemini,claude") reorders or drops backends.
    """
    order = (order or os.environ.get("JARVIS_CALL_LLM", "gemini,claude")).lower()
    names = [n.strip() for n in order.split(",") if n.strip()]

    def _contents(history: list[tuple[str, str]]) -> list[dict]:
        out: list[dict] = []
        for who, text in history:
            role = "model" if who == "jarvis" else "user"
            label = {"caller": "Caller", "owner": "Owner"}.get(who, "")
            part = f"{label}: {text}" if label and role == "user" else text
            if out and out[-1]["role"] == role:
                out[-1]["parts"][0]["text"] += "\n" + part
            else:
                out.append({"role": role, "parts": [{"text": part}]})
        if not out or out[0]["role"] != "user":
            out.insert(0, {"role": "user", "parts": [{"text": "(call connected)"}]})
        if out[-1]["role"] != "user":
            out.append({"role": "user", "parts": [{"text": "(continue)"}]})
        return out

    def _gemini(system: str, history: list[tuple[str, str]]) -> str:
        if gemini is not None:
            return gemini(
                system, _contents(history), os.environ.get("JARVIS_CALL_MODEL", "")
            )
        key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not key:
            raise RuntimeError("GOOGLE_API_KEY not set")
        from google import genai
        from google.genai import types

        model = os.environ.get("JARVIS_CALL_MODEL", "").strip() or _default_model()
        resp = genai.Client(api_key=key).models.generate_content(
            model=model,
            contents=_contents(history),
            config=types.GenerateContentConfig(
                system_instruction=system, max_output_tokens=220, temperature=0.4
            ),
        )
        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            raise RuntimeError("empty Gemini reply")
        return text

    def _claude(system: str, history: list[tuple[str, str]]) -> str:
        task = (
            "\n".join(f"{who}: {text}" for who, text in history) or "(call connected)"
        )
        fn = claude
        if fn is None:
            import claude_backend

            def fn(t, s):
                return claude_backend.reply(t, system=s, timeout=45)

        text, warn = fn(task, system)
        if not text:
            raise RuntimeError(warn or "empty Claude reply")
        return text.strip()

    backends = {"gemini": _gemini, "claude": _claude}

    def _run(system: str, history: list[tuple[str, str]]) -> str:
        last: Exception | None = None
        for n in names:
            fn = backends.get(n)
            if fn is None:
                continue
            try:
                return fn(system, history)
            except Exception as exc:
                log.warning("call llm backend %s failed: %s", n, exc)
                last = exc
        raise RuntimeError(f"no call LLM backend worked: {last}")

    async def llm(system: str, history: list[tuple[str, str]]) -> str:
        return await asyncio.to_thread(_run, system, history)

    return llm


def _default_model() -> str:
    try:
        from agent import DIRECT_LLM_MODEL  # heavy import; only on first real use

        return DIRECT_LLM_MODEL
    except Exception:
        return "gemini-3.8-flash"


# ------------------------------------------------------------------- voice
def make_synth(char_loader: Callable[[], dict] | None = None) -> Callable:
    """``synth(text, persona) -> pcm16 mono 24 kHz bytes`` using the Piper
    character voices. persona "jarvis" -> JARVIS_CALL_VOICE (default "jarvis");
    persona "owner" -> JARVIS_CALL_OWNER_VOICE (a character id with its own
    Piper model; falls back to the jarvis voice when unset or missing)."""

    cache: dict[str, Any] = {}

    def _char(persona: str):
        if "all" not in cache:
            if char_loader is not None:
                cache["all"] = char_loader()
            else:
                import characters

                cache["all"] = characters.load_all()
        chars = cache["all"]
        if persona == "owner":
            cid = os.environ.get("JARVIS_CALL_OWNER_VOICE", "").strip()
        else:
            cid = os.environ.get("JARVIS_CALL_VOICE", "jarvis").strip()
        return chars.get(cid) or chars.get("jarvis") or next(iter(chars.values()))

    def synth(text: str, persona: str = "jarvis") -> bytes:
        import character_voice
        from calls.call_bridge import resample_s16

        pcm, sr = character_voice.render_pcm(text, _char(persona))
        arr = np.asarray(pcm)
        if arr.dtype.kind == "f":
            arr = np.clip(arr, -1.0, 1.0) * 32767.0
        raw = arr.astype("<i2").tobytes()
        out, _ = resample_s16(raw, int(sr), TTS_RATE)
        return out

    return synth


# ------------------------------------------------------------------- ears
def make_transcribe() -> Callable[[np.ndarray], str]:
    """``transcribe(float32 mono 16 kHz) -> text`` via the local faster-whisper."""
    holder: dict[str, Any] = {}

    def transcribe(audio: np.ndarray) -> str:
        if "stt" not in holder:
            from local_stt import FasterWhisperSTT

            holder["stt"] = FasterWhisperSTT()
        segs = holder["stt"]._transcribe(audio, "en", threading.Event())
        return " ".join(s.text.strip() for s in segs).strip()

    return transcribe


class Ears:
    """Bridge PCM frames -> VAD endpointing -> STT -> ``on_text``.

    Runs on the event loop. ``barge()`` fires when the caller starts talking,
    so the runtime can flush Jarvis's speech at once.
    """

    def __init__(
        self,
        *,
        vad: Any,
        transcribe: Callable[[np.ndarray], str],
        on_text: Callable[[str], Any],
        barge: Callable[[], Any] | None = None,
        end_silence_ms: int = 600,
    ) -> None:
        from games.voice.vad import Endpointer

        self._ep = Endpointer(vad, end_silence_ms=end_silence_ms, max_s=15.0)
        self._transcribe = transcribe
        self._on_text = on_text
        self._barge = barge
        self._buf = b""
        self._was_speech = False
        self._q: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.muted = False
        self._bg: set[asyncio.Future] = set()

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._worker())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    def feed(self, pcm: bytes) -> None:
        """Sync callback for CallBridge.start(on_audio)."""
        if self.muted:
            return
        self._buf += pcm
        step = FRAME_SAMPLES * 2
        while len(self._buf) >= step:
            chunk, self._buf = self._buf[:step], self._buf[step:]
            frame = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
            utt = self._ep.feed(frame, time.monotonic())
            speech = self._ep.in_speech
            if speech and not self._was_speech and self._barge is not None:
                with contextlib.suppress(Exception):
                    r = self._barge()
                    if asyncio.iscoroutine(r):
                        self._bg.add(t := asyncio.ensure_future(r))
                        t.add_done_callback(self._bg.discard)
            self._was_speech = speech
            if utt is not None:
                self._q.put_nowait(utt.audio)

    async def _worker(self) -> None:
        while True:
            audio = await self._q.get()
            try:
                text = await asyncio.to_thread(self._transcribe, audio)
            except Exception:
                log.exception("stt failed")
                continue
            if text and text.strip():
                try:
                    r = self._on_text(text.strip())
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    log.exception("on_text failed")


# ----------------------------------------------------------------- runtime
class CallRuntime:
    def __init__(
        self,
        *,
        driver: Any,
        llm: Callable | None = None,
        synth: Callable | None = None,
        transcribe: Callable | None = None,
        vad_factory: Callable[[], Any] | None = None,
        route_factory: Callable[[], Any] | None = None,
        bridge_factory: Callable[[Any], Any] | None = None,
        stores: Any = None,
        settings: Callable = runtime_settings,
        owner_name: str | None = None,
        poll_s: float = POLL_S,
    ) -> None:
        self.driver = driver
        self.llm = llm
        self.synth = synth
        self.transcribe = transcribe
        self.vad_factory = vad_factory
        self.route_factory = route_factory
        self.bridge_factory = bridge_factory
        self.stores = stores if stores is not None else load_stores()
        self.settings = settings
        self.owner_name = owner_name or os.environ.get("JARVIS_OWNER_NAME", "the owner")
        self.poll_s = poll_s

        self.session: CallSession | None = None
        self._call_task: asyncio.Task | None = None
        self._seen_key: str | None = None
        self._stop = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._tasks: list[asyncio.Task] = []
        self._cmd_offset = 0
        self._cmd_seen: set[str] = set()
        self._sched_stop: threading.Event | None = None

    # ---- state ----
    @property
    def busy(self) -> bool:
        return self._call_task is not None and not self._call_task.done()

    def _lazy_defaults(self) -> None:
        if self.llm is None:
            self.llm = make_llm()
        if self.synth is None:
            self.synth = make_synth()
        if self.transcribe is None:
            self.transcribe = make_transcribe()
        if self.vad_factory is None:
            from games.voice.vad import make_vad

            self.vad_factory = make_vad
        if self.route_factory is None:
            from calls.audio_route import AudioRoute

            self.route_factory = AudioRoute
        if self.bridge_factory is None:

            def _bf(route):
                from calls.call_bridge import CallBridge

                return CallBridge(sink_in=route.sink_in, sink_out=route.sink_out)

            self.bridge_factory = _bf

    # ---- number resolution ----
    def resolve_number(self, caller: str) -> str:
        caller = (caller or "").strip()
        try:
            from calls import store

            n = (
                store.try_normalize(caller)
                if any(c.isdigit() for c in caller)
                else None
            )
            if n:
                return n
            contacts = getattr(self.stores, "contacts", None)
            if contacts is not None and caller:
                hits = contacts.find(caller)
                if len(hits) == 1:
                    return hits[0].number
        except Exception:
            log.debug("number resolve failed", exc_info=True)
        return caller

    # ---- watcher ----
    async def poll_once(self) -> bool:
        """One watcher tick. True when a session was spawned."""
        inc = await self.driver.detect_incoming()
        if inc is None:
            self._seen_key = None
            return False
        key = f"{inc.caller}|{sorted((inc.raw or {}).items()) if inc.raw else ''}"
        if self.busy or key == self._seen_key:
            return False
        self._seen_key = key
        number = self.resolve_number(inc.caller)
        self._spawn("in", number, "")
        return True

    async def watch(self) -> None:
        while not self._stop:
            try:
                await self.poll_once()
            except Exception:
                log.exception("incoming watcher failed")
            await asyncio.sleep(self.poll_s)

    # ---- dial entry ----
    def dial(self, job: dict) -> dict:
        """Dialer for voice_commands.set_dialer / scheduler. Sync, thread-safe."""
        if self._loop is None or self._stop:
            return {"status": "failed", "reason": "call runtime is not running"}
        if self.busy:
            return {"status": "blocked", "reason": "a call is already active"}
        number = str(job.get("number") or "").strip()
        if not number:
            return {"status": "failed", "reason": "no number"}
        fut = asyncio.run_coroutine_threadsafe(self._dial_on_loop(job), self._loop)
        try:
            return fut.result(timeout=10)
        except Exception as exc:
            return {"status": "failed", "reason": str(exc)}

    async def _dial_on_loop(self, job: dict) -> dict:
        if self.busy:
            return {"status": "blocked", "reason": "a call is already active"}
        self._spawn("out", str(job["number"]).strip(), str(job.get("goal") or ""))
        return {"status": "started"}

    # ---- per-call ----
    def _spawn(self, direction: str, number: str, goal: str) -> None:
        self._call_task = asyncio.ensure_future(self.run_call(direction, number, goal))

    async def run_call(self, direction: str, number: str, goal: str = "") -> None:
        self._lazy_defaults()
        route = self.route_factory()
        ok = await asyncio.to_thread(route.setup)
        if not ok:
            log.error("audio route setup failed; call not handled")
            with contextlib.suppress(Exception):
                self.stores.calllog.audit(
                    "call_skipped",
                    {"number": number, "reason": "audio route setup failed"},
                )
            await asyncio.to_thread(route.teardown)
            return
        bridge = self.bridge_factory(route)
        session: CallSession | None = None
        ears: Ears | None = None
        route_task: asyncio.Task | None = None
        try:

            async def speaker(text: str, persona: str) -> None:
                pcm = await asyncio.to_thread(self.synth, text, persona)
                await bridge.play(pcm)
                # hold until queued audio drains (flush on barge-in empties it)
                while getattr(bridge, "pending_ms", 0) > 0 and session.state not in (
                    "ended",
                    "owner",
                ):
                    await asyncio.sleep(0.05)

            session = CallSession(
                direction=direction,
                number=number,
                llm=self.llm,
                speaker=speaker,
                driver=self.driver,
                stores=self.stores,
                bridge=bridge,
                settings=self.settings,
                goal=goal,
                owner_name=self.owner_name,
            )
            self.session = session
            ears = Ears(
                vad=self.vad_factory(),
                transcribe=self.transcribe,
                on_text=session.on_caller_text,
                barge=lambda: self._barge(session, bridge),
            )
            ears.start()
            await bridge.start(ears.feed)
            route_task = asyncio.ensure_future(self._route_loop(route))
            if direction == "in":
                await session.start_inbound()
            else:
                await session.start_outbound()
            monitor = asyncio.ensure_future(self._monitor(session, direction))
            try:
                await session.wait_ended()
            finally:
                monitor.cancel()
        except Exception:
            log.exception("call failed")
            if session is not None and session.state != "ended":
                with contextlib.suppress(Exception):
                    await session.end("failed")
        finally:
            if route_task is not None:
                route_task.cancel()
            if ears is not None:
                await ears.close()
            with contextlib.suppress(Exception):
                await bridge.stop()
            await asyncio.to_thread(route.teardown)
            self.session = None

    async def _barge(self, session: CallSession, bridge: Any) -> None:
        if session.state == "handling" and getattr(bridge, "pending_ms", 0) > 0:
            await bridge.flush()

    async def _route_loop(self, route: Any) -> None:
        """Keep moving WhatSie's call streams onto our sinks while the call runs."""
        try:
            while True:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(route.route_streams)
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            return

    async def _monitor(self, session: CallSession, direction: str) -> None:
        """End the session when the far side vanishes (missed ring / caller hung up)."""
        empty = 0
        was_in_call = False
        while session.state != "ended":
            await asyncio.sleep(self.poll_s)
            try:
                if session.state == "ringing":
                    inc = await self.driver.detect_incoming()
                    empty = empty + 1 if inc is None else 0
                    if direction == "in" and empty >= MISS_POLLS:
                        await session.end("missed")
                        return
                else:
                    live = await self.driver.is_in_call()
                    was_in_call = was_in_call or bool(live)
                    empty = 0 if live else empty + 1
                    if was_in_call and empty >= MISS_POLLS:
                        await session.end("caller_hangup")
                        return
            except Exception:
                log.debug("monitor tick failed", exc_info=True)

    # ---- owner command queue ----
    def _commands_path(self):
        return events.calls_dir() / "commands.jsonl"

    def _seek_commands_to_end(self) -> None:
        try:
            self._cmd_offset = self._commands_path().stat().st_size
        except OSError:
            self._cmd_offset = 0

    async def poll_commands(self) -> int:
        path = self._commands_path()
        try:
            size = path.stat().st_size
        except OSError:
            return 0
        if size < self._cmd_offset:
            self._cmd_offset = 0
        if size == self._cmd_offset:
            return 0
        with path.open("rb") as f:
            f.seek(self._cmd_offset)
            data = f.read()
        end = data.rfind(b"\n")
        if end < 0:
            return 0
        self._cmd_offset += end + 1
        n = 0
        for raw in data[: end + 1].splitlines():
            try:
                rec = json.loads(raw.decode("utf-8"))
                cid, name = str(rec["id"]), str(rec["name"])
                ts = float(rec.get("ts", 0))
            except (ValueError, KeyError, TypeError, UnicodeError):
                continue
            if cid in self._cmd_seen or time.time() - ts > COMMAND_MAX_AGE_S:
                continue
            self._cmd_seen.add(cid)
            res = await events.handle_command(name)
            n += 1
            with contextlib.suppress(Exception):
                self.stores.calllog.audit(
                    "hud_command",
                    {"name": name, "ok": res.get("ok"), "src": rec.get("src")},
                )
        return n

    async def command_loop(self) -> None:
        self._seek_commands_to_end()
        while not self._stop:
            try:
                await self.poll_commands()
            except Exception:
                log.exception("command poll failed")
            await asyncio.sleep(0.25)

    # ---- lifecycle ----
    async def start(self) -> None:
        """Run on the target loop: start watcher + command poller."""
        self._loop = asyncio.get_running_loop()
        self._stop = False
        self._tasks = [
            asyncio.ensure_future(self.watch()),
            asyncio.ensure_future(self.command_loop()),
        ]

    async def shutdown(self) -> None:
        self._stop = True
        s = self.session
        if s is not None and s.state != "ended":
            with contextlib.suppress(Exception):
                await s.handle_command("hangup")
        for t in self._tasks:
            t.cancel()
        if self._call_task is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(self._call_task, 5)

    def start_thread(self) -> threading.Thread:
        """Own event loop on a daemon thread; also wires dialer + scheduler."""
        ready = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.start())
            ready.set()
            try:
                loop.run_forever()
            finally:
                loop.run_until_complete(self.shutdown())
                loop.close()

        self._thread = threading.Thread(target=run, name="call-runtime", daemon=True)
        self._thread.start()
        ready.wait(10)
        self._wire_dialer()
        return self._thread

    def _install_command_proxy(self) -> None:
        """events.handle_command must run on the runtime loop (the session's
        locks live there); voice tools call it from the agent's loop."""
        orig = self._orig_handle = events.handle_command
        loop = self._loop

        async def proxy(name: str) -> dict:
            try:
                here = asyncio.get_running_loop()
            except RuntimeError:
                here = None
            if loop is None or here is loop or not loop.is_running():
                return await orig(name)
            fut = asyncio.run_coroutine_threadsafe(orig(name), loop)
            return await asyncio.wrap_future(fut)

        events.handle_command = proxy

    def _wire_dialer(self) -> None:
        from calls import scheduler, voice_commands

        self._install_command_proxy()
        voice_commands.set_dialer(self.dial)
        _t, self._sched_stop = scheduler.start_thread(
            voice_commands.dial_sync, expiry=scheduler.RuleExpiry()
        )

    def stop_thread(self) -> None:
        from calls import voice_commands

        voice_commands.set_dialer(None)
        orig = getattr(self, "_orig_handle", None)
        if orig is not None:
            events.handle_command = orig
        if self._sched_stop is not None:
            self._sched_stop.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(10)


_runtime: CallRuntime | None = None


def maybe_start(driver: Any = None) -> CallRuntime | None:
    """Start the runtime when JARVIS_CALLS=1. Idempotent; never raises."""
    global _runtime
    if not enabled():
        return None
    if _runtime is not None:
        return _runtime
    try:
        if driver is None:
            from calls.wa_call import CdpCallDriver

            driver = CdpCallDriver()
        _runtime = CallRuntime(driver=driver)
        _runtime.start_thread()
        log.info("call runtime started (JARVIS_CALLS=1)")
    except Exception:
        log.exception("call runtime failed to start")
        _runtime = None
    return _runtime
