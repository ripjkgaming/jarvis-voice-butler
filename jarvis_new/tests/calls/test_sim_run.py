# ruff: noqa: F811
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from test_sim_unit import FakeLLM, piper, scn  # noqa: F401

from calls import sim


async def test_full_run_writes_artifacts(tmp_path, piper):
    r = sim.SimRunner(
        out_root=tmp_path / "sim", run_id="r1", llm=FakeLLM(), piper=piper, voice="v"
    )
    rep = await r.run_many([scn()])
    res = rep["results"][0]
    assert res["status"] == "PASS", res
    d = tmp_path / "sim" / "r1" / "t1"
    assert (d / "turn-01.wav").exists() and (d / "turn-02.wav").exists()
    evs = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
    kinds = {e["event"] for e in evs}
    assert {"state", "transcript", "speaking", "summary"} <= kinds
    assert (tmp_path / "sim" / "r1" / "report.json").exists()
    assert "t1" in (tmp_path / "sim" / "r1" / "report.md").read_text()
    assert res["turns"][1]["llm_ms"] == 7 and res["turns"][1]["tts_ms"] is not None


async def test_fail_reported(tmp_path, piper):
    s = scn(expect={"must_contain": ["zebra"]})
    r = sim.SimRunner(
        out_root=tmp_path / "sim", run_id="r2", llm=FakeLLM(), piper=piper, voice="v"
    )
    rep = await r.run_many([s])
    assert rep["results"][0]["status"] == "FAIL" and rep["passed"] == 0


async def test_hud_appends_live_events(tmp_path, piper, monkeypatch):
    live = tmp_path / "livecalls"
    monkeypatch.setenv("JARVIS_CALLS_DIR", str(live))
    r = sim.SimRunner(
        out_root=tmp_path / "sim",
        run_id="r3",
        llm=FakeLLM(),
        piper=piper,
        voice="v",
        hud=True,
        pace=False,
    )
    await r.run_many([scn()])
    lines = (live / "events.jsonl").read_text().splitlines()
    assert any('"speaking"' in x for x in lines) and any(
        '"summary"' in x for x in lines
    )
    import os

    assert os.environ["JARVIS_CALLS_DIR"] == str(live)  # restored


async def test_missing_piper_degrades(tmp_path):
    r = sim.SimRunner(
        out_root=tmp_path / "sim",
        run_id="r4",
        llm=FakeLLM(),
        piper="/nonexistent",
        voice="v",
    )
    rep = await r.run_many([scn()])
    assert rep["results"][0]["tts_errors"]


def test_scenarios_load():
    sc = sim.load_scenarios()
    assert sc and all(v.expect is not None for v in sc.values())


async def test_scripted_scenario_with_calendar(tmp_path, piper):
    sc = sim.load_scenarios()["book_clash_alternatives"]
    r = sim.SimRunner(out_root=tmp_path / "sim", run_id="r5", piper=piper, voice="v", scripted=True)
    rep = await r.run_many([sc])
    assert rep["results"][0]["status"] == "PASS", rep["results"][0]["checks"]
