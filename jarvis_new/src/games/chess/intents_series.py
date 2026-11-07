"""Voice intents for game series, game history and board analysis.

One entry point, ``handle(game, hit, raw)``, called from ``ChessGame.handle_text``
right after ``commands.classify``. It returns a ``Reply`` when it owns the
intent, or ``None`` so the caller falls back to the legacy handler. Handlers
(``_i_*``) are module level functions taking the game first, return text
synchronously (<10 ms, no engine awaits) and push long work through
``game._spawn``. Sibling modules (series, store, history, review, takeover)
are built in parallel, so they are imported lazily and a missing module only
produces a "not available yet" reply.

Game attributes used (all created lazily on the game object):
  game.store      GameStore            game.series     SeriesController|None
  game.hist_view  HistoryView|None     game.on_series_event(event, **kw)  optional
                  hook the takeover loop installs; events: start, pause, resume,
                  cancel, option.
Routing (legacy intents remapped only while the new context is active):
  ctl_prev / ctl_next / review_goto -> history while a history view is open
  analyse "what's the score"        -> series_score while a series is active
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import chess

from games.chess import commands, templates
from games.chess.reply import Reply

SCORE_PHRASES = frozenset(
    {"what's the score", "what is the score", "score", "whats the score"}
)
_ACTIVE = ("running", "paused")
NOT_YET = "That is not available yet, Sir."


def _mod(name: str) -> Any | None:
    try:
        return importlib.import_module(f"games.chess.{name}")
    except ImportError:
        return None


def _w(n: int) -> str:
    return templates.number_words(n)


def _bad(text: str, kind: str = "series") -> Reply:
    return Reply(text, ok=False, kind=kind)


# ------------------------------------------------------------------ plumbing
def _store(game: Any) -> Any | None:
    st = getattr(game, "store", None)
    if st is None:
        mod = _mod("store")
        if mod is None:
            return None
        st = mod.GameStore()
        game.store = st
    return st


def _series_active(game: Any) -> bool:
    ctl = getattr(game, "series", None)
    s = getattr(ctl, "state", None)
    return bool(s and s.status in _ACTIVE)


def _hist_active(game: Any) -> bool:
    v = getattr(game, "hist_view", None)
    return v is not None and getattr(v, "record", None) is not None


def _notify(game: Any, event: str, **kw: Any) -> None:
    cb = getattr(game, "on_series_event", None)
    if cb is None:
        return
    try:
        out = cb(event, **kw)
        if inspect.isawaitable(out):
            game._spawn(out)
    except Exception:  # a broken listener must never break a voice turn
        pass


def _take(rec: Any, ply: int) -> dict:
    m = (rec["moves"] if isinstance(rec, dict) else rec.moves)[ply - 1]
    return m if isinstance(m, dict) else vars(m)


# -------------------------------------------------------------------- routing
def route(game: Any, hit: commands.Intent, raw: str) -> commands.Intent:
    """Remap legacy intents that mean something else in series/history context."""
    name = hit.name
    if _hist_active(game):
        if name == "ctl_next":
            return commands.Intent("hist_step", {"delta": 1}, "history")
        if name == "ctl_prev":
            return commands.Intent("hist_step", {"delta": -1}, "history")
        if name == "review_goto":
            return commands.Intent("hist_goto", dict(hit.args), "history")
    if name == "analyse" and _series_active(game):
        words = (
            commands.text_norm.clean(raw)
            if hasattr(commands, "text_norm")
            else raw.lower()
        )
        if (
            any(v in SCORE_PHRASES for v in commands.text_norm.variants(raw))
            or words in SCORE_PHRASES
        ):
            return commands.Intent("series_score", {}, "series")
    return hit


async def handle(game: Any, hit: commands.Intent, raw: str = "") -> Reply | None:
    hit = route(game, hit, raw)
    fn = _HANDLERS.get(hit.name)
    if fn is None:
        return None
    out = fn(
        game,
        **{k: v for k, v in hit.args.items() if k in inspect.signature(fn).parameters},
    )
    if inspect.isawaitable(out):
        out = await out
    if out is not None and hasattr(game, "_changed_soon"):
        game._changed_soon()  # series / history / list state may have changed
    return out


# --------------------------------------------------------------------- series
def _i_series_start(
    game: Any,
    total: int | None = None,
    colour: str = "alternate",
    level: int | None = None,
    move_time: float | None = None,
    speak: str = "results",
) -> Reply:
    mod = _mod("series")
    store = _store(game)
    if mod is None or store is None:
        return _bad("Series play is not available yet, Sir.")
    if _series_active(game):
        return _bad("A series is already running, Sir. Say 'cancel the series' first.")
    ctl = mod.SeriesController(store)
    try:
        ctl.start(
            total=total, colour=colour, level=level, move_time=move_time, speak=speak
        )
    except ValueError:
        return _bad("I can manage between one and a hundred games, Sir.")
    game.series = ctl
    _notify(game, "start")
    how = {"white": "as white", "black": "as black"}.get(colour, "alternating colours")
    head = (
        "until you say stop"
        if total is None
        else f"{_w(total)} game{'s' * (total != 1)}"
    )
    return Reply(f"Very good, Sir. {head[0].upper() + head[1:]}, {how}.", kind="series")


def _i_series_bad_count(game: Any, total: int = 0) -> Reply:
    return _bad("I can manage between one and a hundred games, Sir.")


def _need_series(game: Any) -> Reply | None:
    if not _series_active(game):
        return _bad("There's no series running, Sir.")
    return None


def _i_series_option(
    game: Any,
    colour: str | None = None,
    level: int | None = None,
    move_time: float | None = None,
    speak: str | None = None,
) -> Reply:
    if (r := _need_series(game)) is not None:
        return r
    s = game.series.state
    if colour:
        s.colour = colour
    if level is not None:
        s.level = level
    if move_time is not None:
        s.move_time = move_time
    if speak:
        s.speak = speak
    game.series._save()
    _notify(game, "option")
    return Reply("Noted, Sir. That applies from the next game.", kind="series")


def _i_series_left(game: Any) -> Reply:
    if (r := _need_series(game)) is not None:
        return r
    left = game.series.remaining()
    if left is None:
        return Reply("We're playing until you say stop, Sir.", kind="series")
    return Reply(
        f"{_w(left).capitalize()} game{'s' * (left != 1)} to go, Sir."
        if left
        else "That was the last game, Sir.",
        kind="series",
    )


def _i_series_score(game: Any) -> Reply:
    if (r := _need_series(game)) is not None:
        return r
    s = game.series.state
    return Reply(
        f"The series stands at {game.series.score_text()}, Sir.",
        kind="series",
        data={"series": _brief(s)},
    )


def _brief(s: Any) -> dict:
    return {
        "id": s.id,
        "wins": s.wins,
        "draws": s.draws,
        "losses": s.losses,
        "total": s.total,
        "status": s.status,
    }


def _i_series_pause(game: Any) -> Reply:
    if (r := _need_series(game)) is not None:
        return r
    if not game.series.pause():
        return _bad("The series is already paused, Sir.")
    _notify(game, "pause")
    return Reply(
        "Series paused, Sir. Say 'continue the series' when ready.", kind="series"
    )


def _i_series_resume(game: Any) -> Reply:
    ctl = getattr(game, "series", None)
    if ctl is None or ctl.state is None or ctl.state.status != "paused":
        return _bad("No paused series to continue, Sir.")
    ctl.resume()
    _notify(game, "resume")
    return Reply("Continuing the series, Sir.", kind="series")


def _i_series_cancel(game: Any) -> Reply:
    if (r := _need_series(game)) is not None:
        return r
    game.series.cancel()
    _notify(game, "cancel")
    return Reply("Series cancelled, Sir. The games so far are kept.", kind="series")


def _i_series_resume_offer(game: Any) -> Reply:
    mod = _mod("series")
    store = _store(game)
    if mod is None or store is None:
        return _bad("Series play is not available yet, Sir.")
    cur = getattr(game, "series", None)
    if cur is not None and cur.state is not None and cur.state.status == "running":
        return _bad("A series is already running, Sir.")
    if cur is not None and cur.state is not None and cur.state.status == "paused":
        ctl, st = cur, cur.state
    else:
        ctl = mod.SeriesController(store)
        st = ctl.resume_offer()
        if st is None:
            return _bad("There's no unfinished series, Sir.")
    if st.status == "paused":
        ctl.resume()
    game.series = ctl
    game.pending = None
    _notify(game, "resume")
    return Reply(
        f"Resuming your series at game {_w(len(st.game_ids) + 1)}, Sir.", kind="series"
    )


# -------------------------------------------------------------------- history
def _i_list_games(game: Any) -> Reply:
    store = _store(game)
    if store is None:
        return _bad(NOT_YET, "history")
    items = store.list(limit=20)
    if not items:
        game.games_list = []
        return Reply(
            "You have no games on record yet, Sir.", kind="history", data={"games": []}
        )
    game.games_list = items
    top = items[0]
    say = (
        f"{_w(len(items)) if len(items) < 20 else 'Twenty'} game{'s' * (len(items) != 1)} on record. "
        f"The latest ended {_res(top.get('result'))}. Say 'open game one' to review it."
    )
    return Reply(say, kind="history", data={"games": items})


def _res(r: str | None) -> str:
    return {
        "1-0": "one nil to white",
        "0-1": "one nil to black",
        "1/2-1/2": "in a draw",
    }.get(r or "", "unfinished")


def _where(view: Any) -> str:
    p, tot = view.current_ply, view.total_plies
    if p == 0:
        return "the starting position"
    rec = view.record
    san = _take(rec, p).get("san", "")
    return f"move {(p + 1) // 2}, {san}"


def _hist_data(view: Any) -> dict:
    return {
        "history": {
            "ply": view.current_ply,
            "total": view.total_plies,
            "fen": view.fen(),
        }
    }


def _i_open_game(game: Any, ref: str = "1") -> Reply:
    hist = _mod("history")
    store = _store(game)
    if hist is None or store is None:
        return _bad(NOT_YET, "history")
    if getattr(game.state, "mode", "play") in ("takeover", "spectate"):
        return _bad("Release control first, Sir. Say 'my turn'.", "history")
    view = hist.HistoryView(store)
    try:
        view.open(ref)
    except hist.HistoryError:
        return _bad("I can't find that game, Sir.", "history")
    game.hist_view = view
    r = Reply(
        f"Game opened, {_w(view.total_plies // 2 + view.total_plies % 2)} moves. Say 'step back' or 'go to move' and a number.",
        kind="history",
        data=_hist_data(view),
    )
    return r


def _need_view(game: Any) -> Reply | None:
    if not _hist_active(game):
        return _bad("Open a game first, Sir. Say 'list games'.", "history")
    return None


def _i_hist_step(game: Any, delta: int = 1) -> Reply:
    if (r := _need_view(game)) is not None:
        return r
    v = game.hist_view
    before = v.current_ply
    v.step(delta)
    if v.current_ply == before:
        return Reply(
            "That's the " + ("start" if delta < 0 else "end") + " of the game, Sir.",
            kind="history",
            data=_hist_data(v),
        )
    return Reply(f"{_where(v).capitalize()}.", kind="history", data=_hist_data(v))


def _i_hist_goto(game: Any, n: int = 1) -> Reply:
    if (r := _need_view(game)) is not None:
        return r
    v = game.hist_view
    v.goto_move(n)
    return Reply(f"{_where(v).capitalize()}.", kind="history", data=_hist_data(v))


def _i_hist_close(game: Any) -> Reply:
    if not _hist_active(game):
        return _bad("No game is open, Sir.", "history")
    game.hist_view = None
    return Reply("Closed, Sir.", kind="history")


def _branch_ply(n: int, user_white: bool) -> int:
    """Position just before the user's move n."""
    return 2 * (n - 1) + (0 if user_white else 1)


