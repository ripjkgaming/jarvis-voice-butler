"""Object-specific voice commands written by the model that built the object.

make3d/edit3d ask Codex to write <project>/commands.json next to the glb:

  {"object": "desk lamp",
   "commands": [
     {"say": ["turn the lamp off", "lights out"], "part": "light_bulb", "action": "off", "reply": "Lamp off, Sir."},
     {"say": ["power down"], "steps": [{"part": "light_bulb", "action": "off"}, {"part": "fan_blades", "action": "stop"}],
      "reply": "Powering down."}
   ]}

part_command() asks match() first; on a hit, run() turns the command into the same
fx dict the hologram stage already understands. Falls back to the generic parser."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

import model3d_parts as mp

ACTIONS = {
    "on",
    "off",
    "dim",
    "brighten",
    "open",
    "close",
    "spin",
    "stop",
    "faster",
    "slower",
    "reverse",
    "extend",
    "retract",
    "raise",
    "lower",
    "turn_left",
    "turn_right",
    "wave",
    "reset",
}
_DROP = {
    "please",
    "jarvis",
    "sir",
    "the",
    "a",
    "an",
    "my",
    "can",
    "you",
    "could",
    "would",
}


def _norm(s: str) -> list[str]:
    return [
        w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if w and w not in _DROP
    ]


def path_for(glb: Path | None) -> Path | None:
    return glb.with_name("commands.json") if glb else None


def load(glb: Path | None) -> list[dict]:
    p = path_for(glb)
    if not p or not p.is_file():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for c in data.get("commands", []) if isinstance(data, dict) else []:
        if not isinstance(c, dict) or not c.get("say"):
            continue
        steps = c.get("steps") or (
            [{"part": c.get("part"), "action": c.get("action")}]
            if c.get("action")
            else []
        )
        steps = [
            s
            for s in steps
            if isinstance(s, dict)
            and s.get("part")
            and str(s.get("action", "")).replace(" ", "_") in ACTIONS
        ]
        if not steps:
            continue
        out.append(
            {
                "say": [
                    str(x)
                    for x in (c["say"] if isinstance(c["say"], list) else [c["say"]])
                ],
                "steps": steps,
                "reply": str(c.get("reply") or ""),
            }
        )
    return out


def match(glb: Path | None, text: str) -> dict | None:
    """Best command for the spoken text, or None. Exact > contained > fuzzy (>=0.84)."""
    t = _norm(text)
    if not t:
        return None
    tj = " ".join(t)
    best, best_s, tied = None, 0.0, []
    for c in load(glb):
        for phrase in c["say"]:
            p = _norm(phrase)
            if not p:
                continue
            pj = " ".join(p)
            if pj == tj:
                s = 1.0
            elif len(p) >= 2 and (f" {pj} " in f" {tj} "):
                s = 0.95 - 0.01 * (len(t) - len(p))
            else:
                ps, ts = set(p), set(t)
                if (
                    ps == ts
                ):  # same words, other order: "turn lamp off" / "turn off lamp"
                    s = 0.97
                elif (
                    len(t) >= 2 and ts <= ps
                ):  # shorter form of a phrase: "power down" ~ "power down display"
                    s = 0.9 - 0.02 * (len(p) - len(t))
                else:
                    s = difflib.SequenceMatcher(None, pj, tj).ratio()
                    if s < 0.84:
                        continue
            if s > best_s + 1e-9:
                best, best_s, tied = c, s, [c]
            elif abs(s - best_s) <= 1e-9 and c not in tied:
                tied.append(c)
    if len(tied) > 1 and best_s < 0.95:
        # "spin the wheels" fits the group command and each single wheel: take the unique widest command.
        # "raise the arm" fits upper_arm and lower_arm equally: no unique winner, let the generic parser decide.
        top = max(len(c["steps"]) for c in tied)
        wide = [c for c in tied if len(c["steps"]) == top]
        return wide[0] if len(wide) == 1 else None
    return best


_COMP = re.compile(r"(edge|wire|glow|outline)")


def base_of(pid: str, parts: list[dict]) -> str | None:
    """Wireframe companion meshes (<part>_edge_lines, <part>_glowing_edges...) follow their base part."""
    best = None
    for q in parts:
        b = q["id"]
        if (
            b != pid
            and pid.startswith(b + "_")
            and _COMP.search(pid[len(b) :])
            and (best is None or len(b) > len(best))
        ):
            best = b
    return best


def run(glb: Path, cmd: dict, fx: dict, text: str = "") -> tuple[dict, str]:
    """Apply every step; returns (new fx, spoken reply). Unknown part names are skipped."""
    parts = mp.parts_of(glb)
    byid = {p["id"]: p for p in parts}
    byname = {mp._sanitize(p["name"]).lower(): p for p in parts}
    reply = ""
    for st in cmd["steps"]:
        names = st["part"] if isinstance(st["part"], list) else [st["part"]]
        ids, role = [], "generic"
        for n in names:
            p = byid.get(mp._sanitize(str(n))) or byname.get(
                mp._sanitize(str(n)).lower()
            )
            if p:
                ids.append(p["id"])
                role = p["role"]
        if not ids:
            continue
        ids += [
            p["id"]
            for p in parts
            if p["id"] not in ids and base_of(p["id"], parts) in ids
        ]
        act = str(st["action"]).replace(" ", "_")
        cap = {"role": role, "label": str(names[0]).replace("_", " ")}
        fx, r = mp.apply(
            fx, cap, ids, act, st.get("color") or text if act in ("on",) else ""
        )
        reply = reply or r
    return fx, cmd["reply"] or reply or "Done."


def list_phrases(glb: Path | None, limit: int = 8) -> str:
    cs = load(glb)
    if not cs:
        return ""
    return "Try: " + "; ".join(f"“{c['say'][0]}”" for c in cs[:limit]) + "."
