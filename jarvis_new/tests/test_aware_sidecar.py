"""Always Aware sidecar tick, end to end with fakes (no real capture, no network)."""

import json
import types

import numpy as np
import pytest

from aware import ledger, scheduler, sidecar, skip, spans, vision
from aware import log as alog
from aware import state as st
from aware.types import GateResult, OfferOutcome, TakeoverPlan

SCREENS = [
    {"name": "HDMI-A-2", "x": 0, "y": 0, "w": 640, "h": 360, "priority": 1},
    {"name": "eDP-1", "x": 640, "y": 0, "w": 640, "h": 360, "priority": 2},
    {"name": "DP-1", "x": 1280, "y": 0, "w": 640, "h": 360, "priority": 3},
]
WINS = [
    {
        "id": "w1",
        "app": "konsole",
        "title": "pytest",
        "x": 10,
        "y": 10,
        "w": 600,
        "h": 300,
        "minimized": False,
    },
    {
        "id": "w2",
        "app": "keepassxc",
        "title": "db",
        "x": 700,
        "y": 10,
        "w": 300,
        "h": 200,
        "minimized": False,
    },
]
T0 = 1_780_000_000.0


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    monkeypatch.delenv("JARVIS_AWARE", raising=False)
    return tmp_path


def reply(**kw):
    obs = {
        "summary": "Running pytest; auth tests fail",
        "activity": "debugging",
        "app": "konsole",
        "subject": "jarvis_new",
        "importance": 2,
        "sensitive": False,
        "signals": ["tests_failed"],
        "opportunities": [],
    }
    obs.update(kw)
    return {
        "obs": vision.parse(
            {"candidates": [{"content": {"parts": [{"text": json.dumps(obs)}]}}]}
        ),
        "model": "gemini-x",
        "tokens": 1400,
        "bytes_up": 20000,
        "latency_ms": 900,
    }


class Fake:
    def __init__(self, tmp_path, seed=0):
        self.tmp = tmp_path
        self.rng = np.random.default_rng(seed)
        self.now = T0
        self.asked = []
        self.answer = reply()
        self.shots = 0
        self.ctx_overrides = {}
        self.modules = {}
        self.taken = []
        self.static = None

    def frame(self):
        self.shots += 1
        if self.static is not None:
            return self.static.copy()
        return self.rng.integers(0, 255, (360, 1920, 3), dtype=np.uint8)

    def gather(self, outputs, hud, now, tab=None):
        ctx = skip.Context(state=st.load(), now=now, outputs=outputs, hud=hud)
        for k, v in self.ctx_overrides.items():
            setattr(ctx, k, v)
        return ctx

    def encode(self, workdir, key, out):
        frames = sorted(workdir.glob(f"{key}_*.jpg"))
        assert len(frames) == 6
        out.write_bytes(b"MP4" + key.encode())
        return out

    def ask(self, clips, ctx):
        self.asked.append((clips, ctx))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def workdir(self):
        d = self.tmp / f"work{len(self.asked)}_{self.shots}"
        d.mkdir(mode=0o700)
        return d

    def deps(self):
        return sidecar.Deps(
            now=lambda: self.now,
            signals=lambda: scheduler.Signals(),
            screens=lambda: SCREENS,
            hud=lambda screens: "DP-1",
            tabs=lambda: [],
            active=lambda: {"id": "w1", "app": "konsole", "title": "pytest"},
            gather=self.gather,
            windows=lambda: [dict(w) for w in WINS],
            shoot=self.frame,
            workdir=self.workdir,
            encode=self.encode,
            ask=self.ask,
            sleep=lambda s: None,
            speak=self._speak,
            modules=lambda name: self.modules.get(name),
            run_takeover=lambda plan, offer_id: self.taken.append(plan),
        )

    async def _speak(self, text):
        self.spoken = text


def enable():
    st.update(enabled=True)


def look_twice(runner):
    """Jarvis watches before offering: the first look only notes it."""
    first = runner.tick()
    assert first["opportunities"][0]["gate"] == "watching", first
    return runner.tick()


def test_disabled_tick_touches_nothing(tmp_path):
    f = Fake(tmp_path)
    row = sidecar.Runner(f.deps()).tick()
    assert row["reason"] == "disabled" and f.shots == 0
    assert not (tmp_path / "aware" / "log").exists()
    assert not (tmp_path / "aware" / "state.json").exists()


