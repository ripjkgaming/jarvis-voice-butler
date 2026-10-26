import pytest

from action_outcome import ActionOutcome, parse_outcome


def test_verified_requires_matching_effect_evidence():
    with pytest.raises(ValueError):
        ActionOutcome(
            "url.launch",
            "verified",
            "started",
            "matched",
            "owner_desktop",
            evidence={"exit_code": 0},
        )
    with pytest.raises(ValueError):
        ActionOutcome("file.write", "verified", "started", "unavailable", "filesystem")
    with pytest.raises(ValueError):
        ActionOutcome(
            "input.click", "verified", "started", "matched", "backend_browser"
        )


def test_minimal_file_evidence_roundtrips():
    outcome = ActionOutcome(
        "file.write",
        "verified",
        "started",
        "matched",
        "filesystem",
        evidence={"bytes_matched": True, "bytes": 3},
    )
    result = outcome.result()
    assert result["ok"] and result["say"]
    assert parse_outcome(result) == outcome
    assert result["outcome"]["version"] == 1
    assert "action_id" in result["outcome"]


def test_launch_is_accepted_but_not_verified():
    outcome = ActionOutcome(
        "url.launch", "launch_requested", "started", surface="owner_desktop"
    )
    assert outcome.result()["ok"]
    assert not outcome.verified
    assert "not visually verified" in outcome.say
    assert not outcome.retry_safe


@pytest.mark.parametrize(
    "state,dispatch,check",
    [
        ("failed", "started", "mismatched"),
        ("dispatched_unverified", "unknown", "unavailable"),
    ],
)
def test_uncertainty_is_not_retry_authority(state, dispatch, check):
    assert not ActionOutcome(
        "file.write", state, dispatch, check, "filesystem"
    ).retry_safe


def test_bad_or_legacy_result_fails_closed():
    assert parse_outcome({"ok": True}) is None
    assert parse_outcome({"outcome": {"version": 999, "state": "verified"}}) is None
    assert (
        parse_outcome(
            {
                "outcome": ActionOutcome("file.write", "blocked").to_dict()
                | {"evidence": {"text": "private"}}
            }
        )
        is None
    )


def test_evidence_never_accepts_content_or_paths():
    with pytest.raises(ValueError):
        ActionOutcome(
            "file.write",
            "failed",
            evidence={"path": "/home/personal", "text": "private"},
        )


def test_setting_exact_target_and_unknown_mute():
    with pytest.raises(ValueError):
        ActionOutcome(
            "setting.volume",
            "verified",
            "started",
            "matched",
            "audio_service",
            evidence={"expected_pct": 40, "actual_pct": 70},
        )
    with pytest.raises(ValueError):
        ActionOutcome(
            "setting.mute",
            "verified",
            "started",
            "matched",
            "audio_service",
            evidence={"expected_muted": False, "actual_muted": None},
        )


def test_receipt_evidence_is_immutable():
    outcome = ActionOutcome(
        "file.write",
        "verified",
        "started",
        "matched",
        "filesystem",
        evidence={"bytes_matched": True, "bytes": 3},
    )
    with pytest.raises(TypeError):
        outcome.evidence["bytes_matched"] = False
