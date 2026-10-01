import asyncio
import json

import pytest

import goodnight as g


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_GOODNIGHT", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    "text",
    [
        "Goodnight",
        "good night Jarvis",
        "Jarvis, I'm going to sleep",
        "I'm going to bed now",
        "okay goodnight sir",
        "time for bed",
    ],
)
def test_goodnight_phrases(text):
    assert g.is_goodnight(text)


@pytest.mark.parametrize(
    "text",
    [
        "I can't sleep",
        "what's a good night's sleep",
        "turn the volume down",
        "goodnight everyone",
        "I'm going to sleep in the car after I finish this report",
        "",
    ],
)
def test_not_goodnight(text):
    assert not g.is_goodnight(text)


def test_decide_requires_five_quiet_minutes_and_no_coding():
    assert g.decide(100, 400, None) == "wait"  # idle probe says idle, but too soon
    assert g.decide(400, 100, None) == "wait"  # touched the keyboard recently
    assert g.decide(400, 400, None) == "lock"
    assert g.decide(400, 400, True) == "hold"
    assert g.decide(400, None, None) == "lock"  # probe unavailable -> time only
    assert g.decide(200, None, None) == "wait"


def test_arm_and_cancel(monkeypatch):
    spawned = []
    token = g.arm(spawn=lambda *a, **k: spawned.append(a))
    assert g.pending() and spawned
    assert json.loads(g.state_path().read_text())["token"] == token
    assert g.cancel() is True and not g.pending()
    assert g.cancel() is False


def _fake_proc(root, pid, ppid, comm, cmd, cpu=0):
    d = root / str(pid)
    d.mkdir()
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_bytes(b"\0".join(c.encode() for c in cmd) + b"\0")
    fields = ["S", str(ppid)] + ["0"] * 9 + [str(cpu), "0"] + ["0"] * 10
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields))


def test_no_agent_process_means_not_coding(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    _fake_proc(root, 10, 1, "bash", ["bash"])
    assert g.coding_active(root, tmp_path, sleep=lambda s: None) is False


def test_idle_agent_is_not_coding_but_busy_one_is(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    _fake_proc(root, 20, 1, "claude", ["claude"], cpu=100)
    assert g.coding_active(root, tmp_path, sleep=lambda s: None) is False

    # CPU grows between the two samples -> working
    def grow(_):
        _fake_proc_update(root, 20, 5000)

    assert g.coding_active(root, tmp_path, sleep=grow) is True


def _fake_proc_update(root, pid, cpu):
    stat = (root / str(pid) / "stat").read_text().split()
    stat[13] = str(cpu)
    (root / str(pid) / "stat").write_text(" ".join(stat))


def test_recent_transcript_write_counts_as_coding(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    _fake_proc(root, 30, 1, "node", ["node", "/usr/lib/claude"])
    proj = tmp_path / ".claude" / "projects" / "p"
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text("{}")
    assert g.coding_active(root, tmp_path, sleep=lambda s: None) is True


def test_electron_desktop_app_is_ignored(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    _fake_proc(root, 40, 1, "claude", ["claude", "--type=renderer"])
    assert g.coding_active(root, tmp_path, sleep=lambda s: None) is False


def test_watch_locks_once_quiet_and_idle(monkeypatch):
    import input_idle

    token = g.arm(now=1000.0, spawn=lambda *a, **k: None)
    clock = [1000.0]
    monkeypatch.setattr(input_idle, "ensure_helper", lambda *a, **k: True)
    monkeypatch.setattr(input_idle, "idle_seconds", lambda *a, **k: 1000.0)
    monkeypatch.setattr(g, "coding_active", lambda *a, **k: False)
    locked = []
    monkeypatch.setattr(g, "_lock", lambda: locked.append(1) or True)

    def sleep(s):
        clock[0] += s

    assert g.watch(token, now=lambda: clock[0], sleep=sleep) == "locked"
    assert locked == [1] and not g.pending()
    assert clock[0] - 1000.0 >= g.IDLE_S


def test_watch_holds_while_coding_then_locks(monkeypatch):
    import input_idle

    token = g.arm(now=0.0, spawn=lambda *a, **k: None)
    clock = [0.0]
    monkeypatch.setattr(input_idle, "ensure_helper", lambda *a, **k: True)
    monkeypatch.setattr(input_idle, "idle_seconds", lambda *a, **k: 9999.0)
    answers = iter([True, True, False])
    monkeypatch.setattr(g, "coding_active", lambda *a, **k: next(answers))
    monkeypatch.setattr(g, "_lock", lambda: True)

    def sleep(s):
        clock[0] += s

    assert g.watch(token, now=lambda: clock[0], sleep=sleep) == "locked"


def test_watch_stops_when_cancelled(monkeypatch):
    import input_idle

    token = g.arm(now=0.0, spawn=lambda *a, **k: None)
    monkeypatch.setattr(input_idle, "ensure_helper", lambda *a, **k: True)
    monkeypatch.setattr(input_idle, "idle_seconds", lambda *a, **k: 0.0)
    locked = []
    monkeypatch.setattr(g, "_lock", lambda: locked.append(1) or True)
    calls = [0]

    def sleep(s):
        calls[0] += 1
        if calls[0] == 3:
            g.cancel()

    assert g.watch(token, now=lambda: 0.0 + calls[0] * 15, sleep=sleep) == "cancelled"
    assert not locked


def test_assistant_goodnight_says_farewell_and_arms_but_other_speech_cancels(
    monkeypatch,
):
    from livekit.agents import inference

    from agent import Assistant

    for k in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.setenv(k, "x" * 32)
    monkeypatch.setenv("LIVEKIT_URL", "ws://localhost:7880")
    a = Assistant(llm=inference.LLM(model="openai/gpt-4.1-mini"))
    said = []

    class S:
        def say(self, text, **k):
            said.append(text)

    monkeypatch.setattr(type(a), "session", property(lambda self: S()), raising=False)
    assert asyncio.run(a._goodnight("goodnight jarvis")) is True
    assert said and "Goodnight" in said[0] and g.pending()
    assert asyncio.run(a._goodnight("what's the weather")) is False
    assert not g.pending()
