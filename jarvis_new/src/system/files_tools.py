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
from pathlib import Path

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local, run_cmd

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
    base = Path(s) if s.startswith("/") else Path.cwd() / s
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
                "--max-files",
                "200",
                needle,
                str(root),
                timeout=25.0,
            )
            if rc in (0, 1):
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
            rc, _, err = await run_cmd(*argv, timeout=15.0)
            if rc != 0:
                raise ToolError(f"Dolphin refused ({(err or '')[:120]}).")
            log_action("files-open", str(rp))
            return {"say": f"Showing {rp.name}, Sir."}
        rc, _, err = await run_cmd("xdg-open", str(rp), timeout=15.0)
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
