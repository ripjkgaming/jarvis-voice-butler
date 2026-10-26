import asyncio

import pytest

import action_guard
from action_outcome import ActionOutcome, parse_outcome
from system import core


@pytest.mark.parametrize(
    "state,check",
    [
        ("verified", "matched"),
        ("failed", "mismatched"),
        ("dispatched_unverified", "unavailable"),
    ],
)
async def test_cancelled_caller_never_releases_owned_dispatched_worker(
    monkeypatch, state, check
):
    loop = asyncio.get_running_loop()
    queued = []

    def submit(executor, fn, *args):
        future = loop.create_future()
        queued.append((future, fn, args))
        return future

    monkeypatch.setattr(loop, "run_in_executor", submit)
    args = {"action": "set", "level": 40}
    evidence = {"expected_pct": 40, "actual_pct": 40 if state == "verified" else 70}
    receipt = ActionOutcome(
        "setting.volume", state, "started", check, "audio_service", evidence=evidence
    )
    pending = asyncio.create_task(
        core._guarded_thread_action(
            "set_volume", args, "model", lambda: receipt.result(), "Done"
        )
    )
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert action_guard.claim("volume_set", {"level": 40}, "route")["ok"] is None
    future, fn, work_args = queued[0]
    future.set_result(fn(*work_args))
    await asyncio.sleep(0)
    duplicate = action_guard.claim("volume_set", {"level": 40}, "fast")
    assert duplicate is not None
    assert parse_outcome(duplicate["outcome"]) == receipt
    assert parse_outcome(action_guard.duplicate_reply(duplicate, "Done")) == receipt


def test_failed_sdk_event_never_authorizes_replay():
    from claim_guard import ClaimGuard, reprompt

    g = ClaimGuard()
    g.note_user("send my message")
    g.note_result("click", None, is_error=True)
    verdict = g.check("Done.")
    assert verdict and verdict.uncertain
    assert "Do not repeat" in reprompt(verdict, "send my message")
