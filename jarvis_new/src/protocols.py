"""Protocol system: numbered voice/text macros ("Initiate protocol 3").

Assignments live in ``~/.config/jarvis/protocols.json`` as number -> record::

    {"12": {"action": "builtin:network-theatre", "hidden": true,
            "silent": true, "locked": true},
     "3":  {"action": "open spotify", "hidden": false, "silent": false}}

``action`` is either a ``builtin:<name>`` or free text replayed through the
instant voice route (bridge.handle_route). Executing a protocol never
restates its action: the only reply is "Protocol N engaged" (or "Protocol N
is unassigned"), and none at all for a silent protocol. Hidden protocols are
omitted from every listing. Protocol 12 is seeded on every load, locked
(voice cannot edit, clear or flag it), hidden and silent.

Parsing is pure; :func:`handle` does the I/O. Both voice and typed text reach
it through ``bridge._match_voice_tool`` (tool ``protocol``).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

BUILTIN_NETWORK = "builtin:network-theatre"
NETWORK_PROTOCOL = 12

_LOCK = threading.Lock()

#: HUD toast: the web view polls /protocol/state for it. {"seq", "text", "ts"}
_notice: dict[str, Any] = {"seq": 0, "text": "", "ts": 0.0}


def store_path() -> Path:
    override = os.environ.get("JARVIS_PROTOCOLS_PATH")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "jarvis" / "protocols.json"


def _seed() -> dict[str, dict]:
    return {
        str(NETWORK_PROTOCOL): {
            "action": BUILTIN_NETWORK,
            "hidden": True,
            "silent": True,
            "locked": True,
        }
    }


def load(path: Path | None = None) -> dict[str, dict]:
    """Store contents with protocol 12 forced back to its locked seed."""
    path = path or store_path()
    data: dict[str, dict] = {}
    try:
        raw = json.loads(path.read_text("utf-8"))
        if isinstance(raw, dict):
            for key, rec in raw.items():
                if not str(key).isdigit() or not isinstance(rec, dict):
                    continue
                action = rec.get("action")
                if not isinstance(action, str) or not action.strip():
                    continue
                data[str(int(key))] = {
                    "action": action.strip(),
                    "hidden": bool(rec.get("hidden", False)),
                    "silent": bool(rec.get("silent", False)),
                    **({"locked": True} if rec.get("locked") else {}),
                }
    except (OSError, ValueError):
        pass
    data.update(_seed())
    return data


def save(data: dict[str, dict], path: Path | None = None) -> None:
    path = path or store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


# ---------------------------------------------------------------- parsing

_UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip

_NUM = r"(\d{1,3}|[a-z]+(?:[\s-][a-z]+)?)"
_VERB_RUN = r"(?:initiate|execute|engage|activate|run|launch|start)"
_VERB_SET = r"(?:assign|set|bind|map|link|attach|program)"
_LEAD = r"^(?:please\s+|can you\s+|could you\s+)?"

_RUN = re.compile(
    _LEAD + _VERB_RUN + r"\s+protocol\s+(?:number\s+)?" + _NUM + r"[.!]?$"
)
_ASSIGN = re.compile(
    _LEAD
    + _VERB_SET
    + r"\s+protocol\s+(?:number\s+)?"
    + _NUM
    + r"\s+(?:to|as|for|so that|so it)\s+(.+)$"
)
_ASSIGN_REV = re.compile(
    _LEAD
    + _VERB_SET
    + r"\s+(.+?)\s+(?:to|as|on)\s+protocol\s+(?:number\s+)?"
    + _NUM
    + r"[.!]?$"
)
_FLAG = re.compile(
    _LEAD
    + r"(?:make|set|mark)\s+protocol\s+(?:number\s+)?"
    + _NUM
    + r"\s+(hidden|silent|visible|audible|quiet|loud)[.!]?$"
)
_CLEAR = re.compile(
    _LEAD
    + r"(?:clear|unassign|remove|delete|forget|unbind)\s+protocol\s+(?:number\s+)?"
    + _NUM
    + r"[.!]?$"
)
_LIST = re.compile(
    _LEAD
    + r"(?:list|show|read)(?:\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?(?:the\s+)?)?protocols?[.!]?$"
    r"|^what protocols (?:do i have|are (?:assigned|set))[.?!]?$"
)
_STAND_DOWN = re.compile(
    r"^(?:jarvis[,\s]+)?(?:stand down|stand-down|abort protocol|disengage)[.!]?$"
)


def parse_number(token: str) -> int | None:
    """Digits or spoken number ("twelve", "twenty one") -> int, else None."""
    token = (token or "").strip().lower().replace("-", " ")
    if token.isdigit():
        return int(token)
    words = token.split()
    if len(words) == 1:
        if words[0] in _UNITS:
            return _UNITS[words[0]]
        return _TENS.get(words[0])
    if len(words) == 2 and words[0] in _TENS and 1 <= _UNITS.get(words[1], 0) <= 9:
        return _TENS[words[0]] + _UNITS[words[1]]
    return None


def _norm(text: str) -> str:
    text = re.sub(r"[,;]+", " ", (text or "").lower().strip())
    return re.sub(r"\s+", " ", text)


def parse(text: str) -> dict | None:
    """Spoken/typed text -> {"op": ..., ...} or None when not a protocol command.

    ops: run(n) · assign(n, action) · clear(n) · flag(n, flag, value) · list ·
    abort. Pure.
    """
    t = _norm(text)
    if not t:
        return None
    if _STAND_DOWN.match(t):
        return {"op": "abort"}
    if _LIST.match(t):
        return {"op": "list"}
    for pattern, op in ((_RUN, "run"), (_CLEAR, "clear")):
        m = pattern.match(t)
        if m:
            n = parse_number(m.group(1))
            return {"op": op, "n": n} if n is not None else None
    m = _FLAG.match(t)
    if m:
        n = parse_number(m.group(1))
        if n is None:
            return None
        word = m.group(2)
        flag = "hidden" if word in ("hidden", "visible") else "silent"
        return {
            "op": "flag",
            "n": n,
            "flag": flag,
            "value": word in ("hidden", "silent", "quiet"),
        }
    m = _ASSIGN.match(t)
    if m:
        n = parse_number(m.group(1))
        action = m.group(2).strip(" .!")
        return (
            {"op": "assign", "n": n, "action": action}
            if n is not None and action
            else None
        )
    m = _ASSIGN_REV.match(t)
    if m:
        n = parse_number(m.group(2))
        action = m.group(1).strip(" .!")
        return (
            {"op": "assign", "n": n, "action": action}
            if n is not None and action
            else None
        )
    return None


# ---------------------------------------------------------------- engine


def _notify(text: str) -> None:
    _notice["seq"] += 1
    _notice["text"] = text
    _notice["ts"] = time.time()


def notice() -> dict[str, Any]:
    return dict(_notice)


def _reply(
    text: str, *, ok: bool = True, silent: bool = False, notice_text: str | None = None
) -> dict:
    """Result dict. ``say`` is what is spoken; "" means say nothing."""
    if notice_text is None:
        notice_text = text
    if not silent and notice_text:
        _notify(notice_text)
    return {"ok": ok, "say": "" if silent else text}


def handle(
    cmd: dict,
    *,
    run_builtin: Callable[[str], dict] | None = None,
    run_text: Callable[[str], bool] | None = None,
    abort: Callable[[], None] | None = None,
    path: Path | None = None,
) -> dict:
    """Execute a parsed command. Returns {"ok", "say"} (say == "" = silent).

    ``run_builtin(name)`` starts a builtin (returns {"ok": bool}), ``run_text``
    replays free text through the instant route (True = it resolved), ``abort``
    stands down the running scan. All three default to the real ones.
    """
    op = cmd.get("op")
    if op == "abort":
        (abort or _default_abort)()
        return {"ok": True, "say": ""}
    with _LOCK:
        data = load(path)
        if op == "run":
            return _run(int(cmd["n"]), data, run_builtin, run_text)
        if op == "list":
            shown = sorted(
                (int(n) for n, rec in data.items() if not rec.get("hidden")),
            )
            if not shown:
                return _reply("No protocols are assigned.")
            numbers = ", ".join(str(n) for n in shown)
            return _reply(
                f"Protocols assigned: {numbers}."
                if len(shown) > 1
                else f"Protocol {shown[0]} is assigned."
            )
        n = int(cmd["n"])
        key = str(n)
        rec = data.get(key)
        if rec and rec.get("locked"):
            return _reply(f"Protocol {n} is locked.", ok=False)
        if op == "assign":
            action = str(cmd["action"]).strip()
            if action.startswith("builtin:"):
                return _reply(
                    "Builtin protocols cannot be assigned by voice.", ok=False
                )
            keep = rec or {}
            data[key] = {
                "action": action,
                "hidden": bool(keep.get("hidden", False)),
                "silent": bool(keep.get("silent", False)),
            }
            save(data, path)
            return _reply(f"Protocol {n} assigned.")
        if op == "clear":
            if rec is None:
                return _reply(f"Protocol {n} is unassigned.", ok=False)
            del data[key]
            save(data, path)
            return _reply(f"Protocol {n} cleared.")
        if op == "flag":
            if rec is None:
                return _reply(f"Protocol {n} is unassigned.", ok=False)
            rec[cmd["flag"]] = bool(cmd["value"])
            save(data, path)
            return _reply(
                f"Protocol {n} is now {'' if cmd['value'] else 'not '}{cmd['flag']}."
            )
    return {"ok": False, "say": "", "error": "unknown protocol op"}


def _run(
    n: int,
    data: dict[str, dict],
    run_builtin: Callable[[str], dict] | None,
    run_text: Callable[[str], bool] | None,
) -> dict:
    rec = data.get(str(n))
    if rec is None:
        return _reply(f"Protocol {n} is unassigned.", ok=False)
    silent = bool(rec.get("silent"))
    action = rec["action"]
    if action.startswith("builtin:"):
        runner = run_builtin or _default_builtin
        res = runner(action.split(":", 1)[1])
        ok = bool(res.get("ok"))
        # A builtin that could not even start reports why (e.g. nmap missing)
        # on the HUD; the engaged line is only for a run that really began.
        if not ok:
            reason = str(res.get("error") or f"Protocol {n} failed.")
            _notify(reason)
            return {"ok": False, "say": "", "error": reason}
        return _reply(f"Protocol {n} engaged", silent=silent)
    ok = (run_text or _default_text)(action)
    if not ok:
        return _reply(f"Protocol {n} failed.", ok=False)
    return _reply(f"Protocol {n} engaged", silent=silent)


def _default_builtin(name: str) -> dict:
    if name == "network-theatre":
        import netscan

        return netscan.start()
    return {"ok": False, "error": f"Unknown builtin {name}."}


def _default_text(action: str) -> bool:
    """Replay the stored action through the instant route, reply discarded."""
    try:
        from bridge import handle_route

        code, payload = handle_route({"text": action})
        return (
            code == 200
            and bool(payload.get("ok"))
            and ((payload.get("action") or {}).get("ok", True) is not False)
        )
    except Exception:
        return False


def _default_abort() -> None:
    try:
        import netscan

        netscan.abort()
    except Exception:
        pass
