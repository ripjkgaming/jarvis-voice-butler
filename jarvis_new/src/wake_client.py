"""Always-on hands-free client: say "hey Jarvis" -> talk -> auto-leave.

Idle state costs nothing cloud-side: the laptop mic is monitored
locally by openWakeWord (fully offline, no account, no key) and no
room exists until the keyword fires. On wake the client shows the HUD,
mints a token that dispatches the worker into a fresh room, preserves
the opening microphone audio until the agent joins, and plays the agent
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

JARVIS_WAKE_THRESHOLD (default 0.35) tunes sensitivity; lower hears
more, higher false-alarms less.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
import logging
import os
import socket
import threading
import time
from pathlib import Path

from jarvis_rt.transport.client import VoiceClient
from jarvis_rt.transport.tokens import load_secret, mint_call_token
from mint_token import SUMMON_TTL, voice_url
from mint_token import active_call_exists as _call_active
from voice_barge_signals import TOPIC as BARGE_TOPIC
from voice_barge_signals import BargeSync
from voice_barge_signals import decode as decode_barge
from clap_detect import (
    PAIR_LEVEL_DB,
    PAIR_MAX_S,
    PAIR_MIN_S,
    CalibrationWatcher,
    ClapDetector,
    ClapGrace,
    SleepStateReader,
    default_calibration_path,
    default_home,
    should_wake,
    write_wav,
)
from wake_events import WakeEventBroadcaster

logger = logging.getLogger("jarvis-wake")

MIC_RATE = 48000
# School wake: stop recording the question after this long even if the
# room never goes quiet, and treat blocks under SPEECH_DROP x Sir's wake
# phrase loudness as silence.
RECORD_MAX_S = 6.0
SPEECH_DROP = 0.3
BLOCKSIZE = 1536  # 48k samples decimate exactly to a 512-sample 16k frame
CLAP_RING_S = 20.0  # raw mic kept for clap_last.wav (diagnosis of misses)
CLAP_RINGS_KEEP = 20  # clap_rings/clap_<epoch>.wav files kept
CLAP_DUMP_S = 10.0  # at most one clap_last.wav save per onset burst this often
CLAP_LOG_PER_S = 5  # clap-level onset log lines per second, at most
OWW_FRAME = 1280  # openWakeWord native frame: 80ms at 16kHz
WAKE_MODEL = "hey_jarvis"
# Legacy direct-stream rewake acknowledgement. Buffered calls use the HUD
# while joining so this local speech cannot contaminate their first turn.
ACK_LINE = "Loading in, Sir."
# Max chars of a typed seed that may ride a talk summon into the call.
# Spoken openers are short; anything longer belongs in /chat text mode.
TALK_TEXT_MAX = 500
AGENT_JOIN_TIMEOUT = 25.0
CONNECT_TIMEOUT_S = 10.0
# Hub close code 4003: a call is already live, so this summon stays out.
ROOM_BUSY_CODE = 4003
# Agent speech from the hub is PCM16 mono 24 kHz.
AGENT_RATE = 24000
# Attribute budget of a call token (jarvis_rt.transport.tokens).
ATTR_MAX_BYTES = 1024
MIC_READY_SETTLE_S = 0.3
# Retain a complete supported join wait without trimming the first words.
# At 48 kHz mono int16 this queue holds at most about 2.9 MB.
MIC_BUFFER_MAX_S = 30.0
MIC_QUEUE_MAX_BLOCKS = int(MIC_BUFFER_MAX_S * MIC_RATE / BLOCKSIZE)
# Detection trails the keyword. A short tail saves an immediate question's
# onset without replaying the full wake phrase as a second user turn.
NORMAL_PREROLL_BLOCKS = 5  # 160 ms; school mode keeps its full 1.5 s ring
# Only digital zero is safe to trim without another speech detector. Keep
# an endpoint-sized gap, then discard queued excess silence to catch up.
MIC_CATCHUP_SILENCE_S = 0.6
SCHOOL_CONFIRM_S = 2.5  # audio after a school-mode wake checked for "hey Jarvis"
IDENTITY = "jarvis-master"
# In-call "hey Jarvis" restarts the call only when it has gone stale: the
# agent silent this long. Mid-conversation, the name just goes to the agent.
REWAKE_STALE_S = 20.0
REWAKE_THRESHOLD = 0.8
REWAKE_FRAMES = 2  # consecutive frames at or above REWAKE_THRESHOLD


def wake_threshold() -> float:
    """Sensitivity 0..1: explicit > JARVIS_WAKE_THRESHOLD > 0.35 default."""
    try:
        return float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.35"))
    except ValueError:
        return 0.35


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
            queue.mic_overflowed = False
            return n
        n += 1


def enqueue_mic_block(queue: asyncio.Queue, raw: bytes) -> None:
    """Bound capture memory without silently losing the start of a request."""
    try:
        queue.put_nowait(raw)
    except asyncio.QueueFull:
        # The consumer aborts this capture rather than treating the remaining
        # tail as a complete command. Never evict the first words to make room.
        queue.mic_overflowed = True


def mic_buffer_failed(queue: asyncio.Queue) -> bool:
    """Report a broken capture locally; never ask the model to act on it."""
    if not getattr(queue, "mic_overflowed", False):
        return False
    logger.warning("microphone buffer exceeded; retry required")
    with contextlib.suppress(Exception):
        from hud_events import caption

        caption(
            "jarvis",
            "The microphone buffer filled before I could listen. Please try again.",
        )
    return True


def room_has_active_call(identities: list[str]) -> bool:
    """True when a room already hosts a call (user + agent). Pure.

    Rooms are unique per session, so 2+ participants means someone is
    already talking to an agent — summoning another would layer a
    second Jarvis voice over the first.
    """
    return len(identities) >= 2


async def active_call_exists() -> bool:
    """True when the voice hub already hosts a live call.

    Reads the worker's ``voice_active.json`` on a thread. Fails open
    (returns False) on errors: a missed double-voice guard is better than
    going deaf because the status file could not be read.
    """
    try:
        return bool(await asyncio.to_thread(_call_active))
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


#: File under $JARVIS_HOME stamped the instant the wake word fires, so the
#: HUD turns purple before the room exists (connect + agent join is ~1-3 s).
HUD_WAKING_FILE = "hud_waking"


_boot_log: list[list] = []


def mark_hud_waking() -> None:
    """Stamp "wake heard, call starting" for the HUD. Fail-soft.

    Starts the boot log the HUD shows while the call comes up; each
    later hud_stage() appends the real step in progress."""
    _boot_log.clear()
    hud_stage("wake")


def hud_stage(stage: str) -> None:
    """Record a real call-setup step ("transcribe", "connect", ...) in the
    waking file as {"log": [[stage, ts], ...]}. The bridge serves it on
    /room; the HUD words it. Fail-soft."""
    _boot_log.append([stage, round(time.time(), 3)])
    try:
        path = hud_room_path().with_name(HUD_WAKING_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"log": _boot_log[-16:]}))
        tmp.replace(path)
    except OSError as exc:
        logger.debug("hud waking mark failed: %s", exc)


def clear_hud_waking() -> None:
    """Drop the waking stamp (call up, over, or wake rejected). Fail-soft."""
    # PTT and rewake begin at "check", without another hotword stamp.
    # Do not carry a completed call's first timestamp into that generation.
    _boot_log.clear()
    try:
        hud_room_path().with_name(HUD_WAKING_FILE).unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("hud waking clear failed: %s", exc)


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


def extract_announce_text(msg: object) -> str | None:
    """Announcement text from ``{"announce": "..."}``, else None. Pure.

    Same limits as a typed seed: a stripped string of 1..TALK_TEXT_MAX chars.
    """
    if not isinstance(msg, dict):
        return None
    text = msg.get("announce")
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())
    if not text or len(text) > TALK_TEXT_MAX:
        return None
    return text


def _fit_announce(reason: str, text: str) -> str:
    """Shorten an announcement until its token attributes fit the claim cap."""
    while text and (
        len(json.dumps({"jarvis.wake": reason, "jarvis.announce": text}).encode())
        > ATTR_MAX_BYTES
    ):
        text = text[: int(len(text) * 0.9)]
    return text


def mint_summon_token(*, room: str, reason: str = "wake", announce: str = "") -> str:
    """Speaker-mode call token for ``room``. The hub starts the agent on join.

    Carries the summon reason (the agent picks its greeting) and, for a
    silent-call announce, the spoken opening line. Raises ValueError on a bad
    room or attributes. Call tokens are single use: callers mint per attempt.
    """
    attrs = {"jarvis.wake": reason}
    fitted = _fit_announce(reason, announce) if announce else ""
    if fitted:
        # Spoken by the agent as the call's opening line (silent-call announce).
        attrs["jarvis.announce"] = fitted
    return mint_call_token(room, IDENTITY, "speaker", SUMMON_TTL, attrs, load_secret())


async def _connect_or_fail(client: VoiceClient, run_task: asyncio.Task) -> bool:
    """True once the hub accepts the speaker. False when it refuses or closes
    the call, or does not answer within CONNECT_TIMEOUT_S. Other errors raise."""
    waiter = asyncio.ensure_future(client.wait_connected(CONNECT_TIMEOUT_S))
    try:
        done, _ = await asyncio.wait(
            {waiter, run_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        if not waiter.done():
            waiter.cancel()
    if waiter in done and not waiter.cancelled():
        error = waiter.exception()
        if error is None:
            return True
        if isinstance(error, TimeoutError):
            return False
        raise error
    return False


class MicUplink:
    """48 kHz capture blocks in, 16 kHz PCM16 hub mic frames out (one send each)."""

    def __init__(self, client: VoiceClient) -> None:
        from audio_dsp import Downsampler48to16

        self._client = client
        self._down = Downsampler48to16()

    async def capture(self, raw: bytes) -> bool:
        import numpy as np

        if not raw:
            return True
        pcm16 = self._down.process(np.frombuffer(raw, dtype=np.int16)).tobytes()
        if not pcm16:
            return True
        return await self._client.send_mic(pcm16)

    async def aclose(self) -> None:
        return None


class WakeClient:
    def __init__(self) -> None:
        # Best-effort broadcast of wake / call_ended to local subscribers
        # (the chess voice engine pauses its own listening on these).
        self._events = WakeEventBroadcaster()
        try:
            from dotenv import load_dotenv

            load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
        except Exception as exc:
            logger.debug("dotenv load failed: %s", exc)
        self._threshold = wake_threshold()
        self._agent_name = (
            os.environ.get("AGENT_NAME", "my-agent").strip() or "my-agent"
        )
        self._ack_pcm: bytes = b""
        self._ack_rate = 22050
        # Shared local transcription remains lazy for school wake confirmation.
        # Ordinary microphone input only runs the primary openWakeWord detector.
        from keyword_spot import WhisperWorker

        self._spotter = WhisperWorker()
        self._passive_busy = False
        # Mic mute state (tray/HUD driven via the wake.sock listener below).
        # Guarded by a lock: set from the socket thread, read from the
        # asyncio mic loops and the hotword loop.
        self._mic_lock = threading.Lock()
        self._muted = False
        self._mute_epoch = 0
        self._capture_loop = None
        self._capture_queue = None
        self._in_call = False
        # PTT talk request (HUD NumpadEnter / shell talk via wake.sock).
        # Set from the socket thread, consumed by the hotword loop, which
        # then runs the exact hotword summon path (ack + overlay + call).
        self._talk_event = threading.Event()
        self._pending_announce = ""
        # Typed seed for the next summon ("text which activates voice").
        # Guarded by _mic_lock like the mute state; consumed once by
        # _summon_session, never kept across calls.
        self._pending_text = ""
        # In-call hotword (see should_rewake): the openWakeWord model is
        # shared with the listen loop, which is parked while a call runs.
        self._model = None
        self._rewake: asyncio.Event | None = None
        self._last_agent_voice = 0.0
        from talk_gate import TalkOverGate, gate_enabled

        # Ducks room noise and echo while Jarvis speaks (see talk_gate).
        self._talk_gate = TalkOverGate() if gate_enabled() else None
        self._fast_barge = None
        self._barge_sync = None
        self._active_audio_playback = None
        self._voice = None
        # False while a plain call runs: the listen loop's queue is unread
        # then, and buffering a whole call's audio grew it without bound.
        self._listen_queue_live = True

    @property
    def muted(self) -> bool:
        with self._mic_lock:
            return self._muted

    def set_muted(self, muted: bool) -> None:
        with self._mic_lock:
            newly_muted = bool(muted) and not self._muted
            self._muted = bool(muted)
            if newly_muted:
                self._mute_epoch = getattr(self, "_mute_epoch", 0) + 1
        if newly_muted:
            talk_event = getattr(self, "_talk_event", None)
            if talk_event is not None:
                talk_event.clear()
            loop = getattr(self, "_capture_loop", None)
            queue = getattr(self, "_capture_queue", None)
            if loop is not None and queue is not None:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(drain_queue, queue)

    def _publish_event(self, event: str) -> None:
        bus = getattr(self, "_events", None)
        if bus is not None:
            bus.publish(event)

    def _set_in_call(self, in_call: bool) -> None:
        with self._mic_lock:
            was = self._in_call
            self._in_call = bool(in_call)
        if was and not in_call:
            self._publish_event("call_ended")

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
                    if isinstance(msg, dict) and "announce" in msg:
                        text = extract_announce_text(msg)
                        if text is None:
                            reply = {"ok": False, "error": "bad announce text"}
                        elif muted or in_call or _school_quiet():
                            reply = {
                                "ok": False,
                                "error": "muted" if muted else "busy",
                            }
                        else:
                            with self._mic_lock:
                                self._pending_announce = text
                            self._talk_event.set()
                            reply = {"ok": True, "announce": "queued"}
                        new_muted = None
                    elif isinstance(msg, dict) and "character_say" in msg:
                        reply = self._character_say(msg["character_say"])
                        new_muted = None
                    elif isinstance(msg, dict) and "character_prewarm" in msg:
                        # Slow (cold model load): answer from a worker thread on
                        # a dup'd fd so the serial listener keeps serving
                        # character_say (the exit line) meanwhile.
                        threading.Thread(
                            target=self._prewarm_reply,
                            args=(conn.dup(), msg["character_prewarm"]),
                            name="jarvis-char-prewarm",
                            daemon=True,
                        ).start()
                        continue
                    elif isinstance(msg, dict) and "voice_output" in msg:
                        if isinstance(msg["voice_output"], bool):
                            self._voice_output = msg["voice_output"]
                            reply = {"ok": True, "voice_output": self._voice_output}
                        else:
                            reply = {
                                "ok": False,
                                "error": "'voice_output' must be true or false",
                            }
                        new_muted = None
                    elif isinstance(msg, dict) and msg.get("cancel_external") is True:
                        reply = {"ok": True, "cancelled": self._cancel_linked_turn(msg)}
                        new_muted = None
                    elif isinstance(msg, dict) and msg.get("talk") is True:
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
                            from linked_turn import LinkedTurn, external_key

                            key = external_key(msg)
                            if not in_call:
                                with self._mic_lock:
                                    self._pending_linked_turn = (
                                        LinkedTurn(key) if key else None
                                    )
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

    # ---- character voice (control socket "character_say") ----------
    def _voice_output_on(self) -> bool:
        flag = getattr(self, "_voice_output", None)
        if flag is None:
            flag = os.environ.get("JARVIS_VOICE_OUTPUT", "1").strip().lower() not in (
                "0",
                "false",
                "off",
                "no",
            )
            self._voice_output = flag
        return flag

    def _say_event_loop(self):
        loop = getattr(self, "_capture_loop", None)
        if loop is not None and loop.is_running():
            return loop
        loop = getattr(self, "_say_loop", None)
        if loop is None or not loop.is_running():
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever, name="jarvis-say-loop", daemon=True
            ).start()
            self._say_loop = loop
        return loop

    def _prewarm_reply(self, conn: socket.socket, cid: object) -> None:
        with conn:
            reply = self._character_prewarm(cid)
            with contextlib.suppress(OSError):
                conn.sendall(json.dumps(reply).encode())

    def _character_prewarm(self, cid: object) -> dict:
        """Warm this process's voice bank for ``cid`` (blocking, worker thread)."""
        t0 = time.monotonic()
        if not isinstance(cid, str) or not cid.strip():
            return {"ok": False, "error": "bad character", "ms": 0}
        try:
            import character_voice
            from character_runtime import get_runtime

            char = get_runtime().chars.get(cid.strip().lower())
            if char is None:
                return {"ok": False, "error": f"unknown character {cid!r}", "ms": 0}
            ok = bool(character_voice.prewarm(char))
        except Exception as exc:
            logger.warning("character_prewarm failed: %s", exc)
            ok = False
        return {"ok": ok, "ms": int((time.monotonic() - t0) * 1000)}

    def _idle_warm_busy(self) -> bool:
        return self._mic_snapshot()[2] or bool(getattr(self, "_say_active", False))

    def start_idle_warm(self, delay_s: float = 20.0):
        """Warm all non-jarvis voices once, low priority, only while idle."""
        try:
            import character_voice
            from character_runtime import get_runtime

            return character_voice.start_idle_warm(
                list(get_runtime().chars.values()),
                self._idle_warm_busy,
                delay_s=delay_s,
            )
        except Exception as exc:
            logger.debug("idle warm unavailable: %s", exc)
            return None

    def _character_say(self, req: object) -> dict:
        """Render a character line (blocking, socket thread) and queue playback."""
        t0 = time.monotonic()
        if not isinstance(req, dict):
            return {"ok": False, "error": "character_say must be an object"}
        text = req.get("text")
        cid = req.get("character")
        if not isinstance(text, str) or not text.strip() or len(text) > TALK_TEXT_MAX:
            return {"ok": False, "error": "bad text"}
        if not isinstance(cid, str) or not cid.strip():
            return {"ok": False, "error": "bad character"}
        if not self._voice_output_on():
            return {"ok": True, "skipped": "voice_output_off"}
        try:
            import character_voice
            import characters as _characters
            from character_runtime import get_runtime

            char = get_runtime().chars.get(cid.strip().lower())
            if char is None:
                return {"ok": False, "error": f"unknown character {cid!r}"}
            motion = _characters.motion()
            pcm, sr = character_voice.render_pcm(text.strip(), char)
            if req.get("whoosh"):
                pcm = mix_character_whoosh(
                    pcm,
                    sr,
                    character_voice.whoosh_pcm(char, sr),
                    float(motion.get("whoosh_gain_db", -18)),
                )
            pcm = prepare_character_pcm(
                pcm, sr, float(motion.get("audio_crossfade_ms", 120))
            )
        except Exception as exc:
            logger.warning("character_say failed: %s", exc)
            return {"ok": False, "error": "render failed"}
        interrupt = req.get("interrupt", True) is not False
        asyncio.run_coroutine_threadsafe(
            self._say_play(pcm, sr, interrupt), self._say_event_loop()
        )
        return {"ok": True, "ms": int((time.monotonic() - t0) * 1000)}

    async def _say_play(self, pcm, sr: int, interrupt: bool) -> None:
        """Play through the one shared AudioPlayback; newest line wins."""
        from audio_playback import AudioPlayback

        lock = getattr(self, "_say_lock", None)
        if lock is None:
            lock = self._say_lock = asyncio.Lock()
        self._say_seq = seq = getattr(self, "_say_seq", 0) + 1
        output = getattr(self, "_agent_output", None)
        if output is None or getattr(output, "_closed", False):
            output = getattr(self, "_say_output", None)
            if output is None or getattr(output, "_closed", False):
                output = self._say_output = AudioPlayback()
        if interrupt:
            # Gate fade-out of whatever is playing (agent speech or prior line).
            with contextlib.suppress(Exception):
                output.cancel()
        async with lock:
            if interrupt and seq != self._say_seq:
                return  # a newer line superseded this one while queued
            chunk = max(256, sr // 50)
            try:
                for i in range(0, len(pcm), chunk):
                    if interrupt and seq != self._say_seq:
                        break
                    await output.write(pcm[i : i + chunk], sr)
            except Exception as exc:
                logger.warning("character_say playback failed: %s", exc)

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

    def _render_ack(self) -> None:
        """Pre-render 'Loading in, Sir.' with the local voice for instant feedback."""
        try:
            try:
                from src.local_voice import PiperTTS, sentence_split
            except ImportError:
                from local_voice import PiperTTS, sentence_split

            plugin = PiperTTS()
            if not plugin._model_path.exists():
                return
            out = bytearray()
            for sentence in sentence_split(ACK_LINE):
                out += plugin._render_sentence(sentence)
            self._ack_pcm = bytes(out)
            self._ack_rate = plugin.sample_rate
        except Exception as exc:
            logger.warning("ack render failed: %s", exc)

    async def run_forever(self) -> None:
        # Echo-cancel Jarvis's own voice on laptop speakers BEFORE any audio
        # stream opens (streams bind to the default devices at open time).
        import audio_aec

        logger.warning("mic: %s", await asyncio.to_thread(audio_aec.set_mic_headroom))
        logger.warning("aec: %s", await asyncio.to_thread(audio_aec.engage))
        self.start_mic_control()
        self.start_idle_warm()
        self._events.start()
        await self._listen_loop(None)

    def _load_wake_model(self):
        from openwakeword.model import Model

        model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
        return model

    async def _listen_loop(self, model) -> None:
        import numpy as np
        import sounddevice as sd

        from audio_dsp import Downsampler48to16, downsample_48k_to_16k

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=MIC_QUEUE_MAX_BLOCKS)
        self._capture_loop, self._capture_queue = loop, queue

        def _enqueue(raw: bytes, epoch: int) -> None:
            if self.muted or epoch != getattr(self, "_mute_epoch", 0):
                return
            enqueue_mic_block(queue, raw)

        # Local neural noise suppression, once, where blocks enter the
        # queue: the wake word, join buffer and live call all hear it.
        from mic_clean import make_cleaner

        cleaner = None

        clap_raw: deque = deque(maxlen=400)  # ~12 s of raw blocks, detector-side
        clap_ring: deque = deque(maxlen=int(CLAP_RING_S * MIC_RATE / BLOCKSIZE))

        def _audio_callback(indata, frames, time_info, status) -> None:
            nonlocal cleaner
            if status:
                logger.warning("mic status: %s", status)
            if not self._listen_queue_live:
                return
            with self._mic_lock:
                if self._muted:
                    return
                epoch = getattr(self, "_mute_epoch", 0)
            blocks = [bytes(indata)]
            # Raw (pre-denoise) copy for the double-clap detector, and the
            # rolling ring saved as clap_last.wav. Denoising is a speech
            # model: it treats a clap transient as noise.
            clap_raw.append(blocks[0])
            clap_ring.append(blocks[0])
            if cleaner is not None:
                try:
                    blocks = cleaner.process(blocks[0])
                except Exception as exc:
                    logger.warning("noise suppression failed, raw mic: %s", exc)
                    cleaner = None
            with contextlib.suppress(Exception):
                for block in blocks:
                    loop.call_soon_threadsafe(_enqueue, block, epoch)

        with sd.RawInputStream(
            samplerate=MIC_RATE,
            channels=1,
            dtype="int16",
            blocksize=BLOCKSIZE,
            callback=_audio_callback,
        ):
            # Capture starts before ONNX/Piper/noise-model initialization.
            # The same bounded queue is consumed once loading completes.
            if model is None:
                model = await asyncio.to_thread(self._load_wake_model)
                self._model = model
            cleaner = await asyncio.to_thread(make_cleaner, BLOCKSIZE)
            logger.warning(
                "listening for 'hey Jarvis' (offline, nothing leaves the laptop)"
            )
            pending16 = np.zeros(0, dtype=np.int16)
            down16 = Downsampler48to16()
            import school as _school

            hot = {"run": 0}
            from passive_commands import UtteranceSlicer

            passive_slicer = UtteranceSlicer()
            # ~1.5 s of audio before the wake frame: in school mode it is
            # replayed so the transcript starts "Hey Jarvis, ..." (the
            # agent only answers turns that address it).
            import collections

            ring: collections.deque = collections.deque(
                maxlen=int(1.5 * MIC_RATE / BLOCKSIZE)
            )
            ring_epoch = getattr(self, "_mute_epoch", 0)
            # Double-clap wake: fed the RAW mic, only while the sleep state is
            # "asleep". Awake, a clap does nothing at all.
            clap = ClapDetector(
                sample_rate=16000,
                on_candidate=self._clap_candidate_logger(clap_ring),
            )
            cal_watch = CalibrationWatcher(on_change=self._clap_calibration_logger)
            cal_watch.poll(clap)
            logger.warning(
                "clap detector armed on raw mic: peak margin %.1f dB (%s)",
                clap.peak_db,
                "calibrated" if cal_watch.active else "default, no calibration file",
            )
            sleep_state = SleepStateReader()
            clap_armed = False
            clap_grace = ClapGrace()
            logger.warning(
                "clap wake: ratio floor %.1f dB, peak floor %s dBFS, pair within "
                "%.0f dB at %.2f-%.1f s, post-sleep grace %.1f s",
                clap.margin(),
                clap.peak_floor_dbfs,
                PAIR_LEVEL_DB,
                PAIR_MIN_S,
                PAIR_MAX_S,
                clap_grace.grace_s,
            )
            if _school.is_school():
                # Warm the Whisper worker so the first school wake is quick.
                loop.run_in_executor(
                    None, self._spotter.transcribe, np.zeros(16000, np.int16)
                )

            while True:
                raw = await queue.get()
                if mic_buffer_failed(queue):
                    drain_queue(queue)
                    ring.clear()
                    pending16 = np.zeros(0, dtype=np.int16)
                    continue
                if ring_epoch != getattr(self, "_mute_epoch", 0):
                    ring.clear()
                    pending16 = np.zeros(0, dtype=np.int16)
                    ring_epoch = getattr(self, "_mute_epoch", 0)
                # Zero-copy int16 view, vector decimate 48k->16k (was a
                # per-sample int.from_bytes Python loop + list copies).
                block = np.frombuffer(raw, dtype=np.int16)
                if block.size < BLOCKSIZE:
                    continue
                ring.append(raw)
                block16 = down16.process(block)
                pending16 = np.concatenate([pending16, block16])
                if self.muted:
                    ring.clear()
                    self._talk_event.clear()  # never summon while muted
                    pending16 = pending16[-OWW_FRAME:]  # bounded while deaf
                    continue  # tray/HUD mute: deaf but still listening locally
                asleep = sleep_state.is_asleep()
                if asleep != clap_armed:
                    clap.reset()
                    clap_raw.clear()
                    clap_armed = asleep
                    if not asleep:
                        self._dump_clap_ring(clap_ring, "woke")
                cal_watch.poll(clap)
                grace_over = clap_grace.armed(asleep, sleep_state.since(), time.time())
                if asleep:
                    fired = False
                    while clap_raw and not fired:
                        fired = clap.feed(
                            np.frombuffer(clap_raw.popleft(), dtype=np.int16)
                        )
                    if fired:
                        self._log_clap_pair(clap)
                    if not grace_over:
                        if fired:
                            self._dump_clap_ring(
                                clap_ring, "double clap in grace", keep=True
                            )
                        fired = False  # keep the background tracked, ignore claps
                    if should_wake(asleep, fired):
                        clap_raw.clear()
                        self._on_double_clap(clap_ring)
                else:
                    clap_raw.clear()
                if not asleep and not self._in_call and not _school.is_school():
                    utterance = passive_slicer.feed(block16)
                    if utterance is not None and not self._passive_busy:
                        self._passive_busy = True
                        asyncio.create_task(self._passive_command(utterance))
                if self._talk_event.is_set():
                    # PTT (HUD NumpadEnter / shell talk): the hotword summon
                    # path, without the hotword. In-call presses just make
                    # sure the overlay is up (never layer a second call).
                    self._talk_event.clear()
                    pending16 = np.zeros(0, dtype=np.int16)
                    logger.warning("PTT talk requested")
                    self._publish_event("wake")
                    mark_hud_waking()
                    summon_overlay()
                    if not self._in_call:
                        await self._summon_with_rewake(
                            mic_queue=queue,
                            preroll=list(ring)[-NORMAL_PREROLL_BLOCKS:],
                            preserve_join_audio=True,
                        )
                        drain_queue(queue)
                        ring.clear()
                        with contextlib.suppress(Exception):
                            model.reset()
                    continue
                if asleep:
                    # Asleep: only a double clap may wake. The hey-Jarvis
                    # model hears claps and noise as 0.4+ scores, so it must
                    # not start a session here.
                    hot["run"] = 0
                    pending16 = pending16[-OWW_FRAME:]
                    ring.clear()
                    continue
                scores, pending16 = score_frames(model, pending16)
                strict = _school.is_school()
                need = (
                    max(self._threshold, _school.WAKE_THRESHOLD)
                    if strict
                    else self._threshold
                )
                for score in scores:
                    hot["run"] = hot["run"] + 1 if score >= need else 0
                    if hot["run"] >= (_school.WAKE_FRAMES if strict else 1):
                        hot["run"] = 0
                        with self._mic_lock:
                            self._pending_linked_turn = None
                        logger.warning(
                            "wake word detected (%.2f)%s",
                            score,
                            " [school]" if strict else "",
                        )
                        # First thing, before any network: the HUD starts
                        # its link log off this stamp (it turns purple only
                        # once the agent is online).
                        self._publish_event("wake")
                        mark_hud_waking()
                        summon_overlay()
                        if strict:
                            # openWakeWord also fires on a bare "Jarvis" in
                            # chatter. Listen ~2.5 s more, transcribe locally,
                            # and only summon if Sir addressed Jarvis. The
                            # captured audio is replayed into the call, so
                            # nothing said after the wake word is lost.
                            levels = [
                                float(
                                    np.sqrt(
                                        np.mean(
                                            np.frombuffer(b, dtype=np.int16).astype(
                                                np.float32
                                            )
                                            ** 2
                                        )
                                    )
                                )
                                for b in ring
                            ]
                            hud_stage("capture")
                            heard = list(ring) + await self._record_question(
                                queue,
                                max(levels, default=0.0),
                                min(levels, default=None),
                            )
                            pcm16 = downsample_48k_to_16k(
                                np.frombuffer(b"".join(heard), dtype=np.int16)
                            )
                            hud_stage("transcribe")
                            text = await loop.run_in_executor(
                                None, self._spotter.transcribe, pcm16
                            )
                            if _school.addressed(text):
                                question = _school.question_after_address(text)
                                logger.warning("school wake confirmed: %r", text[:80])
                                hud_stage("verify")
                                # The question rides in as the opening text
                                # turn (replaying audio split at the comma
                                # pause and Gemini answered "aves").
                                with self._mic_lock:
                                    self._pending_text = question
                                # A bare "hey Jarvis" gets a short "Yes, Sir?"
                                # from the agent: silence left Sir and Jarvis
                                # both waiting until the call timed out.
                                await self._summon_session(
                                    mic_queue=queue,
                                    reason="school-ask" if question else "school-bare",
                                )
                            else:
                                clear_hud_waking()
                                logger.warning(
                                    "school wake ignored (not addressed): %r", text[:80]
                                )
                            ring.clear()
                        else:
                            await self._summon_with_rewake(
                                mic_queue=queue,
                                preroll=list(ring)[-NORMAL_PREROLL_BLOCKS:],
                                preserve_join_audio=True,
                            )
                        drain_queue(queue)
                        ring.clear()
                        pending16 = np.zeros(0, dtype=np.int16)
                        # openWakeWord keeps its own recent-audio buffer: a
                        # stale "hey Jarvis" in it re-fired after the call.
                        with contextlib.suppress(Exception):
                            model.reset()
                        break

    def _clap_calibration_logger(self, cal, detector) -> None:
        """Logs every calibration change seen by the wake loop (hot reload)."""
        if cal is None:
            logger.warning(
                "clap calibration gone (%s): peak margin %.1f dB (default)",
                default_calibration_path(),
                detector.peak_db,
            )
            return
        logger.warning(
            "clap calibration loaded (%s): calibrated margin %.2f dB, "
            "effective %.1f dB; floors: ratio >= %.1f dB AND peak >= %.1f dBFS",
            default_calibration_path(),
            cal["peak_margin_db"],
            detector.peak_db,
            detector.margin(),
            detector.peak_floor_dbfs,
        )

    def _clap_candidate_logger(self, ring=None):
        """Logs each clap-level onset (margin at or above the effective margin).

        Capped at CLAP_LOG_PER_S lines per second: room noise fires many
        onsets, and the log must not flood. Also saves the raw ring to
        clap_last.wav, at most every CLAP_DUMP_S.
        """
        last_dump = {"t": -1e9}
        window = {"t": -1e9, "n": 0}

        def log_candidate(info: dict) -> None:
            if info["margin_db"] < info["peak_db_cfg"]:
                return  # below the margin: ordinary room noise, not logged
            now = time.monotonic()
            if now - window["t"] >= 1.0:
                window["t"] = now
                window["n"] = 0
            if window["n"] >= CLAP_LOG_PER_S:
                return
            window["n"] += 1
            logger.warning(
                "clap onset peak=%.1fdBFS floor=%.1fdBFS ratio=%.1fdB reason=%s "
                "t=%.2f margin_cfg=%.1f",
                info["peak_db"],
                info["floor_db"],
                info["margin_db"],
                info["reason"],
                info["t"],
                info["peak_db_cfg"],
            )
            if now - last_dump["t"] >= CLAP_DUMP_S:
                last_dump["t"] = now
                self._dump_clap_ring(ring, "onset")

        return log_candidate

    @staticmethod
    def _log_clap_pair(clap) -> None:
        """Log peak and ratio of both claps of a would-be double clap."""
        pair = getattr(clap, "last_pair", None)
        if not pair:
            return
        a, b = pair
        logger.warning(
            "double clap: clap1 peak=%.1fdBFS ratio=%.1fdB t=%.2f, "
            "clap2 peak=%.1fdBFS ratio=%.1fdB t=%.2f, floors ratio>=%.1fdB peak>=%s",
            a.get("peak_db", 0.0),
            a.get("margin_db", 0.0),
            a.get("t", 0.0),
            b.get("peak_db", 0.0),
            b.get("margin_db", 0.0),
            b.get("t", 0.0),
            clap.margin(),
            clap.peak_floor_dbfs,
        )

    def _dump_clap_ring(self, ring, why: str, keep: bool = False) -> None:
        """Write the last CLAP_RING_S of raw mic to $JARVIS_HOME/clap_last.wav.

        keep=True also stores it as clap_rings/clap_<epoch>.wav (last
        CLAP_RINGS_KEEP kept) so a false wake can be diagnosed later.
        """
        if ring is None:
            return
        import numpy as np

        blocks = list(ring)  # one C-level copy: safe against the callback thread

        def write() -> None:
            try:
                pcm = np.frombuffer(b"".join(blocks), dtype=np.int16)
                path = write_wav(default_home() / "clap_last.wav", pcm, MIC_RATE)
                if keep:
                    rings = default_home() / "clap_rings"
                    path = write_wav(
                        rings / f"clap_{int(time.time())}.wav", pcm, MIC_RATE
                    )
                    for old in sorted(rings.glob("clap_*.wav"))[:-CLAP_RINGS_KEEP]:
                        old.unlink(missing_ok=True)
                logger.warning(
                    "clap ring saved: %s (%s, %.1f s raw)",
                    path,
                    why,
                    pcm.size / MIC_RATE,
                )
            except Exception as exc:
                logger.warning("clap ring save failed: %s", exc)

        threading.Thread(target=write, name="clap-ring", daemon=True).start()

    def _on_double_clap(self, clap_ring=None) -> None:
        """Two claps while asleep: ask the sleep controller to wake up.

        Only writes the wake_request file. The wake itself runs in the controller
        service, which has dbus; this process (.venv-wake) has none.
        """
        logger.warning("double clap heard while asleep")
        self._dump_clap_ring(clap_ring, "double clap", keep=True)
        try:
            try:
                from src.sleep_controller import touch_wake_request
            except ImportError:
                from sleep_controller import touch_wake_request
            if not touch_wake_request():
                logger.warning(
                    "double clap: not asleep any more, no wake request written"
                )
        except Exception as exc:
            logger.warning("double clap: wake request failed: %s", exc)

    async def _passive_command(self, pcm16) -> None:
        """Idle-time voice commands (mute / hide / hide all / stop), no wake word."""
        try:
            from intent import hud_commands
            from passive_commands import resolve

            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(None, self._spotter.transcribe, pcm16)
            if not text.strip():
                return
            command = await loop.run_in_executor(None, resolve, text)
            if command is None:
                return
            logger.warning("passive command %r from %r", command, text[:60])
            if command == "stop":
                self._passive_stop()
            else:
                await hud_commands.execute(command, None)
        except Exception as exc:
            logger.warning("passive command failed: %s", exc)
        finally:
            self._passive_busy = False

    def _passive_stop(self) -> None:
        """Fade out whatever Jarvis is saying outside a call."""
        self._say_seq = getattr(self, "_say_seq", 0) + 1  # drop queued lines
        for name in ("_agent_output", "_say_output"):
            output = getattr(self, name, None)
            if output is None or getattr(output, "_closed", False):
                continue
            if hasattr(output, "fade_out"):
                output.fade_out(140.0)
            elif hasattr(output, "cancel"):
                with contextlib.suppress(Exception):
                    output.cancel()

    def _play_ack(self) -> None:
        if not self._ack_pcm:
            return
        from playback_gate import get_playback_gate

        session = get_playback_gate().session()
        self._ack_session = session

        def play():
            from games.voice.tts import SoundDeviceSink

            sink = None
            try:
                if not session.acquire():
                    return
                sink = SoundDeviceSink(self._ack_rate)
                session.write(self._ack_pcm, self._ack_rate, sink.write)
            except Exception as exc:
                logger.warning("ack playback failed: %s", exc)
            finally:
                if sink is not None:
                    sink.close()
                session.close()

        threading.Thread(target=play, name="wake-ack", daemon=True).start()

    def _gate_mic(self, raw: bytes, now: float) -> bytes:
        """Talk-over gate on one outgoing call block; raw on any failure."""
        gate = getattr(self, "_talk_gate", None)
        if gate is None or not raw:
            return raw
        try:
            import numpy as np

            block = np.frombuffer(raw, dtype=np.int16)
            out = gate.process(block, now)
            return raw if out is block else out.tobytes()
        except Exception as exc:
            logger.warning("talk gate failed, disabled: %s", exc)
            self._talk_gate = None
            return raw

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
            if kwargs.get("preserve_join_audio"):
                # The wake detector consumed the old keyword already. Keep
                # this same input and retain the next question during rejoin.
                kwargs = {
                    "mic_queue": kwargs["mic_queue"],
                    "preserve_join_audio": True,
                }
            else:
                kwargs = {}
            summon_overlay()

    @staticmethod
    def _speak_fallback(text: str) -> None:
        """The silent call failed: never lose the announcement, say it locally."""
        with contextlib.suppress(Exception):
            import speak

            speak.speak_local(text)

    async def _summon_session(
        self,
        reason: str = "wake",
        mic_queue=None,
        preroll: list | None = None,
        preserve_join_audio: bool = False,
    ) -> bool:
        """Run one call. True when it ended by an in-call rewake."""
        with self._mic_lock:
            linked = getattr(self, "_pending_linked_turn", None)
            self._pending_linked_turn = None
            self._active_linked_turn = linked
        if linked is not None and linked.cancelled.is_set():
            return False
        capture_epoch = getattr(self, "_mute_epoch", 0)
        # Never layer a second Jarvis over an ongoing call (an earlier
        # summon that hasn't hung up yet). The HUD stays visible either
        # way: "hey Jarvis" always summons the UI.
        try:
            hud_stage("check")
            # The double-call check runs alongside token minting and connect,
            # and is only acted on before the mic goes live.
            call_check = asyncio.create_task(active_call_exists())

            # Consume one typed seed (text→voice trigger): delivered as the
            # opening user turn once the agent joins, then forgotten. Taken
            # here so a summon that never connects drops it instead of
            # leaking it into a later, unrelated call.
            with self._mic_lock:
                seed_text = self._pending_text
                self._pending_text = ""
                announce_text = self._pending_announce
                self._pending_announce = ""
            if announce_text:
                reason = "announce"

            room_name = summon_room_name()
            hud_stage("auth")
            # Preflight: a token that cannot be minted fails before the mic
            # opens. The first attempt reuses it; each retry mints afresh.
            pending_tokens = [
                mint_summon_token(room=room_name, reason=reason, announce=announce_text)
            ]

            def connect_token() -> str:
                if pending_tokens:
                    return pending_tokens.pop()
                return mint_summon_token(
                    room=room_name, reason=reason, announce=announce_text
                )

            client = VoiceClient(voice_url(), connect_token)
        except BaseException:
            # Cancellation before a client exists must not leave the HUD
            # presenting an abandoned call check/authentication as live work.
            clear_hud_waking()
            call_check.cancel()
            raise
        disconnected = asyncio.Event()
        agent_ready = asyncio.Event()
        self._agent_turn_id = None
        self._playback_scope = room_name
        self._agent_output = None
        self._voice = client

        loop = asyncio.get_running_loop()

        async def cancel_linked(payload):
            target = payload.get("turn_id")
            if target is None or target == getattr(self, "_agent_turn_id", None):
                output = getattr(self, "_agent_output", None)
                if output is not None and hasattr(output, "cancel"):
                    output.cancel()
            await self._send_turn(payload)

        async def startup_audio(pending):
            if linked is None:
                # Only linked (external) turns carry jarvis.turn control frames.
                return
            await self._send_turn(
                {"type": "jarvis_turn", "event": "startup_audio", "pending": pending}
            )

        def on_agent_online(_payload=None) -> None:
            # The hub starts the agent job on our join; the first agent state
            # (or caption) means its session is up and can hear the mic.
            agent_ready.set()

        client.on_topic("state", on_agent_online)
        client.on_topic("caption", on_agent_online)
        client.on_topic("flush", lambda payload=None: self._flush_playback(payload))
        client.on_topic(BARGE_TOPIC, self._on_barge)
        client.on_topic("jarvis.turn", self._on_turn_payload)

        run_task = client.start()
        run_task.add_done_callback(lambda _task: disconnected.set())

        rewake = asyncio.Event()
        self._rewake = rewake
        self._last_agent_voice = time.monotonic()
        if mic_queue is None:
            self._listen_queue_live = False
        call_tasks: set[asyncio.Task] = {call_check}
        source = None
        try:
            hud_stage("connect")
            try:
                connected = await _connect_or_fail(client, run_task)
            except Exception:
                if announce_text:
                    self._speak_fallback(announce_text)
                raise
            if not connected:
                if client.last_close_code == ROOM_BUSY_CODE:
                    # A live call already holds the hub: stay out, HUD visible.
                    logger.warning("already in a call; staying out")
                    summon_overlay()
                    return False
                # A signaling/network outage must not kill the offline listener.
                # Cleanup below drains the check and clears unjoined call state.
                # Require a fresh wake/PTT; never replay this opening request.
                logger.warning("call connection failed; returning to listening")
                if announce_text:
                    self._speak_fallback(announce_text)
                return False
            if await call_check:
                # The finally below leaves the call and clears the HUD.
                logger.warning("already in a call; staying out")
                summon_overlay()
                return False
            if linked is not None and linked.cancelled.is_set():
                return False
            if linked is not None:

                def dispatch_cancel(payload):
                    def schedule():
                        task = asyncio.create_task(cancel_linked(payload))
                        call_tasks.add(task)
                        task.add_done_callback(call_tasks.discard)

                    with contextlib.suppress(RuntimeError):
                        loop.call_soon_threadsafe(schedule)

                linked.bind(dispatch_cancel)
            if linked is not None and not seed_text:
                linked.seed()
            if mic_queue is not None and preserve_join_audio:
                with contextlib.suppress(Exception):
                    await startup_audio(True)
            logger.warning("joined %s, waiting for %s", room_name, self._agent_name)
            publish_hud_room(room_name)
            self._set_in_call(True)
            hud_stage("uplink")
            from fast_barge_in import MicBargeIn
            from fast_barge_in import enabled as fast_barge_enabled

            self._fast_barge = (
                MicBargeIn(getattr(self, "_talk_gate", None), self._fast_mute_speaker)
                if fast_barge_enabled()
                and getattr(self, "_talk_gate", None) is not None
                else None
            )
            self._barge_sync = BargeSync() if self._fast_barge is not None else None
            source = MicUplink(client)
            mic_task = asyncio.create_task(
                self._pump_queue(
                    source,
                    mic_queue,
                    agent_ready,
                    preroll or [],
                    preserve_join_audio,
                    capture_epoch,
                    startup_audio,
                )
                if mic_queue is not None
                else self._pump_mic(source)
            )
            call_tasks.add(mic_task)

            # Wait for the agent, then relay its voice to the speakers.
            hud_stage("dispatch")
            track_ready = asyncio.create_task(agent_ready.wait())
            ended = asyncio.create_task(disconnected.wait())
            call_tasks.update((track_ready, ended))
            done, _ = await asyncio.wait(
                {track_ready, ended, mic_task},
                timeout=AGENT_JOIN_TIMEOUT,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ended in done or mic_task in done or track_ready not in done:
                logger.warning("agent join ended before audio; leaving %s", room_name)
                if announce_text:
                    self._speak_fallback(announce_text)
                # The mic also exits normally after detecting a stale-call
                # wake. Only that successful exit requests another session.
                return (
                    rewake.is_set()
                    and not disconnected.is_set()
                    and mic_task in done
                    and not mic_task.cancelled()
                    and mic_task.exception() is None
                )
            if linked is not None and linked.cancelled.is_set():
                return False
            # "online" means the agent has announced itself, not that the remote
            # model has started responding or audio is audible.
            hud_stage("online")
            play_task = asyncio.create_task(self._play_agent(client))
            call_tasks.add(play_task)
            if seed_text and (linked is None or linked.seed()):
                # Text→voice: the typed opener becomes the first user turn, so
                # the agent answers with voice. Fail-soft: a dropped seed never
                # kills audio.
                try:
                    if linked is None:
                        await client.send_chat(seed_text)
                    else:
                        await self._send_turn(
                            {
                                "type": "jarvis_turn",
                                "event": "external_seed",
                                "client_id": linked.key[0],
                                "external_turn_id": linked.key[1],
                                "text": seed_text,
                            }
                        )
                    logger.warning("seeded call with typed text")
                except Exception as exc:
                    logger.warning("seed text send failed: %s", exc)
            rewoken = asyncio.create_task(rewake.wait())
            call_tasks.add(rewoken)
            done, _ = await asyncio.wait(
                {ended, rewoken, play_task, mic_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if play_task in done:
                # A closed stream or failed speaker cannot deliver another
                # answer. Leave instead of holding a silent call indefinitely.
                # Quiet gaps/barge-in keep the playback task alive and stay here.
                logger.warning("agent playback ended; leaving call")
                return False
            if mic_task in done:
                logger.warning("microphone ended; leaving call")
                if mic_task.cancelled() or mic_task.exception() is not None:
                    return False
            return rewake.is_set() and not disconnected.is_set()
        finally:
            # Observe failures AND cancel peers on every exit path. A failed
            # playback task must never prevent the microphone from closing.
            try:
                for task in call_tasks:
                    task.cancel()
                await asyncio.gather(*call_tasks, return_exceptions=True)
                if run_task.done() and not run_task.cancelled():
                    run_task.exception()  # Observed: a fatal close is handled above.
            finally:
                try:
                    if source is not None:
                        with contextlib.suppress(Exception):
                            await source.aclose()
                finally:
                    if linked is not None:
                        linked.close()
                    with self._mic_lock:
                        if getattr(self, "_active_linked_turn", None) is linked:
                            self._active_linked_turn = None
                    clear_hud_room()
                    clear_hud_waking()
                    self._set_in_call(False)
                    self._rewake = None
                    self._listen_queue_live = True
                    self._fast_barge = None
                    self._barge_sync = None
                    self._active_audio_playback = None
                    self._voice = None
                    await client.close()
                    logger.warning("session over, back to listening")

    def _fast_mute_speaker(self, now: float, fade_ms: float = 0.0) -> None:
        """Mute locally without stopping microphone or Gemini input.

        ``fade_ms`` > 0 ramps down first (same resume gate afterwards)."""
        sync = getattr(self, "_barge_sync", None)
        if sync is not None:
            sync.interrupt(now)
        output = getattr(self, "_active_audio_playback", None)
        if output is not None and fade_ms > 0 and hasattr(output, "fade_out"):
            output.fade_out(fade_ms)
        elif output is not None and hasattr(output, "mute"):
            output.mute()

    def _on_barge(self, payload: object) -> None:
        """Feed hub barge-in counters to the model-authoritative BargeSync."""
        sync = getattr(self, "_barge_sync", None)
        if sync is None:
            return
        message = decode_barge(
            json.dumps(payload, separators=(",", ":")).encode(), BARGE_TOPIC
        )
        if message is not None:
            sync.ingest(message)

    def _on_turn_payload(self, payload: object) -> None:
        self._handle_turn_data(json.dumps(payload).encode())

    def _flush_playback(self, payload: object = None) -> None:
        """Server ``flush``: drop agent audio already queued for the speaker now.

        A ``{"fade_ms": n}`` payload (the "stop" voice command) ramps the
        speaker to silence over n ms instead of cutting hard."""
        output = getattr(self, "_agent_output", None)
        if output is None:
            return
        fade_ms = payload.get("fade_ms") if isinstance(payload, dict) else None
        if getattr(self, "_fast_barge", None) is not None and hasattr(output, "mute"):
            # Same gate as a local barge-in: only a new reply after confirmed
            # user speech releases the speaker again (see _play_agent).
            self._fast_mute_speaker(
                time.monotonic(),
                fade_ms=float(fade_ms) if isinstance(fade_ms, (int, float)) else 0.0,
            )
        elif hasattr(output, "cancel"):
            output.cancel()

    async def _send_turn(self, payload: dict) -> None:
        """Publish one jarvis.turn control message on the live call. Fail-soft."""
        client = getattr(self, "_voice", None)
        if client is None:
            return
        with contextlib.suppress(Exception):
            await client.send_topic("jarvis.turn", payload)

    async def _pump_mic(self, source) -> None:
        """Forward live mic blocks to the call (the hotword loop keeps listening).

        While muted the queue is drained but nothing is captured: the call
        stays joined so unmute resumes mid-call with no rejoin.
        """
        import numpy as np
        import sounddevice as sd

        from audio_dsp import Downsampler48to16

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=MIC_QUEUE_MAX_BLOCKS)
        model = self._model
        if model is not None:
            with contextlib.suppress(Exception):
                model.reset()
        pending16 = np.zeros(0, dtype=np.int16)
        down16 = Downsampler48to16()
        hot_run = 0
        from mic_clean import make_cleaner

        cleaner = make_cleaner(BLOCKSIZE)

        def _callback(indata, frames, time_info, status) -> None:
            nonlocal cleaner
            blocks = [bytes(indata)]
            if cleaner is not None:
                try:
                    blocks = cleaner.process(blocks[0])
                except Exception as exc:
                    logger.warning("noise suppression failed, raw mic: %s", exc)
                    cleaner = None
            with contextlib.suppress(Exception):
                for block in blocks:
                    loop.call_soon_threadsafe(enqueue_mic_block, queue, block)

        with sd.RawInputStream(
            samplerate=MIC_RATE,
            channels=1,
            dtype="int16",
            blocksize=BLOCKSIZE,
            callback=_callback,
        ):
            while True:
                raw = await queue.get()
                if mic_buffer_failed(queue):
                    return
                if self.muted:
                    continue
                barge = getattr(self, "_fast_barge", None)
                frames = (
                    barge.feed(raw, time.monotonic())
                    if barge
                    else [self._gate_mic(raw, time.monotonic())]
                )
                # This mic coroutine NEVER waits for model interruption.
                for sent in frames:
                    await source.capture(sent)
                if model is None or self._rewake is None:
                    continue
                # 1536 samples at 48k decimate exactly to 512 at 16k.
                block = np.frombuffer(raw, dtype=np.int16)
                pending16 = np.concatenate([pending16, down16.process(block)])
                scores, pending16 = score_frames(model, pending16)
                for score in scores:
                    hot_run = hot_run + 1 if score >= REWAKE_THRESHOLD else 0
                    quiet = time.monotonic() - self._last_agent_voice
                    if should_rewake(hot_run, quiet):
                        logger.warning(
                            "in-call wake (%.2f), agent quiet %.0fs", score, quiet
                        )
                        self._rewake.set()
                        return

    async def _record_question(
        self, queue, peak: float = 0.0, floor: float | None = None
    ) -> list:
        """After a school wake: blocks until Sir stops talking (0.8 s under
        the noise gate, after at least 0.6 s) or RECORD_MAX_S. Returns raw
        blocks. `peak` is the wake phrase's loudest block RMS: anything
        well under Sir's own voice counts as quiet, so a video playing in
        the room can't hold the gate open for the whole cap. `floor` is the
        room's level from before the wake word; without it the floor is
        learned from the question itself, and steady speech reads as quiet."""
        import numpy as np

        out: list = []
        quiet = 0.0
        block_s = BLOCKSIZE / MIC_RATE
        while len(out) * block_s < RECORD_MAX_S:
            raw = await queue.get()
            out.append(raw)
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(x**2))) if x.size else 0.0
            # Noise floor: drops to quieter blocks at once, creeps up slowly.
            floor = rms if floor is None or rms < floor else floor * 1.01
            peak = max(peak, rms)
            loud = rms > max(150.0, 2.5 * floor, SPEECH_DROP * peak)
            quiet = 0.0 if loud else quiet + block_s
            if len(out) * block_s >= 0.6 and quiet >= 0.8:
                break
        return out

    async def _pump_queue(
        self,
        source,
        queue,
        agent_ready,
        preroll: list,
        preserve_join_audio: bool = False,
        capture_epoch: int | None = None,
        startup_audio=None,
    ) -> None:
        """Forward the existing capture stream once the agent can hear it.

        Normal calls replay the short onset tail and everything captured
        during join exactly once. School calls discard the join backlog:
        their opening question has already been sent as text.
        """
        import numpy as np

        from audio_dsp import Downsampler48to16

        if capture_epoch is None:
            capture_epoch = getattr(self, "_mute_epoch", 0)
        try:
            await asyncio.wait_for(agent_ready.wait(), AGENT_JOIN_TIMEOUT)
        except TimeoutError:
            return
        if preserve_join_audio and startup_audio is not None:
            with contextlib.suppress(Exception):
                await startup_audio(True)
        await asyncio.sleep(MIC_READY_SETTLE_S)  # agent input stream attachment
        if preserve_join_audio and mic_buffer_failed(queue):
            return
        if self.muted or capture_epoch != getattr(self, "_mute_epoch", 0):
            preroll = []
        initial = list(preroll)
        if preserve_join_audio:
            while not queue.empty():
                initial.append(queue.get_nowait())
        else:
            drain_queue(queue)
        replay_epoch = getattr(self, "_mute_epoch", 0)
        # Real-time pace: Gemini runs in manual-activity mode and only keeps
        # audio between the agent's VAD start/end, so a burst replay put
        # most of the question outside that window ("What is the cap").
        next_frame_at = time.monotonic()
        silent_s = 0.0
        silence_gap = MIC_CATCHUP_SILENCE_S
        with contextlib.suppress(ValueError):
            silence_gap = max(
                silence_gap,
                float(os.environ.get("JARVIS_VAD_MIN_SILENCE", "0") or "0") + 0.05,
            )
        with contextlib.suppress(ValueError):
            # SDK endpointing is measured from the last speech sample, so
            # retain the larger delay rather than adding the two waits.
            silence_gap = max(
                silence_gap,
                float(os.environ.get("JARVIS_ENDPOINT_DELAY", "0") or "0") + 0.05,
            )

        async def forward(raw: bytes, *, backlog: bool, gated: bool = False) -> None:
            nonlocal next_frame_at, silent_s
            duration = len(raw) / (2 * MIC_RATE)
            if preserve_join_audio and not any(raw):
                if silent_s >= silence_gap and backlog:
                    return
                silent_s += duration
            else:
                silent_s = 0.0
            # PCM duration, not fixed block size: short frames must keep
            # their natural timing as well. Never accelerate speech.
            now = time.monotonic()
            if now - next_frame_at > duration:
                next_frame_at = now
            if not gated:
                raw = self._gate_mic(raw, now)
            await source.capture(raw)
            next_frame_at += duration
            ahead = next_frame_at - time.monotonic()
            if ahead > 0:
                await asyncio.sleep(ahead)

        for index, raw in enumerate(initial):
            if self.muted or replay_epoch != getattr(self, "_mute_epoch", 0):
                break  # never replay speech buffered before a mute
            if preserve_join_audio and mic_buffer_failed(queue):
                return
            await forward(raw, backlog=index + 1 < len(initial) or not queue.empty())
        initial.clear()
        if preserve_join_audio and startup_audio is not None:
            with contextlib.suppress(Exception):
                await startup_audio(False)
        # Buffered keyword audio is never scored again. Only new audio can
        # request a stale-call restart, just like the former second stream.
        model = self._model if preserve_join_audio else None
        if model is not None:
            with contextlib.suppress(Exception):
                model.reset()
        pending16 = np.zeros(0, dtype=np.int16)
        down16 = Downsampler48to16()
        hot_run = 0
        while True:
            raw = await queue.get()
            if preserve_join_audio and mic_buffer_failed(queue):
                return
            if self.muted:
                continue
            barge = getattr(self, "_fast_barge", None)
            if barge is not None:
                for sent in barge.feed(raw, time.monotonic()):
                    if preserve_join_audio:
                        await forward(sent, backlog=not queue.empty(), gated=True)
                    else:
                        await source.capture(sent)
            elif preserve_join_audio:
                await forward(raw, backlog=not queue.empty())
            else:
                await source.capture(self._gate_mic(raw, time.monotonic()))
            if model is None or self._rewake is None:
                continue
            pending16 = np.concatenate(
                [pending16, down16.process(np.frombuffer(raw, dtype=np.int16))]
            )
            scores, pending16 = score_frames(model, pending16)
            for score in scores:
                hot_run = hot_run + 1 if score >= REWAKE_THRESHOLD else 0
                quiet = time.monotonic() - self._last_agent_voice
                if should_rewake(hot_run, quiet):
                    logger.warning(
                        "in-call wake (%.2f), agent quiet %.0fs", score, quiet
                    )
                    self._rewake.set()
                    return

    @staticmethod
    def _speaker_lease(action: str) -> bool:
        """Bridge arbitrates the desktop and phone speaker across processes."""
        import urllib.error
        import urllib.request

        home = Path(os.environ.get("JARVIS_HOME", "") or Path.home() / ".jarvis")
        token = os.environ.get("JARVIS_BRIDGE_TOKEN", "").strip()
        if not token:
            with contextlib.suppress(OSError):
                token = (home / "bridge_token").read_text().strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        port = os.environ.get("JARVIS_BRIDGE_PORT", "4317")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/speaker",
            data=json.dumps({"surface": "desktop", "action": action}).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                return bool(json.load(response).get("ok"))
        except (OSError, ValueError) as exc:
            logger.warning("desktop speaker lease %s failed: %s", action, exc)
            return False

    def _cancel_linked_turn(self, message: dict) -> bool:
        from linked_turn import external_key

        key = external_key(message)
        if key is None:
            return False
        with self._mic_lock:
            pending = getattr(self, "_pending_linked_turn", None)
            active = getattr(self, "_active_linked_turn", None)
            if pending is not None and pending.key == key:
                self._pending_text = ""
                self._talk_event.clear()
        return bool(
            (pending is not None and pending.cancel(key))
            or (active is not None and active.cancel(key))
        )

    def _handle_turn_data(self, raw: bytes) -> None:
        """Cancel at the device gate, even when an old frame is already queued."""
        from playback_gate import get_playback_gate

        try:
            message = json.loads(raw)
        except (TypeError, ValueError, UnicodeDecodeError):
            return
        if not isinstance(message, dict) or message.get("type") != "jarvis_turn":
            return
        linked = getattr(self, "_active_linked_turn", None)
        if linked is not None:
            linked.observe(message)
        tid = message.get("turn_id")
        if not isinstance(tid, int):
            return
        scoped_id = (getattr(self, "_playback_scope", "call"), tid)
        output = getattr(self, "_agent_output", None)
        if message.get("event") == "cancel":
            get_playback_gate().cancel(scoped_id)
            if (
                tid == getattr(self, "_agent_turn_id", None)
                and output is not None
                and hasattr(output, "cancel")
            ):
                output.cancel()
        elif message.get("event") == "state" and message.get("state") == "speaking":
            current = getattr(self, "_agent_turn_id", None)
            if current is None or tid >= current:
                self._agent_turn_id = tid

    async def _play_agent(self, client) -> None:
        """Play agent audio in order while mic/call callbacks stay responsive."""
        from audio_playback import AudioPlayback, FastAudioPlayback

        barge = getattr(self, "_fast_barge", None)
        output = FastAudioPlayback() if barge is not None else AudioPlayback()
        self._active_audio_playback = output
        # Tests and direct stream consumers can omit a bridge session. Normal
        # calls always carry the scope and require explicit ownership.
        scoped_call = getattr(self, "_playback_scope", None) is not None
        lease = not scoped_call or await asyncio.to_thread(self._speaker_lease, "claim")
        refresh_at = time.monotonic() + 30.0
        idle_say = getattr(self, "_say_output", None)
        if idle_say is not None:
            # One output stream at a time: the call's playback takes over.
            self._say_output = None
            with contextlib.suppress(Exception):
                await idle_say.aclose()
        self._agent_output = output
        fade_left = fade_total = 0
        ack = getattr(self, "_ack_session", None)
        if ack is not None:
            ack.cancelled.set()
        frames = client.audio_frames()
        try:
            async for pcm in frames:
                if scoped_call and time.monotonic() >= refresh_at:
                    lease = await asyncio.to_thread(self._speaker_lease, "claim")
                    refresh_at = time.monotonic() + 30.0
                    if not lease and hasattr(output, "cancel"):
                        output.cancel()
                if not lease:
                    continue
                now = time.monotonic()
                sync = getattr(self, "_barge_sync", None)
                if (
                    barge is not None
                    and sync is not None
                    and sync.ready
                    and now - (barge.last_loud_at or barge.interrupted_at) >= 0.25
                ):
                    # Only a new Gemini speech handle after confirmed user
                    # speech may release the local speaker. Never use a
                    # silence gap or arbitrary timeout to resume old audio.
                    output.resume()
                    barge.resume()
                    sync.complete()
                samples = self._bytes_to_int16(pcm)
                if (
                    samples.size
                    and int(abs(samples).max()) > 500
                    and not getattr(output, "_muted", False)
                ):
                    self._last_agent_voice = time.monotonic()
                gate = getattr(self, "_talk_gate", None)
                if (
                    gate is not None
                    and samples.size
                    and not getattr(output, "_muted", False)
                ):
                    rms = float((samples.astype("float32") ** 2).mean() ** 0.5)
                    gate.note_playback(rms, time.monotonic())
                if _school_quiet():
                    samples = (samples.astype("float32") * _school_gain()).astype(
                        "int16"
                    )
                if samples.size and (fade_left or _character_changed()):
                    if not fade_left:
                        fade_left = fade_total = int(
                            AGENT_RATE * CHAR_FADE_IN_MS / 1000
                        )
                    n = min(fade_left, samples.size)
                    ramp = (
                        fade_total - fade_left + __import__("numpy").arange(n)
                    ) / max(fade_total, 1)
                    samples = samples.astype("float32")
                    samples[:n] *= ramp
                    samples = samples.astype("int16")
                    fade_left -= n
                await output.write(samples, AGENT_RATE)
        finally:
            if getattr(self, "_active_audio_playback", None) is output:
                self._active_audio_playback = None
            try:
                with contextlib.suppress(Exception):
                    await output.aclose()
            finally:
                try:
                    with contextlib.suppress(Exception):
                        await frames.aclose()
                finally:
                    if scoped_call and lease:
                        with contextlib.suppress(Exception):
                            await asyncio.to_thread(self._speaker_lease, "release")


def mix_character_whoosh(pcm, sr: int, whoosh, gain_db: float):
    """Whoosh mixed under the line, soft-limited (peak safe). Pure."""
    import voice_fx

    return voice_fx.mix_under(pcm, whoosh, gain_db)


def prepare_character_pcm(pcm, sr: int, fade_ms: float):
    """Head fade-in so a cut-in never clicks. Pure."""
    import voice_fx

    return voice_fx.fade_in(pcm, sr, fade_ms)


CHAR_FADE_IN_MS = 120


def _character_changed() -> bool:
    """True once after the active character changed. Jarvis-only: always False."""
    import sys

    mod = sys.modules.get("character_runtime")
    if mod is None:
        return False
    try:
        active = mod.get_runtime().active_id()
    except Exception:
        return False
    global _played_character
    if active == _played_character:
        return False
    _played_character = active
    return True


_played_character = "jarvis"


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
