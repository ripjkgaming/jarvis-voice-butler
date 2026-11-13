import io
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from system import research_pdfs as rp  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_caches(monkeypatch, tmp_path):
    """Keep search/page caches and log_action out of the real home directory."""
    monkeypatch.setattr(rp, "SEARCH_CACHE_PATH", tmp_path / "cache" / "search.json")
    monkeypatch.setattr(rp, "PAGE_CACHE_PATH", tmp_path / "cache" / "pages.json")
    monkeypatch.setattr(rp, "_log", lambda category, detail: None)
    monkeypatch.setattr(rp, "SESSION_PATH", tmp_path / "cache" / "session.json")
    monkeypatch.setattr(rp, "hide_viewer_sidebar", lambda: None)
    monkeypatch.setattr(rp, "ARXIV_ENABLED", False)
    monkeypatch.setattr(rp, "dismiss_viewer_dialogs", lambda *a, **k: 0)


def make_pdf(pages: list[str]) -> bytes:
    """Minimal valid multi-page text PDF."""
    objs = []
    n = len(pages)
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(n))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font = 3 + n * 2
    for i, text in enumerate(pages):
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + i * 2} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        lines = [text[j : j + 80] for j in range(0, len(text), 80)]
        body = (
            "BT /F1 10 Tf 12 TL 20 760 Td "
            + " ".join(f"({l.replace('(', '').replace(')', '')}) Tj T*" for l in lines)
            + " ET"
        )
        objs.append(f"<< /Length {len(body)} >>\nstream\n{body}\nendstream".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF".encode()
    return bytes(out)


filler = "the quick brown fox jumps over the lazy dog and keeps running far away " * 3


class FakeResp(io.BytesIO):
    def __init__(
        self, body, ctype="application/pdf", url="https://x.org/a.pdf", clen=None
    ):
        super().__init__(body)
        self.headers = {"Content-Type": ctype}
        if clen is not None:
            self.headers["Content-Length"] = str(clen)
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def patch_open(monkeypatch, resp):
    monkeypatch.setattr(rp, "_urlopen", lambda req, timeout=0: resp)


def test_fetch_rejects_html(monkeypatch, tmp_path):
    patch_open(monkeypatch, FakeResp(b"<html>", "text/html"))
    ok, why = rp.fetch_pdf("https://x.org/a", tmp_path / "a.pdf")
    assert not ok and "not a pdf" in why and not (tmp_path / "a.pdf").exists()


def test_fetch_rejects_fake_pdf_type(monkeypatch, tmp_path):
    patch_open(monkeypatch, FakeResp(b"<html>login</html>"))
    assert not rp.fetch_pdf("https://x.org/a.pdf", tmp_path / "a.pdf")[0]


def test_fetch_rejects_oversized(monkeypatch, tmp_path):
    patch_open(monkeypatch, FakeResp(make_pdf(["hi"]), clen=rp.MAX_BYTES + 1))
    ok, why = rp.fetch_pdf("https://x.org/a.pdf", tmp_path / "a.pdf")
    assert not ok and why == "too large"
    monkeypatch.setattr(rp, "MAX_BYTES", 100)
    patch_open(monkeypatch, FakeResp(make_pdf(["hi"])))
    assert rp.fetch_pdf("https://x.org/a.pdf", tmp_path / "b.pdf") == (
        False,
        "too large",
    )


def test_fetch_rejects_login_wall_and_unsafe(monkeypatch, tmp_path):
    patch_open(
        monkeypatch, FakeResp(make_pdf(["hi"]), url="https://x.org/login/doc.pdf")
    )
    assert rp.fetch_pdf("https://x.org/a.pdf", tmp_path / "a.pdf") == (
        False,
        "login wall",
    )
    assert not rp.fetch_pdf("file:///etc/passwd", tmp_path / "p.pdf")[0]
    assert not rp.fetch_pdf("http://127.0.0.1/a.pdf", tmp_path / "p.pdf")[0]


def test_fetch_accepts_real_pdf(monkeypatch, tmp_path):
    patch_open(monkeypatch, FakeResp(make_pdf(["hello"])))
    assert rp.fetch_pdf("https://x.org/a.pdf", tmp_path / "d" / "a.pdf")[0]
    assert (tmp_path / "d" / "a.pdf").read_bytes().startswith(b"%PDF")


def test_find_pdfs_filters_and_varies(monkeypatch, tmp_path):
    rows = [
        {
            "title": "Lithium battery recycling report",
            "snippet": "",
            "url": "https://a.org/1.pdf",
        },
        {
            "title": "Lithium battery recycling report 2",
            "snippet": "",
            "url": "https://a.org/2.pdf",
        },
        {
            "title": "Lithium battery recycling html",
            "snippet": "",
            "url": "https://b.org/page",
        },
        {
            "title": "Lithium battery recycling big",
            "snippet": "",
            "url": "https://c.org/big.pdf",
        },
        {
            "title": "Lithium battery recycling c",
            "snippet": "",
            "url": "https://d.org/3.pdf",
        },
    ]
    monkeypatch.setattr(rp, "web_search", lambda q: rows)

    def fake(req, timeout=0):
        u = req.full_url
        if u.endswith("page"):
            return FakeResp(b"<html>", "text/html", u)
        if u.endswith("big.pdf"):
            return FakeResp(make_pdf(["x"]), url=u, clen=rp.MAX_BYTES + 5)
        return FakeResp(make_pdf(["x " + u]), url=u)

    monkeypatch.setattr(rp, "_urlopen", fake)
    got = rp.find_pdfs("lithium battery recycling", 3, out_dir=tmp_path)
    urls = [g["url"] for g in got]
    assert len(got) == 3
    assert "https://b.org/page" not in urls and "https://c.org/big.pdf" not in urls
    assert [g["domain"] for g in got][:2] != ["a.org", "a.org"]
    assert all(Path(g["path"]).exists() for g in got)


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="poppler missing")
def test_best_page_and_distinct(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    topic = "lithium battery recycling"
    a.write_bytes(
        make_pdf([filler, filler, "lithium battery recycling " * 12 + filler, filler])
    )
    b.write_bytes(
        make_pdf([filler, "lithium battery recycling " * 12 + filler, filler])
    )
    assert rp.best_page(str(a), topic) == 3
    assert rp.best_page(str(b), topic) == 2
    # identical docs: pages are forced to differ
    c = tmp_path / "c.pdf"
    c.write_bytes(a.read_bytes())
    pages = rp.assign_pages([str(a), str(c)], topic)
    assert len(set(pages)) == 2


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="poppler missing")
def test_no_text_layer_falls_back_to_distinct_pages(tmp_path):
    a = tmp_path / "a.pdf"
    a.write_bytes(make_pdf(["", "", ""]))
    c = tmp_path / "c.pdf"
    c.write_bytes(a.read_bytes())
    assert rp.assign_pages([str(a), str(c)], "anything here") == [1, 2]


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize(
    "scr",
    [
        [{"x": 0, "y": 0, "w": 1920, "h": 1080}],
        [
            {"x": 0, "y": 0, "w": 1920, "h": 1080},
            {"x": 1920, "y": 0, "w": 1920, "h": 1080},
        ],
        [
            {"x": 0, "y": 0, "w": 1920, "h": 1080},
            {"x": 1920, "y": 0, "w": 1366, "h": 768},
            {"x": 3840, "y": 0, "w": 1920, "h": 1080},
        ],
    ],
)
def test_tiles_distinct_and_inside(n, scr):
    rects = rp.tile_rects(n, scr)
    assert len(rects) == n
    assert len({(r["x"], r["y"]) for r in rects}) == n
    for r in rects:
        assert any(
            s["x"] <= r["x"]
            and r["x"] + r["w"] <= s["x"] + s["w"]
            and s["y"] <= r["y"]
            and r["y"] + r["h"] <= s["y"] + s["h"]
            for s in scr
        )
        assert r["w"] <= 0.36 * max(s["w"] for s in scr)
    for i in range(n):  # no real overlap
        for j in range(i + 1, n):
            a, b = rects[i], rects[j]
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            assert not (ox > 0 and oy > 0)


