"""Claim guard: a spoken "done" needs a successful matching tool call."""

from __future__ import annotations

import pytest

import claim_guard as cg
from claim_guard import ClaimGuard


@pytest.mark.parametrize(
    "text,cat",
    [
        ("I've approved all the drafts.", "send"),
        ("All approved, Sir.", "send"),
        ("Done, Sir.", "done"),
        ("Paused.", "media"),
        ("I turned it down.", "media"),
        ("I've turned the volume down.", "media"),
        ("It's muted now.", "media"),
        ("I've opened the drafts.", "window"),
        ("I've deleted it.", "delete"),
        ("I've scheduled it for 9am.", "create"),
    ],
)
def test_claims_detected(text, cat) -> None:
    assert cg.claim_category(text) == cat


@pytest.mark.parametrize(
    "text",
    [
        "I couldn't approve them.",
        "I haven't sent anything yet.",
        "Shall I approve them all?",
        "Once approved they will go out.",
        "The weather is nice today.",
        "Opening your drafts now.",
        "Volume 40 percent.",
    ],
)
def test_non_claims_ignored(text) -> None:
    assert cg.claim_category(text) is None


def _guard():
    t = {"now": 100.0}
    g = ClaimGuard(clock=lambda: t["now"])
    return g, t


def test_unbacked_claim_is_flagged() -> None:
    g, _ = _guard()
    g.note_user("open drafts and approve all")
    g.note_tool("open_drafts_ui", True)  # opened, but nothing approved
    v = g.check("Done, I've approved all of them.")
    assert v is not None and v.category == "send" and v.reason == "no-tool"


def test_backed_claim_passes() -> None:
    g, _ = _guard()
    g.note_user("approve all my drafts")
    g.note_tool("draft_approve_all", True)
    assert g.check("I've approved all of them.") is None


def test_failed_tool_then_success_claim_is_flagged() -> None:
    g, _ = _guard()
    g.note_user("pause the music")
    g.note_tool("media_control", False)
    v = g.check("Paused.")
    assert v is not None and v.reason == "tool-failed" and "media_control" in v.detail


def test_chitchat_and_proactive_speech_not_flagged() -> None:
    g, t = _guard()
    g.note_user("how are you today")
    assert g.check("Done, Sir.") is None
    g.note_user("pause the music")
    t["now"] += 500  # stale: this speech is not answering that request
    assert g.check("Paused.") is None


def test_corrections_are_bounded() -> None:
    g, _ = _guard()
    g.note_user("pause the music")
    assert [g.next_correction() for _ in range(4)] == [1, 2, 0, 0]
    g.note_user("pause it again")  # a new turn resets the budget
    assert g.next_correction() == 1


def test_disabled_by_env(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_CLAIM_GUARD", "0")
    assert cg.enabled() is False


def test_reprompt_and_fixed_line() -> None:
    g, _ = _guard()
    g.note_user("approve all")
    v = g.check("I've approved them all.")
    assert "did NOT happen" in cg.reprompt(v, "approve all")
    assert "wasn't" in cg.fixed_line(v)
