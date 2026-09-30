import asyncio
import contextlib
import difflib
import math
import os
import random
import re
import time
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from google.genai import types as genai_types
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    TurnHandlingOptions,
    cli,
    function_tool,
    inference,
    room_io,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.agents import llm
from livekit.agents.llm import FallbackAdapter, RealtimeModelFallbackAdapter
from livekit.agents.types import NOT_GIVEN
from livekit.agents.voice import UserStateChangedEvent, io
from livekit.plugins import google

try:
    from livekit.plugins import silero
except ImportError:  # pragma: no cover - direct pipeline needs the extra
    silero = None  # type: ignore[assignment]

from browser import BrowserManager

try:
    from src.local_voice import PiperTTS
except ImportError:
    from local_voice import PiperTTS
try:
    from src.local_stt import FasterWhisperSTT
except ImportError:
    from local_stt import FasterWhisperSTT
from prompts import AGENT_INSTRUCTIONS, RESEARCH_INSTRUCTIONS, SYSTEM_INSTRUCTIONS
from system.background import BackgroundTools
from system.budget import record_session
from system.budget import status as budget_status
from system.core import SystemTools
from system.daily import DailyTools
from system.desktop import DesktopTools
from system.devices import DeviceTools
from system.alternatives_tools import AlternativesTools
from system.files_tools import FilesTools
from system.focus_tools import FocusTools
from draft_tools import DraftTools
from notify_tools import NotifyTools
from system.exam_tools import ExamTools
from system.memory_tools import MemoryTools
from system.window_tools import WindowTools
from system.inbox import InboxTools
from system.recall_tools import RecallTools
from system.osint import OsintTools
from system.pentest import PentestTools
from system.projects_tools import ProjectTools
from system.quotes_tools import QuoteTools
from system.school_tools import SchoolTools
from system.vision_tools import VisionTools
from system.workspace_tools import WorkspaceTools
from system.reddit import RedditTools
from tools import BrowserTools

# Resolve against the checkout root, not cwd: the Tauri shell spawns the
# worker with cwd=shell/, where a relative ".env.local" never resolves and
# the worker dies with "api_key is required" (first-run crash loop).
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")


_TOOL_NUDGE_S = 1.5


def _patch_gemini_turn_end() -> bool:
    """Stop Gemini Live answering one turn behind (manual activity mode).

    At end of turn the plugin's generate_reply sends activity_end (which
    already asks Gemini to answer the audio just spoken) AND an empty
    turn_complete client turn. On 3.x Live models that empty turn starts
    a reply to the context *before* the new audio lands, so Sir heard
    the previous answer ("2401 - 1605" -> "2401") while the real one was
    dropped as "server content but no active generation". Suppress only
    that empty trailing turn; instructed replies (greetings) are
    untouched. JARVIS_GEMINI_TURN_PATCH=0 disables. Idempotent.
    """
    if os.environ.get("JARVIS_GEMINI_TURN_PATCH", "1").strip() == "0":
        return False
    try:
        from google.genai import types as _gt
        from livekit.agents.types import NOT_GIVEN
        from livekit.plugins.google.realtime import realtime_api as _ra
    except Exception:
        return False
    sess = _ra.RealtimeSession
    if getattr(sess, "_jarvis_turn_patch", False):
        return True
    orig_generate = sess.generate_reply
    orig_send = sess._send_client_event

    def generate_reply(self, *, instructions=NOT_GIVEN, **kwargs):
        self._jarvis_audio_turn = bool(
            self._in_user_activity
            and instructions is NOT_GIVEN
            and not _ra._needs_reply_placeholder(self._opts.model)
        )
        try:
            return orig_generate(self, instructions=instructions, **kwargs)
        finally:
            self._jarvis_audio_turn = False

    def _send_client_event(self, event):
        if (
            getattr(self, "_jarvis_audio_turn", False)
            and isinstance(event, _gt.LiveClientContent)
            and not event.turns
        ):
            return None
        out = orig_send(self, event)
        if isinstance(event, _gt.LiveClientToolResponse):
            # Without that empty turn, Gemini sits on a tool result and never
            # speaks it (maths computed, silence). Nudge only if no reply has
            # started shortly after, so a normal continuation isn't doubled.
            def _nudge(sess=self) -> None:
                gen = sess._current_generation
                if gen is None or gen._done:
                    orig_send(sess, _gt.LiveClientContent(turns=[], turn_complete=True))

            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().call_later(_TOOL_NUDGE_S, _nudge)
        return out

    sess.generate_reply = generate_reply
    sess._send_client_event = _send_client_event
    sess._jarvis_turn_patch = True
    return True


_patch_gemini_turn_end()


def _say_instructions(text: str) -> str:
    return (
        "Say exactly the following line to Sir, word for word, in your "
        f"normal voice, and nothing else: {text}"
    )


def _patch_say_without_tts() -> bool:
    """Make session.say() speak on the Gemini Live pipeline.

    The realtime session has no TTS and Gemini Live doesn't support say(),
    so every say() raised and was swallowed: instant commands (maths,
    volume, project navigation) ran silently. Without a TTS, route the
    line through the realtime model as a verbatim reply instead, so it
    comes out in the same voice. Idempotent.
    """
    if getattr(AgentSession, "_jarvis_say_patch", False):
        return True
    orig_say = AgentSession.say

    def say(self, text, *, audio=NOT_GIVEN, allow_interruptions=NOT_GIVEN, add_to_chat_ctx=True):
        activity = getattr(self, "_activity", None)
        rt = activity is not None and isinstance(
            getattr(activity, "llm", None), llm.RealtimeModel
        )
        if (
            rt
            and self.tts is None
            and audio is NOT_GIVEN
            and isinstance(text, str)
            and not activity.llm.capabilities.supports_say
        ):
            return self.generate_reply(
                instructions=_say_instructions(text),
                allow_interruptions=allow_interruptions,
            )
        return orig_say(
            self,
            text,
            audio=audio,
            allow_interruptions=allow_interruptions,
            add_to_chat_ctx=add_to_chat_ctx,
        )

    AgentSession.say = say
    AgentSession._jarvis_say_patch = True
    return True


_patch_say_without_tts()


def _realtime_llm():
    """Voice brain with high-usage backups.

    Primary is gemini-3.8-live (note: no "flash" — gemini-3.8-flash is
    the text-only sibling). Base, not -extended-thinking: thinking
    trades latency for reasoning and a voice router wants the opposite.
    When the primary is saturated (429/quota), the adapter fails over
    down VOICE_MODEL_CHAIN preserving chat context; when it recovers,
    later sessions start at the top again (per-session order).
    JARVIS_VOICE_MODELS (comma list) overrides the chain without code.
    """
    return RealtimeModelFallbackAdapter(
        [_voice_model(mid) for mid in _voice_chain()],
    )


# Newest-first voice chain: separate Gemini Live quotas per model, so a
# saturated primary still leaves backups. 3.1 is the older Live voice
# model; 2.5 native-audio is the legacy fallback (full server-speech
# flow, unlimited-free AI Studio quota).
VOICE_MODEL_CHAIN = [
    "gemini-3.8-live",
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-latest",
]


def _voice_chain() -> list:
    """Chain order, env-overridable. Pure (env only)."""
    raw = os.environ.get("JARVIS_VOICE_MODELS", "").strip()
    if not raw:
        return list(VOICE_MODEL_CHAIN)
    models = [m.strip() for m in raw.split(",") if m.strip()]
    return models or list(VOICE_MODEL_CHAIN)


def _voice_model(model_id: str):
    """One chain link with that generation's thinking rules. Pure-ish.

    3.8-live accepts NO thinking knobs (1007 "Thinking level is not
    supported", verified live 2026-09-20). 3.1 Live wants thinkingLevel
    MINIMAL for lowest latency. 2.5 native-audio wants a small
    thinking_budget (32: every thinking token delays first audio).
    Unknown IDs get no thinking config (model default).
    """
    thinking: dict = {}
    if "3.1" in model_id:
        thinking = {
            "thinking_config": genai_types.ThinkingConfig(thinking_level="MINIMAL")
        }
    elif "2.5" in model_id:
        thinking = {"thinking_config": genai_types.ThinkingConfig(thinking_budget=32)}
    return google.realtime.RealtimeModel(
        model=model_id,
        voice="Enceladus",
        # No language code: native-audio models reject explicit codes
        # and default to English anyway.
        **thinking,
        # Endpointing is MANUAL (automatic_activity_detection.disabled):
        # server-side VAD hears nothing for this key (zero transcripts on
        # every call at any sensitivity — verified direct against the Live
        # API). The framework frames turns itself from local endpointing
        # (turn_detection="vad" + Silero below) and the plugin forwards
        # activity_start/activity_end. Verified: manual framing answers.
        realtime_input_config=genai_types.RealtimeInputConfig(
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                disabled=True
            )
        ),
        tool_response_scheduling=genai_types.FunctionResponseScheduling.WHEN_IDLE,
    )


# Cheap pipeline model IDs (LiveKit Inference; billed from credits).
CHEAP_STT_MODEL = "assemblyai/universal-streaming"
CHEAP_LLM_MODEL = "google/gemini-3.8-flash"
# Direct voice brain (REST generateContent namespace): newest GA Flash.
# Intentionally NOT the same value as bridge.py's GEMINI_TEXT_MODEL only
# in history — both now track gemini-3.8-flash; the LiveKit RealtimeModel
# above lives in the live voice API namespace and must not be "kept in
# sync" with these text IDs.
DIRECT_LLM_MODEL = "gemini-3.8-flash"
# Free cloud fallback voice (debugging + fallback if local voice misbehaves).
CHEAP_TTS_MODEL = "rime/coda"

# Silence before the agent hangs up on its own (seconds).
IDLE_HANGUP_SECONDS = 60.0


def _idle_exceeded(
    last_active: float, now: float, idle_seconds: float = IDLE_HANGUP_SECONDS
) -> bool:
    """True only after a full quiet window with no user/agent activity. Pure."""
    return (now - last_active) >= idle_seconds


