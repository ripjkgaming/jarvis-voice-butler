"""Character registry: loads and validates characters/*.yaml and _motion.yaml.

A new character is a new YAML file; no code change is needed. Boot always
starts as Jarvis; the "recent" hint is informational only.
"""
from __future__ import annotations

import difflib
import json
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ID = "jarvis"
CHARACTERS_DIR = Path(__file__).resolve().parent.parent / "characters"
MOTION_FILE = "_motion.yaml"
REQUIRED_LINES = (
    "greet", "exit", "idle", "error", "ack", "farewell",
    "already_active", "switch_in", "unknown_name", "rollback",
)
_REQUIRED_KEYS = (
    "id", "display_name", "spoken_name", "aliases", "address",
    "persona", "voice", "wake", "theme", "transition", "safety",
)
_COLOR_RE = re.compile(
    r"^(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}"
    r"|rgba\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*(?:0|1|0?\.\d+|1\.0+)\s*\))$"
)
_MOTION_REQUIRED = ("total_ms", "beat_ms", "phases", "signatures")
_MAX_BEAT_MS = 700


class CharacterError(ValueError):
    """Invalid character or motion file; message names file and reason."""


@dataclass(frozen=True)
class Character:
    id: str
    display_name: str
    spoken_name: str
    aliases: tuple[str, ...]
    address: str
    prompt: str
    lines: dict[str, tuple[str, ...]]
    voice: dict[str, Any]
    wake_names: tuple[str, ...]
    theme_tokens: dict[str, Any]
    layout: str
    signature: str
    intensity: float
    drop_character_on: tuple[str, ...] = field(default_factory=tuple)


def _fail(path: Path | str, reason: str) -> CharacterError:
    return CharacterError(f"{Path(path).name}: {reason}")


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise _fail(path, f"unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise _fail(path, "top level must be a mapping")
    return data


def _is_color_key(key: str, value: Any) -> bool:
    return isinstance(value, str) and not key.startswith("font_")


def _validate_motion(data: dict[str, Any], path: Path) -> dict[str, Any]:
    for key in _MOTION_REQUIRED:
        if key not in data:
            raise _fail(path, f"missing key '{key}'")
    total = data["total_ms"]
    if not isinstance(total, (int, float)) or total <= 0:
        raise _fail(path, "total_ms must be positive")
    for key in ("ready_gate_ms", "prewarm_timeout_ms"):
        if key in data and (
            not isinstance(data[key], (int, float)) or isinstance(data[key], bool) or data[key] <= 0
        ):
            raise _fail(path, f"{key} must be positive")
    if (
        "prewarm_timeout_ms" in data
        and isinstance(data.get("ready_gate_ms"), (int, float))
        and data["prewarm_timeout_ms"] < data["ready_gate_ms"]
    ):
        raise _fail(path, "prewarm_timeout_ms must be >= ready_gate_ms")
    beat = data["beat_ms"]
    if not isinstance(beat, (int, float)) or not 0 < beat <= _MAX_BEAT_MS:
        raise _fail(path, f"beat_ms must be in (0, {_MAX_BEAT_MS}]")

    def check_span(name: str, span: Any) -> None:
        if (
            not isinstance(span, (list, tuple)) or len(span) != 2
            or not all(isinstance(v, (int, float)) for v in span)
        ):
            raise _fail(path, f"{name} must be [start_ms, end_ms]")
        if not 0 <= span[0] <= span[1] <= total:
            raise _fail(path, f"{name} {list(span)} outside [0, {total}]")

    for name, span in data["phases"].items():
        if isinstance(span, (int, float)):
            if not 0 <= span <= total:
                raise _fail(path, f"phase {name}={span} outside [0, {total}]")
        else:
            check_span(f"phase {name}", span)
    for sig, parts in data["signatures"].items():
        if not isinstance(parts, dict) or not parts:
            raise _fail(path, f"signature {sig} must be a non-empty mapping")
        for name, span in parts.items():
            check_span(f"signature {sig}.{name}", span)
    return data


def motion(dir: Path | str | None = None) -> dict[str, Any]:  # noqa: A002
    path = Path(dir or CHARACTERS_DIR) / MOTION_FILE
    return _validate_motion(_read_yaml(path), path)


def _str_list(path: Path, name: str, value: Any, *, allow_empty: bool = False) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not all(isinstance(v, str) and v.strip() for v in value)
        or (not value and not allow_empty)
    ):
        raise _fail(path, f"{name} must be a non-empty list of strings")
    return tuple(value)