async def _load_branch(game: Any, rec: Any) -> None:
    moves = [m["uci"] for m in rec.moves]
    await game.new_game(
        colour="white" if rec.white == "user" else "black", moves=moves, record=rec
    )


async def _i_hist_branch(game: Any, n: int | None = None) -> Reply:
    """Branch a NEW game from the saved one (store.branch); original untouched."""
    if _hist_active(game):
        v = game.hist_view
        if n is not None:
            v.goto(_branch_ply(n, v.record.white == "user"))
        rec = v.branch_here()
        game.hist_view = None
        await _load_branch(game, rec)
        return Reply(
            "Branching from here, Sir. The original game is untouched.", kind="history"
        )
    st = game.state
    if n is None or not getattr(st, "move_count", 0):
        return _bad("Open a game first, Sir.", "history")
    target = _branch_ply(n, st.human_colour == chess.WHITE)
    k = st.move_count - target
    if k <= 0:
        return _bad("We haven't reached that move yet, Sir.", "history")
    live = getattr(game, "_rec", None)
    if live is not None and live.moves:
        game._sync_record()
        rec = game.store.branch(live, target)
        await _load_branch(game, rec)
        return Reply(
            f"Branching a new game from move {_w(n)}, Sir. The original is untouched.",
            kind="history",
        )
    st.undo(k)
    game._sync_record()
    await game._changed()
    return Reply(f"Back to move {_w(n)}, Sir. Your turn to continue.", kind="history")


# ------------------------------------------------------------------- analysis
async def _i_analyse_board(game: Any) -> Reply:
    # saved eval -> instant 2-sentence answer; else the engine runs in the background
    return await game.analyse_board_now()


async def _i_explain(
    game: Any, n: int | None = None, which: str | None = None
) -> Reply | None:
    """Explain from saved evals; None -> legacy engine explanation."""
    return await game.explain_saved(n)


_HANDLERS = {
    "series_start": _i_series_start,
    "series_bad_count": _i_series_bad_count,
    "series_option": _i_series_option,
    "series_left": _i_series_left,
    "series_score": _i_series_score,
    "series_pause": _i_series_pause,
    "series_resume": _i_series_resume,
    "series_cancel": _i_series_cancel,
    "series_resume_offer": _i_series_resume_offer,
    "list_games": _i_list_games,
    "open_game": _i_open_game,
    "hist_step": _i_hist_step,
    "hist_goto": _i_hist_goto,
    "hist_close": _i_hist_close,
    "hist_branch": _i_hist_branch,
    "analyse_board": _i_analyse_board,
    "explain": _i_explain,
}