def _engaged_idle_seconds() -> float:
    """Idle budget for engaged calls (JARVIS_ENGAGED_IDLE, default 180 s)."""
    try:
        return max(30.0, float(os.environ.get("JARVIS_ENGAGED_IDLE", "").strip() or 180.0))
    except ValueError:
        return 180.0


def _is_real_turn(text: str, is_final: bool) -> bool:
    """A final transcript of 2+ words. Pure.

    Background noise transcribes as stray single words ("ja", "Oi",
    "Bayer"); counting those as activity kept a noise-held call open for
    half an hour, deaf to "hey Jarvis" the whole time.
    """
    if not is_final:
        return False
    return sum(1 for w in str(text or "").split() if any(c.isalpha() for c in w)) >= 2


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
LOOP_REPEATS = 3  # same sentence this many times in one reply = a loop


def is_repeating(text: str) -> bool:
    """Gemini realtime can degenerate into repeating one sentence for
    minutes ("Assuming we both survive the ordeal, I will now handle
    opening Google Docs for you." x20). True once any sentence of 4+ words
    occurs LOOP_REPEATS times in one reply. Pure."""
    counts: dict[str, int] = {}
    for raw in _SENTENCE_SPLIT.split(text or ""):
        key = " ".join(re.sub(r"[^\w\s]", "", raw.lower()).split())
        if len(key.split()) < 4:
            continue
        counts[key] = counts.get(key, 0) + 1
        if counts[key] >= LOOP_REPEATS:
            return True
    return False


class _HudLiveCaption(io.TextOutput):
    """Tail of the room transcription chain: receives Jarvis's words as the
    TranscriptSynchronizer releases them, i.e. in step with the audio, and
    mirrors the growing line to the HUD (which has no WebRTC to read the
    room's own transcription stream)."""

    def __init__(self) -> None:
        super().__init__(label="HudLiveCaption", next_in_chain=None)
        self._seq = 0
        self._text = ""
        self._open = False
        # Set once the session exists: interrupts a looping reply.
        self.on_loop = None
        self._loop_fired = False

    async def capture_text(self, text: str) -> None:
        if not self._open:
            self._seq += 1
            self._text = ""
            self._open = True
            self._loop_fired = False
        self._text += text
        _write_live_caption(f"{os.getpid()}-{self._seq}", self._text, False)
        if self.on_loop and not self._loop_fired and is_repeating(self._text):
            self._loop_fired = True
            with contextlib.suppress(Exception):
                self.on_loop(self._text)

    def flush(self) -> None:
        if self._open:
            self._open = False
            _write_live_caption(f"{os.getpid()}-{self._seq}", self._text, True)


def _write_live_caption(uid: str, text: str, done: bool) -> None:
    try:
        from hud_events import live_caption

        live_caption(uid, _strip_markup(text), done)
    except Exception:
        pass


def _strip_markup(text: str) -> str:
    try:
        from livekit.agents.tts._provider_format import strip_all_markup

        return strip_all_markup(text)
    except Exception:
        return text


def _wake_summoned(room: object) -> bool:
    """Was this room opened by the offline wake-word client?

    wake_client joins as identity "jarvis-master" and dispatches the
    worker; the spoken "hey Jarvis" is spent on the detector and never
    reaches the agent as a transcript. Without this check the agent
    joins to silence and standby hushes it forever. Frontend/app joins
    use participantName "user", never this identity. Pure (no I/O)."""
    try:
        parts = getattr(room, "remote_participants", None) or {}
        items = parts.values() if hasattr(parts, "values") else parts
        for participant in items or []:
            if getattr(participant, "identity", "") == "jarvis-master":
                return True
    except Exception:
        pass
    return False


def _wake_reason(room: object) -> str:
    """What summoned this call, from the wake client's token attributes:
    "daddy" (the "wake up, daddy's home" phrase), "school-bare" / "school-ask"
    (school wake without / with a question), "wake", or "" (not a
    wake summons). Pure (no I/O)."""
    try:
        parts = getattr(room, "remote_participants", None) or {}
        items = parts.values() if hasattr(parts, "values") else parts
        for participant in items or []:
            if getattr(participant, "identity", "") == "jarvis-master":
                attrs = getattr(participant, "attributes", None) or {}
                return str(attrs.get("jarvis.wake") or "wake")
    except Exception:
        pass
    return ""


DADDY_GREETING = "Welcome back, Sir."


SCHOOL_FIRST_WINDOW_S = 12.0
SCHOOL_BARE_REPLY = "Yes, Sir?"
# Normal-mode wake calls: close this long after Jarvis's last reply, so
# the HUD drops back to idle and the wake word listens again (the 180 s
# engaged budget kept calls open while room chatter reset it).
NORMAL_FOLLOW_UP_S = 15.0
# Longest Sir's "speaking" may hold the window without Jarvis ever
# answering: steady room noise flips VAD to speaking and held it forever.
FOLLOW_UP_HOLD_CAP_S = 20.0


def _normal_follow_up_s() -> float:
    """Normal-mode listening window (JARVIS_FOLLOW_UP, default 15 s)."""
    try:
        return max(5.0, float(os.environ.get("JARVIS_FOLLOW_UP", "").strip() or NORMAL_FOLLOW_UP_S))
    except ValueError:
        return NORMAL_FOLLOW_UP_S


async def _school_follow_up(
    session,
    *,
    clock=time.monotonic,
    tick: float = 0.25,
    first: float = SCHOOL_FIRST_WINDOW_S,
    window: float | None = None,
) -> None:
    """End the call a listening window after Jarvis's last reply.

    School mode uses FOLLOW_UP_S (normal wake calls pass ``window``). The
    window reopens whenever Sir starts speaking or Jarvis thinks or
    speaks; a pending tool call holds it open. The first window is a bit
    longer (the question may still be arriving through the pre-roll).
    Sir "speaking" holds it at most FOLLOW_UP_HOLD_CAP_S unless Jarvis
    answers: noise must never keep the call (and the wake word) hostage.
    """
    import latency
    import school

    state = {"deadline": clock() + first, "held": None, "agent_busy": False}

    def _window() -> float:
        # A confirmation question waits longer for Sir's answer.
        if school.awaiting_answer():
            return school.CONFIRM_WAIT_S
        return window if window is not None else school.FOLLOW_UP_S

    def _agent(ev) -> None:
        new = str(getattr(ev, "new_state", ""))
        if new in ("thinking", "speaking"):
            state["deadline"] = None
            state["held"] = None
            state["agent_busy"] = True
        elif new == "listening":
            state["agent_busy"] = False
            if state["deadline"] is None:
                state["deadline"] = clock() + _window()

    def _user(ev) -> None:
        new = str(getattr(ev, "new_state", ""))
        if new == "speaking":
            state["deadline"] = None
            if state["held"] is None:
                state["held"] = clock()
        elif new == "listening" and state["deadline"] is None:
            state["deadline"] = clock() + _window()

    session.on("agent_state_changed", _agent)
    session.on("user_state_changed", _user)
    while True:
        await asyncio.sleep(tick)
        now = clock()
        d = state["deadline"]
        held = state["held"]
        expired = d is not None and now >= d
        stuck = (
            d is None
            and held is not None
            and not state["agent_busy"]
            and now - held >= FOLLOW_UP_HOLD_CAP_S
        )
        if (expired or stuck) and latency.TRACKER.pending == 0:
            with contextlib.suppress(Exception):
                await session.aclose()
            return


def _briefing_instructions(raw: str) -> str:
    """Turn the briefing bundle into one spoken reply. Pure."""
    return (
        "Deliver Sir's morning briefing now, in three or four natural "
        "spoken sentences: the weather, each headline in a few words, "
        "school, then todos. Use only these facts and do not call any "
        f"tools: {raw}"
    )


def _join_decision(wake: bool, status: str) -> str:
    """Standby join outcome. Pure, tested.

    Wake summons always greet (a voice summons proves presence better
    than the camera). Otherwise: face -> greet, confirmed-empty -> one
    dry remark, unreadable camera -> silence.
    """
    if wake or status == "present":
        return "greet"
    if status == "absent":
        return "remark"
    return "silent"


#: Heard-as spellings of the name (live STT gave "Jeeves" for Jarvis).
#: difflib alone cannot work: jeeves/jarvis scores 0.5 while the
#: background word "jars" scores 0.8, so known manglings are listed.
NAME_ALIASES = frozenset(
    {"jarvis", "jeeves", "jarves", "jarviss", "jarvise", "jervis", "jarwis"}
)


def _name_called(text: str) -> bool:
    """Was Jarvis addressed, allowing STT mangling? "Jarvis", "Jeeves",
    "hey Jarvis" all count; background words ("service", "jars",
    "harvest") do not. A near-miss name is still a summons — never
    silence, never comment on the mishearing. Pure."""
    lowered = text.casefold()
    if "jarvis" in lowered:
        return True
    words = set(re.findall(r"[a-z']+", lowered))
    if words & NAME_ALIASES:
        return True
    return any(
        len(word) >= 5
        and difflib.get_close_matches(word, ["jarvis"], n=1, cutoff=0.8)
        for word in words
    )


_DISMISS = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:jarvis|jarvus|javis|jervis|jeeves)[\s,.!:;-]*"
    r"(?:you(?:'re| are)\s+)?dismiss(?:ed)?(?:\s+(?:now|please))?[\s.!]*$"
    r"|^dismiss(?:ed)?[\s,.!]+(?:jarvis|jarvus|javis|jervis|jeeves)[\s.!]*$",
    re.IGNORECASE,
)


def _is_dismiss(text: str) -> bool:
    """"Jarvis, dismiss" / "Jarvis, you're dismissed": drop the call now,
    no farewell, back to wake-word standby. The whole utterance must be
    the command, so "Jarvis, dismiss that notification" stays a request.
    Pure."""
    return bool(_DISMISS.match((text or "").strip()))


def _is_summons(text: str) -> bool:
    """Short name-call ("Jarvis?", "hey Jarvis", "Jeeves?") vs a full
    addressed request ("Jarvis, what time is it"). Only short summons get
    the spoken "Yes, Sir?" ack: long utterances already carry their
    request to the model, and acking over them stomps the turn. Pure."""
    return _name_called(text) and len(text.split()) <= 4


