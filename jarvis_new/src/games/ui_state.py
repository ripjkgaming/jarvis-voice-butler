"""Extra UI payload fields (series / games list / history / analysis / takeover).

Pure data plumbing. Reads optional attributes off the game with safe defaults
so it works whether or not the series/store/history/review modules exist.
"""

from __future__ import annotations

from typing import Any


def _call(obj: Any, name: str, default: Any = None) -> Any:
    fn = getattr(obj, name, None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            return default
    return default


def series_payload(game: Any) -> dict[str, Any] | None:
    ctl = getattr(game, "series", None)
    if ctl is None:
        return None
    st = getattr(ctl, "state", ctl)
    status = getattr(st, "status", None)
    return {
        "index": getattr(st, "index", None),
        "total": getattr(st, "total", None),
        "score": _call(ctl, "score_text") or _call(st, "score_text"),
        "status": status,
        "wins": getattr(st, "wins", 0),
        "draws": getattr(st, "draws", 0),
        "losses": getattr(st, "losses", 0),
    }


def history_payload(game: Any) -> dict[str, Any]:
    view = getattr(game, "history_view", None)
    if view is None:
        return {"active": False, "ply": None, "total": None, "game_id": None}
    rec = getattr(view, "rec", None) or getattr(view, "record", None)
    moves = getattr(rec, "moves", None)
    return {
        "active": True,
        "ply": getattr(view, "current_ply", None),
        "total": len(moves) if moves is not None else getattr(view, "total", None),
        "game_id": getattr(rec, "id", None),
    }


def analysis_text(game: Any) -> str | None:
    a = getattr(game, "last_analysis", None)
    if a is None:
        return None
    if isinstance(a, str):
        return a
    if isinstance(a, dict):
        for k in ("full_text", "text", "detail"):
            if a.get(k):
                return str(a[k])
        return None
    return str(getattr(a, "text", "") or "") or None


def extra_fields(game: Any) -> dict[str, Any]:
    games = getattr(game, "games_list", None)
    st = getattr(game, "state", None)
    return {
        "series": series_payload(game),
        "games_list": list(games) if games else [],
        "history": history_payload(game),
        "analysis_detail": analysis_text(game),
        "takeover_active": getattr(st, "mode", None) == "takeover",
    }


def full_snapshot(game: Any) -> dict[str, Any]:
    snap = game.snapshot()
    for k, v in extra_fields(game).items():
        snap.setdefault(k, v)
    return snap


def status_suffix(game: Any) -> str:
    """Text appended to the existing TUI status line."""
    f = extra_fields(game)
    out = ""
    s = f["series"]
    if s and s["index"]:
        tot = s["total"] if s["total"] else "∞"
        out += f"  · GAME {s['index']}/{tot}"
        if s["score"]:
            out += f" {s['score']}"
        if s["status"] and s["status"] != "running":
            out += f" ({s['status']})"
    h = f["history"]
    if h["active"]:
        out += f"  · HISTORY ply {h['ply']}/{h['total']}"
    return out
