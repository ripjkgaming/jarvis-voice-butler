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
IDENTITY = "jarvis-master"


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


#: Admission control for the hotword model (perf): the ONNX predict is
#: the whole wake-loop CPU budget, so frames must earn their inference.
#: Deep-digital-silence frames (peak below ~-40dBFS — no wake word ever
#: hides down there) never run. Otherwise an idle stride scores every
#: 3rd frame; any hint of voice resumes full rate for 2s. A ~1s wake
#: word still gets 3+ scored looks; worst-case added delay ~160ms.
_SILENCE_PEAK = 300
_IDLE_STRIDE = 3
_ALERT_SCORE = 0.12
_ALERT_HOLD_S = 2.0


class PredictGate:
    """Per-frame ONNX admission decision. Tiny state, pure logic."""

    def __init__(self) -> None:
        self._skip = 0
        self._alert_until = 0.0

    def admit(self, peak: int, now: float) -> bool:
        """True when this frame must be scored. Never raises."""
        try:
            if peak < _SILENCE_PEAK:
                return False
            if now < self._alert_until:
                return True
            if self._skip > 0:
                self._skip -= 1
                return False
            self._skip = _IDLE_STRIDE - 1
            return True
        except Exception:
            return True

    def note_score(self, score: float, now: float) -> None:
        """Feed a scored result back; voice hints resume full rate."""
        try:
            if float(score) >= _ALERT_SCORE:
                self._alert_until = float(now) + _ALERT_HOLD_S
        except Exception:
            pass


def frame_peak_int16(frame) -> int:
    """Peak abs sample without int16 overflow (min(-32768) can't abs).

    Pure; C-speed min/max (no per-sample Python). Accepts numpy int16
    arrays (zero-copy view) or plain lists.
    """
    try:
        import numpy as np

        arr = np.asanyarray(frame, dtype=np.int16)
        if arr.size == 0:
            return 0
        return int(max(int(arr.max()), -int(arr.min())))
    except Exception:
        return _SILENCE_PEAK


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
    *, url: str, api_key: str, api_secret: str, room: str, agent_name: str
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
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        with sd.RawInputStream(
            samplerate=MIC_RATE,
            channels=1,
            dtype="int16",
            blocksize=BLOCKSIZE,
            callback=_audio_callback,
        ):
            import time as _time

            pending16 = np.zeros(0, dtype=np.int16)
            gate = PredictGate()
            while True:
                raw = await queue.get()
                # Zero-copy int16 view, vector decimate 48k->16k (was a
                # per-sample int.from_bytes Python loop + list copies).
                block = np.frombuffer(raw, dtype=np.int16)
                if block.size < BLOCKSIZE:
                    continue
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
                        await self._summon_session()
                    continue
                while pending16.size >= OWW_FRAME:
                    frame = pending16[:OWW_FRAME]
                    pending16 = pending16[OWW_FRAME:]
                    now = _time.monotonic()
                    if not gate.admit(frame_peak_int16(frame), now):
                        continue
                    try:
                        score = wake_score(model.predict(frame))
                    except Exception as exc:
                        logger.debug("wake predict failed: %s", exc)
                        continue
                    gate.note_score(score, now)
                    if score >= self._threshold:
                        logger.warning("wake word detected (%.2f)", score)
                        self._play_ack()
                        summon_overlay()
                        await self._summon_session()
                        pending16 = np.zeros(0, dtype=np.int16)

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

    async def _summon_session(self) -> None:
        from livekit import rtc

        # Never layer a second Jarvis over an ongoing call (an earlier
        # summon that hasn't hung up yet). The HUD stays visible either
        # way: "hey Jarvis" always summons the UI.
        if await active_call_exists(self._creds):
            logger.warning("already in a call; staying out")
            summon_overlay()
            return

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
        )
        room = rtc.Room()
        disconnected = asyncio.Event()
        agent_audio: asyncio.Queue = asyncio.Queue()

        @room.on("disconnected")
        def _on_disconnected() -> None:
            disconnected.set()

        @room.on("track_subscribed")
        def _on_track(track, _publication, _participant) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                agent_audio.put_nowait(track)

        await room.connect(self._creds["LIVEKIT_URL"], jwt)
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
            mic_task = asyncio.create_task(self._pump_mic(source))

            # Wait for the agent, then relay its voice to the speakers.
            try:
                track = await asyncio.wait_for(agent_audio.get(), AGENT_JOIN_TIMEOUT)
            except TimeoutError:
                logger.warning("no agent joined; leaving %s", room_name)
                await self._cancel_task(mic_task)
                return
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
            await disconnected.wait()
            await self._cancel_task(play_task)
            await self._cancel_task(mic_task)
        finally:
            clear_hud_room()
            self._set_in_call(False)
            await room.disconnect()
            logger.warning("session over, back to listening")

    async def _pump_mic(self, source) -> None:
        """Forward live mic blocks to the room (porcupine keeps listening).

        While muted the queue is drained but nothing is captured: the room
        stays joined so unmute resumes mid-call with no rejoin.
        """
        import sounddevice as sd
        from livekit import rtc

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

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
                output.write(self._bytes_to_int16(pcm))
        finally:
            if output is not None:
                with contextlib.suppress(Exception):
                    output.close()
            with contextlib.suppress(Exception):
                await stream.aclose()


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    asyncio.run(WakeClient().run_forever())


if __name__ == "__main__":
    main()
