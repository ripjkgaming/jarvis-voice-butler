import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jarvis_rt.events import EventEmitter, UserStateChangedEvent


def test_emit_sync_async_once_and_isolation():
    seen = []
    em = EventEmitter()
    em.on("x", lambda e: seen.append(("a", e)))
    em.once("x", lambda e: seen.append(("once", e)))
    em.on("x", lambda e: 1 / 0)  # must not break later handlers

    async def h(e):
        seen.append(("async", e))

    em.on("x", h)

    async def run():
        em.emit("x", 1)
        em.emit("x", 2)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert [s for s in seen if s[0] == "a"] == [("a", 1), ("a", 2)]
    assert [s for s in seen if s[0] == "once"] == [("once", 1)]
    assert len([s for s in seen if s[0] == "async"]) == 2


def test_event_fields():
    e = UserStateChangedEvent(old_state="listening", new_state="speaking")
    assert e.new_state == "speaking" and e.type == "user_state_changed"
