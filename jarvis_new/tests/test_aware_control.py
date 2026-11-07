"""Always Aware voice controls (PLAN.md §6): fast routes + spoken handlers."""

import datetime as dt
import time
from decimal import Decimal

import pytest

from aware import control, spans
from aware import log as alog
from aware import state as st
from aware import transactions as tx

NOW = time.mktime((2026, 11, 7, 14, 0, 0, 0, 0, -1))


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_AWARE", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    "text,want",
    [
        ("Always aware on", ("on", "")),
        ("Jarvis, turn on always aware.", ("on", "")),
        ("always-aware off", ("off", "")),
        ("pause awareness for an hour", ("pause", "for an hour")),
        ("Pause always aware until 5pm", ("pause", "until 5pm")),
        ("pause awareness", ("pause", "")),
        ("what did you see?", ("seen", "")),
        ("what am I doing", ("seen", "")),
        ("shadow mode on", ("shadow_on", "")),
        ("shadow mode off", ("shadow_off", "")),
        ("stop offering code fixes", ("mute", "code fixes")),
        ("you can offer code fixes again", ("unmute", "code fixes")),
        ("set a spend limit of fifty pounds", ("spend_limit", "fifty pounds")),
        ("set a spend limit of £50", ("spend_limit", "£50")),
        ("remove the spend limit", ("spend_limit_off", "")),
        ("what have you paid for this week", ("paid", "this week")),
        ("what did you send today", ("sent", " today")),
        ("awareness status", ("status", "")),
    ],
)
def test_parse_routes(text, want):
    got = control.parse(text)
    assert got is not None and got[0] == want[0] and got[1].strip() == want[1].strip()


@pytest.mark.parametrize(
    "text",
    ["what's the weather", "turn on the lights", "pause the music", "stop", "yes"],
)
def test_parse_leaves_other_speech(text):
    assert control.parse(text) is None


def test_on_off_and_kill(_home, monkeypatch):
    assert "on" in control.handle("on", now=NOW)
    assert st.load()["enabled"] is True
    assert "off" in control.handle("off", now=NOW).lower()
    assert st.load()["enabled"] is False
    st.kill_path().touch()
    assert "kill file" in control.handle("on", now=NOW)
    assert st.load()["enabled"] is False
    st.kill_path().unlink()
    monkeypatch.setenv("JARVIS_AWARE", "0")
    assert "JARVIS_AWARE=0" in control.handle("on", now=NOW)


def test_pause_for_and_until():
    control.handle("on", now=NOW)
    say = control.handle("pause", "for an hour", now=NOW)
    assert st.load()["paused_until"] == NOW + 3600 and "15:00" in say
    control.handle("pause", "for 30 minutes", now=NOW)
    assert st.load()["paused_until"] == NOW + 1800
    control.handle("pause", "until 5", now=NOW)
    assert time.localtime(st.load()["paused_until"]).tm_hour == 17
    assert st.status_word(now=NOW) == "paused"
    assert "how long" in control.handle("pause", "for a fortnight-ish", now=NOW)
    control.handle("resume", now=NOW)
    assert st.status_word(now=NOW) == "on"


def test_shadow_mute_spend():
    control.handle("shadow_on")
    assert st.load()["shadow"] is True
    control.handle("shadow_off")
    assert st.load()["shadow"] is False
    control.handle("mute", "code fixes")
    assert st.is_muted(st.load(), "code_fix")
    assert "again" in control.handle("unmute", "code fixes")
    assert not st.is_muted(st.load(), "code_fix")
    control.handle("mute", "the council website")
    assert st.is_muted(st.load(), "council website")
    assert "£50" in control.handle("spend_limit", "£50")
    assert Decimal(st.load()["spend_cap"]) == Decimal("50")
    assert "£50" in control.handle("spend_limit", "fifty pounds")
    assert "£250" in control.handle("spend_limit", "two hundred and fifty pounds")
    assert "What limit" in control.handle("spend_limit", "lots")
    control.handle("spend_limit_off")
    assert st.load()["spend_cap"] is None


def test_status_line():
    assert control.handle("status", now=NOW).startswith("Always Aware is off")
    control.handle("on", now=NOW)
    st.update(last_reason="sensitive_foreground")
    say = control.handle("status", now=NOW)
    assert "0 calls" in say and "sensitive foreground" in say


