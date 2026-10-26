"""Chess game series: N games (1..100) or until-stop, colour alternation, score.

Store contract (games.chess.store.GameStore), dict based:
  save_series(d: dict), load_series(id) -> dict|None, active_series() -> dict|None
(active = newest series whose status is running/paused.)  Deviation from the
design: SeriesState is persisted as a plain dict (dataclasses.asdict).

next_colours() returns {"user": "white"|"black", "jarvis": <other>} for the
game about to be played (index = games recorded + 1).
summary(analyses): analyses is a list of saved game dicts (or {id: dict});
each has optional id/white/black/moves[{ply,san,eval_cp,eval_mate}], evals
are from White's point of view.
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import asdict, dataclass, field, fields

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
         "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_FILLER = {"games", "game", "of", "please", "x"}
_COLOURS = ("white", "black", "alternate")
_SPEAK = ("moves", "results")


def parse_count(text) -> int | None:
    """Parse a game count (1..100) from digits or words; else None."""
    if text is None:
        return None
    toks = [t for t in re.split(r"[\s\-]+", str(text).lower().strip(" .,!?")) if t]
    toks = [t.strip(".,!?") for t in toks]
    while toks and toks[-1] in _FILLER:
        toks.pop()
    if not toks:
        return None
    n: int | None = None
    if len(toks) == 1 and toks[0].isdigit():
        n = int(toks[0])
    elif toks in (["hundred"], ["a", "hundred"], ["one", "hundred"]):
        n = 100
    elif len(toks) == 1 and toks[0] in _UNITS:
        n = _UNITS[toks[0]]
    elif len(toks) == 1 and toks[0] in _TENS:
        n = _TENS[toks[0]]
    elif (len(toks) == 2 and toks[0] in _TENS and toks[1] in _UNITS
          and 1 <= _UNITS[toks[1]] <= 9):
        n = _TENS[toks[0]] + _UNITS[toks[1]]
    return n if n is not None and 1 <= n <= 100 else None


@dataclass
class SeriesState:
    id: str
    total: int | None = None
    index: int = 1
    wins: int = 0
    draws: int = 0
    losses: int = 0
    colour: str = "alternate"
    level: int | None = None
    move_time: float | None = None
    speak: str = "results"
    status: str = "running"
    game_ids: list = field(default_factory=list)
    started_at: str = ""
    first_user_colour: str = "white"

    @classmethod
    def from_dict(cls, d: dict) -> "SeriesState":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def _other(c: str) -> str:
    return "black" if c == "white" else "white"


def user_outcome(result: str, user_colour: str) -> str | None:
    """'win'|'draw'|'loss' from the user's view; None if unfinished."""
    if result == "1/2-1/2":
        return "draw"
    if result == "1-0":
        return "win" if user_colour == "white" else "loss"
    if result == "0-1":
        return "win" if user_colour == "black" else "loss"
    return None


