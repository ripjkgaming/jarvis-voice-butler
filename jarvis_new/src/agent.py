import asyncio
import os
import time
from pathlib import Path

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
from livekit.agents.voice import UserStateChangedEvent
from livekit.plugins import ai_coustics, google

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
from system.inbox import InboxTools
from system.osint import OsintTools
from system.pentest import PentestTools
from system.reddit import RedditTools
from tools import BrowserTools

# Resolve against the checkout root, not cwd: the Tauri shell spawns the
# worker with cwd=shell/, where a relative ".env.local" never resolves and
# the worker dies with "api_key is required" (first-run crash loop).
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")


def _realtime_llm():
    # gemini-3.1-flash-live-preview CANNOT do generate_reply/say()
    # (the plugin ignores them), which left joins completely silent.
    # 2.5 native-audio supports the full server-speech flow and is
    # equally unlimited-free on the owner's AI Studio key. The -latest
    # alias tracks the GA build (better capacity than dated previews).
    return google.beta.realtime.RealtimeModel(
        model="gemini-2.5-flash-native-audio-latest",
        voice="Enceladus",
        # No language code: 2.5 native-audio rejects explicit codes
        # (1007 for both en-GB and en) and defaults to English anyway.
        # Minimal thinking budget: the default lets the model ponder
        # before the first audio token; a butler router doesn't need
        # deep thought.
        thinking_config=genai_types.ThinkingConfig(thinking_budget=128),
        # NOTE: aggressive endpointing (high sensitivity, 400ms silence)
        # measured SLOWER (~13s): it cut utterances mid-pause, forcing
        # cancelled generations and retries. Server defaults rule.
        realtime_input_config=genai_types.RealtimeInputConfig(
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                disabled=False,
                start_of_speech_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_LOW,
                end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
                prefix_padding_ms=300,
                silence_duration_ms=1500,
            )
        ),
        tool_response_scheduling=genai_types.FunctionResponseScheduling.WHEN_IDLE,
    )


# Cheap pipeline model IDs (LiveKit Inference; billed from credits).
CHEAP_STT_MODEL = "assemblyai/universal-streaming"
CHEAP_LLM_MODEL = "google/gemini-2.5-flash"
# Direct text brains (Gemini API, no Inference). Stable alias: pinned
# "gemini-2.5-flash" rotted (retired for new keys -> instant 404).
# Keep in sync with bridge.py's GEMINI_TEXT_MODEL.
DIRECT_LLM_MODEL = "gemini-flash-latest"
# Free cloud fallback voice (debugging + fallback if local voice misbehaves).
CHEAP_TTS_MODEL = "rime/coda"

# Silence before the agent hangs up on its own (seconds).
IDLE_HANGUP_SECONDS = 60.0


def _idle_exceeded(
    last_active: float, now: float, idle_seconds: float = IDLE_HANGUP_SECONDS
) -> bool:
    """True only after a full quiet window with no user/agent activity. Pure."""
    return (now - last_active) >= idle_seconds


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


def _session_for_pipeline(turn_handling: TurnHandlingOptions) -> AgentSession:
    """Voice session for the active pipeline.

    Both branches carry the same IDLE_HANGUP_SECONDS away budget. The
    framework default (15s) hung up mid-task in the 21:17 incident: it
    fired 15s after the user's last utterance while a handoff was still
    executing, and the call died as USER_INITIATED.
    """
    if _pipeline_name() == "realtime":
        # Gemini realtime handles the voice input and output for this session.
        return AgentSession(
            turn_handling=turn_handling,
            user_away_timeout=IDLE_HANGUP_SECONDS,
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
            vad=silero.VAD.load(),
            stt=FasterWhisperSTT(),
            llm=google.LLM(model=DIRECT_LLM_MODEL),
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
        # Ears: budget streaming STT. Brains: still Gemini. Mouth: the
        # downloaded local voice (zero inference burn).
        # See all available models at https://docs.livekit.io/agents/models/stt/
        stt=inference.STT(model=CHEAP_STT_MODEL, language="en-GB"),
        llm=inference.LLM(model=CHEAP_LLM_MODEL),
        tts=_session_tts(),
        turn_handling=turn_handling,
        # Silence budget: flag the user away so the hangup task below fires.
        user_away_timeout=IDLE_HANGUP_SECONDS,
    )


_shared_local_llm = None


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
            _shared_local_llm = google.LLM(model=DIRECT_LLM_MODEL)
        return _shared_local_llm
    if _shared_local_llm is None:
        _shared_local_llm = inference.LLM(model=CHEAP_LLM_MODEL)
    return _shared_local_llm


