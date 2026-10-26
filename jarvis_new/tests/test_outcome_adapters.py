from types import SimpleNamespace

import pytest

from action_outcome import ActionOutcome
from claim_guard import ClaimGuard, sdk_tool_result
from intent import fast_path, goals


@pytest.mark.parametrize("adapter", [fast_path, goals])
async def test_tuple_adapter_preserves_receipt_and_truth(adapter, monkeypatch):
    import claim_guard

    guard = ClaimGuard()
    guard.note_user("set volume to 40")
    monkeypatch.setattr(claim_guard, "GUARD", guard)
    receipt = ActionOutcome(
        "setting.volume",
        "failed",
        "started",
        "mismatched",
        "audio_service",
        evidence={"expected_pct": 40, "actual_pct": 70},
    )

    class Tools:
        async def set_volume(self, context, **kwargs):
            return receipt.result()

    owners = [Tools()] if adapter is goals else Tools()
    ok, say = await adapter.execute(
        owners, "set_volume", {"action": "set", "level": 40}
    )
    assert not ok and say == receipt.say
    assert guard.check("Done.").uncertain


def test_actual_sdk_serialized_result_is_checked():
    from livekit.agents.llm import FunctionCall
    from livekit.agents.voice.events import FunctionToolsExecutedEvent

    receipt = ActionOutcome(
        "url.launch", "launch_requested", "started", surface="owner_desktop"
    )
    from livekit.agents.llm.utils import make_function_call_output

    call = FunctionCall(name="open_url", call_id="test", arguments="{}")
    output = make_function_call_output(
        fnc_call=call, output=receipt.result(), exception=None
    ).fnc_call_out
    event = FunctionToolsExecutedEvent(
        function_calls=[call], function_call_outputs=[output]
    )
    g = ClaimGuard()
    g.note_user("open my link")
    for call, out in event.zipped():
        g.note_result(call.name, sdk_tool_result(out), is_error=out.is_error)
    assert g.check("The page is visible.").uncertain


def test_sdk_corrupt_or_nonjson_result_does_not_gain_proof():
    g = ClaimGuard()
    g.note_user("save my file")
    for value in ["not json", '{"ok":true}', "x" * 65537]:
        g.note_result("write_file", sdk_tool_result(SimpleNamespace(output=value)))
    assert g.check("Saved.").uncertain
