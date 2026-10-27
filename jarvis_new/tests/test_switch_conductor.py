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


@pytest.mark.parametrize(("a", "b"), PAIRS)
def test_full_timeline_for_each_directed_pair(mk, a, b):
    w = mk()
    if a != "jarvis":
        w.go(a)
        assert w.rt.active_id() == a and w.rt.snapshot()["phase"] == "idle"
        w.calls.clear()
        w.cond.log.clear()
        w.t0 = w.now
    w.go(b)
    m = w.marks()
    for ev, want in [("exiting", 0), ("exit_say", 0), ("signature", 500), ("entering", 900),
                     ("greet_say", 1100), ("settling", 1100), ("complete", 1800)]:
        assert m[ev] == pytest.approx(want, abs=2), ev
    order = [e for e, _t, _m in w.cond.log]
    assert order == ["exiting", "exit_say", "ready", "signature", "entering", "greet_say", "settling", "complete"]
    s = w.says()
    assert [x[1]["character"] for x in s] == [a, b]
    assert s[0][1]["whoosh"] is False and s[1][1]["whoosh"] is True
    assert s[1][1]["interrupt"] is True
    assert w.rt.active_id() == b and w.rt.snapshot()["phase"] == "idle"


def test_interrupted_exit_line_skipped(mk):
    w = mk()
    w.go("ultron", interrupted=True)
    assert [x[1]["character"] for x in w.says()] == ["ultron"]
    assert "exit_say" not in w.marks()


def test_voice_off_typed_switch_is_silent(mk):
    w = mk(voice=False)
    res = w.go("ultron", source="text")
    assert res["reply"]
    assert w.calls == []
    assert w.rt.active_id() == "ultron"


def test_retarget_only_newest_greets(mk):
    w = mk()
    w.events.append((300, lambda: w.cond.start(w.rt.switch("ultron", "voice"))))
    w.go("friday")
    chars = [x[1]["character"] for x in w.says()]
    assert chars == ["jarvis", "ultron"]  # friday never exits/greets; one exit line only
    assert w.rt.active_id() == "ultron" and w.rt.snapshot()["phase"] == "idle"
    assert [t for e, t, _m in w.cond.log if e == "greet_say"] == [2]
    assert [t for e, t, _m in w.cond.log if e == "complete"] == [2]


def test_prewarm_failure_rolls_back_in_previous_voice(mk):
    w = mk(prewarm_ok=False)
    w.go("ultron")
    assert w.rt.active_id() == "jarvis" and w.rt.snapshot()["phase"] == "idle"
    s = w.says()
    assert s[-1][1]["character"] == "jarvis"
    assert "Remaining as Jarvis" in s[-1][1]["text"]
    assert all(x[1]["character"] != "ultron" for x in s)
    assert "rollback" in w.marks() and "greet_say" not in w.marks()


def test_buffered_input_replayed_to_new_character(mk):
    w = mk()
    got = []
    w.cond.set_replayer(lambda item: got.append((w.rt.active_id(), item)))
    done = []
    w.cond.on_complete(lambda ch: done.append(ch.id))
    w.events.append((300, lambda: w.cond.submit_input({"text": "what time is it"})))
    w.go("friday")
    assert got == [("friday", {"text": "what time is it"})]
    assert done == ["friday"]
    assert w.marks()["complete"] <= 500  # fast-forwarded
    assert "greet_say" not in w.marks()


def test_input_when_idle_not_buffered(mk):
    w = mk()
    assert w.cond.submit_input({"text": "x"}) is False