#: Desktop fast path (Needle routing for voice turns). Kill switch:
#: JARVIS_DESKTOP_FASTPATH=0 restores pure-model behavior.
def _desktop_fastpath_enabled() -> bool:
    return os.environ.get("JARVIS_DESKTOP_FASTPATH", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


#: Max words for a fast-path command. Anything longer is dictation or a
#: complex request and stays with the model — this also stops mid-sentence
#: regex misfires ("louder" inside a research ramble).
_FASTPATH_MAX_WORDS = 12
# Answers Gemini Live already gives on its own (it hears the audio before
# our transcript arrives): a fast-path reply would only say it twice.
_MODEL_OWNED_FASTPATH = frozenset({"do_math", "projects_ui", "school_mode"})


def _fastpath_short(text: str) -> str | None:
    """Trimmed text when fast-path eligible, else None. Pure."""
    clean = (text or "").strip()
    if not clean or len(clean.split()) > _FASTPATH_MAX_WORDS:
        return None
    return clean


def desktop_fast_prefix(text: str) -> tuple | None:
    """Microsecond regex pre-match for a voice turn. Pure, never executes.

    Returns the (tool, args, reply) cmd tuple or None. The transcript
    hook uses a hit to drop the pending model turn synchronously, before
    the slower resolve+execute task runs.
    """
    try:
        if not _desktop_fastpath_enabled():
            return None
        clean = _fastpath_short(text)
        if clean is None:
            return None
        from bridge import _match_voice_tool

        hit = _match_voice_tool(clean)
        if hit is not None and hit[0] in _MODEL_OWNED_FASTPATH and _pipeline_name() == "realtime":
            return None
        return hit
    except Exception:
        return None


def _switch_school_now(mode: str) -> None:
    """Realtime pipeline: an exact school-mode command switches at once.

    Gemini Live still owns the spoken reply (it heard the audio), but the
    switch itself no longer depends on the model picking the right tool
    call; its own set_school_mode then finds the mode already set.
    Fail-soft.
    """
    try:
        import school as _school_mod
        from bridge import set_school_mode

        if _school_mod.current() != mode:
            set_school_mode(mode)
    except Exception:
        pass


def desktop_fast_command(text: str) -> dict | None:
    """Full instant resolve+execute for a voice turn (regex → resolver →
    Needle, same as phone /route). Import-safe and fail-open.

    Returns the /route payload (reply + ok action) when the text resolved
    to an executed command, else None (the model handles it). Desktop is
    never guest. May block on subprocess tools — callers run it off the
    event loop.
    """
    try:
        if not _desktop_fastpath_enabled():
            return None
        clean = _fastpath_short(text)
        if clean is None:
            return None
        from bridge import handle_route

        if desktop_fast_prefix(text) is None and _pipeline_name() == "realtime":
            from bridge import _match_voice_tool

            hit = _match_voice_tool(clean)
            if hit is not None and hit[0] in _MODEL_OWNED_FASTPATH:
                if hit[0] == "school_mode":
                    _switch_school_now(str(hit[1].get("mode", "")))
                return None
        code, payload = handle_route({"text": clean})
        if (
            code == 200
            and isinstance(payload, dict)
            and payload.get("reply")
            and (payload.get("action") or {}).get("ok") is True
        ):
            return payload
        return None
    except Exception:
        return None


async def _reply_or_say(
    session: AgentSession, *, instructions: str, fallback: str
) -> None:
    """Server-triggered speech that survives realtime-model quirks.

    generate_reply() is unreliable on some Gemini Live models (the
    model may refuse commentary turns). Fall back to say() with a
    fixed line so the session is never left silent.
    """
    try:
        await session.generate_reply(instructions=instructions)
    except Exception:
        with __import__("contextlib").suppress(Exception):
            await session.say(fallback)


def _pipeline_name() -> str:
    """'realtime' (Gemini Live on the owner's free AI Studio key, $0
    inference), 'local' (cheap STT + Gemini brains + local voice, needs
    LiveKit Cloud Inference), or 'direct' (fully cloud-free for a local
    livekit-server: Silero VAD + local whisper ears, Gemini-direct brains,
    Piper mouth — needs only GOOGLE_API_KEY).

    Realtime is the default: the Live API quota is unlimited, while
    the text-out free tier (20 req/day) cannot feed a voice pipeline.
    JARVIS_PIPELINE=local restores the old Cloud stack;
    JARVIS_PIPELINE=direct is the offline-first stack.
    """
    return os.environ.get("JARVIS_PIPELINE", "realtime").strip().lower()


def _session_tts():
    """Local Piper voice by default; JARVIS_TTS=<inference model> for a
    free cloud voice (e.g. rime/coda). Escape hatch for debugging."""
    override = os.environ.get("JARVIS_TTS", "local").strip().lower()
    if override and override != "local":
        return inference.TTS(model=override)
    return PiperTTS()


def endpointing_delay() -> float:
    """Extra wait (s) after Silero's end of speech before the turn is
    committed (JARVIS_ENDPOINT_DELAY, default 0.15). Pure (env only)."""
    try:
        return max(
            0.0, float(os.environ.get("JARVIS_ENDPOINT_DELAY", "").strip() or 0.15)
        )
    except ValueError:
        return 0.15


def vad_kwargs() -> dict:
    """Silero endpointing knobs, env-overridable. Pure (env only).

    min_silence_duration is the big one: seconds of quiet before a turn
    ends (default 0.55). Lower answers faster but clips pauses; higher
    waits out thinking pauses. activation_threshold is mic sensitivity.
    """

    def _num(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, "").strip() or default)
        except (ValueError, AttributeError):
            return default

    kw = {
        "min_speech_duration": _num("JARVIS_VAD_MIN_SPEECH", 0.05),
        "min_silence_duration": _num("JARVIS_VAD_MIN_SILENCE", 0.55),
        "activation_threshold": _num("JARVIS_VAD_THRESHOLD", 0.5),
    }
    import school

    if school.is_school():
        # Classroom: ignore coughs, chairs and short noise (never looser
        # than an explicit env override).
        for key, value in school.VAD.items():
            kw[key] = max(kw[key], value)
    return kw


def _session_for_pipeline(
    turn_handling: TurnHandlingOptions, vad: object = None
) -> AgentSession:
    """Voice session for the active pipeline.

    Both branches carry the same IDLE_HANGUP_SECONDS away budget. The
    framework default (15s) hung up mid-task in the 21:17 incident: it
    fired 15s after the user's last utterance while a handoff was still
    executing, and the call died as USER_INITIATED.
    """
    if _pipeline_name() == "realtime":
        # Gemini realtime handles the voice input and output for this session.
        # Local Silero VAD feeds turn_detection="vad" (server VAD is deaf
        # for this key — manual activity framing, see _realtime_llm).
        return AgentSession(
            turn_handling=turn_handling,
            user_away_timeout=IDLE_HANGUP_SECONDS,
            vad=(
                vad
                if vad is not None
                else (silero.VAD.load(**vad_kwargs()) if silero is not None else None)
            ),
        )
    if _pipeline_name() == "direct":
        # Cloud-free stack for a local livekit-server: the default
        # inference.VAD/STT/LLM/TurnDetector have no backend here, so every
        # slot gets a direct/local equivalent. Ears are lazy (whisper loads
        # on first utterance); brains need GOOGLE_API_KEY, fail fast if set.
        if silero is None:
            raise RuntimeError(
                "JARVIS_PIPELINE=direct needs livekit-plugins-silero "
                "(uv sync to install the pinned extra)."
            )
        if not os.environ.get("GOOGLE_API_KEY"):
            raise RuntimeError(
                "JARVIS_PIPELINE=direct needs GOOGLE_API_KEY in .env.local."
            )
        return AgentSession(
            vad=vad if vad is not None else silero.VAD.load(**vad_kwargs()),
            stt=FasterWhisperSTT(),
            # 60s HTTP deadline: the plugin default (5s) is rejected by
            # current Gemini models (400 "minimum deadline 10s").
            llm=google.LLM(
                model=DIRECT_LLM_MODEL,
                http_options=genai_types.HttpOptions(timeout=60_000),
            ),
            tts=_session_tts(),
            # No explicit turn_detection: auto mode picks "vad" (a VAD is
            # present, no realtime model), so endpointing stays local too.
            # Same adaptive interruptions + preemptive replies as realtime.
            turn_handling=TurnHandlingOptions(
                interruption={"mode": "adaptive"},
                preemptive_generation={"enabled": True},
            ),
            user_away_timeout=IDLE_HANGUP_SECONDS,
        )
    return AgentSession(
        # Ears: budget streaming STT. Brains: still Gemini (with 3.6-flash
        # backup when 3.8-flash is saturated). Mouth: the downloaded local
        # voice (zero inference burn).
        # See all available models at https://docs.livekit.io/agents/models/stt/
        stt=inference.STT(model=CHEAP_STT_MODEL, language="en-GB"),
        llm=_fallback_text_llm(lambda m: inference.LLM(model=f"google/{m}")),
        tts=_session_tts(),
        turn_handling=turn_handling,
        # Silence budget: flag the user away so the hangup task below fires.
        user_away_timeout=IDLE_HANGUP_SECONDS,
    )


_shared_local_llm = None


# Text-brain fallback chains (high-usage backups): separate quotas per
# model ID, so a saturated primary still leaves options. FallbackAdapter
# fails a turn over on 429/5xx and comes back to the primary next turn.
# (gemini-2.5-flash was retired for new API keys — generate_content 404s —
# so the backup is 3.6-flash, verified live against this key.)
TEXT_FALLBACK_CHAIN = [DIRECT_LLM_MODEL, "gemini-3.6-flash"]


def _fallback_text_llm(make_llm) -> FallbackAdapter:
    """Wrap primary + backups in a FallbackAdapter. Pure-ish (constructs)."""
    return FallbackAdapter([make_llm(m) for m in TEXT_FALLBACK_CHAIN])