class FakeKWin:
    def __init__(self, fail_uuid=None):
        self.windows = {}
        self.placed = {}
        self.n = 0
        self.fail = fail_uuid

    def spawn(self, argv):
        self.n += 1
        uuid = "{w%d}" % self.n
        self.windows[uuid] = {
            "uuid": uuid,
            "pid": 1000 + self.n,
            "x": 0.0,
            "y": 0.0,
            "width": 900.0,
            "height": 700.0,
            "minimized": False,
        }
        if self.n == self.fail:  # window never shows
            del self.windows[uuid]
        return type("P", (), {"pid": 1000 + self.n})()

    def place(self, uuid, rect):
        w = self.windows[uuid]
        w.update(
            x=float(rect["x"]),
            y=float(rect["y"]),
            width=float(rect["w"]),
            height=float(rect["h"]),
        )
        self.placed[uuid] = rect
        return True


def wire(monkeypatch, kw):
    monkeypatch.setattr(rp, "_spawn", kw.spawn)
    monkeypatch.setattr(rp, "kwin_candidates", lambda h: list(kw.windows))
    monkeypatch.setattr(
        rp,
        "kwin_info_by_uuid",
        lambda u: dict(kw.windows[u]) if u in kw.windows else None,
    )
    monkeypatch.setattr(rp, "kwin_place", kw.place)
    monkeypatch.setattr(
        rp, "viewer_command", lambda p, pg: ("okular", ["okular", "--page", str(pg), p])
    )
    monkeypatch.setattr(
        rp,
        "screens",
        lambda: [
            {"x": 0, "y": 0, "w": 1920, "h": 1080},
            {"x": 1920, "y": 0, "w": 1920, "h": 1080},
        ],
    )
    monkeypatch.setattr(rp.time, "sleep", lambda s: None)


