import types

import pytest

from calls import scheduler
from calls import voice_commands as vc
from jarvis_rt import ToolError


class FakeContacts:
    def __init__(self):
        self.rows = {}
        self.n = 0

    def _mk(self, **kw):
        self.n += 1
        c = types.SimpleNamespace(
            id=self.n,
            relationship="",
            notes="",
            trust="unknown",
            **kw,
        )
        self.rows[c.id] = c
        return c

    def add_contact(self, name, number, relationship="", notes="", trust="unknown"):
        return self._mk(name=name, number=number) and self._upd(
            self.n, relationship=relationship, notes=notes, trust=trust
        )

    def _upd(self, i, **kw):
        for k, v in kw.items():
            setattr(self.rows[i], k, v)
        return self.rows[i]

    def update_contact(self, cid, **kw):
        return self._upd(cid, **kw)

    def find(self, name):
        return [c for c in self.rows.values() if name.lower() in c.name.lower()]

    def delete_contact(self, cid):
        return bool(self.rows.pop(cid, None))


class FakeRules:
    def __init__(self):
        self.added, self.active, self.cancelled = [], [], []

    def add_rule(self, **kw):
        r = types.SimpleNamespace(
            id=len(self.added) + 1,
            daily_from=kw.get("daily_from"),
            daily_to=kw.get("daily_to"),
            end=kw.get("end"),
            kind=kw["kind"],
            message=kw.get("message", ""),
        )
        self.added.append(kw)
        self.active.append(r)
        return r

    def cancel_rule(self, rid=None):
        n = len(self.active)
        self.active = []
        self.cancelled.append(rid)
        return n

    def active_rules(self, now=None):
        return list(self.active)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_CALLS_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_SCHEDULED_TASKS", "1")
    monkeypatch.delenv("JARVIS_GOODNIGHT_V2", raising=False)
    monkeypatch.setattr(vc, "_guard", lambda: None)
    contacts, rules = FakeContacts(), FakeRules()
    audits = []
    log = types.SimpleNamespace(audit=lambda k, d=None: audits.append((k, d)))
    monkeypatch.setattr(vc, "_mods", lambda: (contacts, rules, log))
    monkeypatch.setattr(vc, "_dialer", None)
    return types.SimpleNamespace(contacts=contacts, rules=rules, audits=audits)


async def test_add_find_update_delete(env):
    t = vc.CallTools()
    r = await t.add_contact(None, name="Mum", number="+6591234567", trust="family")
    assert "Saved Mum" in r["say"]
    r = await t.add_contact(None, name="Mum", number="+6598765432")
    assert "Updated" in r["say"] and len(env.contacts.rows) == 1
    assert env.contacts.rows[1].trust == "family"  # unchanged by default trust
    assert "+6598765432" in (await t.find_contact(None, name="mum"))["say"]
    await t.update_contact(None, name="Mum", trust="work")
    assert env.contacts.rows[1].trust == "work"
    r = await t.delete_contact(None, name="Mum")
    assert "confirm" in r["say"] and env.contacts.rows
    await t.delete_contact(None, name="Mum", confirmed=True)
    assert not env.contacts.rows


async def test_bad_trust_and_empty_update(env):
    t = vc.CallTools()
    with pytest.raises(ToolError):
        await t.add_contact(None, name="X", number="1", trust="vip")
    await t.add_contact(None, name="X", number="+6591111111")
    with pytest.raises(ToolError):
        await t.update_contact(None, name="X")
    with pytest.raises(ToolError):
        await t.update_contact(None, name="X", trust="vip")


async def test_ambiguous_and_missing(env):
    t = vc.CallTools()
    await t.add_contact(None, name="Dr Tan", number="+6591111111")
    await t.add_contact(None, name="Tan Ah Kow", number="+6592222222")
    with pytest.raises(ToolError, match="More than one"):
        await t.update_contact(None, name="Tan", trust="work")
    with pytest.raises(ToolError, match="No contact"):
        await t.update_contact(None, name="Nobody", trust="work")


async def test_call_contact_uses_injected_dialer(env):
    t = vc.CallTools()
    await t.add_contact(None, name="Mum", number="+6591234567")
    seen = []

    async def dialer(job):
        seen.append(job)
        return {"status": "started"}

    vc.set_dialer(dialer)
    r = await t.call_contact(None, name="Mum", goal="ask about dinner")
    assert "Calling Mum" in r["say"]
    assert seen[0]["number"] == "+6591234567" and seen[0]["goal"] == "ask about dinner"
    assert any(k == "call_requested" for k, _ in env.audits)


