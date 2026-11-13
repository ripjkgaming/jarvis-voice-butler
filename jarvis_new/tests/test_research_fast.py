import json
import threading
import time

import pytest

import projects
import research_fast as rf

REPORT = (
    "## Summary\nRecycling recovers metals. Main firms exist.\n\n## Findings\n- a\n\n"
    "## Sources\n- https://a.example/x - A\n- https://b.example/y\n"
)


def plan_json(n=5):
    return json.dumps(
        {"subquestions": [{"q": f"q{i}", "queries": [f"s{i}"]} for i in range(n)]}
    )


def worker_json(tag="x"):
    return json.dumps(
        {
            "findings": [f"fact {tag} 42%"],
            "sources": [{"url": f"https://{tag}.example/p", "title": tag}],
            "confidence": "high",
        }
    )


def recs(text, tools=()):
    return [
        {"type": "system", "subtype": "init", "tools": list(tools), "mcp_servers": []},
        {"type": "result", "is_error": False, "result": text},
    ]


class Fake:
    def __init__(self, n=5, delay=0.0, plan=None, worker=None, writer=REPORT, slow=()):
        self.n, self.delay, self.plan, self.writer, self.slow = (
            n,
            delay,
            plan,
            writer,
            set(slow),
        )
        self.worker = worker
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, argv, task, timeout, tracker, on_record=None):
        model = argv[argv.index("--model") + 1]
        with self.lock:
            self.calls.append((model, task))
        if (
            model == rf.PLAN_MODEL
            and "--tools" in argv
            and argv[argv.index("--tools") + 1] == ""
        ):
            if self.plan is RuntimeError:
                raise RuntimeError("boom")
            return recs(self.plan if self.plan is not None else plan_json(self.n)), 0
        if model == rf.WRITER_MODEL:
            if on_record:
                on_record({"type": "stream_event"})
            return recs(self.writer), 0
        q = task.split("\n")[0]
        d = 5.0 if any(q.endswith(s) for s in self.slow) else self.delay
        end = time.monotonic() + d
        while time.monotonic() < end:
            if tracker.cancelled:
                raise TimeoutError
            time.sleep(0.02)
        if self.worker:
            return recs(self.worker(task), rf.WORKER_TOOLS), 0
        return recs(worker_json(q[-2:]), rf.WORKER_TOOLS), 0


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_RESEARCH_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(rf.shutil, "which", lambda n: "/bin/claude")
    monkeypatch.setattr(rf, "luna_enabled", lambda: False)


def install(monkeypatch, fake):
    monkeypatch.setattr(rf, "_exec", fake)
    return fake


class Prog:
    def __init__(self):
        self.ev = []

    def on_event(self, e):
        self.ev.append(e)


def test_plan_parse_and_fallback():
    subs = rf.parse_plan("```json\n" + plan_json(6) + "\n```", "t")
    assert len(subs) == 6 and subs[0]["queries"] == ["s0"]
    fb = rf.parse_plan("garbage", "lithium")
    assert len(fb) >= 4 and "lithium" in fb[0]["q"]
    assert len(rf.parse_plan(plan_json(3), "t")) >= 4  # too few -> fallback
    assert len(rf.parse_plan(plan_json(12), "t")) == 8


def test_plan_call_failure_falls_back(monkeypatch):
    install(monkeypatch, Fake(plan=RuntimeError))
    assert len(rf.plan("topic", rf.Tracker())) >= 4