def test_open_tiled_places_each_distinctly(monkeypatch):
    kw = FakeKWin()
    wire(monkeypatch, kw)
    rep = rp.open_tiled([("/a.pdf", 3), ("/b.pdf", 5), ("/c.pdf", 2), ("/d.pdf", 7)])
    assert all(r["opened"] and r["placed"] for r in rep)
    assert len({(r["rect"]["x"], r["rect"]["y"]) for r in rep}) == 4
    assert len(kw.placed) == 4


def test_open_tiled_reports_failure_honestly(monkeypatch):
    kw = FakeKWin(fail_uuid=2)
    wire(monkeypatch, kw)
    monkeypatch.setattr(
        rp,
        "_find_window",
        lambda proc, v, c, b, w: next(
            (dict(x) for u, x in kw.windows.items() if u not in c), None
        ),
    )
    rep = rp.open_tiled([("/a.pdf", 1), ("/b.pdf", 2)])
    assert rep[0]["opened"] and not rep[1]["opened"]
    assert rep[1]["error"]


def test_run_research_says_failure(monkeypatch):
    def fake_pipeline(topic, count):
        return {
            "count": count,
            "budget_s": 40.0,
            "budget_hit": False,
            "folder": "/x",
            "timings": {},
            "winners": [
                {
                    "title": "Alpha Report",
                    "domain": "a.org",
                    "page": 4,
                    "state": "opened",
                    "placed": True,
                    "error": "",
                },
                {
                    "title": "Beta Report",
                    "domain": "b.org",
                    "page": 9,
                    "state": "failed",
                    "placed": False,
                    "error": "window never appeared",
                },
            ],
            "skipped": [],
        }

    monkeypatch.setattr(rp, "run_pipeline", fake_pipeline)
    out = rp.run_research("topic x", 2)
    assert "Alpha Report (page 4, a.org)" in out["say"]
    assert "1 more downloaded but failed to open" in out["say"]
    monkeypatch.setattr(
        rp,
        "run_pipeline",
        lambda t, n: {
            "count": n,
            "budget_s": 40.0,
            "budget_hit": False,
            "folder": "/x",
            "timings": {},
            "winners": [],
            "skipped": [],
        },
    )
    assert "could not find" in rp.run_research("topic x", 2)["say"]


# --- reading layout / paper context -------------------------------------------

NAMED = [
    {"name": "HDMI-A-2", "x": 0, "y": 0, "w": 1920, "h": 1080, "priority": 1},
    {"name": "eDP-1", "x": 1920, "y": 0, "w": 1920, "h": 1080, "priority": 2},
    {"name": "DP-1", "x": 3840, "y": 0, "w": 1920, "h": 1080, "priority": 3},
]


def _names(scr, jarvis, claude, monkeypatch):
    monkeypatch.setattr(rp, "jarvis_screen_name", lambda s: jarvis)
    monkeypatch.setattr(rp, "claude_screen_name", lambda s: claude)
    return [s["name"] for s in rp.reading_screens(scr)]


