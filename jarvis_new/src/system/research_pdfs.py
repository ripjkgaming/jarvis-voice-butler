"""Pull up PDF reports on a topic: search, download, pick pages, tile windows.

"Pull up relevant info on lithium battery recycling" -> run_pipeline() fires
PDF-biased searches at once, downloads candidates as soon as they are known,
and opens each finished PDF in its own small window (positioned through KWin)
at its best page, without waiting for the others. Every window has a slot
from the final tile layout, reserved in arrival order, so windows never
collide. A hard time budget (JARVIS_PDF_BUDGET_S, default 40 s) bounds the
whole thing and says which items were skipped.

Nothing inside a PDF is ever executed; every network call is a plain http(s)
GET (or the search API POST) with no credentials beyond the search keys
already in the environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from jarvis_rt import RunContext, ToolError, function_tool

MAX_BYTES = 40 * 1024 * 1024
TIMEOUT_S = 20
DEFAULT_COUNT = 4
MAX_COUNT = 6
DEFAULT_BUDGET_S = 40.0
SEARCH_STAGE_MAX_S = 12.0
DOWNLOAD_WORKERS = 8
OPEN_GRACE_S = 10.0
PAGE_SCAN_MAX = 80
PAGE_CHUNK = 10
SEARCH_TTL_S = 6 * 3600
CACHE_DIR = Path.home() / ".jarvis" / "research_cache"
SEARCH_CACHE_PATH = CACHE_DIR / "pdf_search.json"
PAGE_CACHE_PATH = CACHE_DIR / "pdf_pages.json"
PROVIDER_GAP_S = {"brave": 1.1, "ddg": 0.6}  # free tiers / bot checks dislike bursts
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
YOU_ENDPOINT = "https://ydc-index.io/v1/search"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DDG_ENDPOINT = "https://lite.duckduckgo.com/lite/"
_LOGIN_RE = re.compile(r"(login|signin|sign-in|sso|auth|paywall|subscribe)", re.I)
_STOP = frozenset(
    "a an and are as at be by for from how in into is it of on or the to what "
    "with about info information relevant report reports paper papers pdf pdfs "
    "find show pull up me some on".split()
)
_SKIP_HOSTS = ("localhost", "127.", "0.0.0.0", "169.254.", "::1")

SEARCH_TEMPLATES = (
    "{t} filetype:pdf report",
    "{t} pdf whitepaper",
    "{t} annual report pdf",
    "{t} filetype:pdf site:arxiv.org OR site:.gov OR site:.edu",
    "{t} filetype:pdf overview",
    "{t} pdf study statistics",
    "{t} filetype:pdf review",
)

_cache_lock = threading.Lock()
_throttle_guard = threading.Lock()
_throttle_state: dict[str, list] = {}


# --- small pure helpers ------------------------------------------------------


def slugify(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:limit].strip("-")) or "topic"


def key_terms(topic: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (topic or "").lower())
    terms = [w for w in words if w not in _STOP and len(w) > 2]
    return list(dict.fromkeys(terms)) or words


def _domain(url: str) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _safe_url(url: str) -> bool:
    p = urllib.parse.urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname.lower()
    if host.startswith(_SKIP_HOSTS) or host.endswith((".local", ".internal")):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    except ValueError:
        pass
    return True


def budget_s_from_env() -> float:
    try:
        v = float(os.environ.get("JARVIS_PDF_BUDGET_S") or DEFAULT_BUDGET_S)
    except ValueError:
        v = DEFAULT_BUDGET_S
    return max(0.5, min(v, 300.0))


def score_result(item: dict, terms: list[str]) -> float:
    """Relevance of a search hit: title weighs double the snippet."""
    title = (item.get("title") or "").lower()
    snip = (item.get("snippet") or "").lower()
    url = (item.get("url") or "").lower()
    s = 0.0
    for t in terms:
        s += 2.0 * (t in title) + 1.0 * (t in snip) + 0.5 * (t in url)
    if url.split("?")[0].endswith(".pdf"):
        s += 1.5
    return s


def rank_with_variety(items: list[dict], terms: list[str]) -> list[dict]:
    """Best-scored first, but a repeat domain is pushed behind fresh ones."""
    pool = sorted(items, key=lambda i: score_result(i, terms), reverse=True)
    out: list[dict] = []
    seen: set[str] = set()
    for it in pool:
        d = _domain(it["url"])
        if d not in seen:
            seen.add(d)
            out.append(it)
    out.extend(i for i in pool if i not in out)
    return out


# --- small JSON caches (search results, page choices) -----------------------


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        pass


def _ts(entry) -> float:
    try:
        return float(entry.get("ts", 0)) if isinstance(entry, dict) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _cache_get(path: Path, section: str, key: str):
    with _cache_lock:
        sec = _load_json(path).get(section)
    return sec.get(key) if isinstance(sec, dict) else None


def _cache_put(path: Path, section: str, key: str, value: dict, keep: int) -> None:
    with _cache_lock:
        data = _load_json(path)
        sec = data.get(section)
        if not isinstance(sec, dict):
            sec = {}
        sec[key] = {**value, "ts": time.time()}
        if len(sec) > keep:
            for old in sorted(sec, key=lambda k: _ts(sec[k]))[: len(sec) - keep]:
                sec.pop(old, None)
        data[section] = sec
        _save_json(path, data)


def _fresh(entry, ttl: float) -> bool:
    return isinstance(entry, dict) and time.time() - _ts(entry) < ttl


# --- search ------------------------------------------------------------------


def _clean(raw: str) -> str:
    import html

    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw or "")).split())


def _http_json(req: urllib.request.Request) -> dict:
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read(4_000_000))


def _search_you(query: str, key: str) -> list[dict]:
    req = urllib.request.Request(
        YOU_ENDPOINT,
        data=json.dumps({"query": query, "count": 10}).encode(),
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )
    data = _http_json(req)
    box = data.get("results", data)
    rows = box.get("web", []) if isinstance(box, dict) else []
    out = []
    for r in rows if isinstance(rows, list) else [rows]:
        if isinstance(r, dict) and r.get("url"):
            sn = r.get("snippets") or r.get("description") or ""
            if isinstance(sn, list):
                sn = " ".join(map(str, sn))
            out.append(
                {
                    "title": _clean(r.get("title", "")),
                    "snippet": _clean(sn),
                    "url": r["url"],
                }
            )
    return out


def _search_brave(query: str, key: str) -> list[dict]:
    url = BRAVE_ENDPOINT + "?" + urllib.parse.urlencode({"q": query, "count": 10})
    req = urllib.request.Request(
        url, headers={"X-Subscription-Token": key, "Accept": "application/json"}
    )
    rows = (_http_json(req).get("web") or {}).get("results") or []
    return [
        {
            "title": _clean(r.get("title", "")),
            "snippet": _clean(r.get("description", "")),
            "url": r["url"],
        }
        for r in rows
        if r.get("url")
    ]


def _search_ddg(query: str) -> list[dict]:
    req = urllib.request.Request(
        DDG_ENDPOINT,
        data=urllib.parse.urlencode({"q": query}).encode(),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        page = r.read(3_000_000).decode("utf-8", "replace")
    links = re.findall(r'<a rel="nofollow" href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
    snips = re.findall(r"result-snippet\">(.*?)</td>", page, re.S)
    out = []
    for i, (url, title) in enumerate(links):
        if "uddg=" in url:  # DDG redirect wrapper
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            url = (q.get("uddg") or [url])[0]
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("http"):
            out.append(
                {
                    "title": _clean(title),
                    "snippet": _clean(snips[i] if i < len(snips) else ""),
                    "url": url,
                }
            )
    return out


def _throttle(name: str) -> None:
    gap = PROVIDER_GAP_S.get(name, 0.0)
    if not gap:
        return
    with _throttle_guard:
        slot = _throttle_state.setdefault(name, [threading.Lock(), 0.0])
    with slot[0]:
        pause = slot[1] + gap - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        slot[1] = time.monotonic()


def web_search(query: str) -> list[dict]:
    """[{title, snippet, url}] merged from every configured provider, queried at once.

    A provider that errors or is rate-limited contributes nothing. [] when all fail.
    """
    you = (os.environ.get("YDC_API_KEY") or os.environ.get("YOU_API_KEY") or "").strip()
    brave = os.environ.get("BRAVE_API_KEY", "").strip()
    providers: list[tuple[str, object]] = []
    if you:
        providers.append(("you", lambda: _search_you(query, you)))
    if brave:
        providers.append(("brave", lambda: _search_brave(query, brave)))
    providers.append(("ddg", lambda: _search_ddg(query)))

    def run(name: str, fn):
        _throttle(name)
        return fn()

    pool = ThreadPoolExecutor(
        max_workers=len(providers), thread_name_prefix="pdf-provider"
    )
    try:
        futs = [pool.submit(run, name, fn) for name, fn in providers]
        out: list[dict] = []
        for fut in futs:  # provider order keeps the merge deterministic
            try:
                out.extend(fut.result(timeout=TIMEOUT_S * 2))
            except Exception:
                continue
        return out
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def cached_search(query: str) -> tuple[list[dict], bool]:
    """(rows, from_cache). Non-empty results are cached for SEARCH_TTL_S."""
    key = " ".join(query.lower().split())
    entry = _cache_get(SEARCH_CACHE_PATH, "queries", key)
    if _fresh(entry, SEARCH_TTL_S) and isinstance(entry.get("rows"), list):
        return entry["rows"], True
    rows = web_search(query)
    if rows:
        _cache_put(SEARCH_CACHE_PATH, "queries", key, {"rows": rows}, keep=200)
    return rows, False


# --- download ----------------------------------------------------------------


def _urlopen(req: urllib.request.Request, timeout: float = TIMEOUT_S):
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_pdf(url: str, dest: Path) -> tuple[bool, str]:
    """GET url to dest iff it is a real PDF within the size cap. (ok, reason)."""
    if not _safe_url(url):
        return False, "unsafe url"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.5"},
        )
        with _urlopen(req, TIMEOUT_S) as r:
            final = getattr(r, "url", None) or r.geturl()
            if final and not _safe_url(final):
                return False, "unsafe redirect"
            if final and _LOGIN_RE.search(urllib.parse.urlparse(final).path):
                return False, "login wall"
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" in ctype or ("pdf" not in ctype and "octet-stream" not in ctype):
                return False, f"not a pdf ({ctype or 'no type'})"
            try:
                if int(r.headers.get("Content-Length") or 0) > MAX_BYTES:
                    return False, "too large"
            except ValueError:
                pass
            deadline = time.monotonic() + TIMEOUT_S * 3
            buf = bytearray()
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_BYTES:
                    return False, "too large"
                if time.monotonic() > deadline:
                    return False, "too slow"
                if len(buf) >= 5 and not buf[:1024].lstrip().startswith(b"%PDF"):
                    return False, "no PDF signature"
    except Exception as exc:
        return False, f"fetch failed ({type(exc).__name__})"
    if not buf[:1024].lstrip().startswith(b"%PDF"):
        return False, "no PDF signature"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(bytes(buf))
    return True, "ok"


def _is_pdf_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(1024).lstrip().startswith(b"%PDF")
    except OSError:
        return False


def research_dir(topic: str, base: Path | None = None) -> Path:
    return (base or Path.home() / "Documents" / "Jarvis Research") / slugify(topic)


def _file_stem(item: dict) -> str:
    return (
        f"{slugify(item.get('title') or _domain(item['url']), 50)}-"
        + (hashlib.sha1(item["url"].encode()).hexdigest()[:6])
    )


def _grab(item: dict, dest_dir: Path, topic: str, want_pages: bool) -> dict:
    """Worker: reuse or download one candidate, then rank its pages. Never raises."""
    dest = dest_dir / f"{_file_stem(item)}.pdf"
    res = {"item": item, "dest": dest, "ok": False, "why": "", "reused": False,
           "ranking": [], "page_hit": False}  # fmt: skip
    try:
        if _is_pdf_file(dest):
            res.update(ok=True, why="cached", reused=True)
        else:
            res["ok"], res["why"] = fetch_pdf(item["url"], dest)
    except Exception as exc:
        res.update(ok=False, why=f"error ({type(exc).__name__})")
    if res["ok"] and want_pages:
        try:
            res["ranking"], res["page_hit"] = page_ranking_cached(str(dest), topic)
        except Exception:
            res["ranking"] = [1]
    return res


def find_pdfs(
    topic: str, n: int = DEFAULT_COUNT, out_dir: Path | None = None
) -> list[dict]:
    """Search, validate and download up to n relevant PDFs (no windows).

    Returns [{path, title, url, domain}] in arrival order, varied by domain.
    """
    res = run_pipeline(topic, n, out_dir, open_windows=False)
    return [
        {k: w[k] for k in ("path", "title", "url", "domain")} for w in res["winners"]
    ]


# --- page choice -------------------------------------------------------------


def page_scores(pages: list[str], topic: str) -> list[float]:
    terms = key_terms(topic)
    scores = []
    for text in pages:
        words = re.findall(r"[a-z0-9]+", text.lower())
        if len(words) < 30:
            scores.append(0.0)
            continue
        hits = 0.0
        distinct = 0
        for t in terms:
            c = sum(1 for w in words if w == t or (len(t) > 4 and w.startswith(t[:-1])))
            hits += c
            distinct += c > 0
        scores.append((hits * (1 + distinct)) / math.sqrt(len(words)))
    return scores


def _page_count(pdf_path: str) -> int:
    """Page count from pdfinfo; 0 when unknown."""
    exe = shutil.which("pdfinfo")
    if not exe:
        return 0
    try:
        out = subprocess.run(
            [exe, str(pdf_path)], capture_output=True, text=True, timeout=20
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    m = re.search(r"^Pages:\s*(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def _pdftotext_range(pdf_path: str, first: int, last: int) -> list[str] | None:
    """Per-page text for pages first..last (1-based). None when pdftotext fails."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        p = subprocess.run(
            [
                exe,
                "-layout",
                "-enc",
                "UTF-8",
                "-f",
                str(first),
                "-l",
                str(last),
                str(pdf_path),
                "-",
            ],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    pages = p.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():  # every page ends with \f
        pages.pop()
    return pages