def test_argv_isolation():
    a = rf.build_argv("claude", model="m", system="s", tools=rf.WORKER_TOOLS)
    assert a[a.index("--tools") + 1] == "WebSearch,WebFetch"
    assert a[a.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert "--restricted" in a and '{"mcpServers":{}}' in a
    b = rf.build_argv("claude", model="m", system="s")
    assert "--allowedTools" not in b and b[b.index("--tools") + 1] == ""


def test_parallel_overlap_report_and_progress(monkeypatch):
    install(monkeypatch, Fake(n=8, delay=0.5))
    p = Prog()
    t0 = time.monotonic()
    report, warn = rf.generate("topic", progress=p, workers=8)
    wall = time.monotonic() - t0
    assert wall < 2.0  # serial would be >= 4 s
    summary, sources = projects.parse_report(report)
    assert summary.startswith("Recycling") and "https://a.example/x" in sources
    pcts = [e["pct"] for e in p.ev if e["kind"] == "stage"]
    assert pcts == sorted(pcts) and pcts[0] == 5
    assert any(e["kind"] == "writing" for e in p.ev)
    assert any("8 of 8" in e["detail"] for e in p.ev if e["kind"] == "stage")


def test_straggler_cutoff(monkeypatch):
    install(monkeypatch, Fake(n=8, delay=0.05, slow=("q6", "q7")))
    t0 = time.monotonic()
    rf.generate("topic", workers=8, soft_s=0.5, hard_s=30)
    assert time.monotonic() - t0 < 3.0  # didn't wait the 5 s stragglers


def test_cache_hit_skips_workers(monkeypatch):
    fake = install(monkeypatch, Fake(n=5, delay=0.05))
    rf.generate("topic", workers=8)
    first = len([c for c in fake.calls if c[1].startswith("Sub-question")])
    assert first == 5
    rf.generate("topic", workers=8)
    assert len([c for c in fake.calls if c[1].startswith("Sub-question")]) == 5


def test_cache_ttl(monkeypatch):
    rf.cache_put("hello", {"findings": ["x"], "sources": []})
    assert rf.cache_get("hello")
    assert rf.cache_get("hello", now=time.time() + rf.CACHE_TTL_S + 10) is None


def test_cancel(monkeypatch):
    install(monkeypatch, Fake(n=5, delay=3))
    flag = {"c": False}
    threading.Timer(0.3, lambda: flag.update(c=True)).start()
    t0 = time.monotonic()
    with pytest.raises(rf.Cancelled):
        rf.generate("topic", is_cancelled=lambda: flag["c"], workers=8)
    assert time.monotonic() - t0 < 2.0


def _luna_ok(seen):
    def luna(task, timeout):
        seen.append(task)
        return json.dumps(
            {"findings": ["fact luna 99%"], "sources": [{"url": "https://l.example/z"}]}
        )

    return luna


def test_haiku_only_by_default_luna_unused(monkeypatch):
    install(monkeypatch, Fake(n=4, delay=0.05))
    monkeypatch.setattr(rf, "luna_enabled", lambda: True)
    seen = []
    monkeypatch.setattr(rf, "_luna_call", _luna_ok(seen))
    report, _ = rf.generate("topic", workers=8)
    assert "## Summary" in report and seen == []


def test_luna_fallback_when_haiku_fails(monkeypatch):
    def bad(task):
        raise RuntimeError("haiku down")

    install(monkeypatch, Fake(n=4, delay=0.0, worker=bad))
    monkeypatch.setattr(rf, "luna_enabled", lambda: True)
    seen = []
    monkeypatch.setattr(rf, "_luna_call", _luna_ok(seen))
    notes = {}
    orig = rf.synthesize

    def spy(topic, n, *a, **k):
        notes["n"] = n
        return orig(topic, n, *a, **k)

    monkeypatch.setattr(rf, "synthesize", spy)
    rf.generate("topic", workers=8)
    assert len(seen) == 4 and "https://l.example/z" in notes["n"]


def test_luna_fallback_off_degrades_quietly(monkeypatch):
    def bad(task):
        raise RuntimeError("haiku down")

    install(monkeypatch, Fake(n=4, worker=bad))
    monkeypatch.setattr(rf, "luna_enabled", lambda: False)
    with pytest.raises(Exception):
        rf.generate("topic", workers=8)  # nothing found -> caller falls back to legacy


def test_paired_mode_opt_in(monkeypatch):
    monkeypatch.setenv("JARVIS_RESEARCH_PAIRED", "1")
    install(monkeypatch, Fake(n=4, delay=0.05))
    monkeypatch.setattr(rf, "luna_enabled", lambda: True)
    seen = []
    monkeypatch.setattr(rf, "_luna_call", _luna_ok(seen))
    rf.generate("topic", workers=8)
    assert len(seen) == 4


def test_merge_dedupes():
    a = {
        "findings": ["Same 5%"],
        "sources": [{"url": "https://u", "title": ""}],
        "confidence": "high",
    }
    b = {
        "findings": ["same 5%", "other"],
        "sources": [{"url": "https://u", "title": ""}],
        "confidence": "low",
    }
    m = rf.merge({"haiku": a, "luna": b})
    assert (
        len(m["findings"]) == 2 and len(m["sources"]) == 1 and m["confidence"] == "low"
    )


def test_missing_summary_raises(monkeypatch):
    install(monkeypatch, Fake(writer="no structure"))
    with pytest.raises(RuntimeError):
        rf.generate("topic", workers=8)


def test_start_research_uses_fast_then_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(projects, "_announce", lambda m: None)
    monkeypatch.setattr(projects.ResearchProgress, "_notify", lambda self: None)
    install(monkeypatch, Fake(n=5, delay=0.05))
    meta = projects.start_research("fast topic", background=False)
    assert meta["status"] == "done" and meta["sources"] and meta["progress"] == 100

    # pipeline failure -> legacy path produces the report
    install(monkeypatch, Fake(writer="broken"))
    called = []

    def legacy(*a, **k):
        called.append(1)
        return REPORT, None

    monkeypatch.setattr(projects.chatgpt_cli, "chatgpt_stream", legacy)
    meta = projects.start_research("other topic", background=False)
    assert called and meta["status"] == "done"

    # kill switch
    called.clear()
    monkeypatch.setenv("JARVIS_RESEARCH_FAST", "0")
    meta = projects.start_research("third topic", background=False)
    assert called and meta["status"] == "done"
