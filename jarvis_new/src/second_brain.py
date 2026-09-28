"""Second Brain data layer: graph of Sir's files/folders + business ops.

No UI here (the lead builds a 3D view later). Roots/excludes come from
$JARVIS_HOME/brain/config.json (a sensible default is created on first
run); the graph lands in $JARVIS_HOME/brain/graph.json.

Fail-soft background style: build() skips what it cannot read, and the
rebuild thread never raises.
"""

from __future__ import annotations

import contextlib
import difflib
import fnmatch
import hashlib
import json
import os
import threading
import time
from pathlib import Path

#: Fixed color per category (the 3D view keys off these).
PALETTE = {
    "code": "#4fc3f7",
    "docs": "#fff176",
    "media": "#ba68c8",
    "design": "#ff8a65",
    "data": "#81c784",
    "automation": "#64ffda",
    "finance": "#aed581",
    "social": "#f06292",
    "jarvis-project": "#90caf9",
    "other": "#bdbdbd",
}

DEFAULT_EXCLUDES = (".git", "node_modules", ".venv*", "__pycache__", "target", ".cache")
DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_NODES = 20000
REBUILD_INTERVAL_S = 30 * 60.0
START_DELAY_S = 60.0
RELATED_EDGE_CAP = 300

_CODE_EXTS = frozenset(
    [
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".vue",
        ".rs",
        ".go",
        ".c",
        ".h",
        ".hpp",
        ".cpp",
        ".cc",
        ".java",
        ".kt",
        ".cs",
        ".swift",
        ".rb",
        ".php",
        ".lua",
        ".pl",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
        ".html",
        ".css",
        ".scss",
        ".ipynb",
        ".r",
        ".sql",
        ".graphql",
        ".proto",
        ".tf",
        ".nix",
        ".zig",
        ".dart",
        ".elm",
    ]
)
_CODE_NAMES = frozenset(
    {"makefile", "dockerfile", "cmakelists.txt", "justfile", "earthfile"}
)
_DOCS_EXTS = frozenset(
    [
        ".md",
        ".markdown",
        ".txt",
        ".rst",
        ".pdf",
        ".doc",
        ".docx",
        ".odt",
        ".rtf",
        ".tex",
        ".epub",
        ".pages",
        ".log",
    ]
)
_MEDIA_EXTS = frozenset(
    [
        ".mp3",
        ".wav",
        ".flac",
        ".ogg",
        ".opus",
        ".m4a",
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".webm",
        ".m4v",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".heic",
        ".heif",
        ".bmp",
        ".tiff",
        ".raw",
        ".cr2",
    ]
)
_DESIGN_EXTS = frozenset(
    [
        ".psd",
        ".ai",
        ".fig",
        ".sketch",
        ".blend",
        ".kra",
        ".xcf",
        ".fbx",
        ".obj",
        ".stl",
        ".gltf",
        ".glb",
        ".dae",
        ".3mf",
        ".usd",
        ".usdz",
        ".c4d",
        ".max",
        ".mb",
        ".ma",
    ]
)
_DATA_EXTS = frozenset(
    [
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".parquet",
        ".xlsx",
        ".xls",
        ".ods",
        ".xml",
        ".avro",
        ".feather",
        ".arrow",
    ]
)
_FINANCE_HINTS = ("invoice", "receipt", "tax", "bill", "budget", "ledger", "payroll")
_AUTOMATION_HINTS = (
    "automation",
    "n8n",
    "zapier",
    "make",
    "workflow",
    "webhook",
    "cron",
)
_SOCIAL_HINTS = ("social", "instagram", "tiktok", "youtube", "twitter", "facebook")


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def brain_dir() -> Path:
    """$JARVIS_HOME/brain (created on demand). Never raises."""
    try:
        path = jarvis_home() / "brain"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return jarvis_home() / "brain"


def undo_dir() -> Path:
    """Undo-log dir for organise plans. Never raises."""
    try:
        path = brain_dir() / "undo"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return brain_dir() / "undo"


def node_id(kind: str, key: str) -> str:
    """Stable short id from kind + key (path or op name). Pure."""
    digest = hashlib.sha1(f"{kind}:{key}".encode()).hexdigest()[:12]
    return f"{kind}-{digest}"