def _scan_texts(pdf_path: str, limit: int) -> list[str]:
    """Text of pages 1..limit, fetched in parallel chunks. [] when pdftotext fails."""
    spans = [
        (s, min(s + PAGE_CHUNK - 1, limit)) for s in range(1, limit + 1, PAGE_CHUNK)
    ]
    if not spans:
        return []
    if len(spans) == 1:
        got = [_pdftotext_range(pdf_path, *spans[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(spans))) as pool:
            got = list(pool.map(lambda sp: _pdftotext_range(pdf_path, *sp), spans))
    if any(g is None for g in got):
        return []
    out: list[str] = []
    for (s, e), g in zip(spans, got):
        out.extend((g + [""] * (e - s + 1))[: e - s + 1])
    return out


def _pypdf_texts(pdf_path: str, limit: int) -> list[str]:
    try:
        from pypdf import PdfReader  # type: ignore

        return [
            (pg.extract_text() or "") for pg in PdfReader(str(pdf_path)).pages[:limit]
        ]
    except Exception:
        return []


def page_ranking(pdf_path: str, topic: str) -> list[int]:
    """1-based pages, best first, cover skipped. Big PDFs: only the first pages are scanned."""
    total = _page_count(pdf_path)
    limit = min(total, PAGE_SCAN_MAX) if total else PAGE_SCAN_MAX
    texts = _scan_texts(pdf_path, limit) or _pypdf_texts(pdf_path, limit)
    if not total:
        while texts and not texts[-1].strip():
            texts.pop()
    if not texts:
        return list(range(1, (total or 1) + 1))
    scores = page_scores(texts, topic)
    order = sorted(range(len(texts)), key=lambda i: (-scores[i], i))
    cands = [i for i in order if scores[i] > 0 and (i > 0 or len(texts) == 1)]
    if not cands and scores[0] > 0:
        cands = [0]
    ranked = [i + 1 for i in cands] + [i + 1 for i in order if i not in cands]
    ranked += list(range(len(texts) + 1, total + 1))  # unscanned tail, last resort
    return ranked


def page_ranking_cached(pdf_path: str, topic: str) -> tuple[list[int], bool]:
    """(page_ranking, from_cache), keyed by file sha256 + topic."""
    try:
        h = hashlib.sha256()
        with open(pdf_path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        key = f"{h.hexdigest()}|{' '.join(topic.lower().split())}"
    except OSError:
        return page_ranking(pdf_path, topic), False
    entry = _cache_get(PAGE_CACHE_PATH, "pages", key)
    cached = entry.get("ranking") if isinstance(entry, dict) else None
    if isinstance(cached, list) and cached:
        try:
            return [int(p) for p in cached], True
        except (TypeError, ValueError):
            pass
    ranking = page_ranking(pdf_path, topic)
    _cache_put(PAGE_CACHE_PATH, "pages", key, {"ranking": ranking}, keep=500)
    return ranking, False


def pick_page(ranking: list[int], avoid: set[int] | frozenset = frozenset()) -> int:
    """First page of the ranking not already used by another window."""
    for p in ranking:
        if p not in avoid:
            return p
    return ranking[0] if ranking else 1


def best_page(
    pdf_path: str, topic: str, avoid: set[int] | frozenset = frozenset()
) -> int:
    """1-based page where the topic's terms are densest (cover skipped)."""
    return pick_page(page_ranking(pdf_path, topic), avoid)


def assign_pages(pdf_paths: list[str], topic: str) -> list[int]:
    used: set[int] = set()
    out = []
    for p in pdf_paths:
        pg = best_page(p, topic, avoid=used)
        used.add(pg)
        out.append(pg)
    return out


# --- tiling ------------------------------------------------------------------


def tile_rects(n: int, screens: list[dict]) -> list[dict]:
    """n distinct, mostly non-overlapping window rects inside the screens.

    Windows are dealt round-robin over the screens (left to right), then laid
    out on a grid per screen. Each window is ~34% of the screen width and
    ~46% of its height, never larger than its grid cell.
    """
    if n <= 0:
        return []
    scr = sorted(
        screens or [{"x": 0, "y": 0, "w": 1920, "h": 1080}],
        key=lambda s: (s["x"], s["y"]),
    )
    buckets: list[list[int]] = [[] for _ in scr]
    for i in range(n):
        buckets[i % len(scr)].append(i)
    rects: list[dict | None] = [None] * n
    gap = 12
    for s, idxs in zip(scr, buckets):
        k = len(idxs)
        if not k:
            continue
        cols = 1 if k == 1 else 2 if k <= 4 else 3
        rows = math.ceil(k / cols)
        cw, ch = s["w"] / cols, s["h"] / rows
        w = int(min(0.34 * s["w"], cw - 2 * gap))
        h = int(min(0.46 * s["h"] if rows > 1 else 0.5 * s["h"], ch - 2 * gap))
        w, h = max(w, 320), max(h, 240)
        for j, i in enumerate(idxs):
            r, c = divmod(j, cols)
            x = int(s["x"] + c * cw + (cw - w) / 2)
            y = int(s["y"] + r * ch + (ch - h) / 2)
            x = min(max(x, s["x"]), s["x"] + s["w"] - w)
            y = min(max(y, s["y"]), s["y"] + s["h"] - h)
            rects[i] = {"x": x, "y": y, "w": w, "h": h}
    return [r for r in rects if r is not None]


def viewer_command(pdf: str, page: int) -> tuple[str, list[str]] | None:
    """(viewer name, argv) for the first installed PDF viewer."""
    if shutil.which("okular"):
        return "okular", ["okular", "--page", str(page), pdf]
    if shutil.which("evince"):
        return "evince", ["evince", f"--page-index={page}", pdf]
    if shutil.which("zathura"):
        return "zathura", ["zathura", "-P", str(page), pdf]
    if shutil.which("mupdf"):
        return "mupdf", ["mupdf", pdf, str(page)]
    for b in ("brave-browser", "brave", "firefox", "chromium", "google-chrome"):
        if shutil.which(b):
            return b, [b, "--new-window", f"file://{pdf}#page={page}"]
    return None


def _spawn(argv: list[str]):
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def screens() -> list[dict]:
    try:
        from system.monitor_window_move import monitors

        return monitors()
    except Exception:
        return []


_INFO_CALL = [
    "busctl", "--user", "--json=short", "call", "org.kde.KWin", "/KWin",
    "org.kde.KWin", "getWindowInfo", "s",
]  # fmt: skip


def kwin_info_by_uuid(uuid: str) -> dict | None:
    try:
        p = subprocess.run(
            [*_INFO_CALL, uuid], capture_output=True, text=True, timeout=3
        )
        if p.returncode:
            return None
        vals = json.loads(p.stdout)["data"][0]
        info = {k: v["data"] for k, v in vals.items()}
        info["uuid"] = uuid
        return info
    except Exception:
        return None


def kwin_candidates(hint: str) -> list[str]:
    """uuids of windows whose title/app matches hint."""
    from system import kwin_windows

    try:
        return [u for u, _ in kwin_windows.find(hint)]
    except Exception:
        return []


def kwin_place(uuid: str, rect: dict) -> bool:
    """Un-maximise, un-minimise and size/position one window by uuid."""
    from system import window_layout

    js = f"""
var id = {json.dumps(uuid)};
var r = {json.dumps(rect)};
var ws = workspace.windowList();
for (var i = 0; i < ws.length; i++) {{
  var w = ws[i];
  if (String(w.internalId) !== id) continue;
  try {{ w.minimized = false; }} catch (e) {{}}
  try {{ w.setMaximize(false, false); }} catch (e) {{}}
  try {{ w.fullScreen = false; }} catch (e) {{}}
  w.frameGeometry = {{x: r.x, y: r.y, width: r.w, height: r.h}};
}}
"""
    return window_layout.run_script(js, name=f"jarvis-pdf-{uuid.strip('{}')[:8]}")


def _find_window(
    proc, viewer: str, claimed: set, baseline: set, wait_s: float
) -> dict | None:
    deadline = time.monotonic() + wait_s
    pid = getattr(proc, "pid", None)
    while time.monotonic() < deadline:
        fresh = []
        for u in kwin_candidates(viewer):
            if u in claimed:
                continue
            info = kwin_info_by_uuid(u)
            if not info:
                continue
            if pid is not None and info.get("pid") == pid:
                return info
            if u not in baseline:
                fresh.append(info)
        if fresh:
            return fresh[0]
        time.sleep(0.4)
    return None


def _rect_ok(info: dict, rect: dict, tol: int = 40) -> bool:
    try:
        return (
            not info.get("minimized")
            and abs(info["x"] - rect["x"]) <= tol
            and abs(info["y"] - rect["y"]) <= tol
            and abs(info["width"] - rect["w"]) <= tol * 2
            and abs(info["height"] - rect["h"]) <= tol * 2
        )
    except (KeyError, TypeError):
        return False


def open_one(
    path: str, page: int, rect: dict, claimed: set, wait_s: float = 15.0
) -> dict:
    """Open one (pdf, page) in a window and place it at rect.

    Returns {path, page, rect, opened, placed, error[, uuid]}. ``opened`` = a
    window exists; ``placed`` = KWin confirms it is at the target rectangle.
    Callers must not run two of these at once (window detection uses a diff).
    """
    row = {"path": path, "page": page, "rect": rect, "opened": False,
           "placed": False, "error": ""}  # fmt: skip
    cmd = viewer_command(path, page)
    if not cmd:
        row["error"] = "no PDF viewer installed"
        return row
    viewer, argv = cmd
    baseline = set(kwin_candidates(viewer))
    try:
        proc = _spawn(argv)
    except OSError as exc:
        row["error"] = f"launch failed: {exc}"
        return row
    info = _find_window(proc, viewer, claimed, baseline, wait_s)
    if not info:
        row["error"] = "window never appeared"
        return row
    claimed.add(info["uuid"])
    row["opened"] = True
    row["uuid"] = info["uuid"]
    kwin_place(info["uuid"], rect)
    for _ in range(6):
        time.sleep(0.3)
        now = kwin_info_by_uuid(info["uuid"])
        if now and _rect_ok(now, rect):
            row["placed"] = True
            break
    if not row["placed"]:
        row["error"] = "window opened but KWin did not confirm its position"
    return row


def open_tiled(items: list[tuple[str, int]], wait_s: float = 15.0) -> list[dict]:
    """Open each (pdf_path, page) in its own tiled window, one after another."""
    rects = tile_rects(len(items), screens())
    claimed: set[str] = set()
    return [
        open_one(path, page, rect, claimed, wait_s)
        for (path, page), rect in zip(items, rects)
    ]


def _open_job(
    path: str, page: int, rect: dict, claimed: set, hard_deadline: float
) -> dict:
    if time.monotonic() >= hard_deadline:
        return {"path": path, "page": page, "rect": rect, "opened": False,
                "placed": False, "error": "skipped: budget ran out"}  # fmt: skip
    return open_one(path, page, rect, claimed)


# --- pipeline ----------------------------------------------------------------


def run_pipeline(
    topic: str,
    count: int = DEFAULT_COUNT,
    out_dir: Path | None = None,
    *,
    open_windows: bool = True,
    budget_s: float | None = None,
) -> dict:
    """Search, download and (optionally) open PDFs as they arrive.

    Stages overlap: all queries run at once, each finished search feeds the
    download pool, and each finished download is paged and opened at once in
    its reserved slot. The first ``count`` valid PDFs win; the rest are
    cancelled. Returns {winners, skipped, failed, timings, budget_s,
    budget_hit, cache, folder}. Winner states: found | pending | opened |
    failed | skipped.
    """
    t0 = time.monotonic()
    budget = budget_s_from_env() if budget_s is None else max(0.5, float(budget_s))
    hard = t0 + budget
    search_stop = t0 + min(budget * 0.4, SEARCH_STAGE_MAX_S)
    n = max(1, min(int(count or DEFAULT_COUNT), MAX_COUNT))
    topic = " ".join((topic or "").split())
    terms = key_terms(topic)
    dest_dir = out_dir or research_dir(topic)
    cap = n * 3 + 2  # candidate PDFs worth knowing about (~3x what we need)
    queries = [t.format(t=topic) for t in SEARCH_TEMPLATES]

    def ms() -> int:
        return round((time.monotonic() - t0) * 1000)

    timings: dict = {"search_ms": None, "first_download_ms": None,
                     "first_window_ms": None, "total_ms": None}  # fmt: skip
    stats = {"search_queries": len(queries), "search_cache_hits": 0,
             "pdf_reused": 0, "page_cache_hits": 0}  # fmt: skip
    state = {"searching": True, "rects": None}
    hits: list[dict] = []
    seen: set[str] = set()
    submitted: set[str] = set()
    winners: list[dict] = []
    winner_domains: set[str] = set()
    spares: list[dict] = []
    used_pages: set[int] = set()
    claimed: set[str] = set()
    failed: list[dict] = []
    skipped: list[dict] = []
    pending: dict = {}  # future -> (kind, payload)

    search_pool = ThreadPoolExecutor(
        max_workers=len(queries), thread_name_prefix="pdf-search"
    )
    dl_pool = ThreadPoolExecutor(
        max_workers=DOWNLOAD_WORKERS, thread_name_prefix="pdf-dl"
    )
    open_pool = (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-open")
        if open_windows
        else None
    )  # one lane: window detection diffs the window list, so opens must not overlap

    def stop_kind(kind: str) -> None:
        for f in [f for f, (k, _) in pending.items() if k == kind]:
            f.cancel()
            pending.pop(f, None)

    def finish_search() -> None:
        if state["searching"]:
            state["searching"] = False
            timings["search_ms"] = ms()
            stop_kind("search")

    def schedule() -> None:
        if len(winners) >= n:
            return
        for item in rank_with_variety(hits, terms)[:cap]:
            if len(submitted) >= cap:
                return
            if item["url"] in submitted:
                continue
            submitted.add(item["url"])
            pending[dl_pool.submit(_grab, item, dest_dir, topic, open_windows)] = (
                "dl",
                item,
            )

    def claim(res: dict) -> None:
        item = res["item"]
        dom = _domain(item["url"])
        win = {"path": str(res["dest"]), "title": item.get("title") or res["dest"].stem,
               "url": item["url"], "domain": dom, "slot": len(winners),
               "state": "pending" if open_windows else "found"}  # fmt: skip
        winners.append(win)
        winner_domains.add(dom)
        if len(winners) >= n:
            stop_kind("dl")
            finish_search()
        if open_windows:
            if state["rects"] is None:
                state["rects"] = tile_rects(n, screens())
            page = pick_page(res["ranking"], used_pages)
            used_pages.add(page)
            win["page"] = page
            rect = state["rects"][win["slot"]]
            pending[
                open_pool.submit(_open_job, win["path"], page, rect, claimed, hard)
            ] = ("open", win)

    def handle(f) -> None:
        kind, payload = pending.pop(f)
        if f.cancelled():
            return
        if kind == "open":
            win = payload
            try:
                row = f.result()
            except Exception as exc:
                row = {"opened": False, "placed": False,
                       "error": f"open error ({type(exc).__name__})"}  # fmt: skip
            win.update(opened=bool(row.get("opened")), placed=bool(row.get("placed")),
                       error=row.get("error", ""))  # fmt: skip
            if row.get("uuid"):
                win["uuid"] = row["uuid"]
            if win["opened"]:
                win["state"] = "opened"
                if timings["first_window_ms"] is None:
                    timings["first_window_ms"] = ms()
            elif str(win["error"]).startswith("skipped"):
                win["state"] = "skipped"
            else:
                win["state"] = "failed"
            return
        if time.monotonic() >= hard:  # late search/download results are ignored
            return
        if kind == "search":
            try:
                rows, hit = f.result()
            except Exception:
                rows, hit = [], False
            if hit:
                stats["search_cache_hits"] += 1
            for r in rows:
                if not isinstance(r, dict):
                    continue
                u = (r.get("url") or "").split("#")[0]
                if u and u not in seen and _safe_url(u):
                    seen.add(u)
                    hits.append({**r, "url": u})
            if len(hits) >= cap or not any(k == "search" for k, _ in pending.values()):
                finish_search()
            schedule()
            return
        # kind == "dl"
        item = payload
        try:
            res = f.result()
        except Exception:
            res = {"item": item, "ok": False, "why": "error"}
        if not res.get("ok"):
            failed.append({"title": item.get("title") or item["url"], "url": item["url"],
                           "reason": res.get("why", "")})  # fmt: skip
            return
        if timings["first_download_ms"] is None:
            timings["first_download_ms"] = ms()
        if res.get("reused"):
            stats["pdf_reused"] += 1
        if res.get("page_hit"):
            stats["page_cache_hits"] += 1
        if len(winners) < n:
            if _domain(item["url"]) in winner_domains and any(
                k == "dl" for k, _ in pending.values()
            ):
                spares.append(
                    res
                )  # same site again: prefer other sites still downloading
            else:
                claim(res)
        if not any(k == "dl" for k, _ in pending.values()):
            while spares and len(winners) < n:
                claim(spares.pop(0))
            if len(winners) >= n:
                stop_kind("dl")

    for q in queries:
        pending[search_pool.submit(cached_search, q)] = ("search", q)

    budget_hit = False
    try:
        while pending:
            now = time.monotonic()
            if now >= hard:
                budget_hit = True
                break
            if state["searching"] and now >= search_stop:
                finish_search()
                schedule()
                continue
            limit = hard - now
            if state["searching"]:
                limit = min(limit, search_stop - now)
            done, _ = wait(list(pending), timeout=max(0.01, min(limit, 0.5)),
                           return_when=FIRST_COMPLETED)  # fmt: skip
            for f in done:
                if f in pending:
                    handle(f)

        if budget_hit:
            for f, (kind, payload) in list(pending.items()):
                if kind == "dl":
                    f.cancel()
                    pending.pop(f, None)
                    skipped.append({"title": payload.get("title") or payload["url"],
                                    "url": payload["url"], "reason": "budget"})  # fmt: skip
                elif kind == "search":
                    f.cancel()
                    pending.pop(f, None)
            grace_end = (
                time.monotonic() + OPEN_GRACE_S
            )  # let windows already opening finish
            while pending:
                left = grace_end - time.monotonic()
                if left <= 0:
                    break
                done, _ = wait(list(pending), timeout=min(left, 0.5),
                               return_when=FIRST_COMPLETED)  # fmt: skip
                for f in done:
                    if f in pending:
                        handle(f)
    finally:
        for pool in (search_pool, dl_pool, open_pool):
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)

    for w in winners:
        if w["state"] == "pending":  # budget ran out before its window finished
            w["state"] = "skipped"
            w["error"] = "still opening when the budget ran out"
        if w["state"] == "skipped":
            skipped.append({"title": w["title"], "url": w["url"], "reason": "budget"})
    timings["total_ms"] = ms()
    return {
        "topic": topic,
        "count": n,
        "winners": winners,
        "skipped": skipped,
        "failed": failed,
        "timings": timings,
        "budget_s": budget,
        "budget_hit": budget_hit or bool(skipped),
        "cache": stats,
        "folder": str(dest_dir),
    }


def _log(category: str, detail: str) -> None:
    try:
        from system import log_action

        log_action(category, detail)
    except Exception:
        pass


# --- orchestration + voice tool ---------------------------------------------


def run_research(topic: str, count: int = DEFAULT_COUNT) -> dict:
    res = run_pipeline(topic, count)
    winners = res["winners"]
    budget = res["budget_s"]
    skipped = res["skipped"]
    _log(
        "research_pdfs",
        f"n={res['count']} winners={len(winners)} skipped={len(skipped)} "
        f"timings={res['timings']}",
    )
    if not winners:
        if res["budget_hit"]:
            say = (
                f"I ran out of the {budget:.0f} second budget before any PDF on "
                f"{topic} finished downloading, Sir."
            )
        else:
            say = f"I could not find downloadable PDF reports on {topic}, Sir."
        return {"say": say, "opened": [], "timings": res["timings"],
                "skipped": [s["title"] for s in skipped]}  # fmt: skip
    opened = [w for w in winners if w["state"] == "opened"]
    parts = []
    for w in opened:
        title = re.sub(r"\s+", " ", w["title"])[:70]
        parts.append(f"{title} (page {w['page']}, {w['domain']})")
    if not opened:
        err = next((w["error"] for w in winners if w.get("error")), "unknown")
        say = (
            f"I downloaded {len(winners)} reports on {topic} to your Jarvis Research "
            f"folder, Sir, but none would open: {err}."
        )
    else:
        say = f"Opened {len(parts)} reports on {topic}, Sir: " + "; ".join(parts) + "."
        failed = sum(1 for w in winners if w["state"] == "failed")
        if failed:
            say += f" {failed} more downloaded but failed to open."
        unplaced = sum(1 for w in opened if not w.get("placed"))
        if unplaced:
            say += f" {unplaced} could not be positioned precisely."
    if skipped:
        say += (
            f" {len(skipped)} more were still downloading when the {budget:.0f} second "
            "budget ran out, so I skipped them."
        )
    return {
        "say": say,
        "opened": parts,
        "folder": res["folder"],
        "timings": res["timings"],
        "skipped": [s["title"] for s in skipped],
    }


class ResearchPdfTools:
    """Voice tool: find PDF reports on a topic and tile them on screen."""

    @property
    def tools(self) -> list:
        return [self.research_pdfs]

    @function_tool()
    async def research_pdfs(
        self, context: RunContext, topic: str, count: int = DEFAULT_COUNT
    ) -> dict:
        """Find PDF reports/papers on a topic and open each in its own small
        window on the screens, each at a different relevant page.

        Use for: "pull up relevant info on lithium battery recycling", "find
        reports on X", "show me papers about Y", "get me PDFs on Z", "open
        some research on W". Not for one named website or a single known file.

        Args:
            topic: The subject, in Sir's words.
            count: How many PDFs to open (default 4, max 6).
        """
        import phone_session

        private_error = phone_session.private_tool_error("research_pdfs")
        if private_error:
            raise ToolError(private_error)
        topic = " ".join((topic or "").split())
        if len(topic) < 3:
            raise ToolError("What topic should I look up, Sir?")
        count = max(1, min(int(count or DEFAULT_COUNT), MAX_COUNT))
        return await asyncio.to_thread(run_research, topic, count)
