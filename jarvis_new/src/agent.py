import asyncio
import os
import time

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

from browser import BrowserManager
from local_voice import PiperTTS
from prompts import AGENT_INSTRUCTIONS, RESEARCH_INSTRUCTIONS, SYSTEM_INSTRUCTIONS
from system.background import BackgroundTools
from system.budget import record_session
from system.budget import status as budget_status
from system.core import SystemTools
from system.daily import DailyTools
from system.devices import DeviceTools
from system.inbox import InboxTools
from system.reddit import RedditTools
from tools import BrowserTools

load_dotenv(".env.local")


def _realtime_llm():
    return google.beta.realtime.RealtimeModel(
        model="gemini-3.1-flash-live-preview",
        voice="Enceladus",
        language="en-GB",
        tool_response_scheduling=genai_types.FunctionResponseScheduling.WHEN_IDLE,
    )


# Cheap pipeline model IDs (LiveKit Inference; billed from credits).
CHEAP_STT_MODEL = "assemblyai/universal-streaming"
CHEAP_LLM_MODEL = "google/gemini-2.5-flash"
# Free cloud fallback voice (debugging + fallback if local voice misbehaves).
CHEAP_TTS_MODEL = "rime/coda"

# Silence before the agent hangs up on its own (seconds).
IDLE_HANGUP_SECONDS = 60.0


def _pipeline_name() -> str:
    """'local' (cheap STT + Gemini brains + local voice) or 'realtime'."""
    return os.environ.get("JARVIS_PIPELINE", "local").strip().lower()


def _session_tts():
    """Local Piper voice by default; JARVIS_TTS=<inference model> for a
    free cloud voice (e.g. rime/coda). Escape hatch for debugging."""
    override = os.environ.get("JARVIS_TTS", "local").strip().lower()
    if override and override != "local":
        return inference.TTS(model=override)
    return PiperTTS()


_shared_local_llm = None


def _default_agent_llm():
    """Realtime model in realtime mode; shared Gemini Flash in local mode.

    Sub-agents need an explicit LLM: llm=None does NOT inherit the
    session default (the activity raises "trying to generate reply
    without an LLM model"). One shared instance mirrors how the
    realtime model is shared across agents.
    """
    global _shared_local_llm
    if _pipeline_name() == "realtime":
        return _realtime_llm()
    if _shared_local_llm is None:
        _shared_local_llm = inference.LLM(model=CHEAP_LLM_MODEL)
    return _shared_local_llm


class ResearchAgent(Agent):
    """Isolated deep-research specialist (separate browser, no shared login).

    Used only when the router decides a task needs a whole separate browser:
    multi-page comparison, citations, long reads. Owns its own BrowserManager
    lifecycle; the main tabs are untouched.
    """

    def __init__(self, llm=None) -> None:
        self.research_browser = BrowserManager.create_research_manager()
        self.research_tools = BrowserTools(self.research_browser)
        super().__init__(
            llm=llm or _default_agent_llm(),
            instructions=RESEARCH_INSTRUCTIONS,
            tools=[*self.research_tools.tools],
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Say one short line that deep research has begun."
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
    }
)
"""Dangerous/rare laptop tools kept off the router.

These live only on the filtered SystemAgent behind the narrow
transfer_to_system_control handoff, so the main agent's per-turn
context stays lean and small models cannot misfire them. Everything
else on the laptop is a direct tool on the Assistant."""