def test_env_kill_and_kill_file(tmp_path, monkeypatch):
    enable()
    f = Fake(tmp_path)
    monkeypatch.setenv("JARVIS_AWARE", "0")
    assert sidecar.Runner(f.deps()).tick()["reason"] == "disabled"
    monkeypatch.delenv("JARVIS_AWARE")
    st.kill_path().touch()
    assert sidecar.Runner(f.deps()).tick()["reason"] == "killed"
    assert f.shots == 0


def test_seen_tick_logs_spans_budget_and_never_sends_hud(tmp_path):
    enable()
    f = Fake(tmp_path)
    row = sidecar.Runner(f.deps()).tick()
    assert row["outcome"] == "seen", row
    assert row["summary"].startswith("Running pytest")
    assert set(row["monitors"]) == {"HDMI-A-2", "eDP-1"}  # never DP-1 (HUD)
    clips, ctx = f.asked[0]
    assert all(c["label"].split()[1] != "DP-1" for c in clips)
    assert any(c["active"] and "HDMI-A-2" in c["label"] for c in clips)
    # the keepass window is listed as hidden, never by title
    side = next(m for m in ctx["monitors"] if m["label"] == "eDP-1")
    assert side["windows"] == ["[hidden]"]
    assert ledger.today(T0) == {"calls": 1, "tokens": 1400}
    assert alog.recent(1, now=T0)[0]["tick_id"] == row["tick_id"]
    assert spans.around(T0)[0]["subject"] == "jarvis_new"
    # workdir wiped after the tick
    assert not list(tmp_path.rglob("*.jpg")) and not list(tmp_path.rglob("*.mp4"))
    audit = (tmp_path / "aware" / "audit.jsonl").read_text().splitlines()
    assert len(audit) == 2 and json.loads(audit[0])["event"] == "upload"


