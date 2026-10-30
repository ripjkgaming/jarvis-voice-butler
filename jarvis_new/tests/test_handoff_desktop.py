"""Regression test: the transfer_to_system_control handoff must survive.

Covers the 21:17 prod incident where the call died ~50ms after the handoff
tool executed. The live handoff checks (real model calls, opt-in via
JARVIS_LIVE_LLM=1) were removed with livekit-agents: they drove the
livekit AgentSession.run and the livekit inference/free-LLM adapters.
Only the offline tool-reachability check remains here.
"""

import pytest

from system.desktop import DesktopTools


@pytest.mark.asyncio
async def test_desktop_tool_ids_reachable_on_specialist() -> None:
    from agent import RARE_SYSTEM_TOOL_IDS, SystemAgent

    specialist = SystemAgent(llm=None, only_ids=RARE_SYSTEM_TOOL_IDS)
    ids = [tool.id for tool in specialist.tools]
    for expected in [tool.id for tool in DesktopTools().tools]:
        assert expected in ids
