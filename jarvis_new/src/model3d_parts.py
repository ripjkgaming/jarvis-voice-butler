"""Per-object motion control for the 3D hologram (voice only).

Works for ANY generated glb. Each mesh node becomes a controllable part:
  1. explicit rig: glTF node extras jarvis_motion / jarvis_axis / jarvis_pivot / jarvis_limit
     (written by the generator, see ~/deck/lib/3d_style.md "MOTION RIG");
  2. else the role is guessed from the node name (door -> hinge, wheel -> spin, bulb -> light ...);
  3. else it is "generic": any part can still be lifted, turned, tilted, slid or spun by name.
The viewer (holo-stage.tsx) turns the `fx` state into motion; this module only
resolves words ("open the left door", "turn the lamp off") into fx changes.

fx = {part_id: {"on": bool, "level": 0..1, "open": 0..1, "spin": float, "swing": -1..1,
                "turn": -1..1, "slide": 0..1, "wave": bool, "color": "#rrggbb"}}
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

ROLE_WORDS = {
    "light": r"bulb|diffuser|lens|headlight|taillight|tail_light|head_light|light|lamp_?head|led|beacon|indicator|screen|display|eye|eyes|glow|halo|aperture|flame|neon|laser",
    "hinge": r"door|lid|hood|bonnet|trunk|boot|flap|hatch|cover|gate|window|visor|cap|wing_flap|shutter|panel_door|cabinet",
    "spin": r"wheel|tire|tyre|fan|blade|blades|propeller|prop|rotor|gear|cog|turbine|drum|knob|dial|spinner|reel|turntable|record",
    "swing": r"lampshade|arm|forearm|upper_arm|lower_arm|neck|head|leg|tail|jaw|ear|finger|hand|boom|shade|tongue|claw|antenna|wing|flipper|trunk_arm|crane|elbow|shoulder",
    "slide": r"drawer|piston|slider|tray|lever|plunger|sliding|rail|telescop",
}
_ORDER = ("light", "hinge", "spin", "slide", "swing")
_SIDE = {
    "left": "left",
    "right": "right",
    "front": "front",
    "back": "back",
    "rear": "back",
    "top": "top",
    "bottom": "bottom",
    "upper": "upper",
    "lower": "lower",
    "fl": "front left",
    "fr": "front right",
    "rl": "back left",
    "rr": "back right",
}
_NOISE = {
    "mesh",
    "object",
    "part",
    "geo",
    "geometry",
    "pivot",
    "joint",
    "pin",
    "bezel",
    "ring",
    "rim",
    "seam",
    "edge",
    "tracer",
    "accent",
    "detail",
    "trim",
    "enamel",
    "low",
    "poly",
    "twelve",
    "sided",
    "brass",
    "facet",
    "faceted",
    "core",
    "weighted",
    "circular",
    "octagonal",
    "graphite",
    "warm",
    "brushed",
    "forest",
    "deep",
    "red",
    "holo",
    "holographic",
}
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "my",
    "it",
    "its",
    "please",
    "turn",
    "switch",
    "put",
    "set",
    "make",
    "can",
    "you",
    "to",
    "on",
    "off",
    "up",
    "down",
    "and",
    "all",
    "this",
    "that",
    "hologram",
    "model",
    "object",
}


def _sanitize(name: str) -> str:
    """Match three.js PropertyBinding.sanitizeNodeName so the viewer finds the mesh."""
    return re.sub(r"[\[\].:/]", "", re.sub(r"\s", "_", name))


def _words(name: str) -> list[str]:
    n = re.sub(r"\.\d+$", "", name)
    n = re.sub(r"([a-z])([A-Z])", r"\1 \2", n)
    return [w for w in re.split(r"[^a-z0-9]+", n.lower()) if w]


def _role(name: str, extras: dict | None, emissive: bool) -> str:
    m = (extras or {}).get("jarvis_motion")
    if m in ROLE_WORDS:
        return m
    low = "_".join(_words(name))
    for role in _ORDER:
        if re.search(rf"(?:^|_)(?:{ROLE_WORDS[role]})(?:s|_|$)", low):
            return role
    return "light" if emissive else "generic"


def read_glb(path: Path) -> dict:
    b = path.read_bytes()
    n = struct.unpack("<I", b[12:16])[0]
    return json.loads(b[20 : 20 + n])


def parts_of(glb: Path | None) -> list[dict]:
    """Mesh nodes of the glb as [{id, name, role, words, extras}]."""
    if not glb or not glb.is_file():
        return []
    try:
        j = read_glb(glb)
    except Exception:
        return []
    mats = j.get("materials", [])
    meshes = j.get("meshes", [])

    def emissive(mi: int | None) -> bool:
        if mi is None or mi >= len(meshes):
            return False
        for pr in meshes[mi].get("primitives", []):
            mt = (
                mats[pr["material"]]
                if pr.get("material") is not None and pr["material"] < len(mats)
                else {}
            )
            ef = mt.get("emissiveFactor")
            if ef and max(ef) > 0.05:
                return True
        return False

    out = []
    for i, nd in enumerate(j.get("nodes", [])):
        if nd.get("mesh") is None:
            continue
        name = nd.get("name") or f"part_{i}"
        ex = nd.get("extras") or {}
        out.append(
            {
                "id": _sanitize(name),
                "name": name,
                "role": _role(name, ex, emissive(nd.get("mesh"))),
                "words": [w for w in _words(name) if w not in _NOISE],
                "extras": {k: v for k, v in ex.items() if k.startswith("jarvis_")},
            }
        )
    return out


def capabilities(glb: Path | None) -> list[dict]:
    """Group parts into voice targets: [{id, label, role, parts:[ids], actions}]."""
    ps = parts_of(glb)
    groups: dict[str, dict] = {}
    for p in ps:
        role = p["role"]
        core = [
            w
            for w in p["words"]
            if w not in _SIDE and w not in ("upper", "lower", "top", "bottom")
        ]
        allw = _words(p["name"])
        key = core[-1] if core else (allw[-1] if allw else p["id"])
        if role == "light":  # all lights act together unless a side is named
            key = "lights"
        g = groups.setdefault(
            key,
            {
                "id": key,
                "label": key.replace("_", " "),
                "role": role,
                "parts": [],
                "words": set(),
            },
        )
        g["parts"].append(p["id"])
        g["words"].update(p["words"])
        if g["role"] == "generic" and role != "generic":
            g["role"] = role
    caps = []
    for g in groups.values():
        g["words"] = sorted(g["words"])
        g["actions"] = ACTIONS[g["role"]]
        caps.append(g)
    return caps


ACTIONS = {
    "light": ["on", "off", "dim", "brighten", "colour"],
    "hinge": ["open", "close"],
    "spin": ["spin", "stop", "faster", "slower", "reverse"],
    "swing": ["raise", "lower", "turn left", "turn right", "wave", "straighten"],
    "slide": ["extend", "retract"],
    "generic": ["raise", "lower", "turn left", "turn right", "spin", "slide", "reset"],
}

# ---------------------------------------------------------------- words -> action

COLORS = {
    "red": "#ff3b30",
    "green": "#34ff7a",
    "blue": "#3b82ff",
    "yellow": "#ffe14a",
    "orange": "#ff9a2e",
    "purple": "#a45bff",
    "pink": "#ff6fb5",
    "white": "#ffffff",
    "cyan": "#5fe3ff",
    "warm": "#ffc46b",
    "cool": "#bfe7ff",
    "gold": "#ffd24d",
}

_ACT = [
    (
        r"\b(turn|switch|put|power)\b.*\boff\b|\boff\b|\bkill\b|\bdarken\b|\bextinguish\b",
        "off",
    ),
    (
        r"\b(turn|switch|put|power)\b.*\bon\b|\bon\b|\blight up\b|\billuminate\b|\bignite\b",
        "on",
    ),
    (r"\bdim\b|\bdimmer\b|\blower the brightness\b", "dim"),
    (r"\bbrighter\b|\bbrighten\b|\bmore light\b", "brighten"),
    (r"\bclose\b|\bshut\b", "close"),
    (r"\bopen\b|\bunlock\b|\bswing open\b", "open"),
    (r"\bstop\b|\bhalt\b|\bfreeze\b|\bstill\b", "stop"),
    (r"\bfaster\b|\bspeed up\b|\bquicker\b", "faster"),
    (r"\bslower\b|\bslow down\b", "slower"),
    (r"\breverse\b|\bother way\b", "reverse"),
    (r"\bspin\b|\brotate\b|\brun\b|\bstart\b|\bwhirl\b", "spin"),
    (r"\braise\b|\blift\b|\bup\b|\bhigher\b|\btilt up\b|\bnod up\b", "raise"),
    (r"\blower\b|\bdown\b|\blet down\b|\btilt down\b|\bdrop\b", "lower"),
    (r"\bleft\b", "turn_left"),
    (r"\bright\b", "turn_right"),
    (r"\bwave\b|\bwag\b|\bwiggle\b|\bflap\b|\bnod\b", "wave"),
    (r"\bextend\b|\bpull out\b|\bslide out\b|\bpush out\b", "extend"),
    (r"\bretract\b|\bpush in\b|\bslide in\b|\bpull in\b", "retract"),
    (r"\bstraighten\b|\breset\b|\bcentre\b|\bcenter\b|\bhome\b|\brest\b", "reset"),
]


def parse_action(text: str) -> str | None:
    t = text.lower()
    for pat, act in _ACT:
        if re.search(pat, t):
            return act
    return None


def resolve_target(text: str, caps: list[dict]) -> list[dict]:
    """Capabilities addressed by the words (side words narrow to matching parts)."""
    toks = [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w and w not in _STOP]
    toks = [{"rear": "back"}.get(w, w) for w in toks]
    scored = []
    for c in caps:
        sc = 0
        for w in toks:
            for cw in c["words"] + [c["id"], c["label"]]:
                if w == cw or (len(w) > 3 and (w.rstrip("s") == cw.rstrip("s"))):
                    sc += 2
        if "light" in toks or "lights" in toks or "lamp" in toks:
            sc += 1 if c["role"] == "light" else 0
        if sc:
            scored.append((sc, c))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    return [c for s, c in scored if s == top]


def side_filter(text: str, cap: dict, parts: list[dict]) -> list[str]:
    sides = [
        w
        for w in re.split(r"[^a-z0-9]+", text.lower())
        if w
        in ("left", "right", "front", "back", "rear", "top", "bottom", "upper", "lower")
    ]
    sides = ["back" if s == "rear" else s for s in sides]
    if not sides or len(cap["parts"]) < 2:
        return cap["parts"]
    byid = {p["id"]: p for p in parts}
    hit = [
        pid
        for pid in cap["parts"]
        if all(
            s in byid[pid]["words"] or (s == "back" and "rear" in byid[pid]["words"])
            for s in sides
        )
    ]
    return hit or cap["parts"]


def apply(fx: dict, cap: dict, ids: list[str], act: str, text: str) -> tuple[dict, str]:
    """Return (new fx, spoken reply). Mutates a copy of fx."""
    fx = {k: dict(v) for k, v in (fx or {}).items()}
    role, label = cap["role"], cap["label"]
    reply = ""
    for pid in ids:
        f = fx.setdefault(pid, {})
        if act == "on":
            f.update(on=True, level=max(0.7, f.get("level", 1.0) or 1.0))
            reply = f"{label.capitalize()} on."
        elif act == "off":
            f.update(on=False)
            reply = f"{label.capitalize()} off."
        elif act == "dim":
            f.update(on=True, level=max(0.15, f.get("level", 1.0) * 0.55))
            reply = f"{label.capitalize()} dimmed."
        elif act == "brighten":
            f.update(
                on=True,
                level=min(
                    1.0, f.get("level", 1.0) / 0.55 if f.get("level", 1.0) < 1 else 1.0
                ),
            )
            reply = f"{label.capitalize()} brighter."
        elif act == "open":
            f["open"] = 1.0
            reply = f"Opening the {label}."
        elif act == "close":
            f["open"] = 0.0
            reply = f"Closing the {label}."
        elif act == "extend":
            f["slide"] = 1.0
            reply = f"Extending the {label}."
        elif act == "retract":
            f["slide"] = 0.0
            reply = f"Retracting the {label}."
        elif act == "spin":
            f["spin"] = f.get("spin") or 1.0
            reply = f"Spinning the {label}."
        elif act == "stop":
            f["spin"] = 0.0
            f["wave"] = False
            reply = f"{label.capitalize()} stopped."
        elif act in ("faster", "slower"):
            cur = f.get("spin") or 1.0
            f["spin"] = max(
                0.15, min(12.0, cur * (1.7 if act == "faster" else 1 / 1.7))
            ) * (1 if cur >= 0 else -1)
            reply = f"{label.capitalize()} {act}."
        elif act == "reverse":
            f["spin"] = -(f.get("spin") or 1.0)
            reply = f"{label.capitalize()} reversed."
        elif act == "raise":
            if role == "slide":
                f["slide"] = 1.0
            else:
                f["swing"] = min(1.0, f.get("swing", 0.0) + 0.5)
            reply = f"Raising the {label}."
        elif act == "lower":
            if role == "slide":
                f["slide"] = 0.0
            else:
                f["swing"] = max(-1.0, f.get("swing", 0.0) - 0.5)
            reply = f"Lowering the {label}."
        elif act in ("turn_left", "turn_right"):
            d = -1 if act == "turn_left" else 1
            f["turn"] = max(-1.0, min(1.0, f.get("turn", 0.0) + 0.5 * d))
            reply = f"Turning the {label} {'left' if d < 0 else 'right'}."
        elif act == "wave":
            f["wave"] = True
            reply = f"Waving the {label}."
        elif act == "reset":
            fx[pid] = {"on": f.get("on", True), "level": 1.0}
            reply = f"{label.capitalize()} reset."
    col = next(
        (hexv for n, hexv in COLORS.items() if re.search(rf"\b{n}\b", text.lower())),
        None,
    )
    if col and role == "light":
        for pid in ids:
            fx.setdefault(pid, {}).update(color=col, on=True)
        reply = f"{label.capitalize()} {next(n for n in COLORS if re.search(rf'\b{n}\b', text.lower()))}."
    return fx, reply


def describe(caps: list[dict]) -> str:
    if not caps:
        return "This model has no named parts to control, Sir."
    bits = [f"{c['label']} ({', '.join(c['actions'][:4])})" for c in caps[:8]]
    return "I can control: " + "; ".join(bits) + "."
