"""Always-on hands-free client: say "hey Jarvis" -> talk -> auto-leave.

Idle state costs nothing cloud-side: the laptop mic is monitored
locally by openWakeWord (fully offline, no account, no key) and no
room exists until the keyword fires. On wake the client says
"Yes, Sir." with the local voice ($0), mints a token that dispatches
the worker into a fresh room, streams the mic, and plays the agent
back. When the agent hangs up (60s of silence) the room closes and
we return to listening.

Needs:
- LiveKit URL/key/secret, read from frontend/.env.local (already there).
- AGENT_NAME (default "my-agent") running via the worker.

Wake-word deps (openwakeword needs Python <= 3.11 for tflite wheels,
but we use the ONNX backend) live in a dedicated venv:

    uv venv .venv-wake --python 3.11
    uv pip install --python .venv-wake/bin/python -e . openwakeword sounddevice
    .venv-wake/bin/python src/wake_client.py   # first run downloads models once

(JARVIS_LOCAL=1 not required; this client only joins rooms, it never
touches the laptop itself.)

JARVIS_WAKE_THRESHOLD (default 0.5) tunes sensitivity; lower hears
more, higher false-alarms less.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import threading
import time
from pathlib import Path

logger = logging.getLogger("jarvis-wake")

MIC_RATE = 48000
BLOCKSIZE = 1536  # 48k samples decimate exactly to a 512-sample 16k frame
OWW_FRAME = 1280  # openWakeWord native frame: 80ms at 16kHz
WAKE_MODEL = "hey_jarvis"
# Max chars of a typed seed that may ride a talk summon into the call.
# Spoken openers are short; anything longer belongs in /chat text mode.
TALK_TEXT_MAX = 500
AGENT_JOIN_TIMEOUT = 25.0
SCHOOL_CONFIRM_S = 2.5  # audio after a school-mode wake checked for "hey Jarvis"
IDENTITY = "jarvis-master"
# In-call "hey Jarvis" restarts the call only when it has gone stale: the
# agent silent this long. Mid-conversation, the name just goes to the agent.
REWAKE_STALE_S = 20.0
REWAKE_THRESHOLD = 0.8
REWAKE_FRAMES = 2  # consecutive frames at or above REWAKE_THRESHOLD


def wake_threshold() -> float:
    """Sensitivity 0..1: explicit > JARVIS_WAKE_THRESHOLD > 0.5 default."""
    try:
        return float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5"))
    except ValueError:
        return 0.5


def frame_16k_chunks(
    pending: list[int], new_samples: list[int], size: int = OWW_FRAME
) -> tuple[list[list[int]], list[int]]:
    """Buffer 16k samples into full native frames. Pure.

    Returns (complete_frames, remainder). Remainder carries over so no
    audio is dropped between mic blocks.
    """
    buf = [*pending, *new_samples]
    frames = [buf[i : i + size] for i in range(0, len(buf) - len(buf) % size, size)]
    return frames, buf[len(frames) * size :]


def wake_score(prediction: dict, name: str = WAKE_MODEL) -> float:
    """Confidence 0..1 for our model from an openWakeWord prediction. Pure."""
    try:
        return float(prediction.get(name, 0.0))
    except (TypeError, ValueError):
        return 0.0


def score_frames(model, pending16):
    """Feed EVERY whole native frame to the model, in order. Pure-ish.

    openWakeWord is streaming: predict() appends each frame to its own
    mel/embedding buffer, so a skipped frame is a hole in the audio it
    hears. A perf gate that skipped silence + 2 of 3 idle frames took
    "hey Jarvis" from 1.00 to 0.00 -- never skip. Returns (scores, the
    partial-frame remainder to keep pending).
    """
    scores: list[float] = []
    while pending16.size >= OWW_FRAME:
        frame = pending16[:OWW_FRAME]
        pending16 = pending16[OWW_FRAME:]
        try:
            scores.append(wake_score(model.predict(frame)))
        except Exception as exc:
            logger.debug("wake predict failed: %s", exc)
    return scores, pending16


def should_rewake(hot_run: int, agent_quiet_s: float) -> bool:
    """In-call hotword: restart the call? Pure.

    Needs a sustained detection (REWAKE_FRAMES) and an agent that has
    been silent REWAKE_STALE_S. A call held open by background noise
    otherwise leaves "hey Jarvis" unanswered indefinitely.
    """
    return hot_run >= REWAKE_FRAMES and agent_quiet_s >= REWAKE_STALE_S


def drain_queue(queue: asyncio.Queue) -> int:
    """Drop everything queued; returns how many items. Stale mic audio
    replayed after a call re-fired the hotword."""
    n = 0
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return n
        n += 1


def room_has_active_call(identities: list[str]) -> bool:
    """True when a room already hosts a call (user + agent). Pure.

    Rooms are unique per session, so 2+ participants means someone is
    already talking to an agent — summoning another would layer a
    second Jarvis voice over the first.
    """
    return len(identities) >= 2


async def active_call_exists(creds: dict[str, str]) -> bool:
    """True when any LiveKit room already hosts a call.

    Fails open (returns False) on API errors: a missed double-voice
    guard is better than going deaf because Cloud hiccuped.
    """
    try:
        from livekit import api

        async with api.LiveKitAPI(
            creds["LIVEKIT_URL"], creds["LIVEKIT_API_KEY"], creds["LIVEKIT_API_SECRET"]
        ) as lk:
            rooms = await lk.room.list_rooms(api.ListRoomsRequest())
            for room in rooms.rooms:
                participants = await lk.room.list_participants(
                    api.ListParticipantsRequest(room=room.name)
                )
                if room_has_active_call(
                    [p.identity for p in participants.participants]
                ):
                    return True
    except Exception as exc:
        logger.warning("call check failed, summoning anyway: %s", exc)
    return False


def load_livekit_env(path: Path | None = None) -> dict[str, str]:
    """Read LiveKit creds from frontend/.env.local. Pure-ish (reads file)."""
    env_path = (
        path or Path(__file__).resolve().parent.parent / "frontend" / ".env.local"
    )
    found: dict[str, str] = {}
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            if key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
                found[key] = value
    except Exception as exc:
        logger.debug("livekit env read failed: %s", exc)
    return found


def downsample_48k_to_16k(frame_48k) -> list[int]:
    """Decimate one 1536-sample mic block to Porcupine's 512-frame. Pure."""
    return [int(x) for x in frame_48k[::3][:512]]


