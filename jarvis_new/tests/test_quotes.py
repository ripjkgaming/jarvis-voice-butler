"""Movie/meme quotes: the matcher is strict, every action is wired."""

import types

import pytest
from livekit.agents.llm import ToolError

import quotes as Q
from system import quotes_tools
from system.quotes_tools import QuoteTools, coding_line, phone_line, vitals_line


@pytest.mark.parametrize(
    ("text", "qid"),
    [
        ("Jarvis, you up?", "you_up"),
        ("I am Iron Man.", "iron_man"),
        ("Wake up, daddy's home!", "daddys_home"),
        ("Jarvis, run a diagnostic.", "diagnostic"),
        ("Houston, we have a problem", "diagnostic"),
        ("Initiate house party protocol", "house_party"),
        ("Clean slate protocol", "clean_slate"),
        ("Show me the money!", "show_me_the_money"),
        ("Open the pod bay doors, HAL", "pod_bay"),
        ("E.T. phone home", "phone_home"),
        ("I'll be back", "ill_be_back"),
        ("Hasta la vista, baby", "ill_be_back"),
        ("May the Force be with you", "the_force"),
        ("It's free real estate", "free_real_estate"),
        ("This is fine.", "this_is_fine"),
        ("It's over 9000!", "over_9000"),
        ("Big brain time", "big_brain"),
        ("Let him cook", "let_him_cook"),
        ("Ight imma head out", "head_out"),
        ("Never gonna give you up", "rickroll"),
        ("Stonks", "stonks"),
        ("Go touch grass", "touch_grass"),
        ("Hello there", "hello_there"),
        ("Bruh", "bruh"),
        ("Skill issue", "skill_issue"),
        ("That's sus", "sus"),
        ("hey jarvis, initiate house party protocl", "house_party"),  # STT typo
    ],
)
def test_every_quote_matches(text, qid):
    q = Q.match_quote(text)
    assert q is not None and q.id == qid


@pytest.mark.parametrize(
    "text",
    [
        "this code is fine",
        "I am ironing my shirt",
        "hello there is a bug in my code",
        "open the doors",
        "I'll be back in five minutes",
        "what's the stock market doing",
        "the grass needs cutting",
        "bro",
        "sums",
        "is my phone at home",
        "run the tests",
        "what is the capital of australia",
        "",
    ],
)
def test_lookalikes_never_match(text):
    assert Q.match_quote(text) is None


def test_table_is_consistent():
    ids = [q.id for q in Q.QUOTES]
    assert len(ids) == len(set(ids))
    assert all(q.action in Q.ACTIONS for q in Q.QUOTES)
    assert all(q.phrases and q.reply for q in Q.QUOTES)
    for q in Q.QUOTES:
        if q.action in ("play", "party", "search"):
            assert q.args.get("query")
    assert '"i am iron man"' in Q.prompt_lines()


class _Session:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never read Sir's real ~/.jarvis: in school mode its loud-action
    guard refused the play/party quotes and these tests failed."""
    import school

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    school._cache.update(path=None, mtime=None, mode=school.NORMAL)


def _ctx():
    return types.SimpleNamespace(session=_Session())


class _Fake:
    """Stands in for SystemTools / InboxTools / BrowserTools."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def tool(context, *args):
            self.calls.append((name, args))
            return {"say": f"{name} ok."}

        return tool


def _tools(monkeypatch, sys=None):
    bridge = []

    def fake_bridge(method, path, body=None):
        bridge.append((method, path, body))
        if path == "/sys":
            return sys or {}
        return {"ok": True}

    monkeypatch.setattr(quotes_tools, "_bridge_call", fake_bridge)
    system, inbox, browser = _Fake(), _Fake(), _Fake()
    return QuoteTools(system=system, inbox=inbox, browser=browser), system, inbox, browser, bridge


