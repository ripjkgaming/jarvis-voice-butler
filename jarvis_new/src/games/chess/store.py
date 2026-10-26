"""Durable chess game + series store (stdlib + python-chess only).

Layout under ``$JARVIS_HOME/chess/games/`` (default ``~/.jarvis``):
``<id>.pgn`` + ``<id>.json`` per game, ``series/<series_id>.json`` per series.
Every write is tmp + fsync + os.replace; the json sidecar is written last and
is the commit marker (a game without a readable json does not exist). The
directory is created lazily on first write, never at import or init.
"""

from __future__ import annotations

import copy
import io
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

import chess
import chess.pgn


def default_root() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    base = Path(home).expanduser() if home else Path.home() / ".jarvis"
    return base / "chess" / "games"


@dataclass
class GameRecord:
    id: str
    series_id: str | None = None
    series_index: int | None = None
    parent_id: str | None = None
    branched_at_ply: int | None = None
    started_at: str = ""
    ended_at: str | None = None
    mode: str = "play"
    white: str = "user"
    black: str = "jarvis"
    result: str = "*"
    termination: str | None = None
    status: str = "active"
    moves: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GameRecord":
        names = {f.name for f in fields(cls)}
        rec = cls(**{k: v for k, v in d.items() if k in names})
        rec.moves = list(rec.moves or [])
        return rec


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def pgn_text(rec: GameRecord) -> str:
    game = chess.pgn.Game()
    game.headers["Event"] = "Jarvis chess"
    game.headers["Date"] = (rec.started_at or _now())[:10].replace("-", ".")
    game.headers["White"] = rec.white
    game.headers["Black"] = rec.black
    game.headers["Result"] = rec.result
    game.headers["GameId"] = rec.id
    node = game
    for m in rec.moves:
        try:
            node = node.add_variation(chess.Move.from_uci(m["uci"]))
        except (KeyError, ValueError):
            break
    buf = io.StringIO()
    print(game, file=buf, end="\n\n")
    return buf.getvalue()


class GameStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else default_root()
        self._clean_tmp()

    # -- paths / housekeeping -------------------------------------------
    @property
    def series_dir(self) -> Path:
        return self.root / "series"

    def _clean_tmp(self) -> None:
        for d in (self.root, self.series_dir):
            try:
                for p in d.glob("*.tmp"):
                    try:
                        p.unlink()
                    except OSError:
                        pass
            except OSError:
                pass

    def _ensure(self, d: Path | None = None) -> Path:
        d = d or self.root
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- games ----------------------------------------------------------
    def new_id(self) -> str:
        return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)

    def new_record(self, **kw: Any) -> GameRecord:
        kw.setdefault("started_at", _now())
        rid = kw.pop("id", None) or self.new_id()
        while (self.root / f"{rid}.json").exists():
            rid = self.new_id()
        return GameRecord(id=rid, **kw)

    def save(self, rec: GameRecord) -> None:
        d = self._ensure()
        _atomic_write(d / f"{rec.id}.pgn", pgn_text(rec))
        _atomic_write(d / f"{rec.id}.json", json.dumps(rec.to_dict()))

    def load(self, game_id: str) -> GameRecord | None:
        try:
            data = json.loads((self.root / f"{game_id}.json").read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("id") != game_id:
                return None
            return GameRecord.from_dict(data)
        except (OSError, ValueError, TypeError):
            return None

    def _ids(self) -> list[str]:
        try:
            return sorted((p.stem for p in self.root.glob("*.json")), reverse=True)
        except OSError:
            return []

    def list(self, limit: int | None = None) -> list[dict]:
        out: list[dict] = []
        for gid in self._ids():
            rec = self.load(gid)
            if rec is None:
                continue
            out.append({
                "id": rec.id, "series_id": rec.series_id, "series_index": rec.series_index,
                "started_at": rec.started_at, "ended_at": rec.ended_at, "mode": rec.mode,
                "white": rec.white, "black": rec.black, "result": rec.result,
                "termination": rec.termination, "status": rec.status,
                "plies": len(rec.moves), "parent_id": rec.parent_id,
            })
            if limit is not None and len(out) >= limit:
                break
        return out

    def find(self, ref: Any) -> GameRecord | None:
        ref = str(ref).strip().lower()
        if not ref:
            return None
        items = self.list()
        if ref.isdigit() and len(ref) < 8:
            i = int(ref)
            return self.load(items[i - 1]["id"]) if 1 <= i <= len(items) else None
        if len(ref) == 10 and ref[4] == "-" and ref[7] == "-":
            key = ref.replace("-", "")
            for it in items:
                if it["id"].startswith(key):
                    return self.load(it["id"])
            return None
        for it in items:
            if it["id"].startswith(ref):
                return self.load(it["id"])
        return None

    def latest_active(self) -> GameRecord | None:
        for it in self.list():
            if it["status"] == "active":
                return self.load(it["id"])
        return None

    def append_move(self, rec: GameRecord, move: dict) -> None:
        rec.moves.append(dict(move))
        self.save(rec)

    def set_eval(self, game_id: str, ply: int, **fields_: Any) -> bool:
        rec = self.load(game_id)
        if rec is None:
            return False
        for m in rec.moves:
            if m.get("ply") == ply:
                m.update(fields_)
                self.save(rec)
                return True
        return False

    def finish(self, rec: GameRecord, result: str, termination: str) -> None:
        rec.result = result
        rec.termination = termination
        rec.status = "finished"
        rec.ended_at = _now()
        self.save(rec)

    def branch(self, rec: GameRecord, ply: int) -> GameRecord:
        """New game with moves[:ply]; the original is never written."""
        new = self.new_record(
            mode="play", white=rec.white, black=rec.black,
            parent_id=rec.id, branched_at_ply=ply,
        )
        new.moves = copy.deepcopy(rec.moves[: max(0, ply)])
        self.save(new)
        return new

    # -- series ---------------------------------------------------------
    def save_series(self, series: Any) -> None:
        data = series if isinstance(series, dict) else (
            asdict(series) if hasattr(series, "__dataclass_fields__") else dict(series.__dict__))
        d = self._ensure(self.series_dir)
        _atomic_write(d / f"{data['id']}.json", json.dumps(data))

    def load_series(self, series_id: str) -> dict | None:
        try:
            data = json.loads((self.series_dir / f"{series_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("id") == series_id else None

    def list_series(self) -> list[dict]:
        out = []
        try:
            names = sorted((p.stem for p in self.series_dir.glob("*.json")), reverse=True)
        except OSError:
            return out
        for n in names:
            s = self.load_series(n)
            if s is not None:
                out.append(s)
        return out

    def active_series(self) -> dict | None:
        for s in self.list_series():
            if s.get("status") in ("running", "paused"):
                return s
        return None
