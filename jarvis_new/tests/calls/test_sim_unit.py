import asyncio
import json
import stat
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from calls import sim

FAKE_PIPER = f"""#!{sys.executable}
import sys, wave, struct
out = sys.argv[sys.argv.index("-f") + 1]
sys.stdin.read()
w = wave.open(out, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
w.writeframes(b"".join(struct.pack("<h", 8000 if i % 40 < 20 else -8000) for i in range(22050 // 2)))
w.close()
"""


@pytest.fixture
def piper(tmp_path):
    p = tmp_path / "fakepiper"
    p.write_text(FAKE_PIPER)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


class FakeLLM:
    def __init__(self, reply="Thanks, I'll pass that on. END"):
        self.reply = reply
        self.records = []

    async def __call__(self, system, history):
        kind = "summary" if system.startswith("Summarise") else "reply"
        self.records.append(
            {"ms": 7, "model": "fake:free", "ok": True, "tried": [], "kind": kind}
        )
        return self.reply


def scn(**kw):
    base = dict(
        id="t1",
        title="t",
        setup={"number": "+15550100099", "rules": [{"kind": "answer_all"}]},
        caller_turns=["Hello, leaving a note for the owner."],
        expect={"events": ["summary"], "must_contain": ["thanks"]},
    )
    base.update(kw)
    return sim.Scenario(**base)


def test_wav_levels(piper, tmp_path):
    sim.synth("hi", tmp_path / "a.wav", piper, "voice")
    lv, dur = sim.wav_levels(tmp_path / "a.wav")
    assert 0.4 < dur < 0.6 and lv and max(lv) > 0.3


def test_evaluate_pass_fail():
    s = scn(
        expect={
            "must_contain": ["hi"],
            "must_not_contain": ["bank"],
            "escalation": True,
            "outcome": "completed",
            "booking": True,
        }
    )
    r = sim.evaluate(s, ["Hi there"], [{"event": "summary"}], "completed", None)
    st = {c["check"]: c["status"] for c in r}
    assert st["must_contain 'hi'"] == "PASS"
    assert st["escalation"] == "FAIL"
    assert st["booking"] == "SKIP"


def test_build_chain_qwen_last():
    c = sim.build_chain("x:free", ["a:free", sim.LAST_RESORT])
    assert (
        c[0] == "x:free" and c[-1] == sim.LAST_RESORT and c.count(sim.LAST_RESORT) == 1
    )


def test_to_chat_messages_alternates():
    m = sim.to_chat_messages([("jarvis", "hi"), ("caller", "a"), ("owner", "b")])
    assert [x["role"] for x in m] == ["user", "assistant", "user"]
    assert "Caller: a" in m[2]["content"]


def test_openrouter_fallback_on_429():
    calls = []

    class Resp:
        def __init__(self, b):
            self.b = b

        def read(self):
            return self.b

        def close(self):
            pass

    def opener(req, timeout):
        body = json.loads(req.data)
        calls.append(body["model"])
        if body["model"] == "a:free":
            raise urllib.error.HTTPError("u", 429, "rl", {}, None)
        return Resp(
            json.dumps(
                {"choices": [{"message": {"content": "<think>x</think>ok"}}]}
            ).encode()
        )

    llm = sim.OpenRouterLLM(["k"], ["a:free", "b:free"], opener=opener)
    out = asyncio.run(llm("sys", [("caller", "hi")]))
    assert out == "ok" and calls == ["a:free", "b:free"]
    assert llm.records[-1]["model"] == "b:free"


def test_openrouter_all_fail_raises():
    def opener(req, timeout):
        raise urllib.error.HTTPError("u", 500, "x", {}, None)

    llm = sim.OpenRouterLLM(["k"], ["a:free"], opener=opener)
    with pytest.raises(RuntimeError):
        asyncio.run(llm("s", [("caller", "x")]))


def test_key_lookup_never_needs_print(tmp_path, monkeypatch):
    (tmp_path / "keys.env").write_text("OPENROUTER_API_KEY_2=sk-test\n")
    keys = sim.openrouter_keys({"JARVIS_HOME": str(tmp_path)})
    assert "sk-test" in keys


def test_sim_call_ids_are_prefixed_in_live_feed(tmp_path):
    """Sim events must not collide with real call ids in the HUD feed."""
    import asyncio
    import json

    from calls import sim

    scn = sim.load_scenarios()["in_family_answer"]
    live = tmp_path / "events.jsonl"
    r = sim.SimRunner(out_root=tmp_path / "out", scripted=True, hud=True, pace=False)
    r.live_events = live
    r.piper = ""  # TTS fails soft: ids are what we test
    asyncio.run(r.run(scn))
    rows = [json.loads(x) for x in live.read_text().splitlines()]
    ids = {x["call_id"] for x in rows if x.get("call_id") is not None}
    assert ids and all(str(i).startswith("sim-") for i in ids)