def test_seen_and_at():
    alog.write({"outcome": "seen", "summary": "Editing the HUD"}, now=NOW - 60)
    assert "Editing the HUD" in control.handle("seen", now=NOW)
    spans.update(
        {"outcome": "seen", "subject": "physics essay", "summary": "Writing"},
        NOW - 3600,
    )
    assert "physics essay" in control.handle("at", "1pm", now=NOW)
    assert "physics essay" in control.handle("at", "an hour ago", now=NOW)
    assert control.handle("at", "9pm yesterday-ish", now=NOW) == ""


def test_paid_and_sent():
    tx.record(
        run="r",
        kind="payment",
        host="scan.co.uk",
        counterparty="Scan",
        amount=Decimal("94.99"),
        currency="GBP",
        items=["SSD"],
        content="",
        offer_text="x",
        result="ok",
        confirmation_ref="1",
        now=NOW - 3600,
    )
    say = control.handle("paid", "this week", now=NOW)
    assert "Scan" in say and "£94.99" in say
    assert control.handle("sent", "today", now=NOW).startswith("Nothing sent today")


def test_takeover_commands_without_modules(monkeypatch):
    monkeypatch.setattr(control, "_lazy", lambda mod, attr: None)
    assert "Nothing" in control.handle("cancel")
    assert "nothing to undo" in control.handle("undo")
    assert "nothing to show" in control.handle("show")


def test_past_ts_variants():
    assert time.localtime(control.past_ts("3pm", NOW + 7200)).tm_hour == 15
    assert control.past_ts("half an hour ago", NOW) == NOW - 1800
    ts = control.past_ts("this morning", NOW)
    assert dt.datetime.fromtimestamp(ts).hour == 10
    assert time.localtime(control.past_ts("1", NOW)).tm_hour == 13


def test_mute_key_aliases(monkeypatch):
    assert control.mute_key("web forms") == "web_form"
    assert control.mute_key("to organise my files") == "file_organise"
    assert control.mute_key("downloads") == "download"
    monkeypatch.setattr(control, "_last_offer_kind", lambda: "test_fix")
    assert control.mute_key("that") == "test_fix"


def test_spend_limit_goes_through_payments_audit(_home):
    control.handle("spend_limit", "£40")
    from aware import payments

    cap, cur = payments.spend_cap()
    assert cap == Decimal("40") and cur == "GBP"
    assert "spend_cap" in (_home / "aware" / "audit.jsonl").read_text()


def test_route_yes_only_while_offer_pending(monkeypatch):
    from aware import offers

    assert control.route("yes go ahead") is None
    monkeypatch.setattr(offers, "pending", lambda now=None: {"id": "of-1", "text": "x"})
    assert control.route("yes, go ahead") == ("answer", "yes")
    assert control.route("never offer that") == ("answer", "never")


def test_route_cancel_only_while_takeover_runs(monkeypatch):
    assert control.route("stop") is None
    fake = type("T", (), {"current": staticmethod(lambda: "run-1")})
    real = control._lazy
    monkeypatch.setattr(
        control,
        "_lazy",
        lambda mod, attr: (
            getattr(fake, attr, None) if mod == "takeover" else real(mod, attr)
        ),
    )
    assert control.route("I've got it") == ("cancel", "")
    assert control.route("carry on") == ("carry_on", "")
    assert control.route("leave it there") == ("leave_there", "")
    assert control.route("always aware off") == ("off", "")


def test_answer_action_records_reply(monkeypatch):
    from aware import offers

    seen = {}

    def answer(offer_id, reply, now=None):
        seen["r"] = (offer_id, reply)
        return {
            "ok": True,
            "say": "Right away, Sir.",
            "plan": None,
            "answer": "yes",
            "reason": "",
        }

    monkeypatch.setattr(offers, "answer", answer)
    assert control.handle("answer", "yes") == "Right away, Sir."
    assert seen["r"] == ("", "yes")
    monkeypatch.setattr(
        offers, "answer", lambda *a, **k: {"ok": False, "reason": "expired"}
    )
    assert "lapsed" in control.handle("answer", "yes")