def test_unchanged_second_tick_skips_upload(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.static = np.full((360, 1920, 3), 90, np.uint8)
    f.static[:, ::7] = 200
    r = sidecar.Runner(f.deps())
    assert r.tick()["outcome"] == "seen"
    f.now += 200
    row = r.tick()
    assert row["outcome"] == "unchanged" and len(f.asked) == 1


def test_skip_rule_logs_and_takes_no_capture(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.ctx_overrides = {"idle": "idle"}
    row = sidecar.Runner(f.deps()).tick()
    assert row == {"ts": T0, "outcome": "skip", "reason": "idle"}
    assert f.shots == 0
    assert sidecar.status(T0)["state"] == "skipped"


def test_redaction_unavailable_fails_closed(tmp_path):
    enable()
    f = Fake(tmp_path)
    d = f.deps()
    d.windows = lambda: None
    row = sidecar.Runner(d).tick()
    assert row["reason"] == "redaction_unavailable" and f.shots == 0


def test_budget_active_only_and_stop(tmp_path, monkeypatch):
    enable()
    monkeypatch.setenv("JARVIS_AWARE_DAILY_CALLS", "10")
    for _ in range(7):
        ledger.record(1, now=T0)
    f = Fake(tmp_path)
    row = sidecar.Runner(f.deps()).tick()
    assert row["monitors"] == ["HDMI-A-2"]  # active monitor only
    for _ in range(3):
        ledger.record(1, now=T0)
    f.now += 300
    assert sidecar.Runner(f.deps()).tick()["reason"] == "budget"


def test_quota_backs_off_thirty_minutes(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.answer = vision.QuotaError("429")
    r = sidecar.Runner(f.deps())
    assert r.tick()["outcome"] == "error"
    f.now += 600
    assert r.tick()["reason"] == "backoff_quota"
    assert ledger.today(T0)["calls"] == 0


def test_hud_unknown_excludes_primary(tmp_path):
    enable()
    f = Fake(tmp_path)
    d = f.deps()
    d.hud = lambda screens: None
    row = sidecar.Runner(d).tick()
    assert row["hud_unknown"] and "HDMI-A-2" not in row["monitors"]


OPP = {
    "kind": "test_fix",
    "title": "Fix failing auth tests",
    "evidence": "pytest shows ImportError in session.py",
    "outcome": "all auth tests pass",
    "success_check": "pytest tests/test_auth.py passes",
    "est_minutes": 5,
    "self_feasibility": 0.9,
}


def _plan():
    return TakeoverPlan(
        id="p1",
        opp_id="o",
        kind="code",
        task_kind="test_fix",
        title="t",
        steps=[],
        success_criteria=["tests pass"],
        time_budget_s=300,
    )


def test_opportunity_goes_gate_offer_takeover(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.answer = reply(opportunities=[OPP])
    seen_opps = []

    async def evaluate(opp):
        seen_opps.append(opp)
        return GateResult(True, None, [], plan=_plan())

    async def maybe_offer(opp, gate):
        return OfferOutcome(status="accepted", offer_id="x")

    offers = types.SimpleNamespace(maybe_offer=maybe_offer, set_speaker=lambda fn: None)
    f.modules = {"gate": types.SimpleNamespace(evaluate=evaluate), "offers": offers}
    row = look_twice(sidecar.Runner(f.deps()))
    opp = seen_opps[0]
    assert opp.kind == "code" and opp.task_kind == "test_fix"
    assert opp.evidence["vision_kind"] == "test_fix"
    assert opp.evidence["subject"] == "jarvis_new"
    assert opp.evidence["context"]["guest"] is False
    assert row["opportunities"][0]["gate"] == "pass"
    assert row["opportunities"][0]["offer"] == "accepted"
    assert [p.id for p in f.taken] == ["p1"]


def test_gate_fail_is_logged_and_no_offer(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.answer = reply(opportunities=[OPP])
    offered = []

    async def evaluate(opp):
        return GateResult(False, "checklist", ["not_git_repo"])

    async def maybe_offer(opp, gate):
        offered.append(opp)

    f.modules = {
        "gate": types.SimpleNamespace(evaluate=evaluate),
        "offers": types.SimpleNamespace(
            maybe_offer=maybe_offer, set_speaker=lambda fn: None
        ),
    }
    row = look_twice(sidecar.Runner(f.deps()))
    assert row["opportunities"][0]["gate"] == "fail:checklist:not_git_repo"
    assert offered == [] and f.taken == []


def test_focus_mode_is_log_only_no_offer(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.answer = reply(opportunities=[OPP])
    f.ctx_overrides = {"focus_active": True}
    offered = []

    async def evaluate(opp):
        return GateResult(True, None, [], plan=_plan())

    async def maybe_offer(opp, gate):
        offered.append(opp)

    f.modules = {
        "gate": types.SimpleNamespace(evaluate=evaluate),
        "offers": types.SimpleNamespace(
            maybe_offer=maybe_offer, set_speaker=lambda fn: None
        ),
    }
    row = look_twice(sidecar.Runner(f.deps()))
    assert row["log_only"] == "focus_mode" and offered == []
    assert row["opportunities"][0]["offer"] == "held:log_only"


def test_missing_gate_module_logs_pending(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.answer = reply(opportunities=[OPP])
    row = look_twice(sidecar.Runner(f.deps()))
    assert row["opportunities"][0]["gate"] == "pending"


def test_sensitive_obs_never_reaches_gate(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.answer = reply(sensitive=True, opportunities=[OPP])
    called = []

    async def evaluate(opp):
        called.append(opp)

    f.modules = {"gate": types.SimpleNamespace(evaluate=evaluate)}
    row = sidecar.Runner(f.deps()).tick()
    assert called == [] and row["opportunities"] == [] and row["sensitive"]


def test_status_payload(tmp_path):
    assert sidecar.status(T0)["state"] == "off"
    enable()
    st.update(next_tick=T0 + 120)
    s = sidecar.status(T0)
    assert s["state"] == "armed" and s["next_tick_s"] == 120 and s["calls_today"] == 0
    st.update(phase="capturing", phase_until=T0 + 5)
    assert sidecar.status(T0)["state"] == "capturing"
    assert sidecar.status(T0 + 10)["state"] == "armed"
    st.update(paused_until=T0 + 3600)
    assert sidecar.status(T0)["state"] == "paused"


def test_active_monitor():
    assert sidecar.active_monitor(WINS, {"id": "w2"}, SCREENS) == "eDP-1"
    assert sidecar.active_monitor(WINS, {"title": "pytest"}, SCREENS) == "HDMI-A-2"
    assert sidecar.active_monitor(WINS, None, SCREENS) is None


def test_bridge_registers_aware_sidecar():
    import bridge

    assert ("aware.sidecar", "JARVIS_AWARE") in bridge.SIDECAR_THREADS


def test_offer_wires_listener_and_in_call(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.answer = reply(opportunities=[OPP])
    got = {}

    async def evaluate(opp):
        got["ctx"] = opp.evidence["context"]
        return GateResult(False, "checklist", ["x"])

    offers = types.SimpleNamespace(
        set_speaker=lambda fn: got.setdefault("speaker", fn),
        set_listener=lambda fn: got.setdefault("listener", fn),
        maybe_offer=None,
    )
    f.modules = {"gate": types.SimpleNamespace(evaluate=evaluate), "offers": offers}
    d = f.deps()
    d.in_call = lambda: True
    look_twice(sidecar.Runner(d))
    assert got["ctx"]["in_call"] is True and "listener" in got and "speaker" in got


def test_takeover_speaks_result_and_records_outcome():
    from aware.types import TakeoverResult

    spoken, outcomes = [], []

    async def run(plan, *, progress):
        progress(0.5, "paused: You've got it, Sir.")
        return TakeoverResult(
            "r1", plan.id, "verified", spoken="Done, Sir. 14 of 14 pass."
        )

    async def speak(text):
        spoken.append(text)

    mods = {
        "takeover": types.SimpleNamespace(run=run),
        "offers": types.SimpleNamespace(
            record_outcome=lambda oid, s: outcomes.append((oid, s))
        ),
    }
    sidecar.run_takeover_blocking(_plan(), speak, "of-1", modules=mods.get)
    assert spoken == ["You've got it, Sir.", "Done, Sir. 14 of 14 pass."]
    assert outcomes == [("of-1", "verified")]
    assert st.load()["phase"] == "idle"


def test_recover_calls_brave_live():
    async def rec():
        return ["run-9"]

    assert sidecar.recover({"brave_live": types.SimpleNamespace(recover=rec)}.get) == [
        "run-9"
    ]
    assert sidecar.recover({}.get) == []


def test_recover_runs_returns_honest_lines():
    from aware.types import TakeoverResult

    res = [TakeoverResult("r", "p", "failed", spoken="A run was interrupted, Sir.")]
    mods = {"takeover": types.SimpleNamespace(recover=lambda: res)}
    assert sidecar.recover_runs(mods.get) == ["A run was interrupted, Sir."]
    assert sidecar.recover_runs({}.get) == []


# ------------------------------------------- watch first, worksheet help, park
SHEET = {
    "kind": "worksheet_help",
    "title": "Question 4, the fractions one",
    "evidence": "Q4: 3/4 + 1/6 = ? answer cell blank",
    "outcome": "Find a common denominator first; twelve works for both",
    "success_check": "Sir fills in question 4",
}


def test_still_screen_is_looked_at_again_while_watching(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.static = np.zeros((360, 1920, 3), dtype=np.uint8)
    f.answer = reply(opportunities=[SHEET], activity="writing")
    r = sidecar.Runner(f.deps())
    first = r.tick()
    assert first["opportunities"][0]["gate"] == "watching" and r.sched.study
    f.now += scheduler.STUDY_S
    second = r.tick()
    assert second["outcome"] == "seen" and len(f.asked) == 2
    assert "history" in f.asked[1][1] and f.asked[1][1]["history"]


def test_worksheet_help_skips_gate_and_goes_to_assist(tmp_path):
    enable()
    f = Fake(tmp_path)
    f.answer = reply(opportunities=[SHEET], activity="writing")
    gated, assisted = [], []

    async def evaluate(opp):
        gated.append(opp)

    async def maybe_assist(opp):
        assisted.append(opp)
        return OfferOutcome(status="accepted", offer_id="x")

    from aware import offers as real_offers

    f.modules = {
        "gate": types.SimpleNamespace(evaluate=evaluate),
        "offers": types.SimpleNamespace(
            is_assist=real_offers.is_assist,
            maybe_assist=maybe_assist,
            set_speaker=lambda fn: None,
        ),
    }
    row = look_twice(sidecar.Runner(f.deps()))
    assert gated == [] and assisted[0].task_kind == "worksheet_help"
    assert row["opportunities"][0]["gate"] == "assist"
    assert row["opportunities"][0]["offer"] == "accepted"
    assert f.taken == []


def test_park_waits_for_presence_then_resumes(tmp_path, monkeypatch):
    enable()
    f = Fake(tmp_path)
    states = iter(["idle", "idle", "active"])
    d = f.deps()
    d.presence = lambda now: next(states)
    r = sidecar.Runner(d)
    r.sched.base, r.sched.quiet_streak = scheduler.MAX_S, 3
    monkeypatch.setattr(scheduler, "PARK_POLL_S", 0.001)
    import threading

    assert r.park(threading.Event()) is True
    assert r.sched.base == scheduler.BASE_S and r.sched.quiet_streak == 0


def test_park_returns_false_when_stopping(tmp_path, monkeypatch):
    enable()
    f = Fake(tmp_path)
    d = f.deps()
    d.presence = lambda now: "idle"
    monkeypatch.setattr(scheduler, "PARK_POLL_S", 0.001)
    import threading

    stop = threading.Event()
    stop.set()
    assert sidecar.Runner(d).park(stop) is False
