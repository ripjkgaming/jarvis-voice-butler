"""Chess settings: ``~/.jarvis/chess.json`` (voice and UI both edit these)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


def jarvis_home() -> Path:
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home).expanduser() if home else Path.home() / ".jarvis"


def settings_path() -> Path:
    return jarvis_home() / "chess.json"


def games_dir() -> Path:
    return jarvis_home() / "games"


def saves_dir() -> Path:
    return games_dir() / "chess"


def lock_path() -> Path:
    return games_dir() / "chess.lock"


DEFAULT_MODELS = [
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
    "openrouter/free",
]

PRESET_ELO = {
    "novice": 800,
    "club": 1200,
    "strong": 1600,
    "expert": 2000,
    "master": 2400,
    "full": 3190,
}

TIME_CONTROLS = {
    "untimed": (0, 0),
    "5+0": (300, 0),
    "10+0": (600, 0),
    "15+10": (900, 10),
}


@dataclass
class ChessSettings:
    # Game
    difficulty: str = "club"
    colour: str = "white"
    time_control: str = "untimed"
    confirm_moves: bool = False
    announce_moves: bool = True
    # Analysis (all passive by default; see on-demand rule)
    stop_ack: str = "spoken"  # spoken | silent
    remember_analysis: bool = False
    commentary_volume: float = 1.0
    commentary_muted: bool = False
    takeover_strength: str = "club"
    hint_default: int = 1
    detail: str = "normal"  # brief | normal | detailed
    audience: str = "club"  # explain pitch: novice | club | advanced
    # Board
    eval_bar: bool = False
    square_labels: bool = True
    reduce_animations: bool = False
    board_size: float = 1.0
    piece_colours: str = "cyan-amber"
    board_theme: str = "dark"
    # Intro
    intro: bool = True
    intro_length: str = "full"  # full | short | off
    # AI
    models: list[str] = field(default_factory=lambda: list(DEFAULT_MODELS))
    online_explanations: bool = True
    speak_explanations: bool = False
    # Voice engine
    whisper_model: str = "base.en"
    device: str = "cpu"
    end_silence_ms: int = 350
    mic_device: str | None = None
    speaker_device: str | None = None
    piper_length_scale: float = 1.0
    echo_gate: bool = False
    require_prefix: bool = False
    paused_clock_during_jarvis: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def update(self, **kw: Any) -> list[str]:
        """Apply known keys (type-coerced); return the keys that changed."""
        changed: list[str] = []
        known = {f.name: f for f in fields(self)}
        for key, value in kw.items():
            if key not in known:
                continue
            cur = getattr(self, key)
            if isinstance(cur, bool) and not isinstance(value, bool):
                value = str(value).lower() in {"1", "true", "on", "yes"}
            elif isinstance(cur, int) and not isinstance(cur, bool):
                value = int(value)
            elif isinstance(cur, float):
                value = float(value)
            if value != cur:
                setattr(self, key, value)
                changed.append(key)
        return changed


def load(path: Path | None = None) -> ChessSettings:
    p = path or settings_path()
    s = ChessSettings()
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return s
    if isinstance(data, dict):
        s.update(**data)
    return s


def save(settings: ChessSettings, path: Path | None = None) -> None:
    p = path or settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings.to_dict(), indent=2))
        tmp.replace(p)
    except OSError:
        pass
