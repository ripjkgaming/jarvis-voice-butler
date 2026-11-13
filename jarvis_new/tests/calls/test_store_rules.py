import datetime as dt

import pytest

from calls import calllog, contacts, rules

NOW = dt.datetime(2026, 11, 13, 12, 0).timestamp()


def at(h, m=0):
    return dt.datetime(2026, 11, 13, h, m).timestamp()


def test_add_validation():
    with pytest.raises(ValueError):
        rules.add_rule("nope")
    with pytest.raises(ValueError):
        rules.add_rule("reject", start=10, end=5)
    with pytest.raises(ValueError):
        rules.add_rule("reject", duration=0)
    with pytest.raises(ValueError):
        rules.add_rule("reject", duration=5, end=9)
    with pytest.raises(ValueError):
        rules.add_rule("reject", daily_from="9:00", daily_to="17:00")
    with pytest.raises(ValueError):
        rules.add_rule("reject", daily_from="09:00")
    with pytest.raises(ValueError):
        rules.add_rule("reject", daily_from="09:00", daily_to="09:00")
    with pytest.raises(ValueError):
        rules.add_rule("reject", deny=["abc"])


def test_duration_window():
    r = rules.add_rule("answer_all", duration=3600, now=NOW)
    assert (r.start, r.end) == (NOW, NOW + 3600)
    assert rules.active_rules(NOW + 10)
    assert not rules.active_rules(NOW + 3600)
    assert not rules.active_rules(NOW - 1)


def test_absolute_window_and_open_ended():
    rules.add_rule("reject", start=NOW, end=NOW + 100)
    rules.add_rule("message_only")
    assert len(rules.active_rules(NOW + 50)) == 2
    assert len(rules.active_rules(NOW + 200)) == 1


def test_daily_window_and_overnight():
    rules.add_rule("reject", daily_from="09:00", daily_to="17:00")
    assert rules.active_rules(at(9)) and rules.active_rules(at(16, 59))
    assert not rules.active_rules(at(17)) and not rules.active_rules(at(8, 59))
    rules.cancel_rule()
    rules.add_rule("reject", daily_from="22:00", daily_to="07:00")
    assert rules.active_rules(at(23)) and rules.active_rules(at(6, 59))
    assert not rules.active_rules(at(7)) and not rules.active_rules(at(12))


def test_cancel_one_and_all_audited():
    a = rules.add_rule("reject")
    b = rules.add_rule("message_only")
    assert rules.cancel_rule(a.id) == 1
    assert rules.cancel_rule(a.id) == 0
    assert [r.id for r in rules.active_rules(NOW)] == [b.id]
    rules.add_rule("answer_all")
    assert rules.cancel_rule() == 2
    assert rules.active_rules(NOW) == []
    assert len(rules.list_rules(include_inactive=True)) == 3
    kinds = [x["kind"] for x in calllog.list_audit(50)]
    assert kinds.count("rule_cancel") == 3 and kinds.count("rule_add") == 3


def test_lists_normalised_dedup():
    r = rules.add_rule("reject", deny=["9123 4567", "+6591234567"], allow=["81234567"])
    assert r.deny == ["+6591234567"] and r.allow == ["+6581234567"]


def test_default_owner_when_nothing():
    d = rules.decide("91234567", NOW)
    assert d.action == "owner" and d.rule_id is None and d.contact is None


def test_rule_kinds_map_to_actions():
    r = rules.add_rule("answer_all", message="hi")
    assert rules.decide("91234567", NOW).action == "answer"
    rules.cancel_rule(r.id)
    rules.add_rule("reject")
    assert rules.decide("91234567", NOW).action == "reject"
    rules.cancel_rule()
    rules.add_rule("message_only")
    assert rules.decide("91234567", NOW).action == "message"


def test_answer_unknown_only_unknown():
    r = rules.add_rule("answer_unknown")
    contacts.add_contact("Known", "81234567")
    d = rules.decide("91234567", NOW)
    assert (d.action, d.rule_id) == ("answer", r.id)
    assert rules.decide("81234567", NOW).action == "owner"
    # unparseable caller id counts as unknown
    assert rules.decide("anonymous", NOW).action == "answer"


def test_newest_rule_wins():
    rules.add_rule("reject")
    n = rules.add_rule("answer_all")
    d = rules.decide("91234567", NOW)
    assert d.action == "answer" and d.rule_id == n.id


def test_precedence_deny_allow_contact_rules():
    rules.add_rule("reject")
    contacts.add_contact("Fam", "91234567", trust="family", policy={"answer": "auto"})
    contacts.add_contact("Spam", "81234567", policy={"answer": "auto"})
    # contact policy beats rule
    d = rules.decide("91234567", NOW)
    assert d.action == "answer" and d.contact.name == "Fam"
    # allow beats rule + contact policy
    rules.add_rule("reject", allow=["92345678"])
    assert rules.decide("92345678", NOW).action == "answer"
    # deny beats allow and contact policy
    rules.add_rule("answer_all", deny=["91234567", "92345678"])
    d = rules.decide("91234567", NOW)
    assert d.action == "reject" and d.reason == "number on deny list"
    assert rules.decide("92345678", NOW).action == "reject"


def test_blocked_contact_rejected_even_if_answer_all():
    contacts.add_contact("Bad", "91234567", trust="blocked")
    rules.add_rule("answer_all")
    assert rules.decide("91234567", NOW).action == "reject"


def test_allow_beats_blocked():
    contacts.add_contact("Bad", "91234567", trust="blocked")
    rules.add_rule("reject", allow=["91234567"])
    assert rules.decide("91234567", NOW).action == "answer"


@pytest.mark.parametrize(
    "pol,action",
    [
        ("auto", "answer"),
        ("message", "message"),
        ("owner", "owner"),
        ("reject", "reject"),
    ],
)
def test_contact_policy_actions(pol, action):
    contacts.add_contact("X", "91234567", policy={"answer": pol})
    rules.add_rule("answer_all")
    assert rules.decide("91234567", NOW).action == action


def test_contact_without_policy_falls_to_rules():
    contacts.add_contact("X", "91234567", trust="work")
    rules.add_rule("message_only")
    assert rules.decide("91234567", NOW).action == "message"


def test_expired_and_stopped_rules_ignored_by_decide():
    rules.add_rule("reject", duration=60, now=NOW)
    assert rules.decide("91234567", NOW + 30).action == "reject"
    assert rules.decide("91234567", NOW + 61).action == "owner"
    rules.add_rule("reject")
    rules.cancel_rule()
    assert rules.decide("91234567", NOW).action == "owner"


def test_decisions_audited_and_optional():
    rules.decide("91234567", NOW)
    assert calllog.list_audit(1)[0]["kind"] == "decision"
    n = len(calllog.list_audit(100))
    rules.decide("91234567", NOW, log=False)
    assert len(calllog.list_audit(100)) == n