async def test_call_contact_failures(env):
    t = vc.CallTools()
    await t.add_contact(None, name="Mum", number="+6591234567")
    with pytest.raises(ToolError, match="No dialer"):
        await t.call_contact(None, name="Mum")
    await t.add_contact(None, name="Spam", number="+6590000000", trust="blocked")
    with pytest.raises(ToolError, match="blocked"):
        await t.call_contact(None, name="Spam")


async def test_schedule_list_cancel_call(env):
    t = vc.CallTools()
    await t.add_contact(None, name="Mum", number="+6591234567")
    r = await t.schedule_call(None, contact="Mum", when="in 10 minutes", goal="dinner")
    assert r["job_id"].startswith("call-") and "Mum" in r["say"]
    assert "dinner" in (await t.list_scheduled_calls(None))["say"]
    out = await t.cancel_scheduled_call(None, job_id=r["job_id"])
    assert "Cancelled 1" in out["say"]
    assert "cancelled" in (await t.list_scheduled_calls(None))["say"]
    with pytest.raises(ToolError):
        await t.schedule_call(None, contact="Mum", when="yesterday")
    with pytest.raises(ToolError):
        await t.cancel_scheduled_call(None)


async def test_auto_answer_set_list_cancel(env):
    t = vc.CallTools()
    r = await t.set_auto_answer(
        None, request="for the next 3 hours answer all calls and say I'm in a meeting"
    )
    kw = env.rules.added[0]
    assert kw["kind"] == "answer_all" and kw["message"] == "I'm in a meeting"
    assert kw["created_by"] == "voice" and "Answer all calls" in r["say"]
    assert "answer all" in (await t.list_call_rules(None))["say"]
    assert "Cancelled 1" in (await t.cancel_auto_answer(None))["say"]
    assert "No auto-answer" in (await t.list_call_rules(None))["say"]
    with pytest.raises(ToolError):
        await t.set_auto_answer(None, request="what is the weather")


async def test_call_control_passthrough(env, monkeypatch):
    from calls import events

    seen = []

    async def hc(name):
        seen.append(name)
        return {
            "ok": name != "hold",
            "detail": "fine" if name != "hold" else "no active call",
        }

    monkeypatch.setattr(events, "handle_command", hc)
    t = vc.CallTools()
    assert (await t.call_control(None, command="Take over"))["command"] == "takeover"
    assert (await t.call_control(None, command="hang up"))["command"] == "hangup"
    assert (await t.call_control(None, command="hangup"))["command"] == "hangup"
    with pytest.raises(ToolError, match="no active call"):
        await t.call_control(None, command="hold")
    with pytest.raises(ToolError):
        await t.call_control(None, command="explode")


def test_parse_contact_phrase():
    assert vc.parse_contact_phrase("Mum's number is 9123 4567") == (
        "number",
        "Mum",
        "9123 4567",
    )
    assert vc.parse_contact_phrase("Dr Tan's mobile number is +65 9123-4567.")[
        2
    ].startswith("+65")
    assert vc.parse_contact_phrase("mark Dr Tan as work") == ("mark", "Dr Tan", "work")
    assert vc.parse_contact_phrase("mark him as vip") is None
    assert vc.parse_contact_phrase("hello") is None


async def test_handle_utterance(env):
    assert await vc.handle_utterance("hello there") is None
    r = await vc.handle_utterance("Mum's number is 9123 4567")
    assert "Saved Mum" in r["say"]
    await vc.handle_utterance("mark Mum as family")
    assert env.contacts.rows[1].trust == "family"


def test_tools_have_unique_ids_and_schedule_store_isolated(env):
    ids = [t.id for t in vc.CallTools().tools]
    assert len(ids) == len(set(ids)) == 12
    assert scheduler.store_path().parent == scheduler.calls_dir()


async def test_guard_blocks_guests(monkeypatch):
    import phone_session

    monkeypatch.undo()
    monkeypatch.setattr(phone_session, "guest_required", lambda: True)
    with pytest.raises(ToolError, match="owner"):
        await vc.CallTools().find_contact(None, name="x")
