import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jarvis_rt import speech_handle as jarvis_sh

try:
    from livekit.agents.voice import speech_handle as livekit_sh
except Exception:  # livekit already removed: only the in-repo implementation runs
    livekit_sh = None

IMPLS = [pytest.param(jarvis_sh, id="jarvis")]
if livekit_sh is not None:
    IMPLS.append(pytest.param(livekit_sh, id="livekit"))

impl = pytest.mark.parametrize("mod", IMPLS)


def make(mod, allow=True):
    return mod.SpeechHandle(
        speech_id="speech_t",
        allow_interruptions=allow,
        input_details=mod.InputDetails(modality="audio"),
    )


@impl
def test_interrupt_disallowed_raises_without_force(mod):
    async def run():
        h = make(mod, allow=False)
        with pytest.raises(RuntimeError, match="does not allow interruptions"):
            h.interrupt()
        return h.interrupted, h.done()

    assert asyncio.run(run()) == (False, False)


@impl
def test_interrupt_disallowed_forced_lands(mod):
    async def run():
        h = make(mod, allow=False)
        ret = h.interrupt(force=True, source="user_turn")
        out = (ret is h, h.interrupted, h.done(), h._interrupt_source)
        h._mark_done()
        return out

    assert asyncio.run(run()) == (True, True, False, "user_turn")


@impl
def test_interrupt_allowed_sets_interrupted_before_done(mod):
    async def run():
        h = make(mod)
        h.interrupt()
        out = (h.interrupted, h.done(), h._interrupt_source)
        h._mark_done()
        return (*out, h.done())

    assert asyncio.run(run()) == (True, False, "programmatic", True)


@impl
def test_interrupt_after_done_is_noop_even_when_disallowed(mod):
    async def run():
        h = make(mod, allow=False)
        h._mark_done()
        ret = h.interrupt()
        return ret is h, h.interrupted, h.done()

    assert asyncio.run(run()) == (True, False, True)


@impl
def test_allow_interruptions_cannot_be_disabled_once_interrupted(mod):
    async def run():
        h = make(mod)
        h.interrupt()
        with pytest.raises(RuntimeError, match="already interrupted"):
            h.allow_interruptions = False
        h.allow_interruptions = True
        out = h.allow_interruptions
        h._mark_done()
        return out

    assert asyncio.run(run()) is True


@impl
def test_wait_for_playout_resolves_on_done_and_await_returns_self(mod):
    async def run():
        h = make(mod)
        waiter = asyncio.ensure_future(h.wait_for_playout())
        await asyncio.sleep(0)
        pending_before = not waiter.done()
        h._mark_done()
        await asyncio.wait_for(waiter, 1)
        h2 = make(mod)
        h2._mark_done()
        awaited = await h2
        return pending_before, waiter.done(), awaited is h2

    assert asyncio.run(run()) == (True, True, True)


@impl
def test_done_callbacks_fire_once_and_late_callback_fires_soon(mod):
    async def run():
        h = make(mod)
        seen = []
        h.add_done_callback(lambda s: seen.append(("cb", s is h)))
        h._mark_done()
        h._mark_done()  # idempotent: must not refire
        await asyncio.sleep(0)
        h.add_done_callback(lambda s: seen.append(("late", s is h)))
        await asyncio.sleep(0)
        return seen

    assert asyncio.run(run()) == [("cb", True), ("late", True)]


@impl
def test_done_callback_errors_are_isolated_and_removal_works(mod):
    async def run():
        h = make(mod)
        seen = []

        def boom(_):
            raise ValueError("bad callback")

        removed = lambda s: seen.append("removed")  # noqa: E731
        h.add_done_callback(boom)
        h.add_done_callback(lambda s: seen.append("ok"))
        h.add_done_callback(removed)
        h.remove_done_callback(removed)
        h._mark_done()
        return seen

    assert asyncio.run(run()) == ["ok"]


@impl
def test_exception_requires_done_then_reports_error(mod):
    async def run():
        h = make(mod)
        try:
            h.exception()
            raised = False
        except asyncio.InvalidStateError:
            raised = True
        err = ValueError("llm failed")
        h._mark_done(err)
        return raised, h.exception() is err, h.done()

    assert asyncio.run(run()) == (True, True, True)


