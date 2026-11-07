"""Always Aware offer policy (PLAN.md §3.2-3.3): caps, cooldown, holds,
shadow, learning, exact send/payment wording. Fake speaker, no real voice."""

import json

import pytest

from aware import feasibility, offers
from aware import state as st
from aware.types import (
    Consequential,
    GateResult,
    OfferAnswer,
    Opportunity,
    PlanStep,
    TakeoverPlan,
)

NOW = 1_800_000_000.0  # fixed, mid-day local time irrelevant (ctx given)
CALM = {
    "guest": False,
    "private": False,
    "locked": False,
    "in_call": False,
    "focus": False,
    "school": False,
    "dnd": False,
    "quiet_hours": False,
    "idle_s": 60,
}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(offers, "POLL_S", 0.001)
    offers._DEFERRED.clear()
    offers.set_speaker(None)
    offers.set_listener(None)
    yield
    offers.set_speaker(None)
    offers.set_listener(None)


def opp(**kw):
    base = {
        "id": "o1",
        "kind": "code",
        "summary": "fix auth test",
        "evidence": {"text": "pytest: 1 failed in test_auth"},
        "est_minutes": 5,
        "task_kind": "test_fix",
        "title": "Fix the failing auth test",
        "subject": "jarvis_new",
        "outcome": "the auth tests pass",
    }
    base.update(kw)
    return Opportunity(**base)


def passed(actions=None, kind="code"):
    plan = TakeoverPlan(
        id="p1",
        opp_id="o1",
        kind=kind,
        task_kind="test_fix",
        title="Fix the failing auth test",
        steps=[PlanStep("fix", "claude_code")],
        success_criteria=["tests pass"],
        time_budget_s=450,
        est_minutes=5,
        actions=list(actions or []),
    )
    return GateResult(True, None, [], plan, opp_id="o1")


def failed():
    return GateResult(False, "checklist", ["auth:login"], None)


class Speaker:
    def __init__(self, reply=None):
        self.said = []
        self.reply = reply

    async def __call__(self, text):
        self.said.append(text)
        if self.reply is not None and len(self.said) == 1:
            assert offers.answer("", self.reply)["ok"]


async def go(o=None, g=None, *, now=NOW, ctx=None, timeout=0.05):
    return await offers.maybe_offer(
        o or opp(),
        g or passed(),
        now=now,
        ctx=CALM if ctx is None else ctx,
        answer_timeout=timeout,
    )


# ------------------------------------------------------------------- basics
async def test_gate_fail_never_offers():
    spk = Speaker("yes")
    offers.set_speaker(spk)
    out = await go(g=failed())
    assert (out.status, out.reason) == ("suppressed", "gate_fail")
    assert spk.said == [] and offers.history() == []


async def test_yes_is_accepted_and_plan_gets_offer_text():
    spk = Speaker("go ahead")
    offers.set_speaker(spk)
    g = passed()
    out = await go(g=g)
    assert out.status == "accepted" and out.spoken
    assert g.plan.offer_text == out.text == spk.said[0]
    assert spk.said[0].startswith("Sir, I can fix the failing auth test in jarvis_new")
    assert spk.said[0].endswith("Shall I take over?")
    assert len(spk.said[0].split()) <= 30
    row = offers.history()[0]
    assert row["answer"] == "yes" and row["kind"] == "test_fix"
    assert offers.pending(NOW) is None  # cleaned up


async def test_no_is_declined():
    offers.set_speaker(Speaker("not now"))
    out = await go()
    assert (out.status, out.reason) == ("declined", "no")


async def test_silence_is_soft_decline():
    offers.set_speaker(Speaker())
    out = await go()
    assert (out.status, out.reason) == ("declined", "silent")
    assert offers.history()[0]["answer"] == "silent"