class SeriesController:
    def __init__(self, store):
        self.store = store
        self.state: SeriesState | None = None

    # -- lifecycle ---------------------------------------------------
    def start(self, total=None, colour="alternate", level=None,
              move_time=None, speak="results") -> SeriesState:
        if total is not None and (isinstance(total, bool) or not isinstance(total, int)
                                  or not 1 <= total <= 100):
            raise ValueError("total must be 1..100 or None")
        if colour not in _COLOURS:
            raise ValueError("bad colour")
        if speak not in _SPEAK:
            raise ValueError("bad speak")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.state = SeriesState(
            id=f"{stamp}-{secrets.token_hex(2)}", total=total, colour=colour,
            level=level, move_time=move_time, speak=speak,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            first_user_colour="black" if colour == "black" else "white")
        self._save()
        return self.state

    def adopt(self, state: SeriesState) -> None:
        self.state = state

    def _save(self) -> None:
        if self.state is not None:
            self.store.save_series(asdict(self.state))

    def pause(self) -> bool:
        if self.state and self.state.status == "running":
            self.state.status = "paused"
            self._save()
            return True
        return False

    def resume(self) -> bool:
        if self.state and self.state.status == "paused":
            self.state.status = "running"
            self._save()
            return True
        return False

    def cancel(self) -> bool:
        """Cancel; recorded games and score are kept."""
        if self.state and self.state.status in ("running", "paused"):
            self.state.status = "cancelled"
            self._save()
            return True
        return False

    # -- play --------------------------------------------------------
    def next_colours(self) -> dict:
        s = self.state
        played = len(s.game_ids)
        user = s.first_user_colour
        if s.colour == "alternate" and played % 2 == 1:
            user = _other(user)
        return {"user": user, "jarvis": _other(user)}

    def record_result(self, game_id, result, user_colour) -> str | None:
        s = self.state
        out = user_outcome(result, user_colour)
        if s is None or out is None or game_id in s.game_ids:
            return None
        s.game_ids.append(game_id)
        if out == "win":
            s.wins += 1
        elif out == "loss":
            s.losses += 1
        else:
            s.draws += 1
        s.index = len(s.game_ids) + 1
        if s.total is not None and len(s.game_ids) >= s.total and s.status != "cancelled":
            s.status = "complete"
        self._save()
        return out

    def remaining(self) -> int | None:
        s = self.state
        if s is None or s.total is None:
            return None
        return max(0, s.total - len(s.game_ids))

    def score_text(self) -> str:
        s = self.state
        if s is None:
            return "No series."
        return f"{s.wins} win{'s' * (s.wins != 1)}, {s.draws} draw{'s' * (s.draws != 1)}, {s.losses} loss{'es' * (s.losses != 1)}"

    def is_done(self) -> bool:
        return self.state is None or self.state.status in ("complete", "cancelled")

    def resume_offer(self) -> SeriesState | None:
        """After restart: unfinished series from the store (also adopted)."""
        d = self.store.active_series()
        if not d:
            return None
        st = SeriesState.from_dict(d)
        if st.status not in ("running", "paused"):
            return None
        if st.total is not None and len(st.game_ids) >= st.total:
            return None
        st.index = len(st.game_ids) + 1
        self.state = st
        return st

    # -- summary -----------------------------------------------------
    def summary(self, analyses) -> dict:
        s = self.state
        games = list(analyses.values()) if isinstance(analyses, dict) else list(analyses or [])
        if isinstance(analyses, dict):
            for k, g in analyses.items():
                g.setdefault("id", k)
        longest = None
        blunder = best = None
        swings = []
        for gi, g in enumerate(games):
            moves = g.get("moves") or []
            gid = g.get("id")
            if longest is None or len(moves) > longest["moves"]:
                longest = {"game_id": gid, "moves": len(moves)}
            if g.get("white") == "user":
                ucol = "white"
            elif g.get("black") == "user":
                ucol = "black"
            else:
                ucol = None
            prev = 0
            for m in moves:
                cur = _cp(m)
                if cur is None:
                    continue
                ply = m.get("ply") or 0
                mover = "white" if ply % 2 == 1 else "black"
                gain = (cur - prev) if mover == "white" else (prev - cur)
                prev = cur
                rec = {"game_id": gid, "ply": ply, "san": m.get("san"),
                       "mover": mover, "swing": gain}
                swings.append(rec)
                if ucol is None or mover == ucol:
                    if gain < 0 and (blunder is None or gain < blunder["swing"]):
                        blunder = rec
                    if gain > 0 and (best is None or gain > best["swing"]):
                        best = rec
        key = sorted(swings, key=lambda r: -abs(r["swing"]))[:3]
        return {
            "score": self.score_text() if s else "",
            "wins": s.wins if s else 0, "draws": s.draws if s else 0,
            "losses": s.losses if s else 0,
            "games": len(s.game_ids) if s else 0,
            "longest_game": longest, "biggest_blunder": blunder,
            "best_move": best, "key_moments": key,
        }


def _cp(m: dict) -> int | None:
    """Eval in centipawns from White's view; mates clamp to +-1000."""
    mate = m.get("eval_mate")
    if mate:
        return 1000 if mate > 0 else -1000
    cp = m.get("eval_cp")
    if cp is None:
        return None
    return max(-1000, min(1000, int(cp)))