def categorize(path_str: str, is_dir: bool = False) -> tuple[str, str]:
    """(category, color) by name/path heuristics. Pure.

    Path hints (finance/automation/social/jarvis) win over extensions so
    e.g. an invoice PDF lands in finance, not docs.
    """
    lowered = (path_str or "").casefold().replace("\\", "/")
    name = lowered.rsplit("/", 1)[-1]
    stem, dot, ext = name.rpartition(".")
    ext = ("." + ext) if dot else ""
    if "jarvis" in lowered:
        return "jarvis-project", PALETTE["jarvis-project"]
    if any(h in lowered for h in _SOCIAL_HINTS):
        return "social", PALETTE["social"]
    if any(h in name for h in _FINANCE_HINTS):
        return "finance", PALETTE["finance"]
    if any(h in name for h in _AUTOMATION_HINTS):
        return "automation", PALETTE["automation"]
    if not is_dir and ext == ".json" and "workflow" in lowered:
        return "automation", PALETTE["automation"]
    if is_dir:
        return "other", PALETTE["other"]
    if ext in _CODE_EXTS or name in _CODE_NAMES:
        return "code", PALETTE["code"]
    if ext in _DOCS_EXTS:
        return "docs", PALETTE["docs"]
    if ext in _MEDIA_EXTS:
        return "media", PALETTE["media"]
    if ext in _DESIGN_EXTS:
        return "design", PALETTE["design"]
    if ext in _DATA_EXTS:
        return "data", PALETTE["data"]
    _ = stem
    return "other", PALETTE["other"]


def default_config() -> dict:
    """Sensible first-run roots (only existing dirs). Pure-ish (stat)."""
    home = Path.home()
    roots: list = []
    for cand in ("Documents", "Desktop", "Downloads", "Projects"):
        if (home / cand).is_dir():
            roots.append(str(home / cand))
    if Path("/mnt/data").is_dir():
        roots.append({"path": "/mnt/data", "max_depth": 2})  # big disk: shallow
    jp = jarvis_home() / "projects"
    if jp.is_dir():
        roots.append(str(jp))
    return {
        "roots": roots,
        "excludes": list(DEFAULT_EXCLUDES),
        "exclude_hidden": True,
        "max_depth": DEFAULT_MAX_DEPTH,
        "max_nodes": DEFAULT_MAX_NODES,
    }


def load_config() -> dict:
    """Read config.json, creating the default on first run. Fail-soft."""
    path = brain_dir() / "config.json"
    try:
        if not path.exists():
            cfg = default_config()
            with contextlib.suppress(OSError):
                path.write_text(json.dumps(cfg, indent=2))
            return cfg
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return default_config()
        cfg = default_config()
        cfg.update({k: v for k, v in data.items() if k in cfg})
        return cfg
    except (OSError, ValueError):
        return default_config()


def _example_operations() -> list[dict]:
    return [
        {
            "name": "example-social-automation",
            "description": "Example: cross-posting clips to socials (edit me).",
            "category": "automation",
            "paths": ["~/Documents/social-automation"],
            "tools": ["n8n"],
            "connections": ["example-client-invoicing"],
        },
        {
            "name": "example-client-invoicing",
            "description": "Example: monthly invoices folder (edit me).",
            "category": "finance",
            "paths": ["~/Documents/invoices"],
            "tools": ["libreoffice"],
            "connections": [],
        },
    ]


def load_operations() -> list[dict]:
    """Live operations from operations.json (examples stay inert).

    Creates the file with 2 example ops under an "examples" key on first
    run so nothing is live until Sir defines real operations.
    """
    path = brain_dir() / "operations.json"
    try:
        if not path.exists():
            with contextlib.suppress(OSError):
                path.write_text(
                    json.dumps(
                        {"examples": _example_operations(), "operations": []},
                        indent=2,
                    )
                )
            return []
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return []
        ops = data.get("operations", [])
        return [o for o in ops if isinstance(o, dict) and o.get("name")]
    except (OSError, ValueError):
        return []


def _root_spec(entry: object, fallback_depth: int) -> tuple[str, int] | None:
    if isinstance(entry, dict):
        p, d = entry.get("path", ""), entry.get("max_depth", fallback_depth)
    else:
        p, d = entry, fallback_depth
    if not isinstance(p, str) or not p.strip():
        return None
    try:
        depth = int(d)
    except (TypeError, ValueError):
        depth = fallback_depth
    return p.strip(), max(0, depth)


def _excluded(name: str, excludes: tuple, exclude_hidden: bool) -> bool:
    if exclude_hidden and name.startswith("."):
        return True
    return any(fnmatch.fnmatchcase(name, pat) for pat in excludes)