def _default_agent_llm():
    """Realtime model in realtime mode; shared Gemini Flash otherwise.

    Sub-agents need an explicit LLM: llm=None does NOT inherit the
    session default (the activity raises "trying to generate reply
    without an LLM model"). One shared instance mirrors how the
    realtime model is shared across agents. The direct pipeline gets
    Gemini-direct (GOOGLE_API_KEY) so specialists stay cloud-free too.
    """
    global _shared_local_llm
    if _pipeline_name() == "realtime":
        return _realtime_llm()
    if _pipeline_name() == "direct":
        if not os.environ.get("GOOGLE_API_KEY"):
            raise RuntimeError(
                "JARVIS_PIPELINE=direct needs GOOGLE_API_KEY in .env.local."
            )
        if _shared_local_llm is None:
            _shared_local_llm = _fallback_text_llm(
                # 60s HTTP deadline: the plugin default (5s) is rejected
                # by current Gemini models (400 "minimum deadline 10s").
                # HttpOptions object (not a dict): the plugin calls
                # .model_copy() on it.
                lambda m: google.LLM(
                    model=m,
                    http_options=genai_types.HttpOptions(timeout=60_000),
                ),
            )
        return _shared_local_llm
    if _shared_local_llm is None:
        _shared_local_llm = _fallback_text_llm(
            lambda m: inference.LLM(model=f"google/{m}"),
        )
    return _shared_local_llm


#: Seconds before a running tool earns a "still on it" progress caption.
SLOW_TOOL_S = 5.0


def _wrap_tools_with_timing(tools: list) -> list:
    """Time every tool call and feed src/latency.py. Idempotent.

    Wraps each FunctionTool's _func in place (schema/description/id are
    untouched) so all ~100 tools across every agent report durations with
    zero per-tool code. Never lets telemetry break a call: the wrapper
    re-raises the tool's own result/exception and swallows only its own
    bookkeeping failures. A second wrap is a no-op (marker flag).
    """
    import inspect as _inspect

    try:
        from latency import TRACKER
    except ImportError:  # pragma: no cover - latency module always ships
        return tools
    try:
        from system import log_action
    except ImportError:  # pragma: no cover
        log_action = None  # type: ignore[assignment]
    for tool in tools or []:
        if getattr(tool, "_jarvis_timed", False):
            continue
        orig = tool._func
        tid = str(getattr(tool, "id", "?"))

        async def _timed(
            *args: object, _orig: object = orig, _tid: str = tid, **kwargs: object
        ):  # type: ignore[no-untyped-def]
            start = time.monotonic()
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            async def _nudge() -> None:
                # P3: tools past SLOW_TOOL_S get a progress caption so a
                # long page load reads as working, not dead air.
                await asyncio.sleep(SLOW_TOOL_S)
                try:
                    from hud_events import caption as _cap

                    _cap("jarvis", f"(still on it: {_tid}…)")
                except Exception:
                    pass
                try:
                    if log_action is not None:
                        log_action("latency-slow", f"{_tid} >{SLOW_TOOL_S:.0f}s")
                except Exception:
                    pass

            slow_task = loop.create_task(_nudge()) if loop is not None else None
            TRACKER.call_started()
            try:
                res = _orig(*args, **kwargs)  # type: ignore[operator]
                if _inspect.isawaitable(res):
                    return await res
                return res
            finally:
                if slow_task is not None:
                    slow_task.cancel()
                try:
                    ms = (time.monotonic() - start) * 1000.0
                    TRACKER.call_finished(_tid, ms)
                    if log_action is not None:
                        log_action("latency", f"{_tid} {ms:.0f}ms")
                except Exception:
                    pass

        tool._func = _timed  # type: ignore[attr-defined]
        tool._jarvis_timed = True  # type: ignore[attr-defined]
    return tools


async def _specialist_watchdog(session: object, entered_at: float) -> None:
    """One-shot stuck check 30s after a handoff landing.

    If no tool is running AND nothing finished recently, the specialist
    is in the known silent stalemate (handoff done, zero tools, zero
    words): nudge it to report or transfer back, else say a fallback
    line. Anything but infinite quiet. Never raises.
    """
    await asyncio.sleep(30.0)
    try:
        from latency import TRACKER

        with contextlib.suppress(Exception):
            from system import log_action as _log_watch

            _log_watch(
                "handoff",
                f"watchdog pending={TRACKER.pending} last_end={TRACKER.last_tool_end}",
            )
        recent = (
            TRACKER.last_tool_end is not None
            and (time.monotonic() - TRACKER.last_tool_end) < 25.0
        )
        if TRACKER.pending > 0 or recent:
            return  # working or just finished; stay out of the way
        try:
            await session.generate_reply(  # type: ignore[union-attr]
                instructions=(
                    "You went quiet after a handoff. If the task is done "
                    "or stuck, report briefly and call "
                    "transfer_back_to_main. If still working, say one "
                    "progress line."
                )
            )
        except Exception:
            with contextlib.suppress(Exception):
                await session.say(  # type: ignore[union-attr]
                    "Still on it, Sir — one moment."
                )
    except Exception:
        pass


def _end_call_tool() -> EndCallTool:
    """Hang-up tool shared by the router and both specialists.

    Specialists need their own: after a handoff the router's end_call is
    out of scope, and without one the agent must refuse a direct "hang up".
    """
    return EndCallTool(
        extra_description=(
            "Only end the call after the user clearly says they are finished, "
            "says goodbye, or directly asks to end the call. \"Jarvis, dismiss\" "
            "(or \"you're dismissed\") means end it immediately with no farewell."
        ),
        end_instructions=(
            "Give Jarvis's brief, polite British-English farewell, then end the call."
        ),
    )


class ResearchAgent(Agent):
    """Isolated deep-research specialist (separate browser, no shared login).

    Used only when the router decides a task needs a whole separate browser:
    multi-page comparison, citations, long reads. Owns its own BrowserManager
    lifecycle; the main tabs are untouched.
    """

    def __init__(self, llm=None, parent: Agent | None = None) -> None:
        self._parent = parent
        self.task = ""
        self.research_browser = BrowserManager.create_research_manager()
        self.research_tools = BrowserTools(self.research_browser)
        self._end_call_tool = _end_call_tool()
        super().__init__(
            llm=llm or _default_agent_llm(),
            instructions=RESEARCH_INSTRUCTIONS,
            tools=[*self.research_tools.tools, *self._end_call_tool.tools],
        )
        _wrap_tools_with_timing(self.tools)

    # generate_reply is unreliable on some Gemini Live models (it may
    # refuse commentary turns): never let it raise out of on_enter, or
    # the handoff lands dead with no turn at all. Tool-first: speech
    # only as progress, like SystemAgent.on_enter.
    async def on_enter(self) -> None:
        # generate_reply is unreliable on some Gemini Live models (it may
        # refuse commentary turns): say a deterministic fallback instead
        # of landing silent, then arm the stuck watchdog.
        entered = time.monotonic()
        try:
            await self.session.generate_reply(
                instructions=(
                    f"Your research topic, verbatim from Sir: {self.task!r}. Begin the "
                    "task instantly by calling the first tool. Chain tools "
                    "silently until done, then report results and "
                    "transfer_back_to_main. Anything you speak must be progress "
                    "or results — never a bare acknowledgement."
                )
            )
        except Exception:
            with __import__("contextlib").suppress(Exception):
                await self.session.say("On it, Sir — researching now.")
        with __import__("contextlib").suppress(Exception):
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.get_running_loop().create_task(
                    _specialist_watchdog(self.session, entered)
                )
            )

    @function_tool()
    async def transfer_back_to_main(self, context: RunContext):
        """Return control to the main Jarvis router from this specialist."""
        return (self._parent if self._parent is not None else self), (
            "Returning to Jarvis."
        )

    async def aclose(self) -> None:
        with __import__("contextlib").suppress(Exception):
            await self.research_browser.close()


RARE_SYSTEM_TOOL_IDS: frozenset[str] = frozenset(
    {
        "power_control",
        "confirm_power_action",
        "run_command",
        "confirm_command_action",
        "open_app",
        "window_action",
        "media_control",
        "set_brightness",
        "keyboard_light",
        "monitor_setup",
        "open_on_monitor",
        "home_control",
        "play_game",
        # Pentest active tier: scoped + confirm-gated, SystemAgent only.
        "confirm_pentest_action",
        "nmap_scan",
        "nikto_sweep",
        "gobuster_dir",
        "hashcat_crack",
        "msf_aux",
        "msf_exploit",
        "scope_allow",
        "scope_revoke",
        "scope_list",
        "pentest_report",
        "freeze_testing",
        # Desktop-control tier: sees the screen and injects input.
        "confirm_desktop_action",
        "desktop_screenshot",
        "desktop_locate_text",
        "desktop_click",
        "desktop_type",
        "desktop_key",
        "desktop_scroll",
    }
)
"""Dangerous/rare laptop tools kept off the router.

These live only on the filtered SystemAgent behind the narrow
transfer_to_system_control handoff, so the main agent's per-turn
context stays lean and small models cannot misfire them. Everything
else on the laptop is a direct tool on the Assistant."""

HANDOFF_WHATSAPP_IDS: frozenset[str] = frozenset(
    {
        "whatsapp_status",
        "whatsapp_chats",
        "whatsapp_read",
        "whatsapp_draft",
        "do_math",
    }
)
"""WhatsApp tools mirrored onto the handoff specialist.

They stay direct on the router too (common laptop tools), but a
combined "open whatsie and send a message" task hands the whole job
to system control — without these the specialist could open the app
and then do nothing (stall seen Sep-2026: handoff done, zero tools,
zero words). transfer_to_system_control unions these with the rare
ids so the chain open_app -> whatsapp_draft -> transfer_back_to_main
completes behind one handoff."""