async def test_never_mutes_kind_until_reenabled():
    spk = Speaker("never offer that again")
    offers.set_speaker(spk)
    out = await go()
    assert out.reason == "never"
    assert st.load()["muted"]["test_fix"] == 0
    assert len(spk.said) == 1  # the voice agent speaks answer()'s ack, not the sidecar
    other = opp(evidence={"text": "different failure"})
    out = await go(other, now=NOW + 3 * 86400)
    assert (out.status, out.reason) == ("suppressed", "muted")
    offers.unmute("test_fix")
    offers.set_speaker(Speaker("yes"))
    assert (await go(other, now=NOW + 3 * 86400)).status == "accepted"


async def test_no_speaker_suppressed():
    out = await go()
    assert (out.status, out.reason) == ("suppressed", "no_speaker")


async def test_listener_is_called():
    seen = []

    async def listen(oid, text):
        seen.append(oid)
        offers.answer(oid, OfferAnswer.YES)

    offers.set_speaker(Speaker())
    offers.set_listener(listen)
    out = await go()
    assert out.status == "accepted" and seen == [out.offer_id]


# --------------------------------------------------------------- rate/cooldown
async def test_rate_limit_30_min_and_cooldown_24h():
    offers.set_speaker(Speaker("no"))
    await go()
    other = opp(evidence={"text": "another failure"})
    out = await go(other, now=NOW + 29 * 60)
    assert (out.status, out.reason) == ("deferred", "rate_30m")
    # same fingerprint is never re-offered within 24 h
    out = await go(now=NOW + 2 * 3600)
    assert (out.status, out.reason) == ("suppressed", "cooldown")
    offers.set_speaker(Speaker("yes"))
    assert (await go(now=NOW + 25 * 3600)).status == "accepted"


async def test_fingerprint_ignores_volatile_tick_fields():
    a = opp(
        evidence={
            "text": "pytest: 1 failed",
            "tick_id": "t1",
            "context": {"idle_s": 40},
        }
    )
    b = opp(evidence={"text": "pytest:  1 failed", "tick_id": "t2"})
    assert a.fingerprint() == b.fingerprint()


def test_daily_cap_six():
    day0 = offers._start_of_day(NOW)
    hist = [{"ts": day0 + 60 + i * 1900, "fingerprint": f"f{i}"} for i in range(6)]
    now = day0 + 60 + 6 * 1900
    res = offers.check(opp(), passed(), state=st.load(), hist=hist, ctx=CALM, now=now)
    assert res == ("deferred", "daily_cap")
    res = offers.check(
        opp(), passed(), state=st.load(), hist=hist[:5], ctx=CALM, now=now
    )
    assert res is None


# ------------------------------------------------------------------- holds
@pytest.mark.parametrize("flag", ["in_call", "focus", "school", "dnd", "quiet_hours"])
async def test_holds_defer(flag):
    spk = Speaker("yes")
    offers.set_speaker(spk)
    out = await go(ctx={**CALM, flag: True})
    assert (out.status, out.reason) == ("deferred", flag)
    assert spk.said == []


@pytest.mark.parametrize("flag", ["guest", "private", "locked"])
async def test_guest_private_locked_suppress(flag):
    offers.set_speaker(Speaker("yes"))
    out = await go(ctx={**CALM, flag: True})
    assert (out.status, out.reason) == ("suppressed", flag)


@pytest.mark.parametrize(
    "idle,reason", [(5, "busy"), (900, "away"), (None, "pause_unknown")]
)
async def test_natural_pause_required(idle, reason):
    offers.set_speaker(Speaker("yes"))
    out = await go(ctx={**CALM, "idle_s": idle})
    assert (out.status, out.reason) == ("deferred", reason)


async def test_deferral_expires_after_10_min():
    offers.set_speaker(Speaker("yes"))
    held = {**CALM, "in_call": True}
    assert (await go(ctx=held)).status == "deferred"
    assert (await go(ctx=held, now=NOW + 5 * 60)).status == "deferred"
    out = await go(ctx=held, now=NOW + 11 * 60)
    assert (out.status, out.reason) == ("suppressed", "expired")