def _load_mtimes() -> dict:
    try:
        data = json.loads((brain_dir() / ".mtimes.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_mtimes(cache: dict) -> None:
    with contextlib.suppress(OSError):
        (brain_dir() / ".mtimes.json").write_text(json.dumps(cache))


def load_graph() -> dict | None:
    """Last built graph, or None. Never raises."""
    try:
        data = json.loads((brain_dir() / "graph.json").read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def build(incremental: bool = True, config: dict | None = None) -> dict:
    """Walk roots, write graph.json, return stats.

    Incremental: directories whose mtime matches the cache reuse the
    previous graph's subtree (file creation bumps dir mtime on Linux,
    so new files are still picked up).
    """
    started = time.time()
    cfg = config if isinstance(config, dict) else load_config()
    max_depth = int(cfg.get("max_depth", DEFAULT_MAX_DEPTH))
    max_nodes = int(cfg.get("max_nodes", DEFAULT_MAX_NODES))
    excludes = tuple(cfg.get("excludes", DEFAULT_EXCLUDES) or ())
    exclude_hidden = bool(cfg.get("exclude_hidden", True))

    prev = load_graph() if incremental else None
    prev_by_path: dict[str, dict] = {}
    if isinstance(prev, dict):
        for n in prev.get("nodes", []) or []:
            if isinstance(n, dict) and n.get("path"):
                prev_by_path[n["path"]] = n
    cache = _load_mtimes() if incremental else {}
    new_cache: dict[str, float] = dict(cache)  # reused subtrees keep old entries

    nodes: list[dict] = []
    by_id: dict[str, dict] = {}
    edges: list[dict] = []
    seen_paths: set[str] = set()
    skipped = 0
    changed = 0

    def add_node(node: dict) -> bool:
        if len(nodes) >= max_nodes or node["id"] in by_id:
            return False
        nodes.append(node)
        by_id[node["id"]] = node
        return True

    def subtree_paths(prefix: str) -> list[str]:
        with_slash = prefix.rstrip("/") + "/"
        return [p for p in prev_by_path if p == prefix or p.startswith(with_slash)]

    def reuse_subtree(dir_path: str) -> bool:
        # Copy the previous subtree verbatim (ids are stable hashes).
        reused = 0
        for p in subtree_paths(dir_path):
            old = prev_by_path[p]
            if old["id"] in by_id:
                continue
            if len(nodes) >= max_nodes:
                break
            nodes.append(dict(old))
            by_id[old["id"]] = nodes[-1]
            reused += 1
        if isinstance(prev, dict):
            ids = {
                prev_by_path[p]["id"]
                for p in subtree_paths(dir_path)
                if p in prev_by_path
            }
            for e in prev.get("edges", []) or []:
                # Only contains edges: related/uses/connects are rebuilt
                # fresh below, so copying them would duplicate them.
                if (
                    isinstance(e, dict)
                    and e.get("kind") == "contains"
                    and e.get("from") in ids
                    and e.get("to") in ids
                ):
                    edges.append(dict(e))
        return reused > 0

    for entry in cfg.get("roots", []) or []:
        spec = _root_spec(entry, max_depth)
        if spec is None:
            continue
        raw_root, root_depth = spec
        root = Path(os.path.expanduser(raw_root))
        try:
            if not root.is_dir():
                continue
            real_root = str(root.resolve())
        except OSError:
            continue
        if real_root in seen_paths:
            continue
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            if len(nodes) >= max_nodes:
                break
            current, depth = stack.pop()
            try:
                key = str(current.resolve())
            except OSError:
                skipped += 1
                continue
            if key in seen_paths:
                continue
            seen_paths.add(key)
            try:
                st = current.stat()
            except OSError:
                skipped += 1
                continue
            is_root = current == root
            label = current.name or str(current)
            if not is_root and _excluded(label, excludes, exclude_hidden):
                continue
            cat, color = categorize(str(current), is_dir=True)
            if "projects" in str(current).replace("\\", "/") and (
                "jarvis" in str(current).lower()
            ):
                cat, color = "jarvis-project", PALETTE["jarvis-project"]
            nid = node_id("folder", str(current))
            node = {
                "id": nid,
                "type": "folder",
                "label": label,
                "path": str(current),
                "category": cat,
                "color": color,
                "size": 0,
                "mtime": st.st_mtime,
                "tags": [],
            }
            if nid not in by_id and not add_node(node):
                skipped += 1  # node cap reached
                continue
            new_cache[str(current)] = st.st_mtime
            if depth >= root_depth:
                continue
            old_mtime = cache.get(str(current))
            if (
                incremental
                and prev is not None
                and old_mtime is not None
                and old_mtime == st.st_mtime
                and reuse_subtree(str(current))
            ):
                continue
            changed += 1
            try:
                with os.scandir(current) as it:
                    entries = sorted(it, key=lambda e: e.name.lower())
            except OSError:
                skipped += 1
                continue
            for e in entries:
                if _excluded(e.name, excludes, exclude_hidden):
                    continue
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                    is_link = e.is_symlink()
                except OSError:
                    skipped += 1
                    continue
                if is_link:
                    continue  # no symlink loops, no double counting
                full = str(Path(e.path))
                try:
                    est = e.stat(follow_symlinks=False)
                except OSError:
                    skipped += 1
                    continue
                if is_dir:
                    child = {
                        "id": node_id("folder", full),
                        "type": "folder",
                        "label": e.name,
                        "path": full,
                        "category": categorize(full, is_dir=True)[0],
                        "color": categorize(full, is_dir=True)[1],
                        "size": 0,
                        "mtime": est.st_mtime,
                        "tags": [],
                    }
                    if add_node(child):
                        edges.append(
                            {"from": node["id"], "to": child["id"], "kind": "contains"}
                        )
                        if depth + 1 <= root_depth:
                            stack.append((Path(e.path), depth + 1))
                    else:
                        skipped += 1
                else:
                    cat2, color2 = categorize(full)
                    ext = Path(e.name).suffix.lower().lstrip(".")
                    child = {
                        "id": node_id("file", full),
                        "type": "file",
                        "label": e.name,
                        "path": full,
                        "category": cat2,
                        "color": color2,
                        "size": est.st_size,
                        "mtime": est.st_mtime,
                        "tags": [ext] if ext else [],
                    }
                    if add_node(child):
                        edges.append(
                            {"from": node["id"], "to": child["id"], "kind": "contains"}
                        )
                    else:
                        skipped += 1
                if len(nodes) >= max_nodes:
                    break
            if len(nodes) >= max_nodes:
                break

    # --- operations ------------------------------------------------------
    for op in load_operations():
        name = str(op.get("name", ""))[:80]
        if not name:
            continue
        cat = str(op.get("category", "other") or "other")
        op_node = {
            "id": node_id("operation", name),
            "type": "operation",
            "label": name,
            "path": "",
            "category": cat,
            "color": PALETTE.get(cat, PALETTE["other"]),
            "size": 0,
            "mtime": 0.0,
            "tags": [str(t)[:30] for t in (op.get("tools", []) or []) if t][:8],
        }
        if op_node["id"] in by_id:
            continue
        add_node(op_node)
        for raw in (op.get("paths", []) or []) + []:
            p = Path(os.path.expanduser(str(raw)))
            pid = node_id("file" if p.is_file() else "folder", str(p))
            target = by_id.get(pid)
            if target is None and len(nodes) < max_nodes:
                try:
                    exists, is_f = p.exists(), p.is_file()
                except OSError:
                    exists, is_f = False, False
                if exists:
                    c, col = categorize(str(p), is_dir=not is_f)
                    target = {
                        "id": pid,
                        "type": "file" if is_f else "folder",
                        "label": p.name,
                        "path": str(p),
                        "category": c,
                        "color": col,
                        "size": 0,
                        "mtime": 0.0,
                        "tags": [],
                    }
                    add_node(target)
            if target is not None:
                edges.append(
                    {"from": op_node["id"], "to": target["id"], "kind": "uses"}
                )
        for tool in op.get("tools", []) or []:
            tool = str(tool)[:60]
            if not tool:
                continue
            tid = node_id("tool", tool)
            if tid not in by_id and len(nodes) < max_nodes:
                add_node(
                    {
                        "id": tid,
                        "type": "tool",
                        "label": tool,
                        "path": "",
                        "category": "other",
                        "color": PALETTE["other"],
                        "size": 0,
                        "mtime": 0.0,
                        "tags": [],
                    }
                )
            edges.append({"from": op_node["id"], "to": tid, "kind": "uses"})
    op_ids = {node_id("operation", str(o.get("name", ""))) for o in load_operations()}
    for op in load_operations():
        src = node_id("operation", str(op.get("name", "")))
        for other in op.get("connections", []) or []:
            dst = node_id("operation", str(other))
            if src in by_id and dst in op_ids and dst in by_id:
                edges.append({"from": src, "to": dst, "kind": "connects"})

    # --- related: same file stem living in different folders (capped) ----
    stems: dict[str, list[str]] = {}
    for n in nodes:
        if n["type"] != "file":
            continue
        stem = Path(n["label"]).stem.casefold().strip()
        if len(stem) >= 3:
            stems.setdefault(stem, []).append(n["id"])
    related = 0
    for ids in stems.values():
        if len(ids) < 2:
            continue
        for a, b in zip(sorted(ids), sorted(ids)[1:8]):
            if related >= RELATED_EDGE_CAP:
                break
            edges.append({"from": a, "to": b, "kind": "related"})
            related += 1
        if related >= RELATED_EDGE_CAP:
            break

    by_cat: dict[str, int] = {}
    for n in nodes:
        by_cat[n["category"]] = by_cat.get(n["category"], 0) + 1
    stats = {
        "nodes": len(nodes),
        "edges": len(edges),
        "by_category": by_cat,
        "skipped": skipped,
        "changed": changed,
        "incremental": bool(incremental),
        "duration_s": round(time.time() - started, 2),
    }
    graph = {"generated": time.time(), "nodes": nodes, "edges": edges, "stats": stats}
    with contextlib.suppress(OSError):
        out = brain_dir() / "graph.json"
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(graph))
        os.replace(tmp, out)
    _save_mtimes(new_cache)
    return stats


def _score(query: str, node: dict) -> float:
    """Fuzzy match score for one node. Pure."""
    q = (query or "").casefold().strip()
    if not q:
        return 0.0
    label = str(node.get("label", "")).casefold()
    path = str(node.get("path", "")).casefold()
    base = path.rsplit("/", 1)[-1]
    if label == q or base == q:
        return 100.0
    if label.startswith(q):
        return 70.0
    if q in label:
        return 50.0
    for tag in node.get("tags", []) or []:
        if q == str(tag).casefold():
            return 45.0
    if q in base:
        return 30.0
    if q in path:
        return 20.0
    ratio = difflib.SequenceMatcher(None, q, label).ratio()
    if ratio >= 0.6:
        return ratio * 40.0
    words = [w for w in q.split() if w]
    if words and all(w in f"{label} {path}" for w in words):
        return 25.0
    return 0.0


def search(query: str, limit: int = 8, graph: dict | None = None) -> list[dict]:
    """Ranked nodes matching labels/paths/tags. Never raises."""
    try:
        graph = graph if isinstance(graph, dict) else load_graph()
        if not isinstance(graph, dict):
            return []
        limit = max(1, min(50, int(limit or 8)))
        scored = [
            (_score(query, n), n)
            for n in graph.get("nodes", []) or []
            if isinstance(n, dict) and _score(query, n) > 0
        ]
        scored.sort(key=lambda pair: (-pair[0], pair[1].get("label", "")))
        return [n for _, n in scored[:limit]]
    except Exception:
        return []


def neighbors(node_id_: str, depth: int = 1, graph: dict | None = None) -> list[dict]:
    """Nodes within `depth` hops (any edge kind, either direction)."""
    try:
        graph = graph if isinstance(graph, dict) else load_graph()
        if not isinstance(graph, dict):
            return []
        adj: dict[str, set[str]] = {}
        for e in graph.get("edges", []) or []:
            if not isinstance(e, dict):
                continue
            a, b = e.get("from", ""), e.get("to", "")
            if a and b:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
        if node_id_ not in adj:
            return []
        seen = {node_id_}
        frontier = {node_id_}
        for _ in range(max(1, depth)):
            nxt: set[str] = set()
            for nid in frontier:
                nxt |= adj.get(nid, set()) - seen
            seen |= nxt
            frontier = nxt
            if not frontier:
                break
        seen.discard(node_id_)
        by_id_map = {
            n["id"]: n for n in graph.get("nodes", []) or [] if isinstance(n, dict)
        }
        return [by_id_map[i] for i in sorted(seen) if i in by_id_map]
    except Exception:
        return []


def start_thread(
    interval_s: float = REBUILD_INTERVAL_S, delay_s: float = START_DELAY_S
) -> tuple[threading.Thread, threading.Event]:
    """Rebuild the graph every `interval_s` (after `delay_s`). Returns (thread, stop)."""
    stop = threading.Event()

    def _loop() -> None:
        if stop.wait(delay_s):
            return
        while not stop.is_set():
            with contextlib.suppress(Exception):
                build(incremental=True)
            stop.wait(interval_s)

    thread = threading.Thread(target=_loop, name="second-brain", daemon=True)
    thread.start()
    return thread, stop
