"""The WhatsApp reply rules: one-hour wait, groups, one note, exams, tone."""

from __future__ import annotations

import datetime as dt

import pytest

import exams
import notify
import wa_autoreply as war
import wa_mimic

NOW = dt.datetime(2026, 10, 8, 10, 45).timestamp()  # during Physics P4 below


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jh"))
    monkeypatch.setenv("JARVIS_OWNER_PHONE", "+65 8753 4735")
    for var in (
        "JARVIS_WA_AUTOREPLY",
        "JARVIS_WA_RUDE",
        "JARVIS_WA_WAIT_MIN",
        "JARVIS_WA_BLOCK",
        "JARVIS_WA_MIMIC",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(wa_mimic, "DEFAULT_MIMIC", ())
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"route": "test"})
    return tmp_path


def m(text, me=False, sender="Kabir", when=""):
    return {"text": text, "me": me, "sender": sender, "when": when}


class WA:
    def __init__(self, chats, reads):
        self.chats, self.reads, self.opened, self.sends = chats, reads, [], []

    async def list_chats(self, n):
        return self.chats

    async def read_chat(self, name, n):
        self.opened.append(name)
        return self.reads[name]

    async def send_chat(self, name, text):
        self.sends.append((name, text))
        return {"ok": True}


def asker(
    reply="Hello! This is Jarvis, my master's automated assistant. He'll get back to you.",
):
    def ask(prompt):
        ask.prompts.append(prompt)
        return {
            "reply": reply,
            "rude": False,
            "disengage": False,
            "for_owner": "hi",
        }, None

    ask.prompts = []
    return ask


def dm(msgs, header="Kabir\ncontact info"):
    return {"ok": True, "header": header, "messages": msgs}


# --- one-hour wait (decided from the list, chat not opened) ---


@pytest.mark.asyncio
async def test_waits_an_hour_without_opening_the_chat(home) -> None:
    now = dt.datetime(2026, 10, 9, 18, 0).timestamp()  # no exam
    wa = WA(
        [{"name": "Kabir", "unread": 1, "time": "17:30"}], {"Kabir": dm([m("u free?")])}
    )
    ask = asker()
    out = await war.run(wa=wa, ensure=lambda: True, now=now, ask=ask)
    assert out == ["Kabir: waiting (31 min left)"]
    assert wa.opened == [] and wa.sends == []  # still unread on his phone
    out = await war.run(wa=wa, ensure=lambda: True, now=now + 31 * 60, ask=ask)
    assert "Kabir: sent" in out and wa.opened == ["Kabir"]


@pytest.mark.asyncio
async def test_read_on_phone_resets_the_clock(home) -> None:
    now = dt.datetime(2026, 10, 9, 18, 0).timestamp()
    wa = WA(
        [{"name": "Kabir", "unread": 1, "time": "17:30"}], {"Kabir": dm([m("u free?")])}
    )
    await war.run(wa=wa, ensure=lambda: True, now=now, ask=asker())
    wa.chats = [{"name": "Kabir", "unread": 0, "time": "17:30"}]
    await war.run(wa=wa, ensure=lambda: True, now=now + 60, ask=asker())
    assert "Kabir" not in war.load_state()["unread_since"]


def test_list_time() -> None:
    now = dt.datetime(2026, 10, 9, 18, 0).timestamp()
    assert war.list_time({"time": "17:30"}, now) == now - 30 * 60
    assert war.list_time({"time": "Yesterday"}, now) == now - 86400
    assert war.list_time({"time": ""}, now) == now


# --- groups ---


def test_group_mentions(home) -> None:
    assert war.group_mentions_owner([m("rudra u coming?")])
    assert war.group_mentions_owner([m("@Rudra Karthik check this")])
    assert war.group_mentions_owner([m("someone ping him")])
    assert war.group_mentions_owner([m("@6587534735 you there")])
    assert not war.group_mentions_owner([m("who's bringing snacks")])
    assert not war.group_mentions_owner([m("pinging the server")])


@pytest.mark.asyncio
async def test_group_without_mention_is_ignored(home, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_WA_WAIT_MIN", "0")
    wa = WA(
        [],
        {
            "Class 10B": dm(
                [m("homework is page 4")], header="Class 10B\nclick here for group info"
            )
        },
    )
    ask = asker()
    out = await war.handle_chat(
        {"name": "Class 10B"}, wa, {"chats": {}}, NOW + 86400, True, ask
    )
    assert out == "skip-group-no-mention" and ask.prompts == []


# --- one note, no takeover ---


def test_replied_since_owner() -> None:
    sent = ["Hello! This is Jarvis, my master's automated assistant."]
    assert war.replied_since_owner([m("hi"), m(sent[0], me=True), m("ok and?")], sent)
    assert not war.replied_since_owner(
        [m("hi"), m(sent[0], me=True), m("yo", me=True), m("?")], sent
    )
    assert not war.replied_since_owner([m("hi")], sent)


@pytest.mark.asyncio
async def test_one_note_until_sir_writes(home, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_WA_WAIT_MIN", "0")
    later = NOW + 86400  # no exam
    wa = WA([], {"Kabir": dm([m("u free?")])})
    state = {"chats": {}}
    assert (
        await war.handle_chat({"name": "Kabir"}, wa, state, later, True, asker())
        == "sent"
    )
    note = wa.sends[0][1]
    wa.reads["Kabir"] = dm([m("u free?"), m(note, me=True), m("ok but when??")])
    ask = asker()
    out = await war.handle_chat({"name": "Kabir"}, wa, state, later + 600, True, ask)
    assert out == "skip-waiting-for-owner" and ask.prompts == [] and len(wa.sends) == 1


# --- tone ---


def test_helpful_polite_by_default(monkeypatch) -> None:
    text = war.reply_system()
    assert "NOT standing in for him" in text and "always polite" in text
    assert "QUICK to turn rude" not in text
    monkeypatch.setenv("JARVIS_WA_RUDE", "1")
    assert "QUICK to turn rude" in war.reply_system()


# --- exams ---


@pytest.mark.asyncio
async def test_exam_reply_goes_out_at_once_and_says_so(home, monkeypatch) -> None:
    exams.add(
        {"title": "Physics P4", "date": "2026-10-08", "start": "10:30", "end": "11:30"}
    )
    real = exams.current_exam
    monkeypatch.setattr(
        exams, "current_exam", lambda now=None: real(dt.datetime.fromtimestamp(NOW))
    )
    wa = WA(
        [{"name": "Kabir", "unread": 1, "time": "10:44"}], {"Kabir": dm([m("u free?")])}
    )
    ask = asker(
        "Hello! This is Jarvis. He's in an exam right now until 11:30, wish him luck! He'll get back to you after."
    )
    out = await war.run(wa=wa, ensure=lambda: True, now=NOW, ask=ask)
    assert "Kabir: sent" in out  # no one-hour wait during an exam
    assert "owner_status: in an exam (Physics P4) until 11:30" in ask.prompts[0]
    assert "wish him luck" in war.reply_system()


def test_mimic_prompt_carries_exam_status(home) -> None:
    prompt = wa_mimic.build_prompt(
        "Raphael",
        False,
        [m("yo", sender="Raphael")],
        "",
        [],
        "10:45",
        status="in an exam (Physics P4) until 11:30",
    )
    assert "owner_status: in an exam (Physics P4) until 11:30" in prompt
    assert "wish me luck" in wa_mimic.system_prompt()
