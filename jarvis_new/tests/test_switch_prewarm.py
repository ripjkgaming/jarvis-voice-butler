"""Switch conductor prewarm: wake-rpc hook, 1000 ms target vs 3000 ms timeout."""

import sys
import threading

import pytest

sys.path.insert(0, "src")

import character_runtime as cr
import switch_conductor as sc


class World:
    def __init__(self, tmp_path, monkeypatch, prewarm_ms=0, prewarm_ok=True, no_socket=False):
        monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
        cr.reset_runtime()
        sc.reset_conductor()
        self.rt = cr.get_runtime()
        self.now = 100.0
        self.t0 = self.now
        self.calls: list[tuple[int, dict]] = []
        self.prewarm_ms = prewarm_ms
        self.prewarm_ok = prewarm_ok
        self.no_socket = no_socket
        self.release = threading.Event()
        self.cond = sc.Conductor(
            self.rt,
            clock=lambda: self.now,
            sleep=self._sleep,
            wake_rpc=self._rpc,
            spawn=lambda fn: fn(),
            voice_on=lambda: True,
        ).install()

    def _sleep(self, dt):
        self.now += dt
        if (self.now - self.t0) * 1000 >= self.prewarm_ms:
            self.release.set()

    def _rpc(self, payload):
        self.calls.append((round((self.now - self.t0) * 1000), payload))
        if "character_prewarm" in payload:
            if self.no_socket:
                return None
            if self.prewarm_ms:
                assert self.release.wait(5)
            return {"ok": self.prewarm_ok, "ms": self.prewarm_ms}
        return {"ok": True}

    def go(self, target="ultron"):
        res = self.rt.switch(target, "voice")
        assert self.cond.start(res, source="voice")

    def marks(self):
        return {ev: ms for ev, _t, ms in self.cond.log}


@pytest.fixture
def mk(tmp_path, monkeypatch):
    yield lambda **kw: World(tmp_path, monkeypatch, **kw)
    cr.reset_runtime()
    sc.reset_conductor()


def test_hook_calls_wake_rpc_with_target(mk):
    w = mk()
    w.go("ultron")
    assert [p for _t, p in w.calls if "character_prewarm" in p] == [{"character_prewarm": "ultron"}]
    assert w.rt.active_id() == "ultron"


def test_missing_wake_socket_is_ok(mk):
    w = mk(no_socket=True)
    w.go("friday")
    assert w.rt.active_id() == "friday" and "rollback" not in w.marks()


def test_slow_prewarm_1500ms_no_rollback_enters_when_ready(mk):
    w = mk(prewarm_ms=1500)
    w.go("ultron")
    m = w.marks()
    assert "rollback" not in m
    assert w.rt.active_id() == "ultron" and w.rt.snapshot()["phase"] == "idle"
    assert 1450 <= m["ready"] <= 1700
    assert m["ready"] <= m["entering"] <= m["ready"] + 120
    assert m["entering"] > 1000  # past the target, still entered


def test_fast_prewarm_enters_at_in_chrome_start(mk):
    w = mk(prewarm_ms=300)
    w.go("ultron")
    m = w.marks()
    assert "ready" in m and m["ready"] >= 250  # fake clock outruns the real thread
    assert m["entering"] == pytest.approx(900, abs=100)


def test_prewarm_failure_rolls_back(mk):
    w = mk(prewarm_ok=False)
    w.go("ultron")
    assert w.rt.active_id() == "jarvis"
    assert "rollback" in w.marks() and "ready" not in w.marks()


def test_prewarm_3500ms_rolls_back_in_previous_voice(mk):
    w = mk(prewarm_ms=3500)
    w.go("ultron")
    m = w.marks()
    assert w.rt.active_id() == "jarvis"
    assert 2950 <= m["rollback"] <= 3250 and "entering" not in m
    w.release.set()
    says = [p["character_say"] for _t, p in w.calls if "character_say" in p]
    assert says[-1]["character"] == "jarvis"
    assert "Remaining as Jarvis" in says[-1]["text"]
    assert all(s["character"] != "ultron" for s in says)


def test_motion_validates_prewarm_timeout(tmp_path):
    import characters

    base = characters.motion()
    assert base["prewarm_timeout_ms"] == 3000 and base["ready_gate_ms"] == 1000
    bad = tmp_path / "m"
    bad.mkdir()
    import yaml

    (bad / "_motion.yaml").write_text(yaml.safe_dump({**base, "prewarm_timeout_ms": -1}))
    with pytest.raises(characters.CharacterError):
        characters.motion(bad)
