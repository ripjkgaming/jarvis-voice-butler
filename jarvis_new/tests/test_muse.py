"""Muse delegation: config, brief-building, and the confirm gate.

src/system/muse.py (pure helpers) and src/system/muse_tools.py (the
read-back + confirm tools). Senders are injected so nothing real is sent.
"""

from __future__ import annotations

import pytest
from livekit.agents.llm import ToolError

from system import muse
from system.muse_tools import MuseTools


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_MUSE", "1")  # configured; see the opt-in tests below
    monkeypatch.delenv("JARVIS_MUSE_EMAIL", raising=False)
    monkeypatch.delenv("JARVIS_MUSE_CHAT", raising=False)


def test_muse_email_reads_env_and_validates(monkeypatch) -> None:
    assert muse.muse_email() == ""
    monkeypatch.setenv("JARVIS_MUSE_EMAIL", "muse@meta.com")
    assert muse.muse_email() == "muse@meta.com"
    monkeypatch.setenv("JARVIS_MUSE_EMAIL", "not-an-email")
    assert muse.muse_email() == ""


def test_muse_chat_default_and_override(monkeypatch) -> None:
    assert muse.muse_chat() == "Muse"
    monkeypatch.setenv("JARVIS_MUSE_CHAT", "Meta Muse")
    assert muse.muse_chat() == "Meta Muse"


def test_pick_channel(monkeypatch) -> None:
    assert muse.pick_channel("whatsapp") == "whatsapp"
    assert muse.pick_channel("wa") == "whatsapp"
    assert muse.pick_channel("email") == "email"
    assert muse.pick_channel("gmail") == "email"
    # auto: WhatsApp when no email is set, email once it is.
    assert muse.pick_channel("auto") == "whatsapp"
    monkeypatch.setenv("JARVIS_MUSE_EMAIL", "muse@meta.com")
    assert muse.pick_channel("auto") == "email"


def test_email_and_wa_briefs() -> None:
    subject, body = muse.email_brief("  research the  history of the jet engine.  ")
    assert subject == "Task for Muse: research the history of the jet engine"
    assert "research the history of the jet engine." in body
    assert "Jarvis" in body
    wa = muse.wa_brief("draft a poem about rain")
    assert "draft a poem about rain" in wa and "Muse" in wa


def test_blocklist_excludes_muse_chat(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_MUSE_CHAT", "Muse")
    import wa_autoreply

    assert "muse" in wa_autoreply.blocklist()


# --- confirm gate ---


def _tools():
    sent = {"email": [], "wa": []}

    def email_sender(to, subject, body):
        sent["email"].append((to, subject, body))

    async def wa_sender(name, text):
        sent["wa"].append((name, text))

    return MuseTools(email_sender=email_sender, wa_sender=wa_sender), sent


@pytest.mark.asyncio
async def test_whatsapp_delegation_needs_confirm_then_sends() -> None:
    tools, sent = _tools()
    proposed = await MuseTools.delegate_to_muse(  # type: ignore[arg-type]
        tools, None, task="research the jet engine", via="whatsapp"
    )
    assert "Muse" in proposed["say"] and "confirm" in proposed["say"].lower()
    assert sent == {"email": [], "wa": []}  # nothing sent on propose
    done = await MuseTools.confirm_muse_send(tools, None)  # type: ignore[arg-type]
    assert "Delegated to Muse" in done["say"]
    assert len(sent["wa"]) == 1 and "research the jet engine" in sent["wa"][0][1]


@pytest.mark.asyncio
async def test_email_delegation_uses_configured_address(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_MUSE_EMAIL", "muse@meta.com")
    tools, sent = _tools()
    await MuseTools.delegate_to_muse(  # type: ignore[arg-type]
        tools, None, task="summarise this paper", via="email"
    )
    await MuseTools.confirm_muse_send(tools, None)  # type: ignore[arg-type]
    assert len(sent["email"]) == 1
    to, subject, _body = sent["email"][0]
    assert to == "muse@meta.com" and subject.startswith("Task for Muse:")


@pytest.mark.asyncio
async def test_email_without_address_is_refused() -> None:
    tools, _ = _tools()
    with pytest.raises(ToolError):
        await MuseTools.delegate_to_muse(  # type: ignore[arg-type]
            tools, None, task="do a thing", via="email"
        )


@pytest.mark.asyncio
async def test_confirm_without_proposal_is_refused() -> None:
    tools, _ = _tools()
    with pytest.raises(ToolError):
        await MuseTools.confirm_muse_send(tools, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_confirm_burns_authorization() -> None:
    tools, sent = _tools()
    await MuseTools.delegate_to_muse(  # type: ignore[arg-type]
        tools, None, task="task one", via="whatsapp"
    )
    await MuseTools.confirm_muse_send(tools, None)  # type: ignore[arg-type]
    # A second confirm with no new proposal must not resend.
    with pytest.raises(ToolError):
        await MuseTools.confirm_muse_send(tools, None)  # type: ignore[arg-type]
    assert len(sent["wa"]) == 1


@pytest.mark.asyncio
async def test_empty_task_refused() -> None:
    tools, _ = _tools()
    with pytest.raises(ToolError):
        await MuseTools.delegate_to_muse(tools, None, task="   ")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_send_failure_surfaces_and_clears(monkeypatch) -> None:
    async def bad_wa(name, text):
        raise RuntimeError("chat not open")

    tools = MuseTools(wa_sender=bad_wa)
    await MuseTools.delegate_to_muse(  # type: ignore[arg-type]
        tools, None, task="task", via="whatsapp"
    )
    with pytest.raises(ToolError):
        await MuseTools.confirm_muse_send(tools, None)  # type: ignore[arg-type]
    # After a failed send the queue is cleared, not stuck.
    with pytest.raises(ToolError):
        await MuseTools.confirm_muse_send(tools, None)  # type: ignore[arg-type]


def test_muse_is_opt_in(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("JARVIS_MUSE", raising=False)
    monkeypatch.setattr(muse, "_MUSE_EMAIL_FILE", tmp_path / "none.txt")
    assert muse.enabled() is False
    # Not set up: no tools offered, and the chat isn't special-cased.
    assert MuseTools().tools == []
    import wa_autoreply

    assert "muse" not in wa_autoreply.blocklist()
    monkeypatch.setenv("JARVIS_MUSE_EMAIL", "muse@meta.com")
    assert muse.enabled() is True
    assert len(MuseTools().tools) == 2


@pytest.mark.asyncio
async def test_tools_refuse_when_not_set_up(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("JARVIS_MUSE", raising=False)
    monkeypatch.setattr(muse, "_MUSE_EMAIL_FILE", tmp_path / "none.txt")
    tools = MuseTools()
    with pytest.raises(ToolError, match="isn't set up"):
        await MuseTools.delegate_to_muse(tools, None, task="x")  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="isn't set up"):
        await MuseTools.confirm_muse_send(tools, None)  # type: ignore[arg-type]
