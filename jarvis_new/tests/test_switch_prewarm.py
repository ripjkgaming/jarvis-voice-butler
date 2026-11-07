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
