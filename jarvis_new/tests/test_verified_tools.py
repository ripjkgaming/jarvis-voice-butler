from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_audio_ctl import FakeSink

import action_guard
from action_outcome import parse_outcome
from system import audio_ctl, core
from tools import BrowserTools


@pytest.fixture(autouse=True)
def inline_executor(monkeypatch):
    async def guarded(tool, args, source, work, fallback):
        result = work()
        receipt = parse_outcome(result)
        action_guard.claim(tool, args, source)
        action_guard.settle(tool, args, source, receipt.accepted, outcome=receipt)
        return result

    monkeypatch.setattr(core, "_guarded_thread_action", guarded)
    monkeypatch.setattr(core, "log_action", lambda *a: None)


async def test_write_tool_confined_and_no_approval_bypass(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(core, "HOME_ROOT", tmp_path)
    target = tmp_path / "note"
    result = await core.SystemTools.write_file(
        core.SystemTools(), None, str(target), "abc"
    )
    assert parse_outcome(result).verified
    import phone_session

    monkeypatch.setattr(
        phone_session,
        "check_approval_effect",
        lambda *a, **k: (_ for _ in ()).throw(core.LocalSystemError("revoked")),
    )
    with pytest.raises(Exception, match="revoked"):
        await core.SystemTools.write_file(
            core.SystemTools(), None, str(tmp_path / "other"), "abc"
        )
    assert not (tmp_path / "other").exists()


async def test_backend_url_and_click_receipts(monkeypatch):
    browser = SimpleNamespace(
        open_url=AsyncMock(
            return_value={"url": "https://example.com", "tab": "main", "wall": None}
        ),
        click=AsyncMock(return_value={"url": "https://example.com", "tab": "main"}),
    )
    tools = BrowserTools(browser)
    result = await BrowserTools.open_url(tools, None, "https://example.com")
    assert parse_outcome(result).verified
    assert parse_outcome(result).surface == "backend_browser"
    result = await BrowserTools.click(tools, None, "harmless")
    assert not parse_outcome(result).verified
    assert parse_outcome(result).effect == "input.click"


async def test_volume_tool_keeps_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    sink = FakeSink(pct=20)
    original = audio_ctl.volume_result
    monkeypatch.setattr(
        audio_ctl,
        "volume_result",
        lambda *a, **k: original(*a, **k, run=sink.run, which=sink.which),
    )
    result = await core.SystemTools.set_volume(core.SystemTools(), None, "set", 40)
    assert parse_outcome(result).verified
    assert (
        action_guard.claim("volume_set", {"level": 40}, "route")["outcome"]
        == result["outcome"]
    )
