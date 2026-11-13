"""Recipe-only holograms: store a tiny recipe.json per slug, build the GLB on demand into a small cache.

recipe kinds: {"kind":"shape3d","name":slug} | {"kind":"molecule","smiles":..,"label":..} |
{"kind":"build","name":..,"parts":[..]}. The GLB lives only in ~/.cache/jarvis-holo (newest KEEP kept).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path.home() / "jarvis-projects" / "3d"
CACHE = Path.home() / ".cache" / "jarvis-holo"
KEEP = 8


def save(slug: str, recipe: dict) -> Path:
    d = ROOT / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / "recipe.json"
    p.write_text(json.dumps(recipe, separators=(",", ":")))
    return p


def load(slug: str) -> dict | None:
    try:
        return json.loads((ROOT / slug / "recipe.json").read_text())
    except (OSError, ValueError):
        return None


def materialize(slug: str) -> Path | None:
    """GLB for a recipe slug (built into the cache if missing/stale), else None."""
    r = load(slug)
    if not r:
        return None
    out = CACHE / f"{slug}.glb"
    src = ROOT / slug / "recipe.json"
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    import colorbuild
    import shape3d

    CACHE.mkdir(parents=True, exist_ok=True)
    k = r.get("kind")
    if k == "shape3d":
        v, f = shape3d.SHAPES[r["name"]]()
        shape3d.write_glb(out, v, f)
    elif k == "molecule":
        colorbuild.render_molecule(r["smiles"], out)
    elif k == "build":
        colorbuild.render_build(r["parts"], out)
    else:
        return None
    files = sorted(CACHE.glob("*.glb"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP:]:
        old.unlink(missing_ok=True)
    return out


def materialize4d(slug: str) -> Path | None:
    """shape4d.json for a 4D recipe slug, built into the cache."""
    r = load(slug)
    if not r or r.get("kind") != "shape4d":
        return None
    out = CACHE / f"{slug}.4d.json"
    src = ROOT / slug / "recipe.json"
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    import shape4d

    got = shape4d.build(r["name"])
    if not got:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    label, v, e = got
    out.write_text(
        json.dumps({"name": label, "verts": shape4d.normalise(v), "edges": e})
    )
    return out