def summon_room_name(now: float | None = None) -> str:
    """Unique room per summon so stale sessions never collide. Pure."""
    return f"jarvis-{int(now if now is not None else time.time())}"


#: File under $JARVIS_HOME naming the live call room for the HUD.
HUD_ROOM_FILE = "hud_room"

#: Rooms older than this are treated as stale (crashed wake never cleared).
HUD_ROOM_MAX_AGE_S = 15 * 60


def hud_room_path() -> Path:
    """Path of the HUD room file. Pure (env only)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / HUD_ROOM_FILE


def publish_hud_room(room: str) -> bool:
    """Publish the live room for the HUD watcher. Fail-soft, never raises."""
    try:
        path = hud_room_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(room.strip())
        return True
    except OSError as exc:
        logger.debug("hud room publish failed: %s", exc)
        return False


def clear_hud_room() -> None:
    """Remove the HUD room file (call over). Fail-soft, never raises."""
    try:
        hud_room_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("hud room clear failed: %s", exc)


def read_hud_room(max_age_s: float = HUD_ROOM_MAX_AGE_S) -> str | None:
    """Live room for HUD joiners, or None (no call / stale / unreadable).

    Pure-ish (reads one small file). Staleness guards a wake that died
    without clearing: a real call never idles past the agent's own
    silence hangup, so anything older than `max_age_s` is garbage.
    """
    try:
        path = hud_room_path()
        room = path.read_text().strip()
        if not room:
            return None
        age = time.time() - path.stat().st_mtime
        if age > max_age_s:
            return None
        return room
    except OSError:
        return None


def shell_talk_candidates() -> list[list[str]]:
    """Argvs that ask the running shell to show the overlay. Pure (env).

    Order: repo debug build, installed binaries on PATH. Every entry ends
    with the `talk` verb (single-instance CLI: show overlay + EVENT_TALK).
    """
    cands: list[list[str]] = []
    repo = Path(__file__).resolve().parent.parent
    debug_shell = repo / "shell" / "src-tauri" / "target" / "debug" / "jarvis-shell"
    cands.append([str(debug_shell), "talk"])
    cands.append(["jarvis-shell", "talk"])
    cands.append(["jarvis", "talk"])
    return cands


def summon_overlay() -> None:
    """Show the HUD overlay, fire-and-forget. Fail-soft, never raises.

    Runs the shell `talk` verb on a daemon thread so the wake loop never
    blocks on a missing binary: no shell installed, no UI — the headless
    voice path below still works.
    """

    def _try() -> None:
        import subprocess

        for argv in shell_talk_candidates():
            try:
                subprocess.run(argv, timeout=5, capture_output=True)
                return
            except Exception as exc:
                logger.debug("overlay summon via %s failed: %s", argv[0], exc)
                continue

    thread = threading.Thread(target=_try, name="jarvis-summon-ui", daemon=True)
    thread.start()


def wake_socket_path() -> Path:
    """Unix socket for out-of-process mic control. Pure (env only).

    ``$JARVIS_HOME/wake.sock`` (default ``~/.jarvis/wake.sock``) — the
    same ``JARVIS_HOME`` contract the shell sets for every sidecar.
    """
    home = os.environ.get("JARVIS_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / "wake.sock"


def handle_mic_command(
    msg: object, *, muted: bool, threshold: float, in_call: bool
) -> tuple[dict, bool | None]:
    """Handle one mic-control message. Pure.

    Returns ``(reply, new_muted)`` where ``new_muted`` is ``None`` unless
    the message flips the mute. ``{"mute": bool}`` pauses/resumes both the
    hotword loop and the in-call mic pump; ``{"status": true}`` reports
    ``{muted, threshold, in_call}`` without changing anything.
    """
    if not isinstance(msg, dict):
        return {"ok": False, "error": "message must be a JSON object"}, None
    if "mute" in msg:
        if not isinstance(msg["mute"], bool):
            return {"ok": False, "error": "'mute' must be true or false"}, None
        return {"ok": True, "muted": msg["mute"]}, msg["mute"]
    if msg.get("status") is True:
        return (
            {"ok": True, "muted": muted, "threshold": threshold, "in_call": in_call},
            None,
        )
    return (
        {
            "ok": False,
            "error": 'unknown command (want {"mute": bool} or {"status": true})',
        },
        None,
    )


def handle_talk_request(msg: object, *, muted: bool) -> tuple[dict, bool, bool]:
    """Handle one PTT talk request. Pure.

    Returns ``(reply, wants_talk, unmute)``. ``{"talk": true}`` asks for a
    voice call right now (HUD NumpadEnter / shell talk) — the same summon
    the hotword performs, without saying "hey Jarvis". An explicit talk
    press unmutes first: holding PTT while muted must still let Sir speak.
    Anything else is rejected with ``wants_talk=False``.
    """
    if not isinstance(msg, dict):
        return {"ok": False, "error": "message must be a JSON object"}, False, False
    if msg.get("talk") is not True:
        return (
            {"ok": False, "error": 'unknown command (want {"talk": true})'},
            False,
            False,
        )
    return {"ok": True, "talk": "requested", "muted": False}, True, muted


def extract_talk_text(msg: object) -> str | None:
    """Typed seed for a talk summon, if present and sane. Pure.

    Returns the stripped text (1..TALK_TEXT_MAX chars) from
    ``{"talk": true, "text": "..."}``, else None. Blank, over-long,
    or non-string text is not a seed — callers treat it as no text
    (bridge rejects the over-long/wrong-typed cases with 400 first,
    so reaching here with one just means "plain talk").
    """
    if not isinstance(msg, dict):
        return None
    text = msg.get("text")
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or len(text) > TALK_TEXT_MAX:
        return None
    return text


def mint_summon_token(
    *, url: str, api_key: str, api_secret: str, room: str, agent_name: str,
    reason: str = "wake",
) -> str:
    """JWT that joins `room` and dispatches the worker. Pure (no I/O)."""
    from livekit.api import (
        AccessToken,
        RoomAgentDispatch,
        RoomConfiguration,
        VideoGrants,
    )

    token = AccessToken(api_key, api_secret)
    token.with_identity(IDENTITY).with_name("Sir")
    # Why we summoned ("wake", "daddy"): the agent picks its greeting.
    token.with_attributes({"jarvis.wake": reason})
    token.with_grants(VideoGrants(room_join=True, room=room))
    token.with_room_config(
        RoomConfiguration(agents=[RoomAgentDispatch(agent_name=agent_name)])
    )
    return token.to_jwt()


class WakeClient:
    def __init__(self) -> None:
        try:
            from dotenv import load_dotenv

            load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
        except Exception as exc:
            logger.debug("dotenv load failed: %s", exc)
        self._creds = load_livekit_env()
        self._threshold = wake_threshold()
        self._agent_name = (
            os.environ.get("AGENT_NAME", "my-agent").strip() or "my-agent"
        )
        self._ack_pcm: bytes = b""
        self._ack_rate = 22050
        # "daddy" second wake phrase (keyword_spot): Whisper worker is
        # spawned lazily on the first utterance it has to check.
        from keyword_spot import WhisperWorker

        self._spotter = WhisperWorker()
        self._spot_tasks: set = set()
        # Mic mute state (tray/HUD driven via the wake.sock listener below).
        # Guarded by a lock: set from the socket thread, read from the
        # asyncio mic loops and the hotword loop.
        self._mic_lock = threading.Lock()
        self._muted = False
        self._in_call = False
        # PTT talk request (HUD NumpadEnter / shell talk via wake.sock).
        # Set from the socket thread, consumed by the hotword loop, which
        # then runs the exact hotword summon path (ack + overlay + call).
        self._talk_event = threading.Event()
        # Typed seed for the next summon ("text which activates voice").
        # Guarded by _mic_lock like the mute state; consumed once by
        # _summon_session, never kept across calls.
        self._pending_text = ""
        # In-call hotword (see should_rewake): the openWakeWord model is
        # shared with the listen loop, which is parked while a call runs.
        self._model = None
        self._rewake: asyncio.Event | None = None
        self._last_agent_voice = 0.0
        # False while a plain call runs: the listen loop's queue is unread
        # then, and buffering a whole call's audio grew it without bound.
        self._listen_queue_live = True

    @property
    def muted(self) -> bool:
        with self._mic_lock:
            return self._muted

    def set_muted(self, muted: bool) -> None:
        with self._mic_lock:
            self._muted = bool(muted)

    def _set_in_call(self, in_call: bool) -> None:
        with self._mic_lock:
            self._in_call = bool(in_call)

    def _mic_snapshot(self) -> tuple[bool, float, bool]:
        with self._mic_lock:
            return self._muted, self._threshold, self._in_call

    def start_mic_control(self) -> threading.Thread:
        """Serve ``{"mute"/"status"}`` JSON on ``wake.sock`` (daemon thread).

        Mute pauses hotword detection AND the in-call mic pump; the room
        stays joined so unmute resumes instantly. Returns the thread.
        """
        thread = threading.Thread(
            target=self._serve_mic_control, name="jarvis-mic-control", daemon=True
        )
        thread.start()
        return thread

    def _serve_mic_control(self) -> None:
        path = wake_socket_path()
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(path))
        except OSError:
            # Bind failed: live listener or a stale file from a dead one.
            # Probe — unlink only when nothing answers.
            if self._socket_live(str(path)):
                logger.warning("wake.sock already served; mic control off here")
                server.close()
                return
            with contextlib.suppress(OSError):
                path.unlink()
            try:
                server.bind(str(path))
            except OSError as exc:
                logger.warning("mic control socket unavailable: %s", exc)
                server.close()
                return
        server.listen(8)
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(5.0)
                try:
                    raw = conn.recv(4096)
                except OSError:
                    continue
                try:
                    msg = json.loads(raw.decode())
                except (ValueError, UnicodeDecodeError):
                    reply: dict = {"ok": False, "error": "message must be JSON"}
                    new_muted: bool | None = None
                else:
                    muted, threshold, in_call = self._mic_snapshot()
                    if isinstance(msg, dict) and msg.get("talk") is True:
                        reply, wants_talk, unmute = handle_talk_request(
                            msg, muted=muted
                        )
                        new_muted = None
                        if wants_talk:
                            if unmute:
                                self.set_muted(False)
                            # A typed seed rides the fresh summon into the
                            # call; mid-call it has nowhere to go (the room
                            # handle lives in _summon_session), so it is
                            # dropped and reported rather than kept stale.
                            seed = extract_talk_text(msg)
                            if seed and not in_call:
                                with self._mic_lock:
                                    self._pending_text = seed
                                reply["text"] = "seeded"
                            elif seed:
                                reply["text"] = "dropped:in-call"
                            self._talk_event.set()
                    else:
                        reply, new_muted = handle_mic_command(
                            msg, muted=muted, threshold=threshold, in_call=in_call
                        )
                if new_muted is not None:
                    self.set_muted(new_muted)
                with contextlib.suppress(OSError):
                    conn.sendall(json.dumps(reply).encode())

    @staticmethod
    def _socket_live(path: str) -> bool:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(1.0)
            probe.connect(path)
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def _check_config(self) -> None:
        missing = [
            k
            for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
            if not self._creds.get(k)
        ]
        if missing:
            raise RuntimeError(f"Missing in frontend/.env.local: {', '.join(missing)}")

    def _render_ack(self) -> None:
        """Pre-render 'Yes, Sir.' with the local voice for instant feedback."""
        try:
            try:
                from src.local_voice import PiperTTS, sentence_split
            except ImportError:
                from local_voice import PiperTTS, sentence_split

            plugin = PiperTTS()
            if not plugin._model_path.exists():
                return
            out = bytearray()
            for sentence in sentence_split("Yes, Sir."):
                out += plugin._render_sentence(sentence)
            self._ack_pcm = bytes(out)
            self._ack_rate = plugin.sample_rate
        except Exception as exc:
            logger.warning("ack render failed: %s", exc)

    async def run_forever(self) -> None:
        self._check_config()
        self.start_mic_control()
        self._render_ack()
        from openwakeword.model import Model

        # ONNX backend: tflite-runtime wheels don't cover this system's
        # Python, and ONNX scores identically. Models download once on
        # first run (cached under site-packages/openwakeword/resources).
        model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
        self._model = model
        logger.warning(
            "listening for 'hey Jarvis' (offline, nothing leaves the laptop)"
        )
        await self._listen_loop(model)

    async def _listen_loop(self, model) -> None:
        import numpy as np
        import sounddevice as sd

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _audio_callback(indata, frames, time_info, status) -> None:
            if status:
                logger.warning("mic status: %s", status)
            if not self._listen_queue_live:
                return
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        with sd.RawInputStream(
            samplerate=MIC_RATE,
            channels=1,
            dtype="int16",
            blocksize=BLOCKSIZE,
            callback=_audio_callback,
        ):
            pending16 = np.zeros(0, dtype=np.int16)
            import keyword_spot as _spot
            import school as _school

            spot_on = _spot.enabled()
            segmenter = _spot.Segmenter() if spot_on else None
            spot_state = {"busy": False, "last": 0.0}
            hot = {"run": 0}
            # ~1.5 s of audio before the wake frame: in school mode it is
            # replayed so the transcript starts "Hey Jarvis, ..." (the
            # agent only answers turns that address it).
            import collections

            ring: collections.deque = collections.deque(
                maxlen=int(1.5 * MIC_RATE / BLOCKSIZE)
            )
            if _school.is_school():
                # Warm the Whisper worker so the first school wake is quick.
                loop.run_in_executor(None, self._spotter.transcribe, np.zeros(16000, np.int16))

            async def _check_daddy(seg) -> None:
                try:
                    text = await loop.run_in_executor(None, self._spotter.transcribe, seg)
                finally:
                    spot_state["busy"] = False
                if not _spot.has_daddy(text) or self._in_call or self.muted:
                    return
                if _school.is_school():
                    return  # school mode: only a strict "hey Jarvis" summons
                now = time.monotonic()
                if now - spot_state["last"] < _spot.COOLDOWN_S:
                    return
                spot_state["last"] = now
                logger.warning("daddy wake heard: %r", text[:80])
                summon_overlay()
                await self._summon_session(reason="daddy")

            while True:
                raw = await queue.get()
                # Zero-copy int16 view, vector decimate 48k->16k (was a
                # per-sample int.from_bytes Python loop + list copies).
                block = np.frombuffer(raw, dtype=np.int16)
                if block.size < BLOCKSIZE:
                    continue
                ring.append(raw)
                pending16 = np.concatenate([pending16, block[::3][:512]])
                if self.muted:
                    self._talk_event.clear()  # never summon while muted
                    pending16 = pending16[-OWW_FRAME:]  # bounded while deaf
                    continue  # tray/HUD mute: deaf but still listening locally
                if self._talk_event.is_set():
                    # PTT (HUD NumpadEnter / shell talk): the hotword summon
                    # path, without the hotword. In-call presses just make
                    # sure the overlay is up (never layer a second call).
                    self._talk_event.clear()
                    pending16 = np.zeros(0, dtype=np.int16)
                    logger.warning("PTT talk requested")
                    self._play_ack()
                    summon_overlay()
                    if not self._in_call:
                        await self._summon_with_rewake()
                        drain_queue(queue)
                        with contextlib.suppress(Exception):
                            model.reset()
                    continue
                if segmenter is not None and not self._in_call and not _school.is_school():
                    for seg in segmenter.feed(block[::3][:512]):
                        if not spot_state["busy"]:
                            spot_state["busy"] = True
                            task = asyncio.create_task(_check_daddy(seg))
                            self._spot_tasks.add(task)
                            task.add_done_callback(self._spot_tasks.discard)
                scores, pending16 = score_frames(model, pending16)
                strict = _school.is_school()
                need = max(self._threshold, _school.WAKE_THRESHOLD) if strict else self._threshold
                for score in scores:
                    hot["run"] = hot["run"] + 1 if score >= need else 0
                    if hot["run"] >= (_school.WAKE_FRAMES if strict else 1):
                        hot["run"] = 0
                        logger.warning("wake word detected (%.2f)%s", score, " [school]" if strict else "")
                        summon_overlay()
                        if strict:
                            # openWakeWord also fires on a bare "Jarvis" in
                            # chatter. Listen ~2.5 s more, transcribe locally,
                            # and only summon if Sir addressed Jarvis. The
                            # captured audio is replayed into the call, so
                            # nothing said after the wake word is lost.
                            heard = list(ring) + await self._record_question(queue)
                            pcm16 = np.concatenate(
                                [np.frombuffer(b, dtype=np.int16)[::3] for b in heard]
                            )
                            text = await loop.run_in_executor(
                                None, self._spotter.transcribe, pcm16
                            )
                            if _school.addressed(text):
                                question = _school.question_after_address(text)
                                logger.warning("school wake confirmed: %r", text[:80])
                                # The question rides in as the opening text
                                # turn (replaying audio split at the comma
                                # pause and Gemini answered "aves").
                                with self._mic_lock:
                                    self._pending_text = question
                                await self._summon_session(mic_queue=queue)
                            else:
                                logger.warning("school wake ignored (not addressed): %r", text[:80])
                            ring.clear()
                        else:
                            self._play_ack()
                            await self._summon_with_rewake()
                        drain_queue(queue)
                        pending16 = np.zeros(0, dtype=np.int16)
                        # openWakeWord keeps its own recent-audio buffer: a
                        # stale "hey Jarvis" in it re-fired after the call.
                        with contextlib.suppress(Exception):
                            model.reset()
                        break

    def _play_ack(self) -> None:
        if not self._ack_pcm:
            return
        try:
            import sounddevice as sd

            with contextlib.suppress(Exception):
                sd.stop()
                sd.wait()
            sd.play(
                self._bytes_to_int16(self._ack_pcm),
                samplerate=self._ack_rate,
                blocking=False,
            )
        except Exception as exc:
            logger.warning("ack playback failed: %s", exc)

    @staticmethod
    def _bytes_to_int16(raw: bytes):
        import numpy as np

        return np.frombuffer(raw, dtype=np.int16)

    @staticmethod
    async def _cancel_task(task: asyncio.Task | None) -> None:
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _summon_with_rewake(self, **kwargs) -> None:
        """Summon; while the call ends by an in-call "hey Jarvis" (stale
        call), ack and summon a fresh one."""
        while await self._summon_session(**kwargs):
            logger.warning("hey Jarvis in a stale call: starting a fresh one")
            kwargs = {}
            self._play_ack()
            summon_overlay()

    async def _summon_session(
        self, reason: str = "wake", mic_queue=None, preroll: list | None = None
    ) -> bool:
        """Run one call. True when it ended by an in-call rewake."""
        from livekit import rtc

        # Never layer a second Jarvis over an ongoing call (an earlier
        # summon that hasn't hung up yet). The HUD stays visible either
        # way: "hey Jarvis" always summons the UI.
        if await active_call_exists(self._creds):
            logger.warning("already in a call; staying out")
            summon_overlay()
            return False

        # Consume one typed seed (text→voice trigger): delivered as the
        # opening user turn once the agent joins, then forgotten. Taken
        # here so a summon that never connects drops it instead of
        # leaking it into a later, unrelated call.
        with self._mic_lock:
            seed_text = self._pending_text
            self._pending_text = ""

        # The HUD joins this room receive-only (eyes on the call; the mic
        # permission is denied in the webview, so the HUD never publishes).
        # Publish the name only once WE are in — the watcher joins live
        # rooms, never stale names.
        room_name = summon_room_name()
        jwt = mint_summon_token(
            url=self._creds["LIVEKIT_URL"],
            api_key=self._creds["LIVEKIT_API_KEY"],
            api_secret=self._creds["LIVEKIT_API_SECRET"],
            room=room_name,
            agent_name=self._agent_name,
            reason=reason,
        )
        room = rtc.Room()
        disconnected = asyncio.Event()
        agent_audio: asyncio.Queue = asyncio.Queue()
        agent_ready = asyncio.Event()

        @room.on("disconnected")
        def _on_disconnected() -> None:
            disconnected.set()

        @room.on("track_subscribed")
        def _on_track(track, _publication, _participant) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                agent_ready.set()
                agent_audio.put_nowait(track)

        rewake = asyncio.Event()
        self._rewake = rewake
        self._last_agent_voice = time.monotonic()
        if mic_queue is None:
            self._listen_queue_live = False
        try:
            await room.connect(self._creds["LIVEKIT_URL"], jwt)
        except Exception:
            self._listen_queue_live = True
            self._rewake = None
            raise
        logger.warning("joined %s, waiting for %s", room_name, self._agent_name)
        publish_hud_room(room_name)
        self._set_in_call(True)
        try:
            # Publish the mic, stamped SOURCE_MICROPHONE. The default is
            # SOURCE_UNKNOWN, which AgentSession input streams reject
            # (accepted_sources={microphone}) — the agent heard silence on
            # every call while raw subscribers heard us fine.
            source = rtc.AudioSource(MIC_RATE, 1)
            mic_track = rtc.LocalAudioTrack.create_audio_track("jarvis-mic", source)
            await room.local_participant.publish_track(
                mic_track,
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
            )
            mic_task = asyncio.create_task(
                self._pump_queue(source, mic_queue, agent_ready, preroll or [])
                if mic_queue is not None
                else self._pump_mic(source)
            )

            # Wait for the agent, then relay its voice to the speakers.
            try:
                track = await asyncio.wait_for(agent_audio.get(), AGENT_JOIN_TIMEOUT)
            except TimeoutError:
                logger.warning("no agent joined; leaving %s", room_name)
                await self._cancel_task(mic_task)
                return False
            play_task = asyncio.create_task(self._play_agent(track))
            if seed_text:
                # Text→voice: the typed opener becomes the first user
                # turn over the standard chat topic, so the agent answers
                # with voice. Fail-soft: a dropped seed never kills audio.
                try:
                    await room.local_participant.send_text(seed_text, topic="lk.chat")
                    logger.warning("seeded call with typed text")
                except Exception as exc:
                    logger.warning("seed text send failed: %s", exc)
            ended = asyncio.create_task(disconnected.wait())
            rewoken = asyncio.create_task(rewake.wait())
            await asyncio.wait({ended, rewoken}, return_when=asyncio.FIRST_COMPLETED)
            ended.cancel()
            rewoken.cancel()
            await self._cancel_task(play_task)
            await self._cancel_task(mic_task)
            return rewake.is_set() and not disconnected.is_set()
        finally:
            clear_hud_room()
            self._set_in_call(False)
            self._rewake = None
            self._listen_queue_live = True
            await room.disconnect()
            logger.warning("session over, back to listening")

    async def _pump_mic(self, source) -> None:
        """Forward live mic blocks to the room (porcupine keeps listening).

        While muted the queue is drained but nothing is captured: the room
        stays joined so unmute resumes mid-call with no rejoin.
        """
        import numpy as np
        import sounddevice as sd
        from livekit import rtc

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        model = self._model
        if model is not None:
            with contextlib.suppress(Exception):
                model.reset()
        pending16 = np.zeros(0, dtype=np.int16)
        hot_run = 0

        def _callback(indata, frames, time_info, status) -> None:
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        with sd.RawInputStream(
            samplerate=MIC_RATE,
            channels=1,
            dtype="int16",
            blocksize=960,
            callback=_callback,
        ):
            while True:
                raw = await queue.get()
                if self.muted:
                    continue
                samples = len(raw) // 2
                frame = rtc.AudioFrame(raw, MIC_RATE, 1, samples)
                # Awaited: newer livekit made capture_frame a coroutine and
                # fire-and-forget silently drops every mic frame (the agent
                # heard silence on every call).
                await source.capture_frame(frame)
                if model is None or self._rewake is None:
                    continue
                # 960 samples at 48k decimate exactly to 320 at 16k.
                block = np.frombuffer(raw, dtype=np.int16)
                pending16 = np.concatenate([pending16, block[::3]])
                scores, pending16 = score_frames(model, pending16)
                for score in scores:
                    hot_run = hot_run + 1 if score >= REWAKE_THRESHOLD else 0
                    quiet = time.monotonic() - self._last_agent_voice
                    if should_rewake(hot_run, quiet):
                        logger.warning("in-call wake (%.2f), agent quiet %.0fs", score, quiet)
                        self._rewake.set()
                        return

    async def _record_question(self, queue) -> list:
        """After a school wake: blocks until Sir stops talking (0.8 s under
        the noise gate, after at least 0.6 s) or 8 s. Returns raw blocks."""
        import numpy as np

        out: list = []
        quiet = 0.0
        floor = None
        block_s = BLOCKSIZE / MIC_RATE
        while len(out) * block_s < 8.0:
            raw = await queue.get()
            out.append(raw)
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(x**2))) if x.size else 0.0
            # Noise floor: drops to quieter blocks at once, creeps up slowly.
            floor = rms if floor is None or rms < floor else floor * 1.01
            loud = rms > max(150.0, 2.5 * floor)
            quiet = 0.0 if loud else quiet + block_s
            if len(out) * block_s >= 0.6 and quiet >= 0.8:
                break
        return out

    async def _pump_queue(self, source, queue, agent_ready, preroll: list) -> None:
        """School-mode mic: forward the listen loop's own queue (backlog
        since the wake word first, then live) instead of a second stream.

        Waits for the agent first: frames published before it subscribes
        are dropped by LiveKit (live test: only "France" of "what is the
        capital of France" arrived). The queue keeps filling meanwhile.
        """
        from livekit import rtc

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(agent_ready.wait(), AGENT_JOIN_TIMEOUT)
        await asyncio.sleep(0.3)  # let the agent's input stream attach
        # Drop what piled up while the call connected (the question already
        # went in as text): replaying it made live follow-ups seconds late.
        while not queue.empty():
            queue.get_nowait()
        # Real-time pace: Gemini runs in manual-activity mode and only keeps
        # audio between the agent's VAD start/end, so a burst replay put
        # most of the question outside that window ("What is the cap").
        t0 = time.monotonic()
        for n, raw in enumerate(preroll, 1):
            await source.capture_frame(rtc.AudioFrame(raw, MIC_RATE, 1, len(raw) // 2))
            ahead = t0 + n * BLOCKSIZE / MIC_RATE - time.monotonic()
            if ahead > 0:
                await asyncio.sleep(ahead)
        while True:
            raw = await queue.get()
            if self.muted:
                continue
            await source.capture_frame(rtc.AudioFrame(raw, MIC_RATE, 1, len(raw) // 2))

    async def _play_agent(self, track) -> None:
        """Play the agent's voice on the laptop speakers."""
        import sounddevice as sd
        from livekit.rtc import AudioStream

        stream = AudioStream(track)
        output = None
        try:
            async for event in stream:
                frame = event.frame
                pcm = (
                    frame.data.tobytes()
                    if hasattr(frame.data, "tobytes")
                    else bytes(frame.data)
                )
                if output is None or output.samplerate != frame.sample_rate:
                    if output is not None:
                        output.close()
                    output = sd.OutputStream(
                        samplerate=frame.sample_rate, channels=1, dtype="int16"
                    )
                    output.start()
                samples = self._bytes_to_int16(pcm)
                if samples.size and int(abs(samples).max()) > 500:
                    self._last_agent_voice = time.monotonic()
                if _school_quiet():
                    samples = (samples.astype("float32") * _school_gain()).astype("int16")
                output.write(samples)
        finally:
            if output is not None:
                with contextlib.suppress(Exception):
                    output.close()
            with contextlib.suppress(Exception):
                await stream.aclose()


def _school_quiet() -> bool:
    try:
        import school

        return school.is_school()
    except Exception:
        return False


def _school_gain() -> float:
    import school

    return school.SCHOOL_VOLUME


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    asyncio.run(WakeClient().run_forever())


if __name__ == "__main__":
    main()
