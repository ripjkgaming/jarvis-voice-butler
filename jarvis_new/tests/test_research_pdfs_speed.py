"""Pipeline timing tests for system.research_pdfs, using sleeping fakes.

No network, no real viewers, no windows: search, download, pdftotext, the
window opener and KWin are all replaced by fakes that sleep for a known time,
so overlap, ordering and budget behaviour can be asserted from wall clock.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from system import research_pdfs as rp

SCREENS = [
    {"x": 0, "y": 0, "w": 1920, "h": 1080},
    {"x": 1920, "y": 0, "w": 1920, "h": 1080},
]
FILLER = "the quick brown fox jumps over the lazy dog and keeps running far away " * 3


class Rec:
    def __init__(self):
        self.lock = threading.Lock()
        self.searches: list[str] = []
        self.fetches: list[str] = []
        self.texts: list[tuple] = []
        self.opens: list[dict] = []
        self.done_at: dict[str, float] = {}

    def add(self, name: str, value) -> None:
        with self.lock:
            getattr(self, name).append(value)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(rp, "SEARCH_CACHE_PATH", tmp_path / "cache" / "search.json")
    monkeypatch.setattr(rp, "PAGE_CACHE_PATH", tmp_path / "cache" / "pages.json")
    monkeypatch.setattr(rp, "_log", lambda category, detail: None)
    monkeypatch.setattr(rp, "_throttle_state", {})
    monkeypatch.setattr(rp, "screens", lambda: SCREENS)
    monkeypatch.delenv("JARVIS_PDF_BUDGET_S", raising=False)


def rows_for(n: int, delays_by_idx=None) -> list[dict]:
    return [
        {
            "title": f"Lithium battery recycling report {i}",
            "snippet": "lithium battery recycling study",
            "url": f"https://site{i}.org/report{i}.pdf",
        }
        for i in range(n)
    ]


def install(monkeypatch, *, rows, delays=None, search_delay=0.05, slow_query=None,
            slow_search=0.0, open_delay=0.1, pdftotext_delay=0.05, page_count=12,
            fetch_default=0.05):
    rec = Rec()
    delays = delays or {}

    def fake_search(query):
        rec.add("searches", query)
        time.sleep(slow_search if slow_query and slow_query in query else search_delay)
        return [dict(r) for r in rows]

    def fake_fetch(url, dest):
        rec.add("fetches", url)
        time.sleep(delays.get(url, fetch_default))
        if url.endswith(".html"):
            with rec.lock:
                rec.done_at[url] = time.monotonic()
            return False, "not a pdf (text/html)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4 " + url.encode())
        with rec.lock:
            rec.done_at[url] = time.monotonic()
        return True, "ok"

    def fake_text(pdf, first, last):
        rec.add("texts", (str(pdf), first, last))
        time.sleep(pdftotext_delay)
        hot = 1 + sum(map(ord, Path(pdf).name)) % page_count
        return [
            ("lithium battery recycling " * 12 + FILLER) if p == hot else FILLER
            for p in range(first, last + 1)
        ]

    def fake_open(path, page, rect, claimed, wait_s=15.0):
        start = time.monotonic()
        time.sleep(open_delay)
        rec.add("opens", {"path": path, "page": page, "rect": dict(rect),
                          "start": start, "end": time.monotonic()})  # fmt: skip
        return {"path": path, "page": page, "rect": rect, "opened": True,
                "placed": True, "error": ""}  # fmt: skip

    monkeypatch.setattr(rp, "web_search", fake_search)
    monkeypatch.setattr(rp, "fetch_pdf", fake_fetch)
    monkeypatch.setattr(rp, "_pdftotext_range", fake_text)
    monkeypatch.setattr(rp, "_page_count", lambda pdf: page_count)
    monkeypatch.setattr(rp, "open_one", fake_open)
    return rec


def test_stages_overlap_wall_time_far_below_sum(monkeypatch, tmp_path):
    rec = install(monkeypatch, rows=rows_for(6), search_delay=0.4, pdftotext_delay=0.2,
                  open_delay=0.3, fetch_default=0.5)
    t = time.monotonic()
    res = rp.run_pipeline("lithium battery recycling", 3, tmp_path / "dl")
    wall = time.monotonic() - t
    # Sequential estimate of the sleeps: 7 searches + 3 downloads + 3 pages x 2 chunks + 3 opens.
    sequential = 7 * 0.4 + 3 * 0.5 + 3 * 2 * 0.2 + 3 * 0.3
    assert len(res["winners"]) == 3
    assert len(rec.searches) == 7  # every query variant fired
    assert wall < 3.0 and wall < 0.5 * sequential, (wall, sequential)
    assert res["timings"]["first_window_ms"] < res["timings"]["total_ms"]


def test_first_window_opens_before_last_download_finishes(monkeypatch, tmp_path):
    delays = {f"https://site{i}.org/report{i}.pdf": 0.2 * (i + 1) for i in range(5)}
    rec = install(monkeypatch, rows=rows_for(5), delays=delays, open_delay=0.1)
    res = rp.run_pipeline("lithium battery recycling", 3, tmp_path / "dl")
    winner_urls = [w["url"] for w in res["winners"]]
    assert winner_urls == [f"https://site{i}.org/report{i}.pdf" for i in range(3)]
    last_winner_done = max(rec.done_at[u] for u in winner_urls)
    first_open_start = min(o["start"] for o in rec.opens)
    assert first_open_start < last_winner_done
    assert res["timings"]["first_download_ms"] is not None
    assert res["timings"]["first_window_ms"] is not None


def test_slots_stay_distinct_regardless_of_arrival_order(monkeypatch, tmp_path):
    # Submission order 0..3, but completion order is reversed (3 first).
    delays = {f"https://site{i}.org/report{i}.pdf": 0.6 - 0.15 * i for i in range(4)}
    rec = install(monkeypatch, rows=rows_for(4), delays=delays)
    res = rp.run_pipeline("lithium battery recycling", 4, tmp_path / "dl")
    planned = rp.tile_rects(4, SCREENS)
    rects = [o["rect"] for o in sorted(rec.opens, key=lambda o: o["start"])]
    assert rects == planned  # slot k always gets planned rect k
    assert len({(r["x"], r["y"]) for r in rects}) == 4
    assert [w["slot"] for w in res["winners"]] == [0, 1, 2, 3]
    # the first to finish downloading is the last submitted, and it owns slot 0
    assert res["winners"][0]["url"] == "https://site3.org/report3.pdf"
    pages = [w["page"] for w in res["winners"]]
    assert len(set(pages)) == 4


def test_budget_cutoff_returns_and_names_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_PDF_BUDGET_S", "1.5")
    install(monkeypatch, rows=rows_for(6), fetch_default=5.0, search_delay=0.05)
    t = time.monotonic()
    res = rp.run_pipeline("lithium battery recycling", 2, tmp_path / "dl")
    elapsed = time.monotonic() - t
    assert elapsed < 2.8, elapsed
    assert res["budget_s"] == 1.5 and res["budget_hit"] is True
    assert res["winners"] == []
    assert res["skipped"] and all(s["reason"] == "budget" for s in res["skipped"])
    assert res["timings"]["total_ms"] < 2800


def test_budget_default_and_env_parsing(monkeypatch):
    assert rp.budget_s_from_env() == 40.0
    monkeypatch.setenv("JARVIS_PDF_BUDGET_S", "7")
    assert rp.budget_s_from_env() == 7.0
    monkeypatch.setenv("JARVIS_PDF_BUDGET_S", "bogus")
    assert rp.budget_s_from_env() == 40.0


def test_stops_searching_once_enough_candidates(monkeypatch, tmp_path):
    rec = install(monkeypatch, rows=rows_for(6), search_delay=0.05, slow_query="review",
                  slow_search=5.0)
    t = time.monotonic()
    res = rp.run_pipeline("lithium battery recycling", 1, tmp_path / "dl")
    assert time.monotonic() - t < 2.0
    assert len(res["winners"]) == 1
    assert len(rec.searches) == 7


def test_cache_hit_path_skips_search_download_and_pages(monkeypatch, tmp_path):
    rows = rows_for(4)
    rec = install(monkeypatch, rows=rows, fetch_default=0.05)
    out = tmp_path / "dl"
    first = rp.run_pipeline("lithium battery recycling", 4, out)
    assert len(first["winners"]) == 4
    n_search, n_fetch, n_text = len(rec.searches), len(rec.fetches), len(rec.texts)
    assert n_search == 7 and n_fetch == 4

    second = rp.run_pipeline("lithium battery recycling", 4, out)
    assert len(second["winners"]) == 4
    assert len(rec.searches) == n_search  # search results came from the cache
    assert len(rec.fetches) == n_fetch  # PDFs reused from the research folder
    assert len(rec.texts) == n_text  # page choices reused, keyed by file hash
    assert second["cache"]["search_cache_hits"] >= 1
    assert second["cache"]["pdf_reused"] >= 2
    assert second["cache"]["page_cache_hits"] >= 2
    # arrival order can differ between runs, but the chosen pages must match
    assert sorted(w["page"] for w in second["winners"]) == sorted(
        w["page"] for w in first["winners"]
    )


def test_search_cache_ttl_expires(monkeypatch):
    calls = []

    def fake(query):
        calls.append(query)
        return rows_for(2)

    monkeypatch.setattr(rp, "web_search", fake)
    rp.cached_search("solar panel recycling")
    rp.cached_search("solar panel recycling")
    assert len(calls) == 1
    data = json.loads(rp.SEARCH_CACHE_PATH.read_text())
    for entry in data["queries"].values():
        entry["ts"] -= rp.SEARCH_TTL_S + 60
    rp.SEARCH_CACHE_PATH.write_text(json.dumps(data))
    rp.cached_search("solar panel recycling")
    assert len(calls) == 2


def test_web_search_merges_providers_concurrently(monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "k1")
    monkeypatch.setenv("BRAVE_API_KEY", "k2")

    def slow(name, rows):
        def fn(*a):
            time.sleep(0.4)
            return rows
        return fn

    def broken(*a):
        raise OSError("down")

    monkeypatch.setattr(rp, "_search_you", slow("you", rows_for(2)))
    monkeypatch.setattr(rp, "_search_brave", slow("brave", rows_for(3)))
    monkeypatch.setattr(rp, "_search_ddg", broken)
    t = time.monotonic()
    got = rp.web_search("lithium battery recycling")
    assert time.monotonic() - t < 0.9  # providers ran at the same time
    assert len(got) == 5  # the broken provider contributed nothing


def test_page_ranking_scans_big_pdfs_in_parallel_chunks(monkeypatch, tmp_path):
    calls = []

    def fake_text(pdf, first, last):
        calls.append((first, last))
        return [FILLER] * (last - first + 1)

    monkeypatch.setattr(rp, "_pdftotext_range", fake_text)
    pdf = tmp_path / "big.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    monkeypatch.setattr(rp, "_page_count", lambda p: 25)
    ranking = rp.page_ranking(str(pdf), "lithium")
    assert sorted(calls) == [(1, 10), (11, 20), (21, 25)]
    assert sorted(ranking) == list(range(1, 26))

    calls.clear()
    monkeypatch.setattr(rp, "_page_count", lambda p: 200)
    ranking = rp.page_ranking(str(pdf), "lithium")
    assert max(last for _, last in calls) == rp.PAGE_SCAN_MAX
    assert sorted(ranking) == list(range(1, 201))  # unscanned tail kept as last resort
