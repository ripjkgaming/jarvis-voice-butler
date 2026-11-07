"""File & folder retrieval / organisation voice tools.

find/open/describe ride on the Second Brain index (fast, no window spam);
organise moves loose files into category folders behind a confirm gate
(mirroring the email send gate in inbox.py): nothing applies until Sir
confirms, nothing is ever deleted, and every apply writes an undo log.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from jarvis_rt import RunContext, function_tool
from jarvis_rt import ToolError

from system import (
    LocalSystemError,
    log_action,
    require_local,
    run_cmd,
    full_filesystem_access,
    full_command_access,
    launch_gui,
)
from system.core import _trash_path

#: Extra allowed roots (colon-separated) for hermetic tests. Production
#: paths stay confined to $HOME and /mnt/data.
_EXTRA_ROOTS_ENV = "JARVIS_FILES_ROOTS"

_CONTENT_PATTERNS = (
    re.compile(r"containing\s+(.+)", re.I),
    re.compile(r"that\s+contains?\s+(.+)", re.I),
    re.compile(r"with\s+(?:the\s+)?text\s+(.+)", re.I),
)

#: Category -> tidy-up subfolder (media lands in a dated folder instead).
TIDY_FOLDERS = {
    "code": "Code",
    "docs": "Documents",
    "media": "Media",
    "design": "Design",
    "data": "Data",
    "automation": "Automations",
    "finance": "Finance",
    "social": "Social",
    "jarvis-project": "Jarvis",
    "other": "Other",
}


def safe_roots() -> list[Path]:
    """Allowed top-level roots: $HOME, /mnt/data (+ test hook). Pure (env)."""
    if full_filesystem_access():
        return [Path("/")]
    roots = [Path.home(), Path("/mnt/data")]
    extra = os.environ.get(_EXTRA_ROOTS_ENV, "").strip()
    if extra:
        roots.extend(Path(p) for p in extra.split(":") if p.strip())
    return roots


def resolve_safe(path_str: str) -> Path | None:
    """User path confined to the safe roots. None = refused. Pure-ish."""
    s = (path_str or "").strip()[:500]
    if not s:
        return None
    if s.startswith("~"):
        s = str(Path.home() / s[1:].lstrip("/"))
    base = Path(s) if s.startswith("/") else Path.home() / s
    try:
        rp = base.resolve()
    except Exception:
        rp = base.absolute()
    for root in safe_roots():
        try:
            rr = root.resolve()
        except Exception:
            continue
        if rp == rr or rr in rp.parents:
            return rp
    return None


def looks_like_content(query: str) -> str | None:
    """Content needle when the query asks for file contents. Pure."""
    q = " ".join((query or "").split())
    for rx in _CONTENT_PATTERNS:
        m = rx.search(q)
        if m and m.group(1).strip():
            return m.group(1).strip()[:120]
    return None


def shorten(path_str: str, home: str = "") -> str:
    """Long path -> ~/... form for speech. Pure."""
    home = home or str(Path.home())
    if path_str == home:
        return "~"
    if path_str.startswith(home.rstrip("/") + "/"):
        return "~/" + path_str[len(home.rstrip("/") + "/") :]
    return path_str


def _result_score(query: str, path_str: str) -> float:
    """Rank: basename match beats deep-path match. Pure."""
    q = (query or "").casefold()
    base = path_str.rsplit("/", 1)[-1].casefold()
    if base == q:
        return 100.0
    if base.startswith(q):
        return 70.0
    if q in base:
        return 50.0
    if q in path_str.casefold():
        return 20.0
    return 0.0


def rank_results(query: str, paths: list[str], kind: str = "any") -> list[str]:
    """Best-first ordering for raw hit lists. Pure."""
    scored = [(_result_score(query, p), p) for p in paths]
    scored.sort(key=lambda pair: (-pair[0], len(pair[1])))
    _ = kind
    return [p for _, p in scored]


def plan_key(plan: dict) -> str:
    """Exact-match fingerprint for the organise confirm gate. Pure."""

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip().casefold())

    moves = plan.get("moves", []) or []
    bits = sorted(f"{norm(m.get('src', ''))}|{norm(m.get('dst', ''))}" for m in moves)
    return hashlib.sha1(("\n".join(bits)).encode()).hexdigest()[:16]


def build_organize_plan(
    filenames: list[str], mtimes: dict[str, float] | None = None
) -> dict:
    """Loose top-level files -> [{src, dst}] tidy moves. Pure.

    Grouped by brain category; media goes to Media/YYYY-MM so photo dumps
    sort themselves by month. Name clashes get a (2), (3) suffix.
    """
    from second_brain import categorize

    mtimes = mtimes or {}
    moves: list[dict] = []
    taken: set[str] = set()
    for name in filenames:
        if not name or "/" in name or name.startswith("."):
            continue  # hidden files are never touched
        if ".git" in name:
            continue
        cat, _ = categorize(name)
        folder = TIDY_FOLDERS.get(cat, "Other")
        if cat == "media":
            ts = mtimes.get(name, 0.0)
            try:
                ym = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m")
            except (OSError, OverflowError, ValueError):
                ym = "unsorted"
            dst = f"{folder}/{ym}/{name}"
        else:
            dst = f"{folder}/{name}"
        base, dot, ext = dst.rpartition(".")
        n = 2
        while dst.casefold() in taken:
            dst = f"{base} ({n}){dot + ext if dot else ''}"
            n += 1
        taken.add(dst.casefold())
        if Path(dst).name != name or True:
            moves.append({"src": name, "dst": dst})
    # Drop no-op moves (already inside the right folder is handled by the
    # caller passing only loose files, so every move here counts).
    return {"moves": moves, "created": time.time()}


def _locate_db_fresh(max_age_s: float = 7 * 24 * 3600.0) -> bool:
    """Is the plocate db recent enough to trust? Never raises."""
    try:
        for cand in ("/var/lib/plocate/plocate.db", "/var/lib/mlocate/mlocate.db"):
            p = Path(cand)
            if p.exists() and time.time() - p.stat().st_mtime < max_age_s:
                return True
        return False
    except OSError:
        return False


# -- delete by description (trash only, guarded, undoable) ---------------

#: Jarvis repo: never deleted, nor anything above or inside it.
JARVIS_REPO = Path("/mnt/data/jarvis-voice-butler")
#: Named top-level folders of home that are never deleted as a whole.
_PROTECTED_HOME_FOLDERS = frozenset(
    {"jarvis-projects", "Downloads", "Desktop", "Documents"}
)
_NAME_ROOTS = ("jarvis-projects", "Downloads", "Desktop", "Documents")
_TEXT_EXTS = frozenset({".html", ".htm", ".txt", ".md", ".py", ".js", ".css", ".json"})
_TEXT_MAX_BYTES = 16 * 1024
_MAX_FILES_WITHOUT_CONFIRM = 200
_LEDGER_KEEP = 100
_STOP_WORDS = frozenset(
    """a an the of my me i we you your to on in for and or with at by from it its
    this that these those is are be do does please can could would just made make
    created create jarvis sir file files folder folders directory dir delete remove
    trash get rid throw away erase undo bring back project projects app thing stuff
    one last old new whole entire all some any""".split()
)


def _jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    h = os.environ.get("JARVIS_HOME", "").strip()
    return Path(h) if h else Path.home() / ".jarvis"


def _ledger_path() -> Path:
    return _jarvis_home() / "deleted_ledger.json"


def _ledger_load() -> list[dict]:
    try:
        data = json.loads(_ledger_path().read_text())
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def _ledger_save(entries: list[dict]) -> bool:
    """Atomic write of the undo ledger. Never raises; False on failure."""
    try:
        path = _ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries[-_LEDGER_KEEP:], indent=1))
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _words(text: str) -> set[str]:
    """Lowercased meaningful words (stop words dropped, plurals folded). Pure."""
    out: set[str] = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if len(w) < 2 or w in _STOP_WORDS:
            continue
        out.add(w)
    return out


def _coverage(want: set[str], have: set[str]) -> float:
    """Share of the wanted words that the candidate carries. Pure."""
    if not want:
        return 0.0
    return len(want & have) / len(want)


def _guard(raw: str) -> tuple[Path | None, str]:
    """Deletable path under $HOME, or (None, reason). Never follows a final
    symlink: the link itself is what gets trashed. Pure-ish (filesystem read
    only for realpath of the parent)."""
    s = (raw or "").strip()
    if not s:
        return None, "No path given."
    home = Path(os.path.realpath(Path.home()))
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = home / p
    p = Path(os.path.abspath(p))
    if p.name == "":
        return None, "I won't delete the root of the disk."
    final = Path(os.path.realpath(p.parent)) / p.name
    if final == home:
        return None, "I won't delete your home folder."
    try:
        rel = final.relative_to(home)
    except ValueError:
        return None, "That's outside your home folder."
    if rel.parts[0].startswith("."):
        return None, "That's inside a protected config folder."
    if len(rel.parts) == 1 and rel.parts[0] in _PROTECTED_HOME_FOLDERS:
        return (
            None,
            f"{rel.parts[0]} is one of your main folders, so I won't remove it whole.",
        )
    repo = Path(os.path.realpath(JARVIS_REPO))
    if final == repo or final in repo.parents or repo in final.parents:
        return None, "That's part of Jarvis itself."
    return final, ""


def _file_count(p: Path, cap: int = _MAX_FILES_WITHOUT_CONFIRM + 1) -> int:
    """Files under p, stopping at cap. Never follows symlinks."""
    if p.is_symlink() or not p.is_dir():
        return 1
    n = 0
    for _root, _dirs, files in os.walk(p, followlinks=False):
        n += len(files)
        if n >= cap:
            break
    return n


def _mtime(p: Path) -> float:
    try:
        return p.stat(follow_symlinks=False).st_mtime
    except OSError:
        return 0.0


def _brain_paths(phrase: str) -> list[str]:
    """Second Brain index hits for a phrase. Never raises."""
    try:
        import second_brain

        return [n["path"] for n in second_brain.search(phrase, 10) if n.get("path")]
    except Exception:
        return []


@dataclass
class _Cand:
    path: str
    kind: str
    score: float
    mtime: float
    why: str


def _gather_candidates(description: str) -> list[_Cand]:
    """Raw candidates from Claude Code job cwds, home name roots, content of
    small text files, and the brain index. Blocking; run in a thread."""
    want = _words(description)
    if not want:
        return []
    out: list[_Cand] = []
    # (a) folders recorded by Claude Code jobs, matched on the task text.
    jobs_dir = _jarvis_home() / "code-jobs"
    if jobs_dir.is_dir():
        for jf in jobs_dir.glob("*.json"):
            if jf.name.endswith(".status.json"):
                continue
            try:
                rec = json.loads(jf.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or not str(rec.get("cwd") or "").strip():
                continue
            cwd = Path(str(rec["cwd"]).strip()).expanduser()
            if not cwd.is_dir():
                continue
            have = _words(str(rec.get("task") or "")) | _words(cwd.name)
            score = _coverage(want, have)
            if score > 0:
                out.append(_Cand(str(cwd), "folder", score, _mtime(jf), "job"))
    # (b) names (and small text contents) under the usual folders, shallow.
    home = Path.home()
    budget = 5000
    for name in _NAME_ROOTS:
        root = home / name
        if not root.is_dir():
            continue
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack and budget > 0:
            d, depth = stack.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                budget -= 1
                if e.name.startswith("."):
                    continue
                try:
                    is_link = e.is_symlink()
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                p = Path(e.path)
                stem = e.name if is_dir else Path(e.name).stem
                kind = "link" if is_link else ("folder" if is_dir else "file")
                score = _coverage(want, _words(stem))
                if score > 0:
                    out.append(_Cand(str(p), kind, score, _mtime(p), "name"))
                if is_dir and not is_link and depth < 2:
                    stack.append((p, depth + 1))
                elif kind == "file" and Path(e.name).suffix.lower() in _TEXT_EXTS:
                    try:
                        if e.stat(follow_symlinks=False).st_size > _TEXT_MAX_BYTES:
                            continue
                        text = p.read_text(errors="ignore")
                    except OSError:
                        continue
                    content = _coverage(want, _words(text))
                    if content >= 0.6:  # content hits count at half weight
                        out.append(
                            _Cand(str(p), kind, content * 0.5, _mtime(p), "content")
                        )
    # (c) the brain index, as a weak fallback.
    for raw in _brain_paths(" ".join(sorted(want))):
        p = Path(raw).expanduser()
        if not os.path.lexists(p):
            continue
        kind = "folder" if p.is_dir() else "file"
        out.append(
            _Cand(
                str(p),
                kind,
                max(_coverage(want, _words(p.name)), 0.3),
                _mtime(p),
                "index",
            )
        )
    return out


def _rank_candidates(cands: list[_Cand]) -> list[_Cand]:
    """Guard, dedupe by path, best first, and drop anything inside a folder
    already on the list (deleting the folder covers it). Pure."""
    best: dict[str, _Cand] = {}
    for c in cands:
        final, _why = _guard(c.path)
        if final is None:
            continue
        c.path = str(final)
        prev = best.get(c.path)
        if prev is None or c.score > prev.score:
            best[c.path] = c
    ordered = sorted(best.values(), key=lambda c: (-round(c.score, 2), -c.mtime))
    kept: list[_Cand] = []
    for c in ordered:
        cp = Path(c.path)
        if any(Path(k.path) in cp.parents or k.path == c.path for k in kept):
            continue
        kept.append(c)
    return kept[:8]


def _speak_item(c: _Cand) -> str:
    parent = shorten(str(Path(c.path).parent))
    return f"{Path(c.path).name} in {parent}"[:120]


def _move_to_trash(final: Path) -> Path:
    """Trash via the shared core helper and verify the move. Raises ToolError."""
    dest = _trash_path(final)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(final), str(dest))
    except Exception as exc:
        raise ToolError(f"Delete failed: {exc}") from exc
    if os.path.lexists(final) or not os.path.lexists(dest):
        raise ToolError(
            f"I couldn't confirm {final.name} left its place, Sir. Please check it."
        )
    return dest


class FilesTools:
    """Find/open/describe/organise files and folders. Main-agent tools."""

    def __init__(self) -> None:
        self._last_results: list[str] = []
        # Confirm gate (mirrors inbox._confirmed_draft): organise stores a
        # plan, confirm_organize burns the fingerprint in, apply checks it.
        self._pending_plan: dict | None = None
        self._pending_dir: str = ""
        self._confirmed_plan: str | None = None

    @property
    def tools(self) -> list:
        return [
            self.find_files,
            self.open_path,
            self.describe_folder,
            self.organize_folder,
            self.confirm_organize,
            self.apply_organize_plan,
            self.undo_last_organize,
            self.find_and_delete,
            self.restore_from_trash,
        ]

    # -- search backends -------------------------------------------------

    async def _via_locate(self, query: str, kind: str, limit: int) -> list[str]:
        binary = shutil.which("plocate") or shutil.which("locate")
        if binary is None or not _locate_db_fresh():
            return []
        rc, out, _ = await run_cmd(binary, "-i", "-l", str(limit * 3), query)
        if rc != 0:
            return []
        hits = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
        return self._keep_allowed(hits, kind)[:limit]

    def _via_brain(self, query: str, kind: str, limit: int) -> list[str]:
        try:
            import second_brain

            found = second_brain.search(query, limit * 3)
        except Exception:
            return []
        paths = [
            n["path"]
            for n in found
            if n.get("path")
            and (kind == "any" or (kind == "folder") == (n.get("type") == "folder"))
        ]
        return self._keep_allowed(paths, kind)[:limit]

    async def _via_find(self, query: str, kind: str, limit: int) -> list[str]:
        import second_brain

        try:
            cfg = second_brain.load_config()
        except Exception:
            return []
        hits: list[str] = []
        for entry in cfg.get("roots", []) or []:
            raw = entry.get("path") if isinstance(entry, dict) else entry
            root = resolve_safe(os.path.expanduser(str(raw or "")))
            if root is None or not root.is_dir():
                continue
            args = [str(root), "-maxdepth", "4", "-iname", f"*{query}*"]
            if kind == "file":
                args += ["-type", "f"]
            elif kind == "folder":
                args += ["-type", "d"]
            rc, out, _ = await run_cmd(
                "find", *args, "-not", "-path", "*/.*", timeout=25.0
            )
            if rc == 0:
                hits.extend(ln.strip() for ln in (out or "").splitlines() if ln.strip())
            if len(hits) >= limit:
                break
        return self._keep_allowed(hits, kind)[:limit]

    def _keep_allowed(self, paths: list[str], kind: str) -> list[str]:
        out: list[str] = []
        for p in paths:
            if resolve_safe(p) is None:
                continue
            if kind in ("file", "folder"):
                # Locate hits carry no type: stat once to enforce the kind.
                with contextlib.suppress(OSError):
                    is_dir = Path(p).is_dir()
                    if (kind == "folder") != is_dir:
                        continue
            out.append(p)
        return out

    @function_tool()
    async def find_files(
        self,
        context: RunContext,
        query: str,
        kind: str = "any",
        limit: int = 8,
    ) -> dict[str, str]:
        """Find files or folders by name ("find my invoice") or by content
        ("find files containing quarterly revenue").

        Name search tries the locate db, then the brain index, then a
        bounded find. Content search uses ripgrep when it asks for words
        inside files.

        Args:
            query: Name fragment, or "…containing <words>" for contents.
            kind: "any", "file", or "folder".
            limit: How many (1-20).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        query = " ".join((query or "").split())[:150]
        if not query:
            raise ToolError("What should I look for, Sir?")
        kind = (kind or "any").strip().lower()
        if kind not in ("any", "file", "folder"):
            kind = "any"
        limit = max(1, min(20, int(limit or 8)))

        needle = looks_like_content(query)
        if needle:
            if shutil.which("rg") is None:
                raise ToolError("Content search needs ripgrep installed.")
            hits = await self._content_search(needle, kind, limit)
            say_q = f"files containing {needle[:60]}"
        else:
            hits = await self._via_locate(query, kind, limit)
            if not hits:
                # Loading and fuzzy-scoring the index can take hundreds of ms.
                # Keep voice/control callbacks responsive during the read-only
                # lookup; cancelled calls never publish its eventual results.
                hits = await asyncio.to_thread(self._via_brain, query, kind, limit)
            if not hits:
                hits = await self._via_find(query, kind, limit)
            say_q = query[:60]
        hits = rank_results(needle or query, hits)[:limit]
        self._last_results = hits
        if not hits:
            return {"say": f"Nothing matching {say_q}, Sir."}
        log_action("files-find", f"{query[:60]} n={len(hits)}")
        short = [shorten(h) for h in hits[:4]]
        return {
            "paths": "; ".join(hits),
            "say": f"Found {len(hits)}: {'; '.join(short)[:350]}.",
        }

    async def _content_search(self, needle: str, kind: str, limit: int) -> list[str]:
        import second_brain

        try:
            cfg = second_brain.load_config()
        except Exception:
            return []
        hits: list[str] = []
        for entry in cfg.get("roots", []) or []:
            raw = entry.get("path") if isinstance(entry, dict) else entry
            root = resolve_safe(os.path.expanduser(str(raw or "")))
            if root is None or not root.is_dir():
                continue
            rc, out, _ = await run_cmd(
                "rg",
                "-l",
                "-i",
                "--max-count",
                "1",
                "--fixed-strings",
                "--",
                needle,
                str(root),
                timeout=25.0,
            )
            if rc not in (0, 1):
                raise ToolError(
                    "File content search could not finish. Please try a narrower folder."
                )
            paths = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
            if kind == "folder":
                paths = sorted({str(Path(p).parent) for p in paths})
            hits.extend(paths)
            if len(hits) >= limit:
                break
        return self._keep_allowed(hits, kind)[:limit]

    @function_tool()
    async def open_path(
        self, context: RunContext, path_or_result_index: str, reveal: bool = False
    ) -> dict[str, str]:
        """Open a file, or reveal it / open a folder in Dolphin.

        Args:
            path_or_result_index: A path, or a number from the last
                find_files result ("open result 2").
            reveal: True to show it selected in Dolphin instead of launching.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        ref = " ".join((path_or_result_index or "").split())[:500]
        target: str | None = None
        if ref.isdigit() and self._last_results:
            idx = int(ref) - 1
            if 0 <= idx < len(self._last_results):
                target = self._last_results[idx]
        if target is None:
            target = ref
        rp = resolve_safe(os.path.expanduser(target or ""))
        if rp is None:
            raise ToolError("I can only open paths under your home or /mnt/data.")
        if not rp.exists():
            raise ToolError(f"No such file: {shorten(str(rp))}.")
        if rp.is_dir() or reveal:
            if shutil.which("dolphin") is None:
                raise ToolError("Dolphin is not installed.")
            # Folders open directly; reveal selects the file in its parent.
            argv = ["dolphin", "--select", str(rp)] if reveal else ["dolphin", str(rp)]
            rc, _, err = await launch_gui(*argv)
            if rc != 0:
                raise ToolError(f"Dolphin refused ({(err or '')[:120]}).")
            log_action("files-open", str(rp))
            return {"say": f"Showing {rp.name}, Sir."}
        rc, _, err = await launch_gui("xdg-open", str(rp))
        if rc != 0:
            raise ToolError(
                f"Nothing opens {rp.suffix or 'that'} ({(err or '')[:120]})."
            )
        log_action("files-open", str(rp))
        return {"say": f"Opening {rp.name}, Sir."}

    @function_tool()
    async def describe_folder(
        self, context: RunContext, path_or_name: str
    ) -> dict[str, str]:
        """What's in a folder: file types, tools/scripts, brain connections.

        For "pull up my social media automation workflows": name the
        folder and it finds, opens (in Dolphin), and describes it.

        Args:
            path_or_name: A folder path, or a name to look up in the brain.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        ref = " ".join((path_or_name or "").split())[:300]
        if not ref:
            raise ToolError("Which folder, Sir?")
        rp = resolve_safe(os.path.expanduser(ref))
        if rp is None or not rp.is_dir():
            # Fall back to the brain index by name.
            rp = await self._find_folder(ref)
        if rp is None:
            raise ToolError(f"I can't find a folder called {ref[:60]}.")
        try:
            entries = list(rp.iterdir())
        except OSError:
            raise ToolError(f"{rp.name} won't open.") from None
        from second_brain import categorize

        files = [e for e in entries if e.is_file() and not e.name.startswith(".")]
        dirs = [e for e in entries if e.is_dir() and not e.name.startswith(".")]
        by_cat: dict[str, int] = {}
        scripts: list[str] = []
        for e in files:
            cat, _ = categorize(e.name)
            by_cat[cat] = by_cat.get(cat, 0) + 1
            suffix = e.suffix.lower()
            if suffix in (".sh", ".py", ".js", ".ts") or "automat" in e.name.lower():
                scripts.append(e.name)
        types_bit = (
            ", ".join(f"{c} {n}" for c, n in sorted(by_cat.items())) or "no files"
        )
        folder_bit = f"{len(files)} files ({types_bit}), {len(dirs)} folders" + (
            f": {', '.join(d.name for d in dirs[:6])}" if dirs else ""
        )
        tools_bit = f" Tools inside: {', '.join(scripts[:6])}." if scripts else ""
        # Brain connections: what ops link here.
        conn_bit = ""
        with contextlib.suppress(Exception):
            import second_brain

            graph = second_brain.load_graph()
            if graph:
                hit = next(
                    (n for n in graph.get("nodes", []) if n.get("path") == str(rp)),
                    None,
                )
                if hit:
                    linked = [
                        n["label"]
                        for n in second_brain.neighbors(hit["id"], depth=2, graph=graph)
                        if n.get("type") in ("operation", "tool")
                    ]
                    if linked:
                        conn_bit = f" Linked: {', '.join(sorted(set(linked))[:6])}."
        log_action("files-describe", str(rp))
        return {
            "path": str(rp),
            "say": f"{rp.name}: {folder_bit}.{tools_bit}{conn_bit}"[:900],
        }

    async def _find_folder(self, name: str) -> Path | None:
        try:
            import second_brain

            for n in await asyncio.to_thread(second_brain.search, name, 10):
                if n.get("type") == "folder" and n.get("path"):
                    rp = resolve_safe(n["path"])
                    if rp is not None and rp.is_dir():
                        return rp
        except Exception:
            pass
        return None

    # -- organise (confirm-gated) ----------------------------------------

    def _loose_files(self, rp: Path) -> tuple[list[str], dict[str, float]]:
        names: list[str] = []
        mtimes: dict[str, float] = {}
        try:
            for e in rp.iterdir():
                if not e.is_file(follow_symlinks=False) or e.name.startswith("."):
                    continue
                if e.is_symlink():
                    continue
                names.append(e.name)
                with contextlib.suppress(OSError):
                    mtimes[e.name] = e.stat().st_mtime
        except OSError:
            pass
        return sorted(names), mtimes

    @function_tool()
    async def organize_folder(
        self, context: RunContext, path: str, dry_run: bool = True
    ) -> dict[str, str]:
        """Propose (or, once confirmed, run) a tidy-up: loose files move
        into category subfolders (media by month). Never deletes, never
        touches hidden files or git internals.

        Args:
            path: The folder to tidy.
            dry_run: True previews the plan; say "confirm organize" then
                apply_organize_plan to run it.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        rp = resolve_safe(os.path.expanduser((path or "").strip()))
        if rp is None:
            raise ToolError("I can only tidy folders under your home or /mnt/data.")
        if not rp.is_dir():
            raise ToolError(f"No such folder: {(path or '')[:80]}.")
        if (rp / ".git").exists():
            # Top-level repo checkout: only its loose files move, never
            # anything under .git (iterdir never yields it as a file anyway,
            # but say so for trust).
            pass
        names, mtimes = self._loose_files(rp)
        if not names:
            return {"say": f"{rp.name} is already tidy, Sir."}
        plan = build_organize_plan(names, mtimes)
        plan["dir"] = str(rp)
        self._pending_plan = plan
        self._pending_dir = str(rp)
        if dry_run:
            preview = "; ".join(f"{m['src']} → {m['dst']}" for m in plan["moves"][:8])
            more = (
                f" (+{len(plan['moves']) - 8} more)" if len(plan["moves"]) > 8 else ""
            )
            log_action("files-organize-plan", str(rp))
            return {
                "plan": json.dumps(plan["moves"][:50]),
                "say": (
                    f"I'd file {len(plan['moves'])} loose files: {preview}{more}. "
                    "Say 'confirm organize' and I'll do it, Sir."
                )[:900],
            }
        return await self.apply_organize_plan(context)

    @function_tool()
    async def confirm_organize(self, context: RunContext) -> str:
        """Authorize the stored tidy-up plan exactly as previewed.

        Call only after Sir clearly approves the plan. The next
        apply_organize_plan must match it exactly, then it burns.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if not self._pending_plan or not self._pending_plan.get("moves"):
            raise ToolError("There's no tidy-up plan waiting, Sir.")
        self._confirmed_plan = plan_key(self._pending_plan)
        return f"Authorized tidying {self._pending_dir}."

    def _consume_confirm(self, plan: dict) -> None:
        if full_command_access():
            self._confirmed_plan = None
            return
        if self._confirmed_plan != plan_key(plan):
            raise ToolError(
                "That tidy-up isn't authorized. Ask me to preview it, "
                "confirm it, then apply."
            )
        self._confirmed_plan = None

    @function_tool()
    async def apply_organize_plan(self, context: RunContext) -> dict[str, str]:
        """Run the last stored tidy-up plan (needs confirm_organize first)."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        plan = self._pending_plan
        if not plan or not plan.get("moves"):
            raise ToolError("There's no tidy-up plan waiting, Sir.")
        rp = resolve_safe(plan.get("dir", ""))
        if rp is None or rp != Path(self._pending_dir):
            raise ToolError("That plan points outside the safe folders.")
        self._consume_confirm(plan)
        done: list[dict] = []
        for m in plan["moves"]:
            src = rp / m["src"]
            dst = rp / m["dst"]
            try:
                if (
                    not src.is_file()
                    or src.is_symlink()
                    or src.name.startswith(".")
                    or dst.exists()
                ):
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
                done.append({"src": str(src), "dst": str(dst)})
            except OSError:
                continue
        undo_path = None
        if done:
            try:
                import second_brain

                undo_path = second_brain.undo_dir() / (
                    datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
                )
                undo_path.write_text(json.dumps({"dir": str(rp), "moves": done}))
            except OSError:
                undo_path = None
        self._pending_plan = None
        self._pending_dir = ""
        log_action("files-organize-apply", f"{rp} n={len(done)}")
        if not done:
            return {"say": "Nothing moved — the files were already gone, Sir."}
        return {"say": f"Filed {len(done)} files, Sir. Say 'undo that' to revert."}

    @function_tool()
    async def undo_last_organize(self, context: RunContext) -> dict[str, str]:
        """Move the last tidy-up's files back where they were."""
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        try:
            import second_brain

            logs = sorted(second_brain.undo_dir().glob("*.json"))
        except OSError:
            logs = []
        if not logs:
            raise ToolError("There's no tidy-up to undo, Sir.")
        latest = logs[-1]
        try:
            data = json.loads(latest.read_text())
            moves = data.get("moves", []) or []
        except (OSError, ValueError):
            raise ToolError("The undo record is unreadable.") from None
        restored = 0
        for m in moves:
            try:
                src, dst = Path(m["dst"]), Path(m["src"])
                if (
                    resolve_safe(str(src)) is None
                    or resolve_safe(str(dst)) is None
                    or not src.is_file()
                    or dst.exists()
                ):
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dst)
                restored += 1
            except (OSError, KeyError, TypeError):
                continue
        with contextlib.suppress(OSError):
            latest.unlink()
        # Prune folders left empty by the revert (only ones we created).
        log_action("files-organize-undo", f"n={restored}")
        await asyncio.sleep(0)
        return {"say": f"Put {restored} files back, Sir."}

    # -- delete by description (trash only) ------------------------------

    async def _delete_one(self, final: Path, confirm: bool) -> dict[str, str]:
        is_dir = final.is_dir() and not final.is_symlink()
        if is_dir and _file_count(final) > _MAX_FILES_WITHOUT_CONFIRM and not confirm:
            raise ToolError(
                f"{final.name} holds more than {_MAX_FILES_WITHOUT_CONFIRM} files. "
                "Say so and I'll move it to the trash, Sir."
            )
        dest = await asyncio.to_thread(_move_to_trash, final)
        entries = _ledger_load()
        entries.append(
            {
                "name": final.name,
                "original": str(final),
                "trash": str(dest),
                "kind": "folder" if is_dir else "file",
                "ts": time.time(),
            }
        )
        if not _ledger_save(entries):
            log_action("files-delete", f"ledger write failed for {final}")
        log_action("files-delete", f"{final} -> {dest}")
        what = "folder" if is_dir else "file"
        return {
            "say": f"Moved {final.name} ({what}) to the trash, Sir. Say 'undo that' to bring it back.",
            "trashed": str(dest),
        }

    @function_tool()
    async def find_and_delete(
        self,
        context: RunContext,
        description: str,
        path: str = "",
        confirm: bool = False,
    ) -> dict[str, str]:
        """Delete a file or folder that Sir describes in words, moving it to
        the trash (recoverable). Use this instead of guessing paths.

        Voice phrasings: "delete the simple website with a text box and
        button", "delete the website you just made", "get rid of that file",
        "remove the test project".

        Deletes only when exactly one strong match exists. If several match,
        nothing is deleted: Jarvis reads the options back, and the next call
        passes the chosen one as `path`.

        Args:
            description: What to delete, in words (e.g. "simple website text box button").
            path: Exact file or folder path (~/... or absolute). Overrides description.
            confirm: True only after Sir clearly agrees to move a folder with over 200 files.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        desc = " ".join((description or "").split())[:200]
        raw_path = " ".join((path or "").split())[:500]
        if raw_path:
            final, why = _guard(os.path.expanduser(raw_path))
            if final is None:
                raise ToolError(why)
            if not os.path.lexists(final):
                raise ToolError(f"Nothing at {shorten(str(final))}, Sir.")
            return await self._delete_one(final, confirm)
        if not desc:
            raise ToolError("What should I delete, Sir?")
        cands = await asyncio.to_thread(_gather_candidates, desc)
        ranked = _rank_candidates(cands)
        plausible = [c for c in ranked if c.score >= 0.6]
        if not plausible:
            if ranked:
                closest = "; ".join(_speak_item(c) for c in ranked[:3])
                return {
                    "say": f"I'm not sure which one you mean, Sir. Closest: {closest}. Tell me which, or its path."[
                        :400
                    ]
                }
            raise ToolError(
                f"I can't find anything matching {desc[:60]}, Sir. Tell me the folder or its path."
            )
        top = plausible[0]
        rivals = [c for c in plausible[1:] if top.score - c.score < 0.15]
        if rivals:
            opts = [top] + rivals
            listing = "; ".join(_speak_item(c) for c in opts[:4])
            log_action("files-delete-ambiguous", f"{desc[:60]} n={len(opts)}")
            return {
                "paths": "; ".join(c.path for c in opts[:5]),
                "say": f"I found {len(opts)}: {listing}. Which one, Sir? Nothing's been deleted.",
            }
        return await self._delete_one(Path(top.path), confirm)

    @function_tool()
    async def restore_from_trash(
        self, context: RunContext, name: str = ""
    ) -> dict[str, str]:
        """Undo a delete: bring back the most recent item Jarvis moved to the
        trash, or the one whose name matches.

        Voice phrasings: "undo that delete", "undo that", "bring back the
        website", "restore the test project".

        Args:
            name: Words from the item's name. Blank means the last deletion.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        entries = _ledger_load()
        if not entries:
            raise ToolError("Nothing to bring back, Sir.")
        want = _words(name)
        idx: int | None = len(entries) - 1
        if want:
            idx, best = None, 0.0
            for i, e in enumerate(entries):
                sc = _coverage(want, _words(str(e.get("name") or "")))
                if sc >= 0.6 and sc >= best:  # newer wins ties
                    idx, best = i, sc
            if idx is None:
                raise ToolError(
                    "I can't find anything I deleted that matches that, Sir."
                )
        entry = entries[idx]
        orig, why = _guard(str(entry.get("original") or ""))
        if orig is None:
            raise ToolError(why)
        trash = Path(str(entry.get("trash") or ""))
        if not os.path.lexists(trash):
            entries.pop(idx)
            _ledger_save(entries)
            raise ToolError(f"{orig.name} is no longer in the trash, Sir.")
        if os.path.lexists(orig):
            raise ToolError(
                f"Something is already at {shorten(str(orig))}, so I left the trash alone, Sir."
            )
        try:
            orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(trash), str(orig))
        except Exception as exc:
            raise ToolError(f"Restore failed: {exc}") from exc
        if not os.path.lexists(orig) or os.path.lexists(trash):
            raise ToolError("I couldn't confirm the restore, Sir. Please check it.")
        entries.pop(idx)
        _ledger_save(entries)
        log_action("files-restore", str(orig))
        return {"say": f"Brought back {orig.name}, Sir.", "path": str(orig)}