class SystemAgent(Agent):
    """Local Linux PC-control specialist (safe core + devices + daily).

    Only runs usefully when JARVIS_LOCAL=1; every tool re-checks this so a
    cloud deployment refuses instead of acting on the wrong machine.
    Shutdown/reboot require confirm_power_action first; everything else runs
    immediately and is logged to ~/.jarvis/actions.log.
    """

    def __init__(
        self, llm=None, only_ids: set[str] | frozenset[str] | None = None
    ) -> None:
        self.system_tools = SystemTools()
        self.device_tools = DeviceTools()
        self.daily_tools = DailyTools()
        self.background_tools = BackgroundTools()
        self.inbox_tools = InboxTools()
        self.reddit_tools = RedditTools()
        groups = (
            self.system_tools.tools,
            self.device_tools.tools,
            self.daily_tools.tools,
            self.background_tools.tools,
            self.inbox_tools.tools,
            self.reddit_tools.tools,
        )
        if only_ids is None:
            picked = [tool for group in groups for tool in group]
        else:
            picked = [tool for group in groups for tool in group if tool.id in only_ids]
        super().__init__(
            llm=llm or _default_agent_llm(),
            instructions=SYSTEM_INSTRUCTIONS,
            tools=picked,
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Say one short line that local system control is ready."
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
        self._research_agent: ResearchAgent | None = None
        self._system_agent: SystemAgent | None = None
        self._end_call_tool = EndCallTool(
            extra_description=(
                "Only end the call after the user clearly says they are finished, "
                "says goodbye, or directly asks to end the call."
            ),
            end_instructions=(
                "Give Jarvis's brief, polite British-English farewell, then end the call."
            ),
        )
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
            self._research_agent = ResearchAgent(llm=llm if llm is not None else None)
        return (
            self._research_agent,
            f"Handing off to deep research on {topic}.",
        )

    @function_tool()
    async def transfer_to_system_control(self, context: RunContext, task: str):
        """Hand off rare laptop actions: shutdown/reboot, opening desktop
        programs, moving windows, media keys, brightness, keyboard light,
        monitors, smart home, games.

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
                only_ids=RARE_SYSTEM_TOOL_IDS,
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

    browser = BrowserManager(headless=False)
    ctx.add_shutdown_callback(browser.close)

    # Voice pipeline: cheap local stack by default (budget STT + Gemini
    # brains + downloaded voice = ~$0.003/min). JARVIS_PIPELINE=realtime
    # restores the bundled Gemini realtime model (~$0.05/min).
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
    if _pipeline_name() == "realtime":
        # Gemini realtime handles the voice input and output for this session.
        session = AgentSession(turn_handling=turn_handling)
    else:
        session = AgentSession(
            # Ears: budget streaming STT. Brains: still Gemini. Mouth: the
            # downloaded local voice (zero inference burn).
            # See all available models at https://docs.livekit.io/agents/models/stt/
            stt=inference.STT(model=CHEAP_STT_MODEL, language="en-GB"),
            llm=inference.LLM(model=CHEAP_LLM_MODEL),
            tts=_session_tts(),
            turn_handling=turn_handling,
            # Expressive mode injects the TTS provider's markup guide into the LLM prompt, so the model
            # emits inline delivery tags (emotion, pacing, non-verbal sounds) that the TTS renders and
            # the transcript never shows. Requires a TTS model that supports markup, such as the Fish
            # Audio model above.
            # expressive=True,
            # Silence budget: flag the user away so the hangup task below fires.
            user_away_timeout=IDLE_HANGUP_SECONDS,
        )

    _background_tasks: set[asyncio.Task] = set()

    async def _hangup_when_forgotten() -> None:
        await session.generate_reply(
            instructions=(
                "The user has been silent for a full minute. Say one short "
                "butler-style goodnight line and nothing else."
            )
        )
        await session.aclose()

    @session.on("user_state_changed")
    def _on_user_state_changed(event: UserStateChangedEvent) -> None:
        if event.new_state == "away":
            task = asyncio.create_task(_hangup_when_forgotten())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

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
            await session.generate_reply(instructions=f"Say exactly: {line}")

        _watcher = ProactiveWatcher(read_real_snapshot, _speak_warning)
        _watcher_task = _watcher.start()
        _background_tasks.add(_watcher_task)
        _watcher_task.add_done_callback(_background_tasks.discard)
        ctx.add_shutdown_callback(_watcher.stop)
    except Exception:
        pass

    # Join the room and connect to the user
    await ctx.connect()
    connected_at = time.monotonic()

    async def _record_talk_time() -> None:
        record_session(time.monotonic() - connected_at)

    ctx.add_shutdown_callback(_record_talk_time)

    # Household accounts: refuse at the cap, warn at 70/90%.
    books = budget_status()
    if books["used_minutes"] >= books["limit_minutes"]:
        await session.generate_reply(
            instructions=(
                "Tell the user, in one dry butler sentence, that the monthly "
                "talking allowance is spent and you will be silent until next "
                "month unless they raise the allowance."
            )
        )
        await session.aclose()
        return
    if books["pct"] >= 0.7:
        await session.generate_reply(
            instructions=(
                f"Greet the user as usual, then add one dry aside that the "
                f"household talking accounts stand at {books['pct']:.0%} of "
                f"the monthly allowance."
            )
        )


if __name__ == "__main__":
    cli.run_app(server)
