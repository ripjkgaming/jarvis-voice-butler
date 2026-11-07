"""Always Aware end to end with the REAL P2 gate + offers and fakes for every
machine edge: fake frames -> sidecar tick -> gate (fake probes/verifier) ->
offer spoken through the injected speaker -> 'yes' through the voice route ->
accepted plan handed to the takeover runner. No real capture, no network."""

import types

import pytest
import test_aware_gate as gate_fixtures  # tests/ is on sys.path (prepend mode)
from test_aware_sidecar import OPP, T0, Fake, enable, reply

from aware import control, gate, offers, sidecar
from aware import log as alog
from aware import state as st


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    monkeypatch.delenv("JARVIS_AWARE", raising=False)
    # Hold-context probes that would read the real machine/clock.
    monkeypatch.setattr(
        offers,
        "live_context",
        lambda now: {
            "focus": False,
            "school": False,
            "dnd": False,
            "quiet_hours": False,
        },
    )
    import input_idle

    monkeypatch.setattr(input_idle, "idle_seconds", lambda *a, **k: 45.0)
    yield
    offers.set_speaker(None)
    offers.set_listener(None)
    gate._DEFAULTS.pop("probes", None)
    gate._DEFAULTS.pop("ask_model", None)


def _wire(tmp_path, answer_text):
    enable()
    f = Fake(tmp_path)
    f.answer = reply(
        opportunities=[{**OPP, "evidence": "pytest: 1 failed in tests/test_auth.py"}]
    )
    probes = gate_fixtures.FakeProbes(tmp_path)
    gate.configure(probes=probes, ask_model=gate_fixtures.auto_verifier())
    spoken, taken = [], []

    async def speak(text):
        spoken.append(text)

    async def listen(offer_id, text):
        # Sir answers by voice: the agent's fast route, same process here.
        hit = control.route(answer_text)
        assert hit is not None and hit[0] == "answer", hit
        spoken.append("ACK " + control.handle(*hit))

    d = f.deps()
    d.speak = speak
    d.listen = listen
    d.in_call = lambda: False
    d.modules = lambda name: {"gate": gate, "offers": offers}.get(name)
    d.run_takeover = lambda plan, offer_id: taken.append((plan, offer_id))
    return sidecar.Runner(d), spoken, taken


def test_yes_starts_takeover_with_gated_plan(tmp_path):
    runner, spoken, taken = _wire(tmp_path, "yes, go ahead")
    row = (runner.tick(), runner.tick())[1]
    opp_tag = row["opportunities"][0]
    assert opp_tag["gate"] == "pass", opp_tag
    assert opp_tag["offer"] == "accepted"
    assert spoken[0].endswith("?")  # the offer is a yes/no question
    assert spoken[1].startswith("ACK ")
    plan, offer_id = taken[0]
    assert plan.kind == "code" and plan.task_kind == "test_fix"
    assert plan.offer_text == spoken[0] and offer_id.startswith("of-")
    assert offers.pending() is None
    assert alog.recent(1, now=T0)[0]["opportunities"][0]["offer"] == "accepted"


def test_no_declines_and_nothing_runs(tmp_path):
    runner, _spoken, taken = _wire(tmp_path, "no, not now")
    row = (runner.tick(), runner.tick())[1]
    assert row["opportunities"][0]["offer"] == "declined"
    assert taken == []


def test_shadow_mode_logs_but_never_speaks(tmp_path):
    runner, spoken, taken = _wire(tmp_path, "yes")
    control.handle("shadow_on")
    row = (runner.tick(), runner.tick())[1]
    assert row["opportunities"][0]["gate"] == "pass"
    assert row["opportunities"][0]["offer"] == "suppressed"
    assert spoken == [] and taken == []


def test_muted_kind_is_not_offered(tmp_path):
    runner, spoken, taken = _wire(tmp_path, "yes")
    control.handle("mute", "test fixes")
    row = (runner.tick(), runner.tick())[1]
    assert row["opportunities"][0]["offer"] == "suppressed"
    assert spoken == [] and taken == []
    assert st.is_muted(st.load(), "test_fix")


def test_takeover_result_reported_back_to_offers(tmp_path, monkeypatch):
    from aware.types import TakeoverResult

    seen = []

    async def run(plan, *, progress):
        return TakeoverResult("r1", plan.id, "stuck", spoken="I couldn't finish, Sir.")

    monkeypatch.setattr(
        offers, "record_outcome", lambda oid, s, **k: seen.append((oid, s))
    )
    runner, _spoken, taken = _wire(tmp_path, "yes")
    (runner.tick(), runner.tick())[1]
    plan, offer_id = taken[0]
    said = []

    async def speak(text):
        said.append(text)

    mods = {"takeover": types.SimpleNamespace(run=run), "offers": offers}
    sidecar.run_takeover_blocking(plan, speak, offer_id, modules=mods.get)
    assert said == ["I couldn't finish, Sir."]
    assert seen == [(offer_id, "stuck")]


def test_real_takeover_file_run_verified_and_reported(tmp_path, monkeypatch):
    """Accepted file plan -> REAL takeover.run (journaled) -> spoken line ->
    offers.record_outcome. Reach limited to tmp_path."""
    from aware import fileedit, takeover
    from aware.types import TakeoverPlan

    jh = tmp_path / "jh"
    monkeypatch.setenv("JARVIS_HOME", str(jh))
    home = tmp_path / "home"
    monkeypatch.setattr(
        fileedit.Policy,
        "default",
        classmethod(
            lambda cls: fileedit.Policy(home=home, jarvis_home=jh, roots=(home,))
        ),
    )
    st.update(enabled=True)
    dl = home / "Downloads"
    dl.mkdir(parents=True)
    (dl / "a.pdf").write_text("a")
    plan = TakeoverPlan(
        id="fp1",
        opp_id="o",
        kind="file",
        task_kind="file_organise",
        title="Tidy downloads",
        steps=[],
        success_criteria=[],
        time_budget_s=300,
        targets={"paths": [str(dl / "a.pdf")], "dest": str(home / "PDFs")},
    )
    said, seen = [], []

    async def speak(text):
        said.append(text)

    monkeypatch.setattr(
        offers, "record_outcome", lambda oid, s, **k: seen.append((oid, s))
    )
    mods = {"takeover": takeover, "offers": offers}
    sidecar.run_takeover_blocking(plan, speak, "of-9", modules=mods.get)
    assert (home / "PDFs" / "a.pdf").read_text() == "a"
    assert seen == [("of-9", "verified")]
    assert said and said[0]
    assert takeover.current() is None
    assert st.load()["phase"] == "idle"
