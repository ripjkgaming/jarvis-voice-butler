"""Switch conductor: timeline, retarget, rollback, buffering. Fake clock and RPC."""

import itertools
import sys

import pytest

sys.path.insert(0, "src")

import character_runtime as cr
import switch_conductor as sc


class World:
    def __init__(self, tmp_path, monkeypatch, voice=True, prewarm_ok=True):
        monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
        cr.reset_runtime()
        sc.reset_conductor()
        self.rt = cr.get_runtime()
        self.now = 100.0
        self.calls: list[tuple[float, dict]] = []
        self.events: list[tuple[float, object]] = []
        self.rt.register_prewarm(lambda c: prewarm_ok)
        self.voice = voice
        self.cond = sc.Conductor(
            self.rt,
            clock=lambda: self.now,
            sleep=self._sleep,
            wake_rpc=self._rpc,
            spawn=lambda fn: fn(),
            voice_on=lambda: self.voice,
        )
        self.t0 = self.now

    def _sleep(self, dt):
        self.now += dt
        for ev in list(self.events):
            if (self.now - self.t0) * 1000 >= ev[0]:
                self.events.remove(ev)
                ev[1]()

    def _rpc(self, payload):
        self.calls.append((round((self.now - self.t0) * 1000), payload))
        return {"ok": True, "ms": 400}

    def go(self, target, source="voice", **kw):
        res = self.rt.switch(target, source)
        assert self.cond.start(res, source=source, **kw)
        return res

    def marks(self):
        return {ev: ms for ev, _t, ms in self.cond.log}

    def says(self):
        return [(t, p["character_say"]) for t, p in self.calls if "character_say" in p]


@pytest.fixture
def mk(tmp_path, monkeypatch):
    yield lambda **kw: World(tmp_path, monkeypatch, **kw)
    cr.reset_runtime()
    sc.reset_conductor()


PAIRS = list(itertools.permutations(["jarvis", "friday", "ultron"], 2))














def test_input_when_idle_not_buffered(mk):
    w = mk()
    assert w.cond.submit_input({"text": "x"}) is False