async def test_opportunity_context_flags_apply():
    offers.set_speaker(Speaker("yes"))
    out = await go(opp(evidence={"text": "x", "context": {"focus": True}}))
    assert (out.status, out.reason) == ("deferred", "focus")


async def test_one_pending_at_a_time():
    offers.set_speaker(Speaker("yes"))
    st.write_private(
        offers._pending_path("of-other"),
        json.dumps(
            {"id": "of-other", "created": NOW, "expires": NOW + 90, "answer": None}
        ),
    )
    out = await go()
    assert (out.status, out.reason) == ("deferred", "offer_pending")


# ------------------------------------------------------------------- shadow
async def test_shadow_logs_without_speaking():
    spk = Speaker("yes")
    offers.set_speaker(spk)
    offers.set_shadow(True)
    out = await go()
    assert (out.status, out.reason) == ("suppressed", "shadow")
    assert spk.said == [] and not out.spoken
    row = offers.history()[0]
    assert row["shadow"] is True and row["text"] == out.text


# --------------------------------------------------------- exact wording
async def test_payment_offer_states_merchant_amount_currency_items():
    pay = Consequential(
        kind="payment",
        site="scan.example",
        merchant="Scan",
        amount="94.99",
        currency="GBP",
        items=["2 TB SSD"],
        payment_method="saved Visa ending 4242",
    )
    spk = Speaker("yes")
    offers.set_speaker(spk)
    out = await go(
        opp(
            task_kind="web_form",
            kind="web",
            title="Check out your Scan basket",
            subject="",
            outcome="the order is placed",
        ),
        passed([pay], "web"),
    )
    assert out.status == "accepted"
    text = spk.said[0]
    assert "Scan" in text and "£94.99" in text and "2 TB SSD" in text
    assert "saved Visa ending 4242" in text and text.endswith("Shall I take over?")


def test_send_and_submit_wording():
    send = Consequential(
        kind="send", recipient="Dr Patel", content="I'll be ten minutes late"
    )
    sub = Consequential(kind="submit", site="council.example")
    text = offers.offer_text(opp(), passed([send, sub]))
    assert (
        "Dr Patel" in text and "ten minutes late" in text and "council.example" in text
    )
    assert offers.states_actions(text, [send, sub])


def test_states_actions_rejects_incomplete():
    pay = Consequential(
        kind="payment", merchant="Scan", amount="94.99", currency="GBP", items=["SSD"]
    )
    assert not offers.states_actions("Sir, I can buy it. Shall I?", [pay])
    assert not offers.states_actions("Pay Scan £90.00 for SSD", [pay])
    assert offers.states_actions("Pay Scan £94.99 for SSD", [pay])
    assert not offers.states_actions("x", [Consequential(kind="weird")])


async def test_unstatable_action_is_never_offered():
    spk = Speaker("yes")
    offers.set_speaker(spk)
    bad = Consequential(kind="send", recipient="", content="")
    out = await go(g=passed([bad]))
    assert (out.status, out.reason) == ("suppressed", "offer_text_incomplete")
    assert spk.said == []


def test_long_title_is_trimmed_to_30_words():
    long = opp(title=" ".join(["word"] * 40), outcome="done")
    text = offers.offer_text(long, passed())
    assert len(text.split()) <= 30 and text.endswith("Shall I take over?")


@pytest.mark.parametrize(
    "reply,ans",
    [
        ("yes", "yes"),
        ("Yeah go ahead", "yes"),
        ("do it", "yes"),
        ("take over", "yes"),
        ("no", "no"),
        ("not now", "no"),
        ("I've got it", "no"),
        ("never offer that", "never"),
        ("stop offering those", "never"),
        ("no, never offer that again", "never"),
        ("what's the weather", None),
    ],
)
def test_parse_answer(reply, ans):
    got = offers.parse_answer(reply)
    assert (got.value if got else None) == ans


