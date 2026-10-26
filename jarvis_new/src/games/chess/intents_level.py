"""Ten-level strength ladder intents (plain async functions taking the game).

Applies from the engine's next move. In a running series with a level, the
series level moves too so the next games keep it.
"""

from __future__ import annotations

from games.chess import templates
from games.chess.reply import Reply
from games.chess.settings import LEVELS, difficulty_level, level_elo

RANGE_MSG = "Levels run from one to ten, Sir."


def _series_state(game):
    s = getattr(game, "series", None)
    st = getattr(s, "state", None) if s is not None else None
    return (s, st) if st is not None and st.status == "running" else (None, None)


def _name(n: int) -> str:
    return templates.number_words(n)


async def _apply(game, n: int) -> None:
    game.settings.difficulty = f"level:{n}"
    s, st = _series_state(game)
    if st is not None and st.level is not None:
        st.level = n
        save = getattr(s, "_save", None)
        if save:
            save()
    await game._changed()


async def _i_set_level(game, n: int) -> Reply:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return Reply(RANGE_MSG, ok=False, kind="difficulty")
    if not 1 <= n <= 10:
        return Reply(RANGE_MSG, ok=False, kind="difficulty")
    await _apply(game, n)
    tail = " Full strength." if n == 10 else ""
    return Reply(
        f"Very good, Sir. Level {_name(n)}.{tail}",
        kind="difficulty",
        data={"level": n, "elo": level_elo(n)},
    )


async def _i_level_adjust(game, direction: int) -> Reply:
    cur = difficulty_level(game.settings.difficulty)
    nxt = max(1, min(max(LEVELS), cur + (1 if direction > 0 else -1)))
    if nxt == cur:
        edge = "top" if direction > 0 else "lowest"
        # Make sure the stored form is a level even when nothing moves.
        await _apply(game, cur)
        return Reply(
            f"I'm already at the {edge} level, Sir. Level {_name(cur)}.",
            ok=False,
            kind="difficulty",
            data={"level": cur},
        )
    await _apply(game, nxt)
    word = "up" if direction > 0 else "down"
    return Reply(
        f"Stepping {word} to level {_name(nxt)}, Sir.",
        kind="difficulty",
        data={"level": nxt, "elo": level_elo(nxt)},
    )


async def _i_level_query(game) -> Reply:
    n = difficulty_level(game.settings.difficulty)
    e = level_elo(n)
    about = "full strength" if n == 10 else f"about {_name(e // 100)} hundred"
    return Reply(
        f"I'm playing at level {_name(n)}, Sir, {about}.",
        kind="difficulty",
        data={"level": n, "elo": level_elo(n)},
    )
