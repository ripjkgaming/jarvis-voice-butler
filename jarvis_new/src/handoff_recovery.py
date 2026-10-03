"""Preserve a handoff followed by an ordinary tool result in the same batch.

LiveKit Agents 1.8.2 overwrites its selected agent with every result's
agent_task, including None (both text and realtime pipelines). Thus
transfer_to_system_control + play_media can acknowledge a launch without
ever switching agents. Remember the actual returned agent by call ID and
use the public update_agent API only when the SDK missed that one handoff.
Normal SDK handoffs, failed/interrupted tools and conflicting handoffs
retain the SDK's behavior. No desktop actions are repeated here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from livekit.agents import Agent, AgentSession, RunContext
from livekit.agents.voice.events import FunctionToolsExecutedEvent

log = logging.getLogger("jarvis.handoff")


@dataclass
class _Pending:
    target: Agent
    source: Agent
    speech: object


class _Recovery:
    def __init__(self, session: AgentSession) -> None:
        self.session = session
        self.pending: dict[str, _Pending] = {}

    def completed(self, event: FunctionToolsExecutedEvent) -> None:
        # Consume records even when the SDK handled or interrupted the batch.
        candidates = []
        for call, output in event.zipped():
            pending = self.pending.pop(call.call_id, None)
            if (
                pending is not None
                and not output.is_error
                and not getattr(pending.speech, "interrupted", False)
            ):
                candidates.append(pending)
        if event.has_agent_handoff or len(candidates) != 1:
            return
        if len(event.function_calls) < 2:
            return
        pending = candidates[0]
        if self.session.current_agent is not pending.source:
            return
        self.session.update_agent(pending.target)
        # The specialist's on_enter owns the continuation. Prevent a second
        # router reply while its transition drains the old activity; preserve
        # the completed tool outputs themselves for the conversation history.
        event.cancel_tool_reply()
        log.info("Recovered a handoff lost in a mixed tool batch")


def install(session: AgentSession) -> None:
    """Install before other function_tools_executed handlers. Idempotent."""
    if getattr(session, "_jarvis_handoff_recovery", None) is not None:
        return
    recovery = _Recovery(session)
    session._jarvis_handoff_recovery = recovery
    session.on("function_tools_executed", recovery.completed)
    session.on("close", lambda _event: recovery.pending.clear())


def remember(context: RunContext | None, result: object) -> None:
    """Called by the common tool wrapper after a successful tool return."""
    if context is None:
        return
    values = result if isinstance(result, (tuple, list, set, frozenset)) else (result,)
    targets = [value for value in values if isinstance(value, Agent)]
    if len(targets) != 1:
        return
    recovery = getattr(context.session, "_jarvis_handoff_recovery", None)
    if recovery is None:
        return
    recovery.pending[context.function_call.call_id] = _Pending(
        target=targets[0],
        source=context.session.current_agent,
        speech=context.speech_handle,
    )
    # A completed tool may be abandoned before its event during shutdown.
    # Bound retained references rather than retaining old activities forever.
    while len(recovery.pending) > 32:
        recovery.pending.pop(next(iter(recovery.pending)))