def test_answer_expired_or_unknown():
    assert offers.answer("of-missing", "yes")["reason"] == "no_offer"
    st.write_private(
        offers._pending_path("of-old"),
        json.dumps({"id": "of-old", "created": 0, "expires": 10, "answer": None}),
    )
    assert offers.answer("of-old", "yes", now=100)["reason"] == "expired"


async def test_answer_returns_say_and_plan_for_voice_agent():
    got = {}

    class Grab(Speaker):
        async def __call__(self, text):
            self.said.append(text)
            got["yes"] = offers.answer("", "yes please")
            got["again"] = offers.answer(got["yes"] and offers_id(text), "no")

    def offers_id(_):
        return ""

    offers.set_speaker(Grab())
    out = await go()
    assert out.status == "accepted"
    assert got["yes"]["ok"] and got["yes"]["say"] == "Right away, Sir."
    assert got["yes"]["plan"]["id"] == "p1"
    assert got["again"]["ok"] is False  # nothing pending once answered


def test_answer_never_ack_names_kind():
    st.write_private(
        offers._pending_path("of-n"),
        json.dumps({"id": "of-n", "kind": "web_form", "created": 0, "expires": 1e12}),
    )
    res = offers.answer("of-n", "never offer that")
    assert res["answer"] == "never" and "web forms" in res["say"]
    assert res["plan"] is None


async def test_subject_mute_honoured():
    offers.set_speaker(Speaker("yes"))
    offers.mute("jarvis_new")
    out = await go()
    assert (out.status, out.reason) == ("suppressed", "muted")


# ----------------------------------------------------------------- learning
async def test_three_declines_in_7_days_mute_for_a_week():
    spk = Speaker()
    offers.set_speaker(spk)
    for i in range(3):
        o = opp(evidence={"text": f"failure {i}"})
        out = await go(o, now=NOW + i * 86400)
        assert out.status == "declined"
    assert any("hold off on test fixes for a week" in s for s in spk.said)
    until = st.load()["muted"]["test_fix"]
    assert until > NOW + 6 * 86400
    out = await go(opp(evidence={"text": "failure 9"}), now=NOW + 4 * 86400)
    assert out.reason == "muted"
    offers.set_speaker(Speaker("yes"))
    assert (
        await go(opp(evidence={"text": "failure 10"}), now=NOW + 10 * 86400)
    ).status == "accepted"


async def test_answers_feed_threshold():
    offers.set_speaker(Speaker("yes"))
    base = feasibility.threshold("test_fix")
    await go()
    assert feasibility.threshold("test_fix") < base


async def test_stuck_outcome_raises_threshold_and_logs_miss():
    offers.set_speaker(Speaker("yes"))
    out = await go()
    before = feasibility.threshold("test_fix")
    offers.record_outcome(out.offer_id, "stuck")
    assert feasibility.threshold("test_fix") == pytest.approx(before + 0.05)
    assert offers.history()[0]["outcome"] == "stuck"
    assert feasibility.misses_path().exists()


async def test_maybe_offer_never_raises():
    out = await offers.maybe_offer(None, passed(), now=NOW, ctx=CALM)  # type: ignore[arg-type]
    assert out.status == "suppressed" and out.reason.startswith("error")


# ------------------------------------------------------------ worksheet hints
def sheet(**kw):
    return opp(
        **{
            "kind": "assist",
            "task_kind": "worksheet_help",
            "title": "Question 4, the fractions one",
            "summary": "Question 4",
            "evidence": {"text": "Q4: 3/4 + 1/6 = ? blank"},
            "subject": "maths worksheet",
            "outcome": "Find a common denominator first; twelve works for both",
            **kw,
        }
    )


async def test_hint_offer_yes_speaks_hint_and_never_takes_over():
    spk = Speaker("yes please")
    offers.set_speaker(spk)
    out = await offers.maybe_assist(sheet(), now=NOW, ctx=CALM, answer_timeout=0.05)
    assert out.status == "accepted"
    assert spk.said[0].startswith("Sir, question 4")
    assert spk.said[0].endswith("give you a hint, or leave it?")
    assert spk.said[1] == "Find a common denominator first; twelve works for both."
    assert offers.is_assist(sheet()) and not offers.is_assist(opp())