def _parse(path: Path, data: dict[str, Any], signatures: set[str]) -> Character:
    for key in _REQUIRED_KEYS:
        if key not in data:
            raise _fail(path, f"missing key '{key}'")
    for key in ("id", "display_name", "spoken_name", "address"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise _fail(path, f"{key} must be a non-empty string")
    persona, wake, theme, trans, safety = (
        data["persona"], data["wake"], data["theme"], data["transition"], data["safety"],
    )
    if not isinstance(persona, dict) or not isinstance(persona.get("prompt"), str):
        raise _fail(path, "persona.prompt missing")
    raw_lines = persona.get("lines")
    if not isinstance(raw_lines, dict):
        raise _fail(path, "persona.lines missing")
    lines: dict[str, tuple[str, ...]] = {}
    for kind in REQUIRED_LINES:
        if kind not in raw_lines:
            raise _fail(path, f"missing line kind '{kind}'")
        lines[kind] = _str_list(path, f"lines.{kind}", raw_lines[kind])
    for kind, vals in raw_lines.items():
        if kind not in lines:
            lines[kind] = _str_list(path, f"lines.{kind}", vals)
    if not isinstance(data["voice"], dict):
        raise _fail(path, "voice must be a mapping")
    if not isinstance(wake, dict):
        raise _fail(path, "wake must be a mapping")
    if not isinstance(theme, dict) or not isinstance(theme.get("tokens"), dict):
        raise _fail(path, "theme.tokens missing")
    if not isinstance(theme.get("layout"), str):
        raise _fail(path, "theme.layout missing")
    tokens = theme["tokens"]
    for key, value in tokens.items():
        if _is_color_key(key, value) and not _COLOR_RE.match(value):
            raise _fail(path, f"theme token '{key}' is not a valid color: {value!r}")
    if not isinstance(trans, dict) or trans.get("signature") not in signatures:
        raise _fail(path, f"transition.signature {trans.get('signature') if isinstance(trans, dict) else None!r} not in _motion.yaml")
    intensity = trans.get("intensity")
    if not isinstance(intensity, (int, float)) or isinstance(intensity, bool) or intensity < 0:
        raise _fail(path, "transition.intensity must be a non-negative number")
    if not isinstance(safety, dict):
        raise _fail(path, "safety must be a mapping")
    return Character(
        id=data["id"],
        display_name=data["display_name"],
        spoken_name=data["spoken_name"],
        aliases=_str_list(path, "aliases", data["aliases"]),
        address=data["address"],
        prompt=persona["prompt"],
        lines=lines,
        voice=dict(data["voice"]),
        wake_names=_str_list(path, "wake.names", wake.get("names")),
        theme_tokens=dict(tokens),
        layout=theme["layout"],
        signature=trans["signature"],
        intensity=float(intensity),
        drop_character_on=_str_list(
            path, "safety.drop_character_on", safety.get("drop_character_on", []), allow_empty=True
        ),
    )


def load_all(dir: Path | str | None = None) -> dict[str, Character]:  # noqa: A002
    root = Path(dir or CHARACTERS_DIR)
    signatures = set(motion(root)["signatures"])
    out: dict[str, Character] = {}
    for path in sorted(root.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        char = _parse(path, _read_yaml(path), signatures)
        if path.stem != char.id:
            raise _fail(path, f"id '{char.id}' must match filename")
        if char.id in out:
            raise _fail(path, f"duplicate id '{char.id}'")
        out[char.id] = char
    if DEFAULT_ID not in out:
        raise CharacterError(f"{root}: default character '{DEFAULT_ID}' missing")
    base = set(out[DEFAULT_ID].theme_tokens)
    for char in out.values():
        keys = set(char.theme_tokens)
        if keys != base:
            diff = sorted(keys ^ base)
            raise CharacterError(
                f"{char.id}.yaml: theme token keys differ from {DEFAULT_ID}: {diff}"
            )
    return out


def boot_character(chars: dict[str, Character] | None = None, hint: str | None = None) -> str:
    """Always Jarvis; the hint is accepted and ignored by design."""
    return DEFAULT_ID


def _hint_path() -> Path:
    return Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis") / "character.json"


def read_recent() -> str | None:
    try:
        value = json.loads(_hint_path().read_text(encoding="utf-8")).get("recent")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def write_recent(char_id: str) -> None:
    path = _hint_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"recent": char_id}), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def resolve_name(text: str, chars: dict[str, Character]) -> str | None:
    word = (text or "").strip().lower()
    if not word:
        return None
    names: dict[str, str] = {}
    for cid, char in chars.items():
        for alias in (*char.aliases, char.spoken_name, char.id):
            names.setdefault(alias.lower(), cid)
    if word in names:
        return names[word]
    best: tuple[int, float, str] | None = None
    for alias in difflib.get_close_matches(word, list(names), n=5, cutoff=0.5):
        limit = 1 if len(alias) <= 5 else 2
        dist = _edit_distance(word, alias)
        if dist <= limit:
            key = (dist, -difflib.SequenceMatcher(None, word, alias).ratio(), names[alias])
            if best is None or key < best:
                best = key
    return best[2] if best else None


def line(char: Character, kind: str, **fmt: Any) -> str:
    text = random.choice(char.lines[kind])
    return text.format(**fmt) if fmt else text


def public_dict(char: Character) -> dict[str, Any]:
    return {
        "id": char.id,
        "display_name": char.display_name,
        "spoken_name": char.spoken_name,
        "address": char.address,
        "lines": {k: list(v) for k, v in char.lines.items()},
        "theme_tokens": dict(char.theme_tokens),
        "layout": char.layout,
        "signature": char.signature,
        "intensity": char.intensity,
        "wake_names": list(char.wake_names),
    }
