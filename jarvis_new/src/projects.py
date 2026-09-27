"""Background projects: research (Claude Sonnet via Sir's Pro plan) and
coding (headless opencode, free Muse Spark), plus the voice-only Project
Archive window's command bus.

Lives in the bridge process: agent sessions are short-lived subprocesses,
so jobs started from a voice call must outlive the call. Each project is a
folder under $JARVIS_HOME/projects/<id>/ holding meta.json and its
documents (report.md, sources.md, brief.md / output.log).

Research never mixes with search: quick facts and "search for X" stay on
the search tools, "open <site>" stays on the launcher. Only explicit
research requests become projects.
"""

from __future__ import annotations

import collections
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import claude_cli

OPENCODE_MODEL = os.environ.get(
    "JARVIS_OPENCODE_MODEL", "opencode/muse-spark-1.3-contributor-free"
)
RESEARCH_TIMEOUT_S = 900.0
CODE_TIMEOUT_S = 45 * 60.0
# The free model answers a rate limit by hanging, not failing: no output
# for this long means the engine is stuck, so the job fails honestly.
CODE_STALL_S = 8 * 60.0

RESEARCH_SYSTEM = (
    "You are Jarvis's research analyst. Research the topic thoroughly with "
    "web search and page fetches, then write a precise, well-sourced report "
    "for Sir. Plain markdown only."
)
RESEARCH_BRIEF = """Research this in depth: {topic}

Reply with markdown in EXACTLY this structure:

## Summary
At most 4 plain sentences (they will be read aloud). No markdown inside.

## Findings
Detailed findings as bullets and short sub-sections (### headings allowed).

## Sources
One bullet per source: the full https:// URL, optionally followed by " - title".
"""

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_URL = re.compile(r"https?://[^\s)>\]]+")
_ID_OK = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]{1,40}$")
_LOCK = threading.Lock()
_PROCS: dict[str, subprocess.Popen] = {}


# --- store ---------------------------------------------------------------


def jarvis_home() -> Path:
    return Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))