async def test_hint_offer_waits_for_a_pause_and_needs_a_hint():
    offers.set_speaker(Speaker("yes"))
    busy = {**CALM, "idle_s": 5}
    out = await offers.maybe_assist(sheet(), now=NOW, ctx=busy, answer_timeout=0.05)
    assert (out.status, out.reason) == ("deferred", "busy")
    out = await offers.maybe_assist(
        sheet(outcome=""), now=NOW, ctx=CALM, answer_timeout=0.05
    )
    assert out.reason == "no_hint"


async def test_hint_rate_gap_is_five_minutes():
    offers.set_speaker(Speaker("no"))
    await offers.maybe_assist(sheet(), now=NOW, ctx=CALM, answer_timeout=0.05)
    other = sheet(evidence={"text": "Q5 blank"}, title="Question 5")
    out = await offers.maybe_assist(other, now=NOW + 60, ctx=CALM, answer_timeout=0.05)
    assert out.reason == "rate_5m"
    out = await offers.maybe_assist(
        other, now=NOW + offers.ASSIST_GAP_S + 1, ctx=CALM, answer_timeout=0.05
    )
    assert out.status == "declined"


@pytest.mark.parametrize(
    "reply,choice",
    [
        ("complete it", "complete"),
        ("fill it in please", "complete"),
        ("finish the worksheet", "complete"),
        ("give me a hint", "hint"),
        ("yes please", "hint"),
        ("help me", "hint"),
        ("leave it", "leave"),
        ("no thanks", "leave"),
        ("not now", "leave"),
        ("never offer that again", "never"),
        ("what time is it", None),
    ],
)
def test_parse_choice(reply, choice):
    assert offers.parse_choice(reply) == choice


async def test_complete_choice_runs_completer_and_speaks_result():
    calls = []

    class Res:
        ok, applied, reason, spoken = (
            True,
            2,
            "",
            "Done, Sir. I've filled in 2 answers.",
        )

    def completer(title, context):
        calls.append((title, context))
        return Res()

    offers.set_completer(completer)
    try:
        spk = Speaker("complete it")
        offers.set_speaker(spk)
        o = sheet(
            evidence={"text": "Q4 blank", "window_title": "f.odt - LibreOffice Writer"}
        )
        out = await offers.maybe_assist(o, now=NOW, ctx=CALM, answer_timeout=0.05)
    finally:
        offers.set_completer(None)
    assert out.status == "accepted" and out.reason == "complete"
    assert calls[0][0] == "f.odt - LibreOffice Writer"
    assert calls[0][1]["question"] == "Question 4, the fractions one"
    assert spk.said[-1] == "Done, Sir. I've filled in 2 answers."
    row = offers.history()[0]
    assert row["answer"] == "yes" and row["outcome"] == "completed"


async def test_leave_choice_declines_quietly():
    spk = Speaker("leave it")
    offers.set_speaker(spk)
    out = await offers.maybe_assist(sheet(), now=NOW, ctx=CALM, answer_timeout=0.05)
    assert out.status == "declined" and len(spk.said) == 1


def test_parse_pending_uses_choices_only_for_worksheet_offers(tmp_path):
    assert offers.parse_pending("complete it", NOW) is None  # nothing pending
    import json as _json

    for kind, want in (("worksheet_help", "complete"), ("test_fix", None)):
        offers.pending_dir().mkdir(parents=True, exist_ok=True)
        path = offers._pending_path("of-x")
        path.write_text(
            _json.dumps(
                {"id": "of-x", "kind": kind, "created": NOW, "expires": NOW + 60}
            )
        )
        assert offers.parse_pending("complete it", NOW) == want
        assert offers.answer("of-x", "complete it", NOW)["ok"] is (want is not None)
        path.unlink()