class SystemAgent(Agent):
    """Local Linux PC-control specialist (safe core + devices + daily).

    Only runs usefully when JARVIS_LOCAL=1; every tool re-checks this so a
    cloud deployment refuses instead of acting on the wrong machine.
    Shutdown/reboot require confirm_power_action first; everything else runs
    immediately and is logged to ~/.jarvis/actions.log.
    """

    def __init__(
        self,
        llm=None,
        only_ids: set[str] | frozenset[str] | None = None,
        parent: Agent | None = None,
    ) -> None:
        self._parent = parent
        self.task = ""
        self._end_call_tool = _end_call_tool()
        self.system_tools = SystemTools()
        self.device_tools = DeviceTools()
        self.daily_tools = DailyTools()
        self.background_tools = BackgroundTools()
        self.inbox_tools = InboxTools()
        self.reddit_tools = RedditTools()
        self.osint_tools = OsintTools()
        self.pentest_tools = PentestTools()
        self.desktop_tools = DesktopTools()
        groups = (
            self.system_tools.tools,
            self.device_tools.tools,
            self.daily_tools.tools,
            self.background_tools.tools,
            self.inbox_tools.tools,
            self.reddit_tools.tools,
            self.osint_tools.tools,
            self.pentest_tools.tools,
            self.desktop_tools.tools,
        )
        if only_ids is None:
            picked = [tool for group in groups for tool in group]
        else:
            picked = [tool for group in groups for tool in group if tool.id in only_ids]
        super().__init__(
            llm=llm or _default_agent_llm(),
            instructions=SYSTEM_INSTRUCTIONS,
            # end_call always rides along: after a handoff this agent owns
            # the call, so a direct "hang up" must work here too.
            tools=[*picked, *self._end_call_tool.tools],
        )
        _wrap_tools_with_timing(self.tools)

    # NOTE: on_enter MUST fire a reply: without it the switch lands in
    # silence (stalemate seen 22:46 — handoff done, specialist live, zero
    # tools, zero words). But a bare "ready" settles the turn the other way
    # (stalemate seen 22:06 — 55s of mutual waiting). So the reply carries
    # action-forcing instructions: tools first, speech only as progress.
    # generate_reply is unreliable on some Gemini Live models (it may
    # refuse commentary turns): never let it raise out of on_enter, or
    # the handoff lands dead with no turn at all.
    async def on_enter(self) -> None:
        # Same deal as ResearchAgent: never land silent, arm the watchdog.
        entered = time.monotonic()
        try:
            await self.session.generate_reply(
                instructions=(
                    f"Your task, verbatim from Sir: {self.task!r}. Act on exactly "
                    "the app or site named there, never a substitute from your "
                    "examples. Do not greet or announce readiness: begin the task instantly by calling the first tool. "
                    "Chain tools silently until done, then report results and "
                    "transfer_back_to_main. Anything you speak must be progress or "
                    "results — never a bare acknowledgement."
                )
            )
        except Exception:
            with __import__("contextlib").suppress(Exception):
                await self.session.say("On it, Sir.")
        with __import__("contextlib").suppress(Exception):
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.get_running_loop().create_task(
                    _specialist_watchdog(self.session, entered)
                )
            )

    @function_tool()
    async def transfer_back_to_main(self, context: RunContext):
        """Return control to the main Jarvis router from this specialist."""
        return (self._parent if self._parent is not None else self), (
            "Returning to Jarvis."
        )


def _with_memory(instructions: str) -> str:
    """Append Sir's remembered preferences + a 'last time' line (§2).

    Local strings only (no model call); capped in memory.py. Never raises.
    """
    try:
        import memory

        block = memory.persona_block()
    except Exception:
        block = ""
    return f"{instructions}\n\n# Memory\n{block}" if block else instructions


class Assistant(Agent):
    def __init__(
        self,
        browser: BrowserManager | None = None,
        llm=None,
        system_tools: SystemTools | None = None,
        device_tools: DeviceTools | None = None,
        daily_tools: DailyTools | None = None,
        background_tools: BackgroundTools | None = None,
        inbox_tools: InboxTools | None = None,
        reddit_tools: RedditTools | None = None,
    ) -> None:
        self.browser = browser or BrowserManager(headless=True)
        self.browser_tools = BrowserTools(self.browser)
        self.system_tools = system_tools or SystemTools()
        self.device_tools = device_tools or DeviceTools()
        self.daily_tools = daily_tools or DailyTools()
        self.background_tools = background_tools or BackgroundTools()
        self.inbox_tools = inbox_tools or InboxTools()
        self.reddit_tools = reddit_tools or RedditTools()
        self.draft_tools = DraftTools(self.inbox_tools)
        self.notify_tools = NotifyTools()
        self.osint_tools = OsintTools()
        self.project_tools = ProjectTools()
        self.quote_tools = QuoteTools(
            system=self.system_tools, inbox=self.inbox_tools, browser=self.browser_tools
        )
        self.school_tools = SchoolTools()
        self.files_tools = FilesTools()
        self.alternatives_tools = AlternativesTools()
        self.focus_tools = FocusTools()
        self.vision_tools = VisionTools()
        self.workspace_tools = WorkspaceTools()
        self.recall_tools = RecallTools()
        self.exam_tools = ExamTools()
        self.memory_tools = MemoryTools()
        self.window_tools = WindowTools()
        self._research_agent: ResearchAgent | None = None
        self._system_agent: SystemAgent | None = None
        self._end_call_tool = _end_call_tool()
        super().__init__(
            # A Large Language Model (LLM) is your agent's brain. In realtime
            # mode this is the bundled Gemini voice model; in local-pipeline
            # mode None inherits the session default (Gemini Flash text).
            # See all available models at https://docs.livekit.io/agents/models/llm/
            llm=llm or _default_agent_llm(),
            # To use a realtime model instead of a voice pipeline, replace the LLM
            # with a RealtimeModel and remove the STT/TTS from the AgentSession
            # (Note: This is for the OpenAI Realtime API. For other providers, see https://docs.livekit.io/agents/models/realtime/)
            # 1. Install livekit-agents[openai]
            # 2. Set OPENAI_API_KEY in .env.local
            # 3. Add `from livekit.plugins import openai` to the top of this file
            # 4. Replace the llm argument with:
            #     llm=openai.realtime.RealtimeModel(voice="marin")
            instructions=_with_memory(AGENT_INSTRUCTIONS),
            tools=[
                *self.browser_tools.tools,
                *self._end_call_tool.tools,
                # Common laptop tools live here directly: small models
                # cannot be trusted to hand off, and mail/Reddit must
                # never go near the browser. The rare/dangerous ids in
                # RARE_SYSTEM_TOOL_IDS are excluded and served behind
                # the narrow transfer_to_system_control handoff instead.
                # Passive OSINT rides directly (public data, no confirm);
                # active pentest stays behind the handoff (rare ids).
                *self.osint_tools.tools,
                # Background research (Claude) / coding (opencode) projects
                # + the voice-only Project Archive window.
                *self.project_tools.tools,
                # Movie / meme lines -> fixed safe actions (src/quotes.py).
                *self.quote_tools.tools,
                # School mode switch + loud-action confirmation.
                *self.school_tools.tools,
                # Find / open / describe / tidy Sir's files (second brain).
                *self.files_tools.tools,
                # "Find me a cheaper alternative to X" -> research project.
                *self.alternatives_tools.tools,
                # Focus mode: lock on to a window/tab, drift alerts, review.
                *self.focus_tools.tools,
                # Eyes: appearance feedback, posture/phone watch, gestures.
                *self.vision_tools.tools,
                # Google Docs/Sheets/Drive, Notion, invoice generation.
                *self.workspace_tools.tools,
                # Email reply drafts (send stays behind confirm_email_action).
                *self.draft_tools.tools,
                # "What did you say to X", "what emails did you reply to",
                # "catch me up": read-only recall of WhatsApp/mail logs.
                *self.recall_tools.tools,
                # Exam schedule: next / find / add, fed by schedule imports.
                *self.exam_tools.tools,
                # Long-term memory: recall, last time, remember/forget.
                *self.memory_tools.tools,
                # Windows: list / focus / arrange / undo (reversible, logged).
                *self.window_tools.tools,
                # One voice tool: notify Sir (spoken if present, toast otherwise).
                *self.notify_tools.tools,
                *[
                    tool
                    for group in (
                        self.system_tools.tools,
                        self.device_tools.tools,
                        self.daily_tools.tools,
                        self.background_tools.tools,
                        self.inbox_tools.tools,
                        self.reddit_tools.tools,
                    )
                    for tool in group
                    if tool.id not in RARE_SYSTEM_TOOL_IDS
                ],
            ],
        )
        # NOTE: transfer_* handoff methods are @function_tool methods on this
        # class, so Agent.__init__ auto-discovers them via find_function_tools.
        # Do NOT also pass them explicitly in tools=[...] — that registers
        # each twice and LiveKit raises "duplicate function name".
        _wrap_tools_with_timing(self.tools)

    @function_tool()
    async def latency_report(self, context: RunContext) -> dict[str, str]:
        """Explain what made the last turn slow, with exact timings.

        Call when the user asks why something was slow ("why did that take
        so long?", "what's holding you up?"). Reads the measured per-tool
        breakdown of the last turn — never guesses.
        """
        from latency import TRACKER, format_breakdown, format_spoken

        summary = TRACKER.last
        text = format_spoken(summary)
        return {"say": text, "text": f"{text} Breakdown: {format_breakdown(summary)}"}

    @function_tool()
    async def transfer_to_deep_research(self, context: RunContext, topic: str):
        """Hand off to the isolated deep-research browser.

        Use when the task needs a whole separate browser: comparing several
        pages, gathering citations, or long multi-page reads. The main tabs
        stay untouched. Announce the handoff briefly.

        Args:
            topic: What to research (passed to the research agent).
        """
        if self._research_agent is None:
            llm = getattr(self, "llm", None)
            self._research_agent = ResearchAgent(
                llm=llm if llm is not None else None, parent=self
            )
        self._research_agent.task = topic
        return (
            self._research_agent,
            f"Handing off to deep research on {topic}.",
        )

    @function_tool()
    async def transfer_to_system_control(self, context: RunContext, task: str):
        """Hand off rare laptop actions: shutdown/reboot, opening desktop
        programs, moving windows, media keys, brightness, keyboard light,
        monitors, smart home, games, and full desktop control (seeing the
        screen, clicking, typing into desktop apps).

        Use ONLY for those; everything else on the laptop is a direct
        tool on this agent. Refuses automatically when not running
        locally.

        Args:
            task: What to do on the laptop (passed to the system agent).
        """
        if self._system_agent is None:
            llm = getattr(self, "llm", None)
            self._system_agent = SystemAgent(
                llm=llm if llm is not None else None,
                only_ids=RARE_SYSTEM_TOOL_IDS | HANDOFF_WHATSAPP_IDS,
                parent=self,
            )
        # LiveKit commits this tool's output to OUR chat_ctx, not the
        # specialist's: without this the specialist never saw the task and
        # opened its worked example (Calculator -> KCalc) for every launch.
        self._system_agent.task = task
        return self._system_agent, f"Handing off to system control: {task}."

    @function_tool()
    async def transfer_back_to_main(self, context: RunContext):
        """Return control to the main Jarvis router from a specialist."""
        return self, "Returning to Jarvis."