def _end_call_tool() -> EndCallTool:
    """Hang-up tool shared by the router and both specialists.

    Specialists need their own: after a handoff the router's end_call is
    out of scope, and without one the agent must refuse a direct "hang up".
    """
    return EndCallTool(
        extra_description=(
            "Only end the call after the user clearly says they are finished, "
            "says goodbye, or directly asks to end the call."
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
        self.research_browser = BrowserManager.create_research_manager()
        self.research_tools = BrowserTools(self.research_browser)
        self._end_call_tool = _end_call_tool()
        super().__init__(
            llm=llm or _default_agent_llm(),
            instructions=RESEARCH_INSTRUCTIONS,
            tools=[*self.research_tools.tools, *self._end_call_tool.tools],
        )

    # generate_reply is unreliable on some Gemini Live models (it may
    # refuse commentary turns): never let it raise out of on_enter, or
    # the handoff lands dead with no turn at all. Tool-first: speech
    # only as progress, like SystemAgent.on_enter.
    async def on_enter(self) -> None:
        with __import__("contextlib").suppress(Exception):
            await self.session.generate_reply(
                instructions=(
                    "The handoff message holds your research topic. Begin the "
                    "task instantly by calling the first tool. Chain tools "
                    "silently until done, then report results and "
                    "transfer_back_to_main. Anything you speak must be progress "
                    "or results — never a bare acknowledgement."
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

    # NOTE: on_enter MUST fire a reply: without it the switch lands in
    # silence (stalemate seen 22:46 — handoff done, specialist live, zero
    # tools, zero words). But a bare "ready" settles the turn the other way
    # (stalemate seen 22:06 — 55s of mutual waiting). So the reply carries
    # action-forcing instructions: tools first, speech only as progress.
    # generate_reply is unreliable on some Gemini Live models (it may
    # refuse commentary turns): never let it raise out of on_enter, or
    # the handoff lands dead with no turn at all.
    async def on_enter(self) -> None:
        with __import__("contextlib").suppress(Exception):
            await self.session.generate_reply(
                instructions=(
                    "The handoff message holds your task. Do not greet or announce "
                    "readiness: begin the task instantly by calling the first tool. "
                    "Chain tools silently until done, then report results and "
                    "transfer_back_to_main. Anything you speak must be progress or "
                    "results — never a bare acknowledgement."
                )
            )

    @function_tool()
    async def transfer_back_to_main(self, context: RunContext):
        """Return control to the main Jarvis router from this specialist."""
        return (self._parent if self._parent is not None else self), (
            "Returning to Jarvis."
        )


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
        self.osint_tools = OsintTools()
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
            instructions=AGENT_INSTRUCTIONS,
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
        return self._system_agent, f"Handing off to system control: {task}."

    @function_tool()
    async def transfer_back_to_main(self, context: RunContext):
        """Return control to the main Jarvis router from a specialist."""
        return self, "Returning to Jarvis."


server = AgentServer()


@server.rtc_session(agent_name=os.environ.get("AGENT_NAME", "my-agent"))
async def my_agent(ctx: JobContext):
    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

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
        # The LiveKit turn detector determines when the user is done speaking and the agent should respond.
        # TurnDetector is an end-of-turn model that listens to the user's audio directly, combining
        # semantic understanding with acoustic cues (intonation, pitch, rhythm) for state-of-the-art accuracy.
        # AgentSession supplies the required VAD automatically.
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=inference.TurnDetector(),
        # Adaptive interruptions use the turn detector to tell a real interruption from a
        # backchannel like "mhm" or "right", so the agent keeps talking through the latter.
        interruption={"mode": "adaptive"},
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation={"enabled": True},
    )
    session = _session_for_pipeline(turn_handling)
    # NOTE: local-pipeline expressive mode stays off (see _session_for_pipeline):
    # expressive=True needs a markup-capable TTS such as Fish Audio.
    # _session_tts() returns local Piper by default.

    _background_tasks: set[asyncio.Task] = set()

    # Last user/agent activity: the away timer only watches the user, so a
    # slow tool chain (handoff, research, desktop loop) looks like
    # abandonment. The hangup below refuses to fire while anyone is working.
    _activity_clock = {"last": time.monotonic()}
    _hangup_watching = {"active": False}

    def _mark_active(*_args, **_kwargs) -> None:
        _activity_clock["last"] = time.monotonic()

    session.on("user_input_transcribed", _mark_active)
    session.on("conversation_item_added", _mark_active)
    session.on("function_tools_executed", _mark_active)

    async def _hangup_when_forgotten() -> None:
        if _hangup_watching["active"]:
            return  # one watcher waits out the work; extra firings stand down
        _hangup_watching["active"] = True
        try:
            while not _idle_exceeded(_activity_clock["last"], time.monotonic()):
                await asyncio.sleep(5.0)
            await _reply_or_say(
                session,
                instructions=(
                    "The user has been silent for a full minute. Say one short "
                    "butler-style goodnight line and nothing else."
                ),
                fallback="Very good, Sir. Ring when you require me.",
            )
            await session.aclose()
        finally:
            _hangup_watching["active"] = False

    @session.on("user_state_changed")
    def _on_user_state_changed(event: UserStateChangedEvent) -> None:
        if event.new_state == "away":
            task = asyncio.create_task(_hangup_when_forgotten())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    # Join the room FIRST so the worker is visibly present even while
    # models warm up. session.start before ctx.connect() leaves the
    # room with no agent and the UI stuck on "connecting".
    await ctx.connect()
    connected_at = time.monotonic()

    async def _record_talk_time() -> None:
        record_session(time.monotonic() - connected_at)

    ctx.add_shutdown_callback(_record_talk_time)

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=Assistant(browser),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            video_input=True,
            audio_input=room_io.AudioInputOptions(
                # Default QUAIL_L noise suppression (free). QUAIL_VF_S voice
                # isolation is metered (100 min/mo) and not worth it here.
                noise_cancellation=ai_coustics.audio_enhancement(),
            ),
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
    # Always greet on join so the LLM engages immediately — previously
    # the session stayed silent until the 60s idle goodnight, which
    # looked like "no LLM connected".
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
    if books["pct"] >= 0.7:
        await _reply_or_say(
            session,
            instructions=(
                f"Greet the user as usual, then add one dry aside that the "
                f"household talking accounts stand at {books['pct']:.0%} of "
                f"the monthly allowance."
            ),
            fallback="At your service, Sir.",
        )
    else:
        await _reply_or_say(
            session,
            instructions=(
                "Greet the user briefly as Jarvis, the sarcastic "
                "British butler, and ask what they require. One short "
                "sentence only."
            ),
            fallback="At your service, Sir. What do you require?",
        )


if __name__ == "__main__":
    cli.run_app(server)