def test_reading_screens_never_uses_jarvis_screen(monkeypatch):
    assert _names(NAMED, "DP-1", "HDMI-A-2", monkeypatch) == ["HDMI-A-2", "eDP-1"]
    assert "HDMI-A-2" not in _names(NAMED, "HDMI-A-2", None, monkeypatch)


def test_reading_screens_claude_screen_first(monkeypatch):
    assert _names(NAMED, "DP-1", "HDMI-A-2", monkeypatch)[0] == "HDMI-A-2"
    assert _names(NAMED, None, "DP-1", monkeypatch)[0] == "DP-1"


def test_reading_screens_claude_on_laptop_keeps_it_first(monkeypatch):
    assert _names(NAMED, "DP-1", "eDP-1", monkeypatch)[0] == "eDP-1"


def test_reading_screens_single_screen(monkeypatch):
    assert _names(NAMED[:1], None, None, monkeypatch) == ["HDMI-A-2"]


def test_reading_rects_bounds_and_no_overlap(monkeypatch):
    monkeypatch.setattr(rp, "jarvis_screen_name", lambda s: "DP-1")
    monkeypatch.setattr(rp, "claude_screen_name", lambda s: "HDMI-A-2")
    assert rp.reading_rects(0, NAMED) == []
    assert len(rp.reading_rects(1, NAMED)) == 1
    rects = rp.reading_rects(6, NAMED)
    assert len(rects) == 6
    for r in rects:
        assert r["w"] >= 480
        assert 0 <= r["x"] and r["x"] + r["w"] <= 3840 and r["y"] + r["h"] <= 1080
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            apart = (
                a["x"] + a["w"] <= b["x"]
                or b["x"] + b["w"] <= a["x"]
                or a["y"] + a["h"] <= b["y"]
                or b["y"] + b["h"] <= a["y"]
            )
            assert apart


def test_remember_and_read_papers(monkeypatch, tmp_path):
    monkeypatch.setattr(rp, "SESSION_PATH", tmp_path / "s.json")
    monkeypatch.setattr(rp, "paper_text", lambda path, *a, **k: "word " * 5000)
    wins = [{"state": "opened", "path": f"/p{i}.pdf", "title": f"T{i}", "page": 1}
            for i in range(3)]  # fmt: skip
    rows = rp.remember_papers("topic", wins)
    assert len(rows) == 3 and "text" not in rows[0]
    assert len(rows[0]["excerpt"]) < 2000 and "untrusted" in rows[0]["source"]
    out = rp.read_session_papers("all", 3000)
    assert sum(len(p["text"]) for p in out["papers"]) <= 3000
    assert rp.read_session_papers("2")["papers"][0]["n"] == 2
    assert "error" in rp.read_session_papers("zzz-none")


def test_find_page_explicit_number(monkeypatch):
    monkeypatch.setattr(rp, "_page_count", lambda p: 20)
    assert rp.find_page("/x.pdf", "go to page 12")["page"] == 12
    assert "error" in rp.find_page("/x.pdf", "page 99")


def test_find_page_prefers_contents_for_topic_list(monkeypatch):
    filler = "lorem ipsum dolor sit amet " * 12
    pages = [
        filler,
        filler + " Why choose this syllabus",
        "Contents "
        + filler
        + "\n1 Why choose ........ 2\n2 Syllabus overview ........ 4\n",
        filler + " topic",
    ]
    monkeypatch.setattr(rp, "_page_count", lambda p: 4)
    monkeypatch.setattr(rp, "_all_page_texts", lambda p: pages)
    assert rp.find_page("/x.pdf", "the topic list")["page"] == 3


def test_goto_paper_uses_viewer(monkeypatch, tmp_path):
    monkeypatch.setattr(rp, "SESSION_PATH", tmp_path / "s.json")
    rp._save_json(
        rp.SESSION_PATH, {"papers": [{"n": 1, "title": "Syllabus", "path": "/a.pdf"}]}
    )
    monkeypatch.setattr(
        rp,
        "find_page",
        lambda p, q: {"page": 3, "score": 5.0, "total": 9, "snippet": "c"},
    )
    seen = []
    monkeypatch.setattr(
        rp, "goto_viewer_page", lambda p, pg: (seen.append((p, pg)) or True, "jumped")
    )
    out = rp.goto_paper("", "topic list")
    assert out["ok"] and out["page"] == 3 and seen == [("/a.pdf", 3)]
    rp._save_json(rp.SESSION_PATH, {"papers": []})
    assert "error" in rp.goto_paper("", "x")
