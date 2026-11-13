"""Ten-step strength ladder: table, voice phrases, handlers, series, no collisions."""

import asyncio
from types import SimpleNamespace

import pytest

from games.chess import commands, intents_level as il
from games.chess.engine import elo_of
from games.chess.settings import (
    LEVELS,
    PRESET_ELO,
    PRESET_LEVEL,
    ChessSettings,
    elo_level,
    level_elo,
)


def hit(t):
    h = commands.classify(t)
    return (h.name, h.args) if h else None


def test_table():
    assert [level_elo(n) for n in range(1, 11)] == [
        500,
        700,
        900,
        1100,
        1300,
        1500,
        1800,
        2100,
        2500,
        3190,
    ]
    assert level_elo(0) == 500 and level_elo(99) == 3190
    assert elo_level(500) == 1 and elo_level(3190) == 10
    assert elo_level(1600) == 6 and elo_level(1700) == 7 and elo_level(1) == 1
    assert PRESET_LEVEL == {
        "novice": 2,
        "club": 4,
        "strong": 6,
        "expert": 8,
        "master": 9,
        "full": 10,
    }
    assert all(PRESET_ELO[p] == LEVELS[n] for p, n in PRESET_LEVEL.items())


def test_elo_of_level_and_legacy():
    assert elo_of("level:7") == 1800 and elo_of("level:10") == 3190
    assert elo_of("level:99") == 3190 and elo_of("level:x") == 1200
    assert elo_of("elo:1500") == 1500 and elo_of("club") == 1100


SET = [
    ("level 7", 7),
    ("level seven", 7),
    ("set level to seven", 7),
    ("difficulty level five", 5),
    ("make it level 3", 3),
    ("level ten", 10),
    ("level 10", 10),
    ("set level to 1", 1),
    ("set the difficulty level to six", 6),
    ("level to four", 4),
    ("level for four", 4),
    ("level too two", 2),
    ("level to 5", 5),
    ("set level for 8", 8),
    ("play at level nine", 9),
    ("change the level to three", 3),
    ("level two", 2),
    ("level one", 1),
    ("set difficulty level to ten please", 10),
    ("go to level 4", 4),
]


@pytest.mark.parametrize("t,n", SET)
def test_set(t, n):
    assert hit(t) == ("set_level", {"n": n})


@pytest.mark.parametrize(
    "t", ["level 11", "level 0", "set level to eleven", "level twenty", "level 100"]
)
def test_out_of_range_still_routed(t):
    name, args = hit(t)
    assert name == "set_level" and not 1 <= args["n"] <= 10


@pytest.mark.parametrize(
    "t,n",
    [
        ("max level", 10),
        ("maximum level", 10),
        ("highest level", 10),
        ("set level to max", 10),
        ("lowest level", 1),
        ("minimum level", 1),
        ("easiest level", 1),
        ("set it to the lowest level", 1),
        ("level max", 10),
        ("play at max level", 10),
    ],
)
def test_extremes(t, n):
    assert hit(t) == ("set_level", {"n": n})


@pytest.mark.parametrize(
    "t,d",
    [
        ("a bit harder", 1),
        ("a little harder", 1),
        ("make it a bit harder", 1),
        ("slightly harder", 1),
        ("ramp it up", 1),
        ("crank it up", 1),
        ("level up", 1),
        ("a bit easier", -1),
        ("go easier", -1),
        ("make it a little easier", -1),
        ("slightly easier", -1),
        ("dial it down", -1),
        ("level down", -1),
        ("ease up", -1),
    ],
)
def test_adjust(t, d):
    name, args = hit(t)
    assert name in ("level_adjust", "difficulty_adjust") and args["direction"] == d


@pytest.mark.parametrize(
    "t",
    [
        "what level are you",
        "what level are you at",
        "what's the level",
        "what level is this",
        "which level",
        "what is your level",
        "what level am i playing",
        "current level",
    ],
)
def test_query(t):
    assert hit(t) == ("level_query", {})