async def test_every_action_runs(monkeypatch):
    monkeypatch.setattr(quotes_tools.Q, "COOLDOWN_S", 0.0)
    monkeypatch.setattr(quotes_tools, "END_CALL_DELAY_S", 0.0)
    import projects

    monkeypatch.setattr(projects, "list_projects", lambda: [])
    tools, system, inbox, browser, bridge = _tools(monkeypatch)
    for q in Q.QUOTES:
        out = await QuoteTools.quote_action(tools, _ctx(), quote=q.phrases[0])
        assert out["quote"] == q.id and out["say"].startswith(q.reply)
    names = [c[0] for c in system.calls + inbox.calls + browser.calls]
    for want in ("play_media", "set_volume", "disk_space", "open_app", "battery_status",
                 "morning_briefing", "weather_now", "close_helper", "open_helper_google"):
        assert want in names
    assert ("play_media", ("Rick Astley Never Gonna Give You Up",)) in system.calls
    hides = [b for b in bridge if b[1] == "/tool"]
    assert any(b[2]["args"]["commands"] == [{"action": "hide"}] for b in hides)


async def test_refuses_non_quotes_and_cools_down(monkeypatch):
    tools, system, *_ = _tools(monkeypatch)
    with pytest.raises(ToolError):
        await QuoteTools.quote_action(tools, _ctx(), quote="this code is fine")
    first = await QuoteTools.quote_action(tools, _ctx(), quote="I am Iron Man")
    again = await QuoteTools.quote_action(tools, _ctx(), quote="I am Iron Man")
    assert "Cue the music" in first["say"]
    assert "Once was enough" in again["say"]
    assert len([c for c in system.calls if c[0] == "play_media"]) == 1


async def test_end_call_closes_the_session(monkeypatch):
    import asyncio

    monkeypatch.setattr(quotes_tools, "END_CALL_DELAY_S", 0.0)
    tools, *_ = _tools(monkeypatch)
    ctx = _ctx()
    await QuoteTools.quote_action(tools, ctx, quote="Ight imma head out")
    await asyncio.sleep(0.01)
    assert ctx.session.closed


def test_spoken_lines():
    assert "85 degrees, running hot" in vitals_line(
        {"cpu_temp_c": 85, "load_1_5_15": ["6", "5", "4"], "cpu_count": 12,
         "mem_bytes": {"MemTotal": 100, "MemAvailable": 25}, "home_free_bytes": 50e9}
    )
    assert "monitor" in vitals_line(None)
    assert "tailnet" in phone_line({"phone": None, "phone_tailnet": {"online": True}})
    assert "76 percent, charging" in phone_line({"phone": {"battery": 76, "charging": True}})
    assert "offline" in phone_line({})
    assert "Nothing cooking" in coding_line([])
    assert "1 coding job cooking" in coding_line(
        [{"kind": "code", "status": "running", "title": "hello py"}]
    )


# --- the suit easter egg ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Jarvis, prep the suit",
        "suit up",
        "hey jarvis bring me the mark 42",
        "get the armour ready",
        "where is my suit",
        "deploy the Mark 85",
        "power up the suit",
    ],
)
def test_suit_commands_match(text):
    assert Q.match_suit(text)


@pytest.mark.parametrize(
    "text",
    [
        "I need to file a lawsuit",
        "where is my suitcase",
        "is this suitable",
        "mark this as done",
        "what suits me",
        "prep for my physics exam",
        "I want to wear my suit to the wedding on saturday because it is formal",
    ],
)
def test_suit_lookalikes_never_match(text):
    assert not Q.match_suit(text)


def test_suit_is_not_a_quote_and_never_repeats():
    import random

    assert Q.match_quote("prep the suit") is None
    rng, last = random.Random(0), None
    for _ in range(50):
        line = Q.suit_reply(last, rng)
        assert line != last and line in Q.SUIT_REPLIES
        last = line
    for line in Q.SUIT_REPLIES:
        assert "suit is ready" not in line.lower() and "suit exists" not in line.lower()


async def test_suit_action_refuses_without_cooldown(monkeypatch):
    tools, system, *_ = _tools(monkeypatch)
    first = await QuoteTools.quote_action(tools, _ctx(), quote="Jarvis, prep the suit")
    again = await QuoteTools.quote_action(tools, _ctx(), quote="Jarvis, prep the suit")
    assert first["quote"] == again["quote"] == "suit"
    assert first["say"] != again["say"] and "Once was enough" not in again["say"]
    assert system.calls == []


def test_suit_rule_reaches_the_prompt():
    import prompts

    assert Q.SUIT_RULE in prompts.AGENT_INSTRUCTIONS
    assert "suit up" in Q.prompt_lines()
