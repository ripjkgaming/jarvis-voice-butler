import pytest
from livekit.agents.llm import ToolError

from system import whatsapp as wa
from system.daily import DailyTools


def test_clean_strips_icon_bleed() -> None:
    assert wa.clean("Hello  world") == "Hello world"
    assert wa.clean("  hi   there ") == "hi there"


def test_parse_chat_rows_strips_unread_bleed() -> None:
    rows = [
        {"t": "2 unread messagesMum", "d": "10:02", "s": "hello", "u": "2 unread"},
        {"t": "Dad", "d": "09:00", "s": "ok", "u": ""},
        {"t": "  ", "d": "", "s": "", "u": ""},
    ]
    out = wa.parse_chat_rows(rows)
    assert [c["name"] for c in out] == ["Mum", "Dad"]
    assert out[0]["unread"] == 2
    assert out[1]["unread"] == 0


def test_parse_sender() -> None:
    sender, when = wa.parse_sender("[10:07 pm, 11/09/2026] Feen: ")
    assert sender == "Feen"
    assert "10:07" in when
    assert wa.parse_sender("garbage") == ("", "")


def test_parse_messages_marks_owner_via_tail() -> None:
    rows = [
        {
            "pre": "[10:07 pm, 11/09/2026] Feen: ",
            "text": "hi",
            "meta": "",
            "tailOut": True,
            "me": False,
        },
        {
            "pre": "[10:08 pm, 11/09/2026] Feen: ",
            "text": "again",
            "meta": "",
            "tailOut": False,
            "me": False,
        },
        {
            "pre": "[10:09 pm, 11/09/2026] Mum: ",
            "text": "hello",
            "meta": "",
            "tailOut": False,
            "me": False,
        },
    ]
    out = wa.parse_messages(rows)
    assert [m["me"] for m in out] == [True, True, False]
    assert out[0]["sender"] == "Feen"


def test_parse_messages_skips_media_rows() -> None:
    rows = [{"pre": "", "text": "   ", "meta": "", "tailOut": False, "me": False}]
    assert wa.parse_messages(rows) == []


def test_match_chat_exact_then_substring_then_fuzzy() -> None:
    chats = [{"name": "Mum"}, {"name": "Family Group"}]
    assert wa.match_chat("mum", chats)["name"] == "Mum"
    assert wa.match_chat("family", chats)["name"] == "Family Group"
    assert wa.match_chat("famili grup", chats)["name"] == "Family Group"
    assert wa.match_chat("nobody here xyz", chats) is None


def test_daily_tools_register_whatsapp_chats() -> None:
    ids = [tool.id for tool in DailyTools().tools]
    assert "whatsapp_chats" in ids
    assert "whatsapp_read" in ids
    assert "whatsapp_draft" in ids


@pytest.mark.asyncio
async def test_whatsapp_status_hint_when_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.daily.wa_alive", lambda: False)
    result = await DailyTools.whatsapp_status(DailyTools(), None)  # type: ignore[arg-type]
    assert "remote-debugging-port=9223" in result["say"]


@pytest.mark.asyncio
async def test_whatsapp_chats_lists_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.daily.wa_alive", lambda: True)

    async def fake_list(limit: int = 20):
        return [
            {"name": "Mum", "time": "10:02", "snippet": "hi", "unread": 2},
            {"name": "Dad", "time": "09:00", "snippet": "ok", "unread": 0},
        ]

    monkeypatch.setattr("system.daily._wa_list_chats", fake_list)
    result = await DailyTools.whatsapp_chats(DailyTools(), None)  # type: ignore[arg-type]
    assert "Mum" in result["say"]
    assert len(result["chats"]) == 2


@pytest.mark.asyncio
async def test_whatsapp_read_returns_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.daily.wa_alive", lambda: True)

    async def fake_read(chat: str, n: int = 10):
        return {
            "ok": True,
            "name": "Mum",
            "header": "Mum",
            "is_group": False,
            "messages": [
                {"me": False, "sender": "Mum", "text": "dinner at six?", "meta": ""},
                {"me": True, "sender": "", "text": "yes!", "meta": ""},
            ],
        }

    monkeypatch.setattr("system.daily._wa_read_chat", fake_read)
    result = await DailyTools.whatsapp_read(  # type: ignore[arg-type]
        DailyTools(), None, chat="Mum", n=5
    )
    assert "dinner at six" in result["say"]
    assert "marked read" in result["say"]


@pytest.mark.asyncio
async def test_whatsapp_read_empty_chat_falls_back_to_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr("system.daily.wa_alive", lambda: True)

    async def fake_list(limit: int = 20):
        return [{"name": "Mum", "time": "now", "snippet": "", "unread": 0}]

    monkeypatch.setattr("system.daily._wa_list_chats", fake_list)
    result = await DailyTools.whatsapp_read(DailyTools(), None, chat="")  # type: ignore[arg-type]
    assert "Mum" in result["say"]


@pytest.mark.asyncio
async def test_whatsapp_tools_refuse_when_not_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    with pytest.raises(ToolError, match="only available"):
        await DailyTools.whatsapp_chats(DailyTools(), None)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="only available"):
        await DailyTools.whatsapp_read(DailyTools(), None, chat="Mum")  # type: ignore[arg-type]