@pytest.mark.parametrize(
    "t",
    [
        "e four",
        "knight f three",
        "pawn takes e five",
        "queen to level",
        "take back",
        "resign",
        "series level 3",
        "set series level to five",
        "series level seven",
    ],
)
def test_no_collision(t):
    h = hit(t)
    assert not h or h[0] not in ("set_level", "level_adjust", "level_query")


def test_series_rule_still_wins():
    assert hit("series level 3")[0] == "series_option"


def test_old_phrases_still_work():
    assert hit("novice")[0] == "difficulty"
    assert hit("play at fifteen hundred")[0] == "difficulty"
    assert hit("set difficulty to expert")[0] == "difficulty"
    assert hit("easier")[0] == "difficulty_adjust"


# ---------------------------------------------------------------- handlers
def mkgame(diff="club", series_level=None, running=False):
    g = SimpleNamespace(settings=ChessSettings(difficulty=diff), changed=0)
    st = SimpleNamespace(level=series_level, status="running" if running else "done")
    g.series = SimpleNamespace(state=st, _save=lambda: None) if running else None

    async def _changed():
        g.changed += 1

    g._changed = _changed
    return g


def run(c):
    return asyncio.run(c)


def test_handler_set():
    g = mkgame()
    r = run(il._i_set_level(g, 7))
    assert g.settings.difficulty == "level:7" and g.changed == 1
    assert r.ok and "seven" in r.say.lower()


@pytest.mark.parametrize("n", [0, 11, -3, 100])
def test_handler_out_of_range(n):
    g = mkgame()
    r = run(il._i_set_level(g, n))
    assert not r.ok and "one to ten" in r.say and g.settings.difficulty == "club"


def test_handler_adjust_and_clamp():
    g = mkgame("level:5")
    run(il._i_level_adjust(g, 1))
    assert g.settings.difficulty == "level:6"
    run(il._i_level_adjust(g, -1))
    run(il._i_level_adjust(g, -1))
    assert g.settings.difficulty == "level:4"
    g = mkgame("level:10")
    r = run(il._i_level_adjust(g, 1))
    assert g.settings.difficulty == "level:10" and "top" in r.say.lower()
    g = mkgame("level:1")
    r = run(il._i_level_adjust(g, -1))
    assert g.settings.difficulty == "level:1" and "lowest" in r.say.lower()


def test_adjust_from_legacy_and_elo():
    g = mkgame("club")  # level 4
    run(il._i_level_adjust(g, 1))
    assert g.settings.difficulty == "level:5"
    g = mkgame("elo:1800")
    run(il._i_level_adjust(g, -1))
    assert g.settings.difficulty == "level:6"


def test_query():
    assert "four" in run(il._i_level_query(mkgame("club"))).say.lower()
    assert "seven" in run(il._i_level_query(mkgame("level:7"))).say.lower()
    assert "ten" in run(il._i_level_query(mkgame("full"))).say.lower()


def test_mid_series_updates_series_level():
    g = mkgame("level:3", series_level=3, running=True)
    run(il._i_set_level(g, 8))
    assert g.series.state.level == 8 and g.settings.difficulty == "level:8"
    run(il._i_level_adjust(g, -1))
    assert g.series.state.level == 7 and g.settings.difficulty == "level:7"


def test_series_without_level_unchanged():
    g = mkgame("club", series_level=None, running=True)
    run(il._i_set_level(g, 8))
    assert g.series.state.level is None or g.series.state.level == 8


def test_series_level_scale():
    from games.chess.session import SessionMixin as ChessSession

    assert ChessSession._level_preset(4) == "level:4"
    assert ChessSession._level_preset(None) is None
    assert elo_of(ChessSession._level_preset(12)) == 3190


def test_strength_ordering_engine():
    assert [elo_of(f"level:{n}") for n in range(1, 11)] == sorted(
        elo_of(f"level:{n}") for n in range(1, 11)
    )