def _idle_procs() -> int:
    """Warm standby job processes. Pure (env only).

    LiveKit defaults to one per CPU (12 here x ~366MB each = 4.4GB
    resident just to stand by), which keeps this 15GB laptop swap-bound;
    swap storms stall the audio loop for seconds (measured in
    agent.log). One user needs one active call plus a spare: 2.
    Override with JARVIS_IDLE_PROCS.
    """
    try:
        return max(1, int(os.environ.get("JARVIS_IDLE_PROCS", "").strip() or 2))
    except (ValueError, AttributeError):
        return 2


# Single-user laptop: never refuse a call because the machine is busy.
# The stock 0.7 cutoff tracks system-wide CPU, so builds/tests flipped the
# worker "unavailable" and a wake joined a room no agent ever entered.
server = AgentServer(num_idle_processes=_idle_procs(), load_threshold=math.inf)


@server.rtc_session(agent_name=os.environ.get("AGENT_NAME", "my-agent"))
async def my_agent(ctx: JobContext):
    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }
    # Start the isolated Needle worker now so its model is loaded by the
    # first turn (a native crash there only kills the child, never this job).
    try:
        from intent.needle_router import warm_up as _needle_warm_up

        _needle_warm_up()
    except Exception:
        pass
    # Warm the local launch-router model off the event loop so the first
    # ambiguous "open <thing>" costs ~0.5s, not a ~6s cold load.
    if os.environ.get("JARVIS_LOCAL") == "1":
        from system.launcher import warm_router

        asyncio.get_running_loop().run_in_executor(None, warm_router)

    # Browser must never block the agent joining the room: a visible
    # Chromium (headless=False) fails on headless servers and steals
    # focus locally. Default headless unless explicitly overridden,
    # and degrade to no-browser rather than killing the voice session.
    _headless = os.environ.get("JARVIS_BROWSER_HEADLESS", "1").strip() not in (
        "0",
        "false",
        "no",
    )
    try:
        browser = BrowserManager(headless=_headless)
    except Exception:
        browser = BrowserManager(headless=True)
    ctx.add_shutdown_callback(browser.close)

    # Voice pipeline: Gemini realtime by default (Live API on the
    # owner's AI Studio key = $0 LiveKit inference). JARVIS_PIPELINE=local
    # restores the cheap local stack (budget STT + Gemini brains +
    # downloaded voice = ~$0.003/min).
    turn_handling = TurnHandlingOptions(
        # Local VAD endpointing ("vad" + Silero): server-side turn
        # detection is off (its VAD is deaf for this key — see
        # _realtime_llm), so the framework frames turns itself from the
        # local VAD and the realtime plugin forwards activity markers.
        # (The old inference.TurnDetector is Cloud-metered and was ignored
        # by the realtime model anyway.)
        turn_detection="vad" if silero is not None else None,
        # "vad" (local Silero), never "adaptive": the adaptive detector
        # dials wss://agent-gateway.livekit.cloud (401 with no Cloud
        # credentials) and retries forever. Same Cloud dependency class
        # as the old TurnDetector and QUAIL enhancement.
        interruption={"mode": "vad"},
        # Silero already waits min_silence_duration (0.55 s) before it
        # reports end of speech; the framework's default 0.5 s endpointing
        # delay stacked on top of that, ~1 s of dead air before Gemini even
        # heard the turn had ended. Silero's silence window is the guard
        # against mid-sentence pauses, so the extra delay stays short.
        endpointing={"min_delay": endpointing_delay()},
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation={"enabled": True},
    )
    # P1 pre-warm: Silero's onnx session build blocks ~200ms on the audio
    # loop if built inline (measured in agent.log). Build it in a thread
    # while nothing else needs the loop yet; _session_for_pipeline reuses it.
    _vad = None
    if silero is not None and _pipeline_name() in ("realtime", "direct"):
        with contextlib.suppress(Exception):
            _vad = await asyncio.to_thread(silero.VAD.load, **vad_kwargs())
    session = _session_for_pipeline(turn_handling, vad=_vad)
    # NOTE: local-pipeline expressive mode stays off (see _session_for_pipeline):
    # expressive=True needs a markup-capable TTS such as Fish Audio.
    # _session_tts() returns local Piper by default.

    _background_tasks: set[asyncio.Task] = set()

    # Last user/agent activity: the away timer only watches the user, so a
    # slow tool chain (handoff, research, desktop loop) looks like
    # abandonment. The hangup below refuses to fire while anyone is working.
    _activity_clock = {"last": time.monotonic()}
    _hangup_watching = {"active": False}
    # Early: the hangup watcher below reads this; the gate fills it in.
    _presence = {
        "engaged": False,
        "absence_remarked": False,
        "empty_streak": 0,
        "stopped": False,
        "checkin_idx": 0,
    }

    def _mark_active(*_args, **_kwargs) -> None:
        _activity_clock["last"] = time.monotonic()

    session.on("user_input_transcribed", _mark_active)
    session.on("conversation_item_added", _mark_active)
    session.on("function_tools_executed", _mark_active)

    # Real-turn clock for the idle watchdog: only 2+ word final
    # transcripts and tool runs count, never noise or Jarvis's own replies.
    _turn_clock = {"last": time.monotonic()}

    def _mark_turn(event) -> None:
        if _is_real_turn(
            getattr(event, "transcript", ""), bool(getattr(event, "is_final", False))
        ):
            _turn_clock["last"] = time.monotonic()
            # Proactive engine: Sir answered, so recent announcements
            # were not ignored (IRONMAN_SPEC §1.2 learned penalty).
            with contextlib.suppress(Exception):
                from proactive import engine as _proactive_engine

                _proactive_engine.note_user_turn()

    def _mark_tools(*_args, **_kwargs) -> None:
        _turn_clock["last"] = time.monotonic()

    session.on("user_input_transcribed", _mark_turn)
    session.on("function_tools_executed", _mark_tools)

    # HUD captions: the Tauri overlay has no WebRTC (system WebKitGTK
    # exposes no RTCPeerConnection), so it can never join a room — it
    # reads ~/.jarvis/captions.log via bridge /captions instead.
    # Best-effort, never breaks voice.
    try:
        from hud_events import caption as _hud_caption

        _partial = {"text": "", "at": 0.0}

        _dismissed = {"done": False}

        def _on_user_transcript(event) -> None:
            try:
                text = str(getattr(event, "transcript", "") or "").strip()
                if not text:
                    return
                # "Jarvis, dismiss": stop talking, stop listening, hang up
                # to wake-word standby at once. Checked before everything
                # else (fast path, model, summons ack) and on final
                # transcripts only, so a longer sentence still arriving
                # never gets cut off.
                if (
                    getattr(event, "is_final", False)
                    and not _dismissed["done"]
                    and _is_dismiss(text)
                ):
                    _dismissed["done"] = True
                    with __import__("contextlib").suppress(Exception):
                        session.clear_user_turn()
                    with __import__("contextlib").suppress(Exception):
                        session.interrupt(force=True)
                    with __import__("contextlib").suppress(Exception):
                        _hud_caption("sir", text)
                    with __import__("contextlib").suppress(Exception):
                        from system import log_action as _log_dismiss

                        _log_dismiss("dismiss", "call ended by voice")

                    async def _dismiss() -> None:
                        with __import__("contextlib").suppress(Exception):
                            await session.aclose()

                    try:
                        _dloop = asyncio.get_running_loop()
                    except RuntimeError:
                        _dloop = asyncio.get_event_loop()
                    _dloop.call_soon(lambda lp=_dloop: lp.create_task(_dismiss()))
                    return
                if not getattr(event, "is_final", False):
                    # Live subtitles: interim lines as the words land,
                    # throttled so the tail reads as typing, not lag.
                    try:
                        from hud_events import partial_changed as _partial_due

                        now = time.monotonic()
                        if _partial_due(_partial["text"], _partial["at"], text, now):
                            _partial["text"] = " ".join(text.split())
                            _partial["at"] = now
                            _hud_caption("sir", f"{_partial['text']}…")
                    except Exception:
                        pass
                    return
                _partial["text"] = ""
                _partial["at"] = 0.0
                _hud_caption("sir", text)
                with __import__("contextlib").suppress(Exception):
                    import latency as _lat_begin

                    _lat_begin.TRACKER.turn_begin()
                    # Desktop fast path (Needle routing): act-tier simple
                    # commands ("lock the computer", "turn it up") execute
                    # in ms and the pending model turn is dropped so it
                    # never double-responds. Two stages: a microsecond
                    # regex pre-match clears the turn synchronously, then
                    # a thread runs the full resolve (resolver + Needle)
                    # for paraphrases. Fail-open throughout: any doubt
                    # falls through to the model untouched.
                    try:
                        _prefix_hit = desktop_fast_prefix(text)
                    except Exception:
                        _prefix_hit = None
                    if _prefix_hit is not None:
                        with __import__("contextlib").suppress(Exception):
                            session.clear_user_turn()
                    _lat_begin.TRACKER.mark("fastpath")

                    async def _run_fast() -> None:
                        try:
                            _payload = await asyncio.to_thread(
                                desktop_fast_command, text
                            )
                        except Exception:
                            return
                        if not isinstance(_payload, dict):
                            return
                        with __import__("contextlib").suppress(Exception):
                            session.clear_user_turn()
                        _mark_active()
                        _reply = str(_payload.get("reply") or "Done, Sir.")
                        try:
                            await session.say(_reply)
                        except Exception:
                            return
                        with __import__("contextlib").suppress(Exception):
                            _hud_caption("jarvis", _reply)
                        with __import__("contextlib").suppress(Exception):
                            import latency as _lat_fast

                            _lat_fast.TRACKER.turn_end()

                    try:
                        _floop = asyncio.get_running_loop()
                    except RuntimeError:
                        _floop = asyncio.get_event_loop()
                    _floop.call_soon(
                        lambda lp=_floop: lp.create_task(_run_fast())
                    )
                    # Wake word: the name spoken engages standby at once —
                    # no camera needed when Sir is clearly talking to us.
                    # Ack only bare summons; a full addressed request goes
                    # straight to the model untouched (acking over it
                    # causes the "yes sir then silence" stall: the ack
                    # stomps the turn and standby rules hush the rest).
                    try:
                        if _name_called(text) and not _presence["engaged"]:
                            _presence["engaged"] = True
                            _presence["empty_streak"] = 0
                            _presence["absence_remarked"] = False
                            if not _is_summons(text):
                                return

                            async def _ack_summons() -> None:
                                try:
                                    await session.say("Yes, Sir?")
                                except Exception:
                                    return
                                with __import__("contextlib").suppress(Exception):
                                    _hud_caption("jarvis", "Yes, Sir?")

                            try:
                                _wloop = asyncio.get_running_loop()
                            except RuntimeError:
                                _wloop = asyncio.get_event_loop()
                            _wloop.call_soon(
                                lambda lp=_wloop: lp.create_task(_ack_summons())
                            )
                    except Exception:
                        pass
            except Exception:
                pass

        def _on_convo_item(event) -> None:
            try:
                item = getattr(event, "item", None)
                if str(getattr(item, "role", "") or "") != "assistant":
                    return  # user lines come via the transcript hook above
                text = getattr(item, "text_content", None) or ""
                text = str(text).strip()
                if text:
                    _hud_caption("jarvis", text)
            except Exception:
                pass
            # Latency attribution: close the turn, log the exact breakdown,
            # and speak it proactively on real stalls (user asked to always
            # be told the cause). All best-effort, never breaks voice.
            try:
                import latency as _lat_end

                _summary = _lat_end.TRACKER.turn_end()
                _fmt_break = _lat_end.format_breakdown
                _fmt_say = _lat_end.format_spoken
                _is_slow = _lat_end.should_announce
                with __import__("contextlib").suppress(Exception):
                    from system import log_action as _log

                    _log("latency", f"turn {_fmt_break(_summary)}")
                if _is_slow(_summary):
                    _hud_caption("jarvis", f"(timing: {_fmt_break(_summary)})")

                    async def _announce_slow() -> None:
                        with __import__("contextlib").suppress(Exception):
                            await session.say(_fmt_say(_summary))

                    try:
                        _loop = asyncio.get_running_loop()
                    except RuntimeError:
                        _loop = asyncio.get_event_loop()
                    _loop.call_soon(lambda lp=_loop: lp.create_task(_announce_slow()))
            except Exception:
                pass

        session.on("user_input_transcribed", _on_user_transcript)
        session.on("conversation_item_added", _on_convo_item)
    except Exception:
        pass

    async def _hangup_when_forgotten() -> None:
        if _hangup_watching["active"]:
            return  # one watcher waits out the work; extra firings stand down
        _hangup_watching["active"] = True
        try:
            while not _idle_exceeded(_activity_clock["last"], time.monotonic()):
                await asyncio.sleep(5.0)
            # Engaged calls belong to the presence loop now (it closes on
            # confirmed absence); the idle hangup only takes standby calls.
            if _presence["engaged"]:
                return
            # Fixed line via say(), never generate_reply(): on Gemini Live
            # the model has vocalized the instructions verbatim ("Say a
            # short butler-style goodnight...") instead of following them.
            # A hangup line wants determinism, not variety.
            with contextlib.suppress(Exception):
                await session.say("Very good, Sir. Ring when you require me.")
            await session.aclose()
        finally:
            _hangup_watching["active"] = False

    # Where a reply's wait goes: local end-of-turn wait (EOU) vs Gemini's
    # time to first token and how much context it had to read.
    @session.on("metrics_collected")
    def _on_metrics(event) -> None:
        with contextlib.suppress(Exception):
            from system import log_action as _log

            m = event.metrics
            kind = getattr(m, "type", "")
            if kind == "eou_metrics":
                _log("latency", f"eou {m.end_of_utterance_delay:.2f}s")
            elif kind == "realtime_model_metrics" and m.ttft >= 0:
                _log(
                    "latency",
                    f"model ttft {m.ttft:.2f}s in={m.input_tokens} "
                    f"cached={m.input_token_details.cached_tokens}",
                )

    @session.on("user_state_changed")
    def _on_user_state_changed(event: UserStateChangedEvent) -> None:
        if event.new_state == "away":
            task = asyncio.create_task(_hangup_when_forgotten())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    # P0 turn phases: thinking/speaking transitions timestamp the model's
    # think time and voice start inside every turn's latency breakdown.
    @session.on("agent_state_changed")
    def _on_agent_state_changed(event) -> None:
        try:
            if str(getattr(event, "new_state", "")) in ("thinking", "speaking"):
                import latency as _lat_state

                _lat_state.TRACKER.mark(str(event.new_state))
        except Exception:
            pass

    # P4 graceful degrade: quota exhaustion used to kill the turn with a
    # bare exception. Name the cause out loud (cooldown-gated) instead.
    _quota_said_at = {"at": 0.0}

    @session.on("error")
    def _on_session_error(event) -> None:
        try:
            import latency as _lat_err

            if not _lat_err.is_quota_error(getattr(event, "error", None)):
                return
            now = time.monotonic()
            if now - _quota_said_at["at"] < 120.0:
                return
            _quota_said_at["at"] = now
            with contextlib.suppress(Exception):
                from system import log_action as _log_err

                _log_err("latency", "quota-exhausted degrade line spoken")

            async def _degrade_line() -> None:
                with contextlib.suppress(Exception):
                    await session.say(
                        "The speech brain is rate-limited, Sir — tools "
                        "still work. Try again in a minute."
                    )

            try:
                _eloop = asyncio.get_running_loop()
            except RuntimeError:
                _eloop = asyncio.get_event_loop()
            _eloop.call_soon(lambda lp=_eloop: lp.create_task(_degrade_line()))
        except Exception:
            pass

    # Build the brain BEFORE connecting so pydantic/LLM schema warmup
    # overlaps the network join instead of blocking the audio loop after.
    assistant = Assistant(browser)

    # Join the room FIRST so the worker is visibly present even while
    # models warm up. session.start before ctx.connect() leaves the
    # room with no agent and the UI stuck on "connecting".
    await ctx.connect()
    connected_at = time.monotonic()

    async def _record_talk_time() -> None:
        # The latency probe (scripts/voice_latency.py) is test traffic: it
        # must not spend the household allowance (it used ~20 min in a night).
        if str(getattr(ctx.room, "name", "")).startswith("latency-probe-"):
            return
        record_session(time.monotonic() - connected_at)

    ctx.add_shutdown_callback(_record_talk_time)

    # Word-synced HUD captions; also the loop guard: a reply stuck repeating
    # itself is cut off and Sir gets a short line instead of minutes of it.
    _live_caption = _HudLiveCaption()

    def _on_reply_loop(text: str) -> None:
        with contextlib.suppress(Exception):
            from system import log_action as _log_loop

            _log_loop("loop-guard", f"interrupted repeating reply: {text[-160:]}")
        with contextlib.suppress(Exception):
            session.interrupt()

    _live_caption.on_loop = _on_reply_loop

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=assistant,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            video_input=True,
            # No noise_cancellation: ai_coustics.audio_enhancement() runs
            # against LiveKit Cloud Inference, which doesn't exist behind a
            # local livekit-server — input audio silently never arrives
            # (zero user transcripts on every call). Raw room audio it is.
            audio_input=room_io.AudioInputOptions(),
            # Word-synced captions: the synchronizer paces text to audio
            # playout, then this tail mirrors it to the HUD word by word.
            text_output=room_io.TextOutputOptions(next_in_chain=_live_caption),
            delete_room_on_close=True,
        ),
    )

    # Proactive pillar: background battery/climb watcher. Edge-triggered,
    # cooldown-gated, and fully swallowed on failure so sensors can never
    # break a voice session.
    try:
        from context.telemetry import read_real_snapshot
        from proactive.watcher import ProactiveWatcher

        async def _speak_warning(line: str) -> None:
            # Exact text: say() needs no LLM turn and works on every model.
            with __import__("contextlib").suppress(Exception):
                await session.say(line)

        _watcher = ProactiveWatcher(read_real_snapshot, _speak_warning)
        _watcher_task = _watcher.start()
        _background_tasks.add(_watcher_task)
        _watcher_task.add_done_callback(_background_tasks.discard)

        async def _stop_watcher() -> None:
            # stop() is sync; the job wrapper awaits callbacks, so a
            # bare sync callback crashes shutdown with TypeError.
            _watcher.stop()

        ctx.add_shutdown_callback(_stop_watcher)
    except Exception:
        pass

    # Draft-ready notices: off by default (drafts are announced at creation
    # via notify.send in mail_watch_job). Opt back in with
    # JARVIS_DRAFT_WATCHER=1. Swallowed on failure like the watcher above.
    try:
        from draft_notify import DraftWatcher, announce_enabled

        if (
            os.environ.get("JARVIS_DRAFT_WATCHER", "0").strip().lower()
            in ("1", "true", "yes", "on")
            and announce_enabled()
        ):

            async def _speak_draft(line: str) -> None:
                await session.say(line)

            _draft_watcher = DraftWatcher(_speak_draft)
            _draft_task = _draft_watcher.start()
            _background_tasks.add(_draft_task)
            _draft_task.add_done_callback(_background_tasks.discard)

            async def _stop_draft_watcher() -> None:
                _draft_watcher.stop()

            ctx.add_shutdown_callback(_stop_draft_watcher)
    except Exception:
        pass

    # HUD Region 2: data-channel task events (best-effort, never breaks voice).
    try:
        from hud_events import (
            mirror_to_log,
            publish,
            tool_finish_payload,
            tool_start_payload,
        )

        def _on_hud_tools(executed) -> None:
            try:
                calls = (
                    getattr(executed, "function_calls", None)
                    or getattr(executed, "tools", None)
                    or []
                )
                for call in calls:
                    name = (
                        getattr(call, "name", None)
                        or getattr(call, "tool_name", None)
                        or str(call)[:60]
                    )
                    ok = getattr(call, "is_error", False) is False
                    mirror_to_log(str(name), "tool_start")
                    mirror_to_log(str(name), "tool_finish", "ok" if ok else "error")
                    room = getattr(ctx, "room", None)
                    if room is not None:
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = asyncio.get_event_loop()
                        # Bind loop + per-call values explicitly as defaults:
                        # the bare closure captured the loop variable late and
                        # ensure_future() could land on the wrong loop.
                        loop.call_soon(
                            lambda n=str(name), o=ok, r=room, lp=loop: (
                                lp.create_task(publish(r, tool_start_payload(n))),
                                lp.create_task(
                                    publish(r, tool_finish_payload(n, ok=o))
                                ),
                            )
                        )
            except Exception:
                pass

        session.on("function_tools_executed", _on_hud_tools)
    except Exception:
        pass

    # Household accounts: refuse at the cap, warn at 70/90%.
    books = budget_status()
    if books["used_minutes"] >= books["limit_minutes"]:
        await _reply_or_say(
            session,
            instructions=(
                "Tell the user, in one dry butler sentence, that the monthly "
                "talking allowance is spent and you will be silent until next "
                "month unless they raise the allowance."
            ),
            fallback="The monthly talking allowance is spent, Sir.",
        )
        await session.aclose()
        return

    # Presence gatekeeper: the old always-greet-on-join is what talked to
    # empty rooms ("random yes sir" in the captions). Calls now start in
    # standby. Engagement needs a summons — the name spoken, or the camera
    # gate confirming someone at the desk — and lasts only while the desk
    # is occupied. Absence needs TWO consecutive empty snapshots before
    # acting (one missed face must never kill a live call).
    _checkin_lines = [
        "Still at your post, Sir? I remain at yours.",
        "A brief check-in, Sir — do you require anything?",
        "The hour passes quietly, Sir. I am here if needed.",
    ]
    _absent_lines = [
        "The desk stands empty, Sir. I shall hold my tongue — and the fort.",
        "No sign of you, Sir. Ring when you materialize.",
    ]

    def _budget_aside() -> str:
        if books["pct"] >= 0.7:
            return (
                f" One dry aside, Sir: the household talking accounts stand "
                f"at {books['pct']:.0%} of the monthly allowance."
            )
        return ""

    async def _presence_snapshot() -> dict:
        try:
            from presence import check_presence

            return await asyncio.to_thread(check_presence)
        except Exception:
            return {"status": "unknown", "faces": -1, "say": "Presence check failed."}

    async def _say(line: str) -> bool:
        # Captioned on success: say() alone emits no message event, so
        # without this the overlay never shows proactive speech.
        try:
            await session.say(line)
        except Exception:
            return False
        with contextlib.suppress(Exception):
            _hud_caption("jarvis", line)
        return True

    async def _greet() -> None:
        # One retry: the realtime speech scheduler is occasionally not up
        # on the first say ("skipping new realtime generation"), which
        # used to mute the whole call.
        if await _say(f"Good day, Sir. What do you require?{_budget_aside()}"):
            return
        await asyncio.sleep(2.0)
        await _say("Good day, Sir. What do you require?")

    # Join gate: a wake summons IS the engagement (the spoken words were
    # spent on the offline detector and never arrive as a transcript, so
    # waiting for a name here means eternal silence). Otherwise greet a
    # confirmed face, remark once on a confirmed-empty desk, and stay
    # silent when the camera is unreadable. Greet with one retry: the
    # realtime speech scheduler is occasionally not up on first say.
    import school as _school

    _school_call = _school.is_school()
    _wake = _wake_summoned(ctx.room)
    # A wake always greets whatever the camera says, so skip the check: it
    # took ~11s with the camera present, and anything Sir said in that
    # window (e.g. "launch YouTube") was lost before the session listened.
    _join = (
        {"status": "unknown"} if (_school_call or _wake) else await _presence_snapshot()
    )
    _decision = (
        "school" if _school_call else _join_decision(_wake, str(_join.get("status", "unknown")))
    )
    with contextlib.suppress(Exception):
        from system import log_action as _log_join

        _log_join(
            "presence", f"join wake={_wake} cam={_join.get('status')} -> {_decision}"
        )
    if _decision == "greet" and _wake_reason(ctx.room) == "daddy":
        # "Wake up, daddy's home": the custom welcome plus the briefing.
        _presence["engaged"] = True
        await _say(DADDY_GREETING)
        try:
            _brief = await assistant.inbox_tools.morning_briefing(
                SimpleNamespace(session=session)
            )
            # One instructed turn: the raw bundle has emoji and fragments,
            # and a verbatim say() made Gemini re-fetch it with tools.
            await session.generate_reply(instructions=_briefing_instructions(str(_brief.get("say") or "")))
        except Exception:
            await _say("The briefing is unavailable just now, Sir.")
    elif _decision == "greet":
        _presence["engaged"] = True
        await _greet()
        if _wake:
            # A wake call lives around Sir's request: once the listening
            # window passes with no follow-up it closes, the HUD goes idle
            # and the wake word listens again.
            _fu_task = asyncio.create_task(
                _school_follow_up(session, first=_normal_follow_up_s(), window=_normal_follow_up_s())
            )
            _background_tasks.add(_fu_task)
            _fu_task.add_done_callback(_background_tasks.discard)
    elif _decision == "remark":
        _presence["absence_remarked"] = True
        await _say(_absent_lines[0])
    elif _decision == "school":
        # School mode: no greeting (the strip shows LISTENING), already
        # engaged, and the call only lives around Sir's request.
        _presence["engaged"] = True
        _fu_task = asyncio.create_task(_school_follow_up(session))
        _background_tasks.add(_fu_task)
        _fu_task.add_done_callback(_background_tasks.discard)
        if _wake_reason(ctx.room) == "school-bare":
            # Bare "hey Jarvis": say so, or Sir and Jarvis both wait in
            # silence until the first window closes the call.
            await _say(SCHOOL_BARE_REPLY)
    # "silent": standby; the idle hangup closes us if nobody comes.

    async def _presence_loop() -> None:
        """Camera check-ins every 5-30 min: greet the arrived, check on the
        seated, remark once on the departed (then close an engaged call)."""
        while not _presence["stopped"]:
            await asyncio.sleep(random.uniform(5 * 60, 30 * 60))
            if _presence["stopped"]:
                break
            if _school.is_school():
                continue  # no check-ins or remarks in class
            snap = await _presence_snapshot()
            status = str(snap.get("status", "unknown"))
            if status == "present":
                _presence["empty_streak"] = 0
                if _presence["engaged"]:
                    line = _checkin_lines[
                        _presence["checkin_idx"] % len(_checkin_lines)
                    ]
                    _presence["checkin_idx"] += 1
                    await _say(line)
                else:
                    _presence["engaged"] = True
                    _presence["absence_remarked"] = False
                    await _greet()
            elif status == "absent":
                _presence["empty_streak"] += 1
                if _presence["empty_streak"] < 2:
                    continue  # one miss could be a turned back; confirm first
                if _presence["engaged"]:
                    _presence["engaged"] = False
                    await _say(_absent_lines[1])
                    with contextlib.suppress(Exception):
                        await session.say("Very good, Sir. Ring when you require me.")
                    with contextlib.suppress(Exception):
                        await session.aclose()
                    break
                if not _presence["absence_remarked"]:
                    _presence["absence_remarked"] = True
                    await _say(_absent_lines[0])
            # "unknown": say nothing, change nothing.

    _presence_task = asyncio.create_task(_presence_loop())
    _background_tasks.add(_presence_task)
    _presence_task.add_done_callback(_background_tasks.discard)

    async def _idle_watchdog() -> None:
        """Hang up once no real turn has happened for the budget: 60 s in
        standby, JARVIS_ENGAGED_IDLE when engaged. Polled, not driven by
        the "away" event: steady background noise keeps VAD from ever
        declaring the user away, and an open call leaves the wake client
        deaf to "hey Jarvis"."""
        while not _presence["stopped"]:
            await asyncio.sleep(10.0)
            budget = _engaged_idle_seconds() if _presence["engaged"] else IDLE_HANGUP_SECONDS
            if not _idle_exceeded(_turn_clock["last"], time.monotonic(), budget):
                continue
            if str(getattr(session, "agent_state", "")) in ("thinking", "speaking"):
                continue  # never cut a reply (or a long read-out) short
            with contextlib.suppress(Exception):
                from system import log_action as _log_idle

                _log_idle("presence", f"idle hangup after {budget:.0f}s without a real turn")
            with contextlib.suppress(Exception):
                await session.say("Very good, Sir. Ring when you require me.")
            with contextlib.suppress(Exception):
                await session.aclose()
            break

    _watchdog_task = asyncio.create_task(_idle_watchdog())
    _background_tasks.add(_watchdog_task)
    _watchdog_task.add_done_callback(_background_tasks.discard)

    async def _stop_presence() -> None:
        _presence["stopped"] = True
        _presence_task.cancel()
        _watchdog_task.cancel()
        with contextlib.suppress(Exception):
            await _presence_task

    ctx.add_shutdown_callback(_stop_presence)


if __name__ == "__main__":
    cli.run_app(server)
