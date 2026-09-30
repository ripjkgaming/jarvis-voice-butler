"""Hermetic tests for voice-driven HUD panels (IRONMAN_SPEC §6)."""

from __future__ import annotations

import datetime as dt

import pytest
from livekit.agents.llm import ToolError
from test_bridge import _get

import bridge
import hud_panels
import mail_log
from system.panel_tools import PanelTools


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    return tmp_path


def test_normalize() -> None:
    assert hud_panels.normalize("Email") == "mail"
    assert hud_panels.normalize("calendar panel") == "calendar"
    assert hud_panels.normalize("system stats") == "system"
    assert hud_panels.normalize("weather") is None


def test_show_hide_order() -> None:
    assert hud_panels.show("mail") == ["mail"]
    assert hud_panels.show("calendar") == ["mail", "calendar"]
    assert hud_panels.show("mail") == ["calendar", "mail"]  # re-show moves to the end
    assert hud_panels.hide("calendar") == ["mail"]
    assert hud_panels.show("all") == list(hud_panels.PANELS)
    assert hud_panels.hide("all") == []


def test_payload_only_open_panels_and_fail_soft(monkeypatch) -> None:
    mail_log.record("flagged", sender="Teacher", subject="Form due")
    hud_panels.show("mail")
    hud_panels.show("calendar")

    def broken(now):
        raise RuntimeError("no calendar")

    monkeypatch.setitem(hud_panels.GATHER, "calendar", broken)
    out = hud_panels.payload()
    assert out["visible"] == ["mail", "calendar"]
    assert out["panels"]["mail"]["items"][0] == {
        "who": "Teacher",
        "subject": "Form due",
        "kind": "flagged",
    }
    assert out["panels"]["calendar"] == {"items": [], "error": "unavailable"}
    assert "tasks" not in out["panels"]


def test_every_panel_gathers_on_empty_state(monkeypatch) -> None:
    from proactive.sources import calendar_source

    monkeypatch.setattr(calendar_source, "fetch", lambda now: [])
    now = dt.datetime(2026, 10, 1, 9).timestamp()
    for name, fn in hud_panels.GATHER.items():
        data = fn(now)
        assert isinstance(data, dict), name


@pytest.mark.asyncio
async def test_tools() -> None:
    tools = PanelTools()
    assert (await PanelTools.show_panel(tools, None, panel="email"))[
        "say"
    ] == "Mail is up."  # type: ignore[arg-type]
    assert (await PanelTools.show_panel(tools, None, panel="everything"))[
        "say"
    ] == "All panels up."  # type: ignore[arg-type]
    assert (await PanelTools.hide_panel(tools, None, panel="calendar"))[
        "say"
    ] == "Calendar hidden."  # type: ignore[arg-type]
    assert "calendar" not in hud_panels.visible()
    with pytest.raises(ToolError):
        await PanelTools.show_panel(tools, None, panel="weather")  # type: ignore[arg-type]
    await PanelTools.hide_panel(tools, None)  # type: ignore[arg-type]
    assert hud_panels.visible() == []


def test_bridge_route() -> None:
    hud_panels.show("drafts")
    server, _ = bridge._run_in_thread()
    try:
        code, body = _get(server, "/panels")
        assert code == 200 and body["visible"] == ["drafts"]
        assert body["panels"]["drafts"] == {"items": []}
    finally:
        server.shutdown()
        server.server_close()