@impl
def test_scheduled_flag_and_interruption_hold_counting(mod):
    async def run():
        h = make(mod)
        before = h.scheduled
        h._mark_scheduled()
        h._mark_scheduled()  # idempotent
        out = [before, h.scheduled]
        h._hold_interruptions()
        h._hold_interruptions()
        out.append(h.allow_interruptions)
        h._release_interruptions()
        out.append(h.allow_interruptions)
        h._release_interruptions()
        out.append(h.allow_interruptions)
        h._mark_done()
        return out

    assert asyncio.run(run()) == [False, True, False, False, True]


@impl
def test_hold_does_not_block_forced_interrupt(mod):
    async def run():
        h = make(mod)
        h._hold_interruptions()
        with pytest.raises(RuntimeError):
            h.interrupt()
        h.interrupt(force=True)
        h._release_interruptions()
        out = (h.interrupted, h.allow_interruptions)
        h._mark_done()
        return out

    assert asyncio.run(run()) == (True, True)


@impl
def test_generation_authorization_and_wait(mod):
    async def run():
        h = make(mod)
        out = []
        try:
            await h._wait_for_generation()
        except RuntimeError:
            out.append("no_generation")
        h._authorize_generation()
        waiter = asyncio.ensure_future(h._wait_for_generation())
        await asyncio.sleep(0)
        out.append(waiter.done())
        h._mark_generation_done()
        await asyncio.wait_for(waiter, 1)
        out.append(waiter.done())
        h._mark_done()
        return out

    assert asyncio.run(run()) == ["no_generation", False, True]


@impl
def test_item_added_callbacks_and_chat_items(mod):
    async def run():
        h = make(mod)
        seen = []
        cb = lambda item: seen.append(item)  # noqa: E731
        h._add_item_added_callback(cb)
        h._item_added(["a", "b"])
        h._remove_item_added_callback(cb)
        h._item_added(["c"])
        out = (seen, list(h.chat_items))
        h._mark_done()
        return out

    assert asyncio.run(run()) == (["a", "b"], ["a", "b", "c"])


@impl
def test_step_ids_and_create(mod):
    async def run():
        return mod.SpeechHandle.create(), mod.SpeechHandle.create()

    h, other = asyncio.run(run())
    assert h.id.startswith("speech_") and len(h.id) == len("speech_") + 12
    assert h.num_steps == 1
    assert h._generation_id == f"{h.id}_1"
    assert h._parent_generation_id is None
    assert h.allow_interruptions is True
    assert h.input_details == mod.DEFAULT_INPUT_DETAILS
    assert other.id != h.id


@impl
def test_interrupt_timeout_forces_done(mod, monkeypatch):
    monkeypatch.setattr(mod, "INTERRUPTION_TIMEOUT", 0.01)

    async def run():
        h = make(mod)
        t = asyncio.ensure_future(asyncio.sleep(10))
        h._tasks.append(t)
        h.interrupt()
        await asyncio.wait_for(asyncio.shield(h._done_fut), 1)
        await asyncio.sleep(0)
        return h.done(), h.interrupted, t.cancelled()

    assert asyncio.run(run()) == (True, True, True)


@impl
def test_wait_if_not_interrupted_returns_on_interrupt(mod):
    async def run():
        h = make(mod)
        never = asyncio.get_running_loop().create_future()
        waiter = asyncio.ensure_future(h.wait_if_not_interrupted([never]))
        await asyncio.sleep(0)
        h.interrupt()
        await asyncio.wait_for(waiter, 1)
        # shielded futures are not cancelled along with the gather
        out = (waiter.done(), never.cancelled())
        h._mark_done()
        return out

    assert asyncio.run(run()) == (True, False)


@pytest.mark.skipif(livekit_sh is None, reason="livekit-agents not installed")
def test_parity_scripted_sequence_matches_livekit():
    def scenario(mod):
        async def run():
            h = make(mod, allow=False)
            obs = []
            try:
                h.interrupt()
            except RuntimeError as e:
                obs.append(str(e))
            h.interrupt(force=True, source="audio_activity")
            obs.append((h.interrupted, h.done(), h._interrupt_source))
            seen = []
            h.add_done_callback(lambda s: seen.append(s.id))
            h._mark_done()
            await asyncio.sleep(0)
            obs.append(seen)
            return obs

        return asyncio.run(run())

    assert scenario(jarvis_sh) == scenario(livekit_sh)
