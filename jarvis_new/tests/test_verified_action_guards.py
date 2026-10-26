import action_guard as ag
from action_outcome import ActionOutcome, parse_outcome
from claim_guard import ClaimGuard, fixed_line, reprompt


def test_uncertain_outcome_suppresses_duplicate_and_keeps_speech():
    ag.claim("set_volume", {"action": "set", "level": 40}, "model", request_id="one")
    receipt = ActionOutcome(
        "setting.volume",
        "failed",
        "started",
        "mismatched",
        "audio_service",
        evidence={"expected_pct": 40, "actual_pct": 70},
    )
    ag.settle(
        "set_volume",
        {"action": "set", "level": 40},
        "model",
        False,
        request_id="one",
        outcome=receipt,
    )
    duplicate = ag.claim("volume_set", {"level": 40}, "route", request_id="one")
    assert duplicate is not None
    result = ag.duplicate_reply(duplicate, "Done")
    assert parse_outcome(result) == receipt
    assert "Done" not in result["say"] and "already done" not in result["note"].lower()


def test_launch_duplicate_is_not_open_proof():
    ag.claim("open_app", {"app": "brave"}, "route", request_id="one")
    ag.settle("open_app", {"app": "brave"}, "route", True, "Opened", request_id="one")
    dup = ag.claim("open_app", {"app": "youtube"}, "model", request_id="one")
    assert "not verified" in ag.duplicate_reply(dup, "Done")["say"]


def guard(tool, result, text="open my link"):
    g = ClaimGuard()
    g.note_user(text)
    g.note_result(tool, result)
    return g


def test_launch_never_backs_visibility_or_loaded():
    receipt = ActionOutcome(
        "url.launch", "launch_requested", "started", surface="owner_desktop"
    ).result()
    g = guard("open_url", receipt)
    for say in [
        "Done, Sir.",
        "I've opened the page.",
        "The page is visible.",
        "The page has loaded.",
    ]:
        v = g.check(say)
        assert v is not None, say
        assert "Do not repeat" in reprompt(v, "open my link")
        assert "Nothing actually happened" not in fixed_line(v)
    assert g.check(receipt["say"]) is None


def test_verified_file_does_not_back_window_or_delivery():
    receipt = ActionOutcome(
        "file.write",
        "verified",
        "started",
        "matched",
        "filesystem",
        evidence={"bytes_matched": True, "bytes": 3},
    ).result()
    g = guard("write_file", receipt, "save my file")
    assert g.check("I've saved it.") is None
    assert g.check("I've opened the page.") is not None
    assert g.check("I've delivered it.") is not None


def test_click_and_nonerror_legacy_do_not_back_done():
    g = guard(
        "click",
        ActionOutcome(
            "input.click", "dispatched_unverified", "started", surface="backend_browser"
        ).result(),
        "send my message",
    )
    assert g.check("Delivered.") is not None
    assert g.check("Done.") is not None
    g = ClaimGuard()
    g.note_user("open my link")
    g.note_tool("open_url", True)
    assert g.check("I've opened it.") is not None


def test_verified_volume_wrong_spoken_target_rejected():
    result = ActionOutcome(
        "setting.volume",
        "verified",
        "started",
        "matched",
        "audio_service",
        evidence={"expected_pct": 40, "actual_pct": 40},
    ).result()
    g = guard("set_volume", result, "set volume to 40")
    assert g.check("I've set the volume to 40 percent.") is None
    assert g.check("I've set the volume to 70 percent.") is not None


def test_volume_receipt_cannot_back_pause_or_wrong_direction():
    receipt = ActionOutcome(
        "setting.volume",
        "verified",
        "started",
        "matched",
        "audio_service",
        evidence={"before_pct": 70, "expected_pct": 40, "actual_pct": 40},
    ).result()
    g = guard("set_volume", receipt, "turn volume down")
    assert g.check("I turned it down.") is None
    assert g.check("I turned it up.") is not None
    assert g.check("Paused.") is not None
    assert g.check("Volume 70 percent.") is not None
