import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[2] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from types import SimpleNamespace  # noqa: E402

from calls import events  # noqa: E402


class FakeDriver:
    def __init__(self):
        self.calls = []
        self.in_call = False

    def accept(self):
        self.calls.append("accept")
        self.in_call = True

    def decline(self):
        self.calls.append("decline")

    async def hangup(self):
        self.calls.append("hangup")
        self.in_call = False

    def dial(self, n):
        self.calls.append(("dial", n))
        self.in_call = True

    def is_in_call(self):
        return self.in_call


class FakeBridge:
    def __init__(self):
        self.flushed = 0

    async def flush(self):
        self.flushed += 1


def make_stores(action="answer", message="", contact=None):
    log = {
        "turns": [],
        "audit": [],
        "ended": None,
        "remembered": [],
        "rules_cancelled": 0,
    }
    d = SimpleNamespace(
        action=action, reason="test", rule_id=None, contact=contact, message=message
    )
    contacts = SimpleNamespace(get_by_number=lambda n: contact)
    rules = SimpleNamespace(
        decide=lambda n, now: d,
        active_rules=lambda now: [],
        cancel_rule=lambda id=None: (  # noqa: A006
            log.__setitem__("rules_cancelled", log["rules_cancelled"] + 1) or 1
        ),
    )
    calllog = SimpleNamespace(
        start_call=lambda direction, number, cid, goal: 7,
        add_turn=lambda cid, sp, t, ts=None: log["turns"].append((sp, t)),
        end_call=lambda cid, outcome, summary: log.__setitem__(
            "ended", (outcome, summary)
        ),
        audit=lambda kind, detail: log["audit"].append((kind, detail)),
    )
    mb = SimpleNamespace(
        recall_for_call=lambda c: "likes tea",
        remember_person=lambda cid, f: log["remembered"].append(f),
    )
    ns = SimpleNamespace(
        contacts=contacts, rules=rules, calllog=calllog, memory_bridge=mb
    )
    ns.log = log
    return ns


def make_contact(trust="family", persona="jarvis"):
    return SimpleNamespace(
        id=1,
        name="Mum",
        number="+4470",
        relationship="mother",
        notes="",
        trust=trust,
        policy={"persona": persona},
    )


@pytest.fixture
def sink():
    got = []
    events.set_sink(got.append)
    yield got
    events.set_sink(None)
    events.set_active(None)


@pytest.fixture
def spoken():
    return []


@pytest.fixture
def mk(sink, spoken):
    from calls.agent import CallSession

    def _mk(direction="in", llm=None, stores=None, settings=None, goal="", **kw):
        async def speaker(text, persona):
            spoken.append((text, persona))

        async def default_llm(system, history):
            return "Okay, noted."

        drv = FakeDriver()
        br = FakeBridge()
        s = CallSession(
            direction=direction,
            number="+4470",
            llm=llm or default_llm,
            speaker=speaker,
            driver=drv,
            bridge=br,
            stores=stores or make_stores(),
            settings=settings
            or (
                lambda n, d=None: {"disclose_ai": True, "record_calls": False}.get(n, d)
            ),
            goal=goal,
            **kw,
        )
        s.drv, s.br = drv, br
        return s

    return _mk