def projects_root() -> Path:
    root = jarvis_home() / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def slugify(text: str, limit: int = 40) -> str:
    """Lowercase [a-z0-9-] slug. Pure."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug[:limit].rstrip("-")) or "project"


def new_id(title: str, now: float | None = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    return f"{stamp}-{slugify(title)}"


def valid_id(pid: str) -> bool:
    return bool(_ID_OK.match(pid or ""))


def _dir(pid: str) -> Path:
    return projects_root() / pid


def _write_meta(meta: dict) -> None:
    meta["updated_at"] = time.time()
    path = _dir(meta["id"]) / "meta.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, indent=2))
    tmp.replace(path)


def _read_meta(pid: str) -> dict | None:
    try:
        return json.loads((_dir(pid) / "meta.json").read_text())
    except (OSError, ValueError):
        return None


def _new_meta(kind: str, title: str, prompt: str, model: str, **extra) -> dict:
    now = time.time()
    pid = new_id(title, now)
    # Two starts in the same second with the same title: suffix it.
    n = 2
    base = pid
    while _dir(pid).exists():
        pid = f"{base[: 16 + 40 - 3]}-{n}"
        n += 1
    _dir(pid).mkdir(parents=True)
    meta = {
        "id": pid,
        "kind": kind,
        "title": title[:80],
        "prompt": prompt,
        "status": "running",
        "model": model,
        "variant": None,
        "directory": None,
        "created_at": now,
        "updated_at": now,
        "summary": "",
        "sources": [],
        "error": None,
        "pid": None,
        **extra,
    }
    (_dir(pid) / "brief.md").write_text(f"## Brief\n\n{prompt}\n")
    _write_meta(meta)
    return meta


def list_projects() -> list[dict]:
    """All project metas, newest first. Never raises."""
    out = []
    try:
        for child in projects_root().iterdir():
            if child.is_dir() and valid_id(child.name):
                meta = _read_meta(child.name)
                if meta:
                    out.append(meta)
    except OSError:
        return []
    out.sort(key=lambda m: m.get("created_at", 0), reverse=True)
    return out


_DOC_ORDER = ("report.md", "output.log", "sources.md", "brief.md")
_DOC_TITLES = {
    "report.md": "Report",
    "output.log": "Engine output",
    "sources.md": "Sources",
    "brief.md": "Brief",
}


def documents(pid: str) -> list[dict]:
    """Ordered documents of a project: report first, brief last."""
    folder = _dir(pid)
    names = [n for n in _DOC_ORDER if (folder / n).exists()]
    try:
        extra = sorted(
            p.name
            for p in folder.iterdir()
            if p.suffix in (".md", ".txt") and p.name not in _DOC_ORDER
        )
    except OSError:
        extra = []
    docs = []
    for name in [*names[:-1], *extra, *names[-1:]] if names else extra:
        try:
            text = (folder / name).read_text(errors="replace")
        except OSError:
            continue
        if name == "output.log":
            text = text[-20000:]
        docs.append({"name": name, "title": _DOC_TITLES.get(name, name), "text": text})
    return docs


def get_project(pid: str) -> dict | None:
    if not valid_id(pid):
        return None
    meta = _read_meta(pid)
    if meta is None:
        return None
    docs = documents(pid)
    main = next((d for d in docs if d["name"] in ("report.md", "output.log")), None)
    return {**meta, "documents": docs, "report": main["text"] if main else ""}


def cancel_project(pid: str) -> bool:
    if not valid_id(pid):
        return False
    meta = _read_meta(pid)
    if not meta or meta.get("status") != "running":
        return False
    with _LOCK:
        proc = _PROCS.pop(pid, None)
    if proc is not None:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGTERM)
    meta["status"] = "cancelled"
    _write_meta(meta)
    _announce(meta)
    return True


def _announce(meta: dict) -> None:
    """Desktop notification + action log when a job ends. Fail-soft."""
    verb = {"done": "ready", "failed": "failed", "cancelled": "cancelled"}.get(
        meta.get("status", ""), meta.get("status", "")
    )
    kind = "Research" if meta.get("kind") == "research" else "Coding job"
    with contextlib.suppress(Exception):
        subprocess.run(
            ["notify-send", f"Jarvis: {kind} {verb}", meta.get("title", "")[:120]],
            timeout=5,
            capture_output=True,
        )
    with contextlib.suppress(Exception):
        from system import log_action

        log_action("project", f"{meta.get('kind')} {meta.get('status')} {meta['id']}")


# --- research -------------------------------------------------------------


def parse_report(text: str) -> tuple[str, list[str]]:
    """(summary, source URLs) from the report markdown. Pure."""
    summary = ""
    m = re.search(r"^##\s*Summary\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
    if m:
        summary = " ".join(m.group(1).split())
    sources: list[str] = []
    s = re.search(r"^##\s*Sources\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
    for url in _URL.findall(s.group(1) if s else ""):
        url = url.rstrip(".,;")
        if url not in sources:
            sources.append(url)
    return summary[:900], sources


def start_research(topic: str, *, runner=None, background: bool = True) -> dict:
    """Start a research project on Claude Sonnet. Returns its meta."""
    topic = " ".join((topic or "").split())[:2000]
    meta = _new_meta("research", topic, topic, claude_cli.RESEARCH_MODEL)

    def work() -> None:
        reply, warning = claude_cli.claude_reply(
            RESEARCH_BRIEF.format(topic=topic),
            model=claude_cli.RESEARCH_MODEL,
            system=RESEARCH_SYSTEM,
            tools="WebSearch,WebFetch",
            timeout=RESEARCH_TIMEOUT_S,
            runner=runner,
        )
        current = _read_meta(meta["id"]) or meta
        if current.get("status") == "cancelled":
            return
        if reply:
            (_dir(meta["id"]) / "report.md").write_text(reply)
            summary, sources = parse_report(reply)
            if sources:
                (_dir(meta["id"]) / "sources.md").write_text(
                    "## Sources\n\n" + "\n".join(f"- {u}" for u in sources) + "\n"
                )
            current.update(status="done", summary=summary, sources=sources)
        else:
            current.update(status="failed", error=warning or "no report")
        _write_meta(current)
        _announce(current)

    if background:
        threading.Thread(
            target=work, name=f"research-{meta['id']}", daemon=True
        ).start()
    else:
        work()
    return _read_meta(meta["id"]) or meta


# --- coding ---------------------------------------------------------------

_HARD = re.compile(
    r"\b(refactor|rewrite|migrat\w*|architect\w*|redesign|debug\w*|race condition|"
    r"performance|optimi[sz]\w*|entire|whole (?:codebase|project|repo)|"
    r"multiple files|from scratch|full[- ]stack|end[- ]to[- ]end)\b",
    re.I,
)


def choose_variant(task: str) -> str:
    """'max' for big or hard coding tasks, else 'high'. Pure."""
    if len(task or "") > 280 or _HARD.search(task or ""):
        return "max"
    return "high"


def resolve_directory(directory: str, title: str) -> tuple[Path | None, str | None]:
    """Validated working dir (or a fresh one). Returns (path, error)."""
    home = Path.home().resolve()
    if not (directory or "").strip():
        path = home / "jarvis-projects" / slugify(title)
        n = 2
        base = path
        while path.exists():
            path = base.with_name(f"{base.name}-{n}")
            n += 1
        path.mkdir(parents=True)
        return path, None
    path = Path(directory).expanduser()
    try:
        path = path.resolve()
    except OSError:
        return None, "directory not found"
    if not path.is_dir():
        return None, "directory not found"
    if path == home or path == Path("/") or home not in path.parents:
        return None, "directory must be a project folder inside your home"
    return path, None


def code_argv(task: str, directory: Path, variant: str) -> list[str]:
    """Headless opencode run. Pure."""
    return [
        "opencode",
        "run",
        "--auto",
        "-m",
        OPENCODE_MODEL,
        "--variant",
        variant,
        "--dir",
        str(directory),
        task,
    ]


def tail_summary(text: str, limit: int = 600) -> str:
    """Last ~limit chars of output, ANSI stripped. Pure."""
    clean = _ANSI.sub("", text or "").strip()
    return clean[-limit:].strip()


def start_code(
    task: str,
    *,
    directory: str = "",
    variant: str = "",
    popen=None,
    background: bool = True,
) -> dict:
    """Start a coding project on headless opencode. Returns its meta."""
    task = (task or "").strip()[:4000]
    variant = variant if variant in ("high", "max") else choose_variant(task)
    title = " ".join(task.split())
    path, error = resolve_directory(directory, title)
    meta = _new_meta(
        "code",
        title,
        task,
        OPENCODE_MODEL,
        variant=variant,
        directory=str(path) if path else (directory or None),
    )
    if error:
        meta.update(status="failed", error=error)
        _write_meta(meta)
        return meta
    log_path = _dir(meta["id"]) / "output.log"

    def work() -> None:
        opener = popen or subprocess.Popen
        try:
            with open(log_path, "wb") as log:
                proc = opener(
                    code_argv(task, path, variant),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    cwd=str(path),
                    start_new_session=True,
                )
                with _LOCK:
                    _PROCS[meta["id"]] = proc
                current = _read_meta(meta["id"]) or meta
                current["pid"] = proc.pid
                _write_meta(current)
                started = last_growth = time.monotonic()
                last_size = 0
                rc = None
                stalled = False
                while rc is None:
                    try:
                        rc = proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        size = log_path.stat().st_size if log_path.exists() else 0
                        now = time.monotonic()
                        if size != last_size:
                            last_size, last_growth = size, now
                        if (
                            now - last_growth > CODE_STALL_S
                            or now - started > CODE_TIMEOUT_S
                        ):
                            stalled = now - last_growth > CODE_STALL_S
                            with contextlib.suppress(OSError):
                                os.killpg(proc.pid, signal.SIGTERM)
                            rc = -1
        except OSError as exc:
            rc, stalled = None, False
            current = _read_meta(meta["id"]) or meta
            current.update(
                status="failed", error=f"opencode failed to start: {exc}"[:200]
            )
            _write_meta(current)
            _announce(current)
            return
        finally:
            with _LOCK:
                _PROCS.pop(meta["id"], None)
        current = _read_meta(meta["id"]) or meta
        if current.get("status") == "cancelled":
            return
        output = log_path.read_text(errors="replace") if log_path.exists() else ""
        current["summary"] = tail_summary(output)
        current["pid"] = None
        if rc == 0:
            current["status"] = "done"
        else:
            current["status"] = "failed"
            if stalled:
                current["error"] = (
                    "The coding engine went silent (the free model is likely "
                    "rate-limited). Try again later."
                )
            elif rc == -1:
                current["error"] = "Coding engine timed out."
            else:
                current["error"] = f"opencode exited {rc}"
        _write_meta(current)
        _announce(current)

    if background:
        threading.Thread(target=work, name=f"code-{meta['id']}", daemon=True).start()
    else:
        work()
    return _read_meta(meta["id"]) or meta


# --- UI command bus (voice → Project Archive window) ------------------------


class UiBus:
    """Ordered voice commands for the Project Archive window to replay.

    The window polls since=<seq>. Also remembers what voice last selected,
    so "abort this project" knows which one Sir means (the window is
    voice-only: every selection arrives through here).
    """

    def __init__(self, keep: int = 200) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._items: collections.deque = collections.deque(maxlen=keep)
        self.selected_id: str | None = None
        self.filter = "all"
        self.last_active = 0.0

    def push(self, action: str, **fields) -> int:
        with self._lock:
            self._seq += 1
            cmd = {"seq": self._seq, "action": action}
            cmd.update({k: v for k, v in fields.items() if v is not None})
            self._items.append(cmd)
            self.last_active = time.monotonic()
            if action == "select" and fields.get("project_id"):
                self.selected_id = fields["project_id"]
            if action == "filter" and fields.get("filter"):
                self.filter = fields["filter"]
            return self._seq

    def active(self, window_s: float = 15 * 60.0) -> bool:
        """Sir drove the archive by voice recently (bare "back"/"faster"
        are only ours while it is the surface in use)."""
        return self.last_active > 0 and time.monotonic() - self.last_active < window_s

    def since(self, seq: int | None) -> dict:
        with self._lock:
            if seq is None:
                return {"ok": True, "seq": self._seq, "commands": []}
            return {
                "ok": True,
                "seq": self._seq,
                "commands": [c for c in self._items if c["seq"] > seq],
            }


BUS = UiBus()


# --- voice grammar ----------------------------------------------------------

_ORDINALS = {
    "first": 1, "1st": 1, "one": 1, "1": 1,
    "second": 2, "2nd": 2, "two": 2, "2": 2,
    "third": 3, "3rd": 3, "three": 3, "3": 3,
    "fourth": 4, "4th": 4, "four": 4, "4": 4,
    "fifth": 5, "5th": 5, "five": 5, "5": 5,
    "sixth": 6, "6th": 6, "six": 6, "6": 6,
    "seventh": 7, "7th": 7, "seven": 7, "7": 7,
    "eighth": 8, "8th": 8, "eight": 8, "8": 8,
    "ninth": 9, "9th": 9, "nine": 9, "9": 9,
    "tenth": 10, "10th": 10, "ten": 10, "10": 10,
    "last": -1, "final": -1,
}  # fmt: skip
_ORD = r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last|final|\d{1,2}(?:st|nd|rd|th)?|one|two|three|four|five|six|seven|eight|nine|ten)"
_PROJ = r"(?:research\s+|coding\s+|code\s+)?(?:projects?|archive)"

_RULES: tuple[tuple[str, str], ...] = (
    ("filter", r"^(?:show|filter|list)(?: me)?(?: only)?(?: my| the)? (research|code|coding|all|every ?thing|everything)(?: projects?)?$"),
    ("show", rf"^(?:open|show|bring up|pull up|launch)(?: me)?(?: my| the)? {_PROJ}(?: window| archive)?$"),
    ("show", r"^(?:open|show)(?: the)? project archive$"),
    ("hide", rf"^(?:close|hide)(?: my| the)? {_PROJ}(?: window| archive)?$"),
    ("select_n", rf"^(?:open|select|show|go to|navigate to|switch to|jump to)(?: the)? project(?: number)? {_ORD}$"),
    ("select_n", rf"^(?:open|select|show|go to|navigate to|switch to|jump to)(?: the)? {_ORD} project$"),
    ("doc", rf"^(?:open|show|read|go to|pull up|bring up)(?: up)?(?: the)? {_ORD} (?:document|doc|file|one|report)$"),
    ("doc", rf"^(?:open |show |read )?(?:the )?(?:document|doc|file)(?: number)? {_ORD}$"),
    ("scroll_start", r"^(?:start |begin |keep |now )?(?:scrolling|scroll)(?: down)?(?: through(?: it| them| this)?)?(?: (?:really |very |nice and )?(slowly|slow|gently|quickly|quick|fast|medium))?$"),
    ("scroll_stop", r"^(?:stop|pause|halt|freeze)(?: the)? scrolling$|^(?:stop|pause) scroll$|^hold it(?: there)?$"),
    ("scroll_faster", r"^(?:scroll |go )?faster$|^speed (?:it )?up$"),
    ("scroll_slower", r"^(?:scroll |go )?slower$|^slow (?:it )?down$"),
    ("scroll_up", r"^(?:scroll|page|go) up$"),
    ("scroll_down", r"^(?:scroll|page) down$|^next page$"),
    ("scroll_top", r"^(?:go |scroll |jump )?(?:back )?to the top$"),
    ("scroll_bottom", r"^(?:go |scroll |jump )?to the (?:bottom|end)$"),
    ("close_doc", r"^close(?: the| this)? (?:document|doc|file)$"),
    ("back", r"^(?:go )?back$"),
    ("abort", r"^(?:abort|cancel|kill|stop)(?: this| the| that| my)?(?: research| coding| code)? (?:project|job)$"),
    ("select_name", rf"^(?:navigate|go|switch|jump) to(?: the)? (.+?)(?: {_PROJ})?$"),
    ("select_name", rf"^(?:open|select|show)(?: the| my)? (.+?) {_PROJ}$"),
)  # fmt: skip
_COMPILED = [(name, re.compile(rx)) for name, rx in _RULES]
_SPLIT = re.compile(r"\s*(?:[,;.!?]|\band then\b|\bthen\b|\band\b)\s*")
# STT often drops the commas: also cut right before a command verb.
_VERB_CUT = re.compile(
    r"\s+(?=(?:navigate|switch|jump) to\b|(?:start|begin) scrolling\b|"
    r"open (?:the |document |doc |project )|go back\b)"
)
_SPEED = {"slowly": "slow", "slow": "slow", "gently": "slow", "medium": "medium",
          "quickly": "fast", "quick": "fast", "fast": "fast"}  # fmt: skip


def _ordinal(raw: str) -> int:
    key = re.sub(r"(st|nd|rd|th)$", "", raw) if raw[:1].isdigit() else raw
    return _ORDINALS.get(key, 1)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).split())


def _match_clause(clause: str) -> tuple[str, re.Match] | None:
    for name, rx in _COMPILED:
        m = rx.match(clause)
        if m:
            return name, m
    return None


def _find_project(query: str, projects: list[dict]) -> dict | None:
    """Best title match for a spoken project name, or None."""
    from system.launcher import _score

    q = _norm(query)
    if not q or not projects:
        return None
    best, best_s = None, 0.0
    for p in projects:
        title = _norm(p.get("title", ""))
        s = _score(q, title)
        # Partial names ("the battery one") hit title words.
        words = [w for w in q.split() if len(w) > 3]
        if words and all(w in title.split() for w in words):
            s = max(s, 0.85)
        if s > best_s:
            best, best_s = p, s
    return best if best_s >= 0.6 else None


def parse_voice(text: str, projects: list[dict] | None = None) -> list[dict] | None:
    """Spoken Project Archive commands → [cmd...], or None if not ours.

    Chained clauses ("open research projects, navigate to the battery
    project and open the first document and start scrolling slowly") are
    all-or-nothing: one clause we don't understand hands the whole
    utterance back to the model. Pure apart from reading the project list.
    """
    if not _norm(text):
        return None
    parts = [
        _norm(piece)
        for chunk in _SPLIT.split((text or "").lower())
        for piece in _VERB_CUT.split(chunk)
        if _norm(piece)
    ]
    # Re-join fragments that only parse together ("research and development
    # project"): greedily merge an unparsed piece into the previous one.
    clauses: list[str] = []
    for part in parts:
        if clauses and _match_clause(part) is None:
            clauses[-1] = f"{clauses[-1]} and {part}"
        else:
            clauses.append(part)
    cmds: list[dict] = []
    plist = projects if projects is not None else None
    for clause in clauses:
        hit = _match_clause(clause)
        if hit is None:
            return None
        name, m = hit
        if name == "show":
            cmds.append({"action": "show"})
        elif name == "hide":
            cmds.append({"action": "hide"})
        elif name == "filter":
            word = m.group(1)
            f = (
                "code"
                if word in ("code", "coding")
                else "research"
                if word == "research"
                else "all"
            )
            cmds.append({"action": "filter", "filter": f})
        elif name == "select_n":
            cmds.append({"action": "select", "index": _ordinal(m.group(1))})
        elif name == "select_name":
            if plist is None:
                plist = list_projects()
            proj = _find_project(m.group(1), plist)
            if proj is None:
                # "go to the kitchen" is not ours; a lone unmatched name is
                # only ours when the utterance clearly concerns projects.
                if len(clauses) == 1:
                    return None
                cmds.append({"action": "missing", "query": m.group(1)})
            else:
                cmds.append(
                    {
                        "action": "select",
                        "project_id": proj["id"],
                        "title": proj.get("title", ""),
                    }
                )
        elif name == "doc":
            cmds.append({"action": "open_document", "index": _ordinal(m.group(1))})
        elif name == "scroll_start":
            cmds.append(
                {
                    "action": "scroll",
                    "mode": "start",
                    "speed": _SPEED.get(m.group(1) or "slow", "slow"),
                }
            )
        elif name.startswith("scroll_"):
            cmds.append({"action": "scroll", "mode": name.split("_", 1)[1]})
        elif name == "close_doc":
            cmds.append({"action": "close_document"})
        elif name == "back":
            cmds.append({"action": "back"})
        elif name == "abort":
            cmds.append({"action": "abort"})
    # Bare navigation words ("back", "faster", "go to the top") are only
    # ours when the archive is the active surface: require a projects
    # clause in the utterance or a prior voice selection.
    strong = {"show", "hide", "select", "open_document", "filter", "abort", "missing"}
    if not any(c["action"] in strong for c in cmds) and not BUS.active():
        return None
    return cmds


def _speak_ordinal(n: int) -> str:
    words = [
        "",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
    ]
    return "the last" if n < 0 else (words[n] if 0 < n < len(words) else str(n))


def reply_for(cmds: list[dict]) -> str:
    """Short spoken confirmation for an executed command chain. Pure."""
    for c in cmds:
        if c["action"] == "missing":
            return f"I can't find a project called {c.get('query', 'that')}, Sir."
    last = cmds[-1]
    a = last["action"]
    if a == "scroll":
        return {
            "start": "Scrolling, Sir.",
            "stop": "Holding here.",
            "faster": "Faster.",
            "slower": "Slower.",
        }.get(last.get("mode", ""), "Very good.")
    if a == "open_document":
        return f"Document {_speak_ordinal(last.get('index', 1))}, Sir."
    if a == "select":
        t = last.get("title")
        return f"{t}, Sir." if t else "Right away, Sir."
    if a == "show":
        return "Your projects, Sir."
    if a == "hide":
        return "Archive closed."
    if a == "filter":
        return {"code": "Coding projects.", "research": "Research projects."}.get(
            last.get("filter", ""), "Everything on file."
        )
    if a == "abort":
        return "Project aborted, Sir."
    return "Very good."


# --- shell window ---------------------------------------------------------


def shell_verb(verb: str, run=subprocess.run) -> bool:
    """Run a Jarvis shell CLI verb (projectsshow/projectshide). Fail-soft."""
    repo = Path(__file__).resolve().parent.parent
    for argv in (
        [str(repo / "shell" / "src-tauri" / "target" / "debug" / "jarvis-shell")],
        ["jarvis-shell"],
        ["jarvis"],
    ):
        if not (Path(argv[0]).exists() or shutil.which(argv[0])):
            continue
        try:
            run([*argv, verb], capture_output=True, timeout=10)
            return True
        except Exception:
            continue
    return False


def execute_voice(cmds: list[dict], heard: str = "", run=subprocess.run) -> dict:
    """Carry out parsed commands: window verbs, bus pushes, aborts.

    The first bus command carries what Sir said, for the window's voice
    console. Numbered selections resolve here (against the current filter)
    so "abort this project" always knows which project is meant.
    """
    ok = True
    shown = False
    echo = heard or None

    def show() -> None:
        nonlocal shown, ok
        if not shown:
            ok = shell_verb("projectsshow", run) and ok
            shown = True

    for c in cmds:
        a = c["action"]
        if a == "hide":
            ok = shell_verb("projectshide", run) and ok
            continue
        if a == "missing":
            ok = False
            continue
        if a == "abort":
            pid = BUS.selected_id
            ok = bool(pid and cancel_project(pid)) and ok
            continue
        if a == "select" and not c.get("project_id") and "index" in c:
            plist = [
                p
                for p in list_projects()
                if BUS.filter == "all" or p.get("kind") == BUS.filter
            ]
            i = c["index"] - 1 if c["index"] > 0 else len(plist) - 1
            if not 0 <= i < len(plist):
                ok = False
                continue
            c = {**c, "project_id": plist[i]["id"], "title": plist[i].get("title", "")}
        if a in ("show", "filter", "select", "open_document"):
            show()
        fields = {k: v for k, v in c.items() if k not in ("action", "title")}
        BUS.push(a, heard=echo, **fields)
        echo = None
    return {"ok": ok}
