"""School mode: shared state, commands, loud-action confirmation, follow-up."""

import asyncio
import time
import types

import pytest
from livekit.agents.llm import ToolError

import school


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    school._cache.update(path=None, mtime=None, mode=school.NORMAL)
    school._confirmed_at.update(at=0.0, used=None)
    school._asked_at.update(at=0.0)


def test_mode_roundtrip_and_default(tmp_path):
    assert school.current() == school.NORMAL
    school.set_mode("school")
    assert school.is_school()
    school.set_mode("normal")
    assert school.current() == school.NORMAL
    (tmp_path / "mode.json").write_text("{broken")
    assert school.current() == school.NORMAL


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("enter school mode", "school"), ("school mode", "school"),
        ("Can you turn on school mode please", "school"), ("school mode on", "school"),
        ("exit school mode", "normal"), ("turn off school mode", "normal"),
        ("school mode off", "normal"), ("back to normal mode", "normal"),
        ("what is school mode", None), ("my school mode of transport", None), ("", None),
    ],
)
def test_parse_command(text, want):
    assert school.parse_command(text) == want


@pytest.mark.parametrize(
    ("tool", "args", "loud"),
    [
        ("play_media", {"query": "x"}, True),
        ("set_volume", {"action": "up"}, True),
        ("set_volume", {"action": "set", "level": 70}, True),
        ("set_volume", {"action": "set", "level": 20}, False),
        ("set_volume", {"action": "down"}, False),
        ("open_app", {"app": "sober"}, True),
        ("open_app", {"app": "brave", "url": "https://www.youtube.com"}, True),
        ("open_app", {"app": "files"}, False),
        ("quote_action", {"action": "party"}, True),
        ("quote_action", {"action": "vitals"}, False),
        ("disk_space", {}, False),
    ],
)
def test_is_loud(tool, args, loud):
    assert school.is_loud(tool, args) is loud


def test_confirmation_covers_one_chain_then_expires():
    assert not school.loud_allowed(now=100.0)
    school.confirm_loud(now=100.0)
    assert school.loud_allowed(now=101.0)  # quote guard
    assert school.loud_allowed(now=102.0)  # play_media guard, same action
    assert not school.loud_allowed(now=101.0 + school.LOUD_CHAIN_S + 1)
    school.confirm_loud(now=200.0)
    assert not school.loud_allowed(now=200.0 + school.LOUD_CONFIRM_S + 1)


def test_loud_guard_only_in_school_mode():
    from system.school_tools import loud_guard

    loud_guard("play_media", {"query": "x"})  # normal: fine
    school.set_mode("school")
    with pytest.raises(ToolError):
        loud_guard("play_media", {"query": "x"})
    loud_guard("disk_space", {})
    school.confirm_loud()
    loud_guard("play_media", {"query": "x"})


def test_school_vad_is_stricter():
    import agent

    normal = agent.vad_kwargs()
    school.set_mode("school")
    strict = agent.vad_kwargs()
    assert strict["activation_threshold"] > normal["activation_threshold"]
    assert strict["min_speech_duration"] > normal["min_speech_duration"]
    # ...but a short "yes" still passes (0.25 s / 0.7 dropped it).
    assert strict["activation_threshold"] <= 0.6
    assert strict["min_speech_duration"] <= 0.15
    assert strict["min_silence_duration"] == normal["min_silence_duration"]


class _Session:
    def __init__(self):
        self.handlers = {}
        self.closed = False

    def on(self, name, fn):
        self.handlers[name] = fn

    async def aclose(self):
        self.closed = True

    def emit(self, name, state):
        self.handlers[name](types.SimpleNamespace(new_state=state))


async def test_follow_up_ends_call_after_window(monkeypatch):
    import agent

    now = {"t": 0.0}
    monkeypatch.setattr(school, "FOLLOW_UP_S", 8.0)
    s = _Session()
    task = asyncio.create_task(agent._school_follow_up(s, clock=lambda: now["t"], tick=0.001))
    await asyncio.sleep(0.01)
    s.emit("user_state_changed", "speaking")
    s.emit("agent_state_changed", "thinking")
    now["t"] = 30.0  # thinking holds the call open
    await asyncio.sleep(0.01)
    assert not s.closed
    s.emit("agent_state_changed", "speaking")
    s.emit("agent_state_changed", "listening")  # reply done at t=30
    now["t"] = 35.0
    s.emit("user_state_changed", "speaking")  # follow-up inside 8 s
    s.emit("user_state_changed", "listening")  # deadline 43
    now["t"] = 42.0
    await asyncio.sleep(0.01)
    assert not s.closed
    now["t"] = 44.0
    await asyncio.wait_for(task, 1.0)
    assert s.closed


async def test_follow_up_noise_cannot_hold_call_forever(monkeypatch):
    """VAD stuck on "speaking" (room noise) closes after the hold cap."""
    import agent

    now = {"t": 0.0}
    s = _Session()
    task = asyncio.create_task(agent._school_follow_up(s, clock=lambda: now["t"], tick=0.001))
    await asyncio.sleep(0.01)
    now["t"] = 1.0
    s.emit("user_state_changed", "speaking")  # noise, never ends
    now["t"] = agent.FOLLOW_UP_HOLD_CAP_S
    await asyncio.sleep(0.01)
    assert not s.closed
    now["t"] = 1.0 + agent.FOLLOW_UP_HOLD_CAP_S
    await asyncio.wait_for(task, 1.0)
    assert s.closed


async def test_follow_up_custom_window_for_normal_calls():
    import agent

    now = {"t": 0.0}
    s = _Session()
    task = asyncio.create_task(
        agent._school_follow_up(s, clock=lambda: now["t"], tick=0.001, first=15.0, window=15.0)
    )
    await asyncio.sleep(0.01)
    s.emit("agent_state_changed", "speaking")
    s.emit("agent_state_changed", "listening")  # deadline 15
    now["t"] = 14.0
    await asyncio.sleep(0.01)
    assert not s.closed
    now["t"] = 15.5
    await asyncio.wait_for(task, 1.0)
    assert s.closed


@pytest.mark.parametrize(
    ("text", "addressed", "question"),
    [
        ("Hey Jarvis, what is the capital of France?", True, "what is the capital of France?"),
        ("Jarvis, play music.", True, "play music."),
        ("hey jarvis what time is it", True, "what time is it"),
        ("Hey Jarvis.", True, ""),
        ("Okay class. Jarvis Cocker was a singer in the nineties.", False, None),
        ("what is the capital of France", False, None),
        ("", False, None),
        # Whisper's real mishearings of "hey Jarvis" (wake.log, 2026-09-29).
        ("Are you nervous?", True, ""),
        ("He's nervous. Some of the Iron Man suit.", True, "Some of the Iron Man suit."),
        ("Hey nervous, what time is it?", True, "what time is it?"),
        ("why are you so nervous about the exam", False, None),
        ("he is nervous", False, None),
        # Bare-name summons: whole transcript is only the name.
        ("Jarvis", True, ""),
        ("Jarvis.", True, ""),
        ("  jarvis ", True, ""),
        ("Jervis!", True, ""),
        ("Jarvis Cocker was a singer", False, None),
    ],
)
def test_address_gate_and_question(text, addressed, question):
    assert school.addressed(text) is addressed
    if question is not None:
        assert school.question_after_address(text) == question


# --- Meta (Super) key shortcut (school start menu) ---


def _meta_fake(current=""):
    """Fake subprocess.run-shaped runner with a fake kwinrc Meta value."""
    import types

    calls = []
    state = {"meta": current}

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[0] == "kreadconfig6":
            assert argv == ["kreadconfig6", "--file", "kwinrc",
                            "--group", "ModifierOnlyShortcuts",
                            "--key", "Meta"]
            return types.SimpleNamespace(returncode=0, stdout=state["meta"] + "\n",
                                         stderr="")
        if argv[0] == "kwriteconfig6":
            assert argv[1:6] == ["--file", "kwinrc", "--group",
                                "ModifierOnlyShortcuts", "--key"]
            if "--delete" in argv:
                state["meta"] = ""
            else:
                state["meta"] = argv[-1]
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[0] == "dbus-send":
            assert argv == ["dbus-send", "--session", "--type=method_call",
                            "--dest=org.kde.KWin", "/KWin",
                            "org.kde.KWin.reconfigure"]
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected argv {argv}")

    return run, calls, state


def test_meta_enter_saves_absent_backup_and_sets_jarvis(tmp_path):
    import json

    run, calls, state = _meta_fake("")
    assert school.meta_enter_school(run=run) is True
    backup = json.loads((tmp_path / "meta_shortcut_backup.json").read_text())
    assert backup == {"had_value": False, "value": ""}
    assert state["meta"] == school.META_JARVIS_VALUE
    assert any(c[0] == "kwriteconfig6" and c[-1] == school.META_JARVIS_VALUE
               for c in calls)
    assert any(c[0] == "dbus-send" for c in calls)


def test_meta_double_enter_keeps_original_backup(tmp_path):
    import json

    (tmp_path / "meta_shortcut_backup.json").write_text(
        json.dumps({"had_value": True, "value": "orig-value"}))
    run, calls, state = _meta_fake("whatever")
    assert school.meta_enter_school(run=run) is True
    backup = json.loads((tmp_path / "meta_shortcut_backup.json").read_text())
    assert backup == {"had_value": True, "value": "orig-value"}
    assert state["meta"] == school.META_JARVIS_VALUE


def test_meta_exit_restores_present_value(tmp_path):
    import json

    (tmp_path / "meta_shortcut_backup.json").write_text(
        json.dumps({"had_value": True, "value": "orig-value"}))
    run, calls, state = _meta_fake(school.META_JARVIS_VALUE)
    assert school.meta_exit_school(run=run) is True
    assert state["meta"] == "orig-value"
    assert not (tmp_path / "meta_shortcut_backup.json").exists()
    assert any(c[0] == "dbus-send" for c in calls)


def test_meta_exit_restores_absent_value(tmp_path):
    import json

    (tmp_path / "meta_shortcut_backup.json").write_text(
        json.dumps({"had_value": False, "value": ""}))
    run, calls, state = _meta_fake(school.META_JARVIS_VALUE)
    assert school.meta_exit_school(run=run) is True
    assert state["meta"] == ""
    assert any(c[0] == "kwriteconfig6" and "--delete" in c for c in calls)
    assert not (tmp_path / "meta_shortcut_backup.json").exists()


def test_meta_exit_without_backup_leaves_foreign_value():
    run, calls, state = _meta_fake("foreign-value")
    assert school.meta_exit_school(run=run) is True
    assert state["meta"] == "foreign-value"
    assert [c[0] for c in calls] == ["kreadconfig6"]  # read only, no change


def test_meta_exit_without_backup_clears_own_value():
    run, calls, state = _meta_fake(school.META_JARVIS_VALUE)
    assert school.meta_exit_school(run=run) is True
    assert state["meta"] == ""
    assert any(c[0] == "kwriteconfig6" and "--delete" in c for c in calls)


def test_meta_sync_on_startup(tmp_path):
    import json

    # School mode ensures the Jarvis value (backup records the original).
    run, _, state = _meta_fake("orig-value")
    school.set_mode("school")
    school.meta_sync_on_startup(run=run)
    assert state["meta"] == school.META_JARVIS_VALUE
    backup = json.loads((tmp_path / "meta_shortcut_backup.json").read_text())
    assert backup == {"had_value": True, "value": "orig-value"}
    # Crash recovery: normal mode + leftover backup restores it.
    school.set_mode("normal")
    run2, _, state2 = _meta_fake(school.META_JARVIS_VALUE)
    school.meta_sync_on_startup(run=run2)
    assert state2["meta"] == "orig-value"
    assert not (tmp_path / "meta_shortcut_backup.json").exists()
    # Normal mode with no backup touches nothing.
    run3, calls3, _ = _meta_fake("")
    school.meta_sync_on_startup(run=run3)
    assert [c[0] for c in calls3] == []


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("can you open school mode", "school"),
        ("open school mode", "school"),
        ("open the school mode please", "school"),
        ("turn school mode on", "school"),
        ("switch on school mode", "school"),
        ("launch school mode", "school"),
        ("enable school mode", "school"),
        ("school mode now", "school"),
        ("exit school mode", "normal"),
        ("what is school mode", None),
        ("open youtube", None),
    ],
)
def test_parse_command_voice_variants(text, want):
    assert school.parse_command(text) == want


def test_since_defaults_and_stored(tmp_path):
    assert school.since(home=tmp_path) == 0.0
    school.set_mode("school", home=tmp_path, now=123.0)
    school._cache.update(path=None)
    assert school.since(home=tmp_path) == 123.0


def test_flip_refusal_already(tmp_path):
    school.set_mode("school", home=tmp_path, now=1000.0)
    school._cache.update(path=None)
    assert school.flip_refusal("school", home=tmp_path, now=1005.0) == "already"


def test_flip_refusal_guards_recent_reversal(tmp_path):
    school.set_mode("school", home=tmp_path, now=1000.0)
    school._cache.update(path=None)
    refusal = school.flip_refusal("normal", home=tmp_path, now=1005.0)
    assert refusal is not None and "do NOT switch it back" in refusal


def test_flip_refusal_expires_after_guard(tmp_path):
    school.set_mode("school", home=tmp_path, now=1000.0)
    school._cache.update(path=None)
    assert (
        school.flip_refusal(
            "normal", home=tmp_path, now=1000.0 + school.FLIP_GUARD_S + 1
        )
        is None
    )


async def test_set_school_mode_already_skips_bridge(monkeypatch):
    from system import school_tools as school_tools_mod
    from system.school_tools import SchoolTools

    monkeypatch.setattr(school, "flip_refusal", lambda *a, **k: "already")

    def _fail(*a, **k):
        raise AssertionError("_bridge_call must not run on 'already'")

    monkeypatch.setattr(school_tools_mod, "_bridge_call", _fail)
    got = await SchoolTools.set_school_mode(SchoolTools(), None, on=True)  # type: ignore[arg-type]
    assert got == {"say": "School mode, Sir."}


async def test_set_school_mode_refusal_is_tool_error(monkeypatch):
    from system.school_tools import SchoolTools

    monkeypatch.setattr(school, "flip_refusal", lambda *a, **k: "nope, don't")
    with pytest.raises(ToolError):
        await SchoolTools.set_school_mode(SchoolTools(), None, on=True)  # type: ignore[arg-type]


async def test_set_school_mode_calls_bridge(monkeypatch):
    from system import school_tools as school_tools_mod
    from system.school_tools import SchoolTools

    monkeypatch.setattr(school, "flip_refusal", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(
        school_tools_mod,
        "_bridge_call",
        lambda *a, **k: (calls.append((a, k)), {"ok": True})[1],
    )
    got = await SchoolTools.set_school_mode(SchoolTools(), None, on=True)  # type: ignore[arg-type]
    assert got == {"say": "School mode, Sir."}
    assert calls and calls[0][0] == ("POST", "/mode", {"mode": "school"})


def test_switch_school_now_calls_bridge(monkeypatch):
    import agent
    import bridge

    calls = []
    monkeypatch.setattr(school, "current", lambda *a, **k: "normal")
    monkeypatch.setattr(bridge, "set_school_mode", lambda mode: calls.append(mode))
    agent._switch_school_now("school")
    assert calls == ["school"]


def test_switch_school_now_skips_when_already(monkeypatch):
    import agent
    import bridge

    calls = []
    monkeypatch.setattr(school, "current", lambda *a, **k: "school")
    monkeypatch.setattr(bridge, "set_school_mode", lambda mode: calls.append(mode))
    agent._switch_school_now("school")
    assert calls == []


def test_switch_school_now_swallows_errors(monkeypatch):
    import agent
    import bridge

    def _boom(mode):
        raise RuntimeError("bridge down")

    monkeypatch.setattr(school, "current", lambda *a, **k: "normal")
    monkeypatch.setattr(bridge, "set_school_mode", _boom)
    agent._switch_school_now("school")  # must not raise


async def test_follow_up_waits_longer_for_a_confirmation(monkeypatch):
    import agent

    now = {"t": 0.0}
    monkeypatch.setattr(school, "FOLLOW_UP_S", 8.0)
    monkeypatch.setattr(school, "_asked_at", {"at": 0.0})
    monkeypatch.setattr(school, "_confirmed_at", {"at": 0.0})
    s = _Session()
    task = asyncio.create_task(agent._school_follow_up(s, clock=lambda: now["t"], tick=0.001))
    await asyncio.sleep(0.01)
    s.emit("agent_state_changed", "speaking")
    school.mark_asked(now=time.monotonic())  # "Are you sure, Sir?"
    s.emit("agent_state_changed", "listening")  # deadline 0 + 15
    now["t"] = 12.0  # past the usual 8 s: still waiting for the answer
    await asyncio.sleep(0.01)
    assert not s.closed
    now["t"] = 16.0
    await asyncio.wait_for(task, 1.0)
    assert s.closed


def test_awaiting_answer_clears_once_confirmed():
    school._asked_at["at"] = 0.0
    school._confirmed_at["at"] = 0.0
    assert not school.awaiting_answer(now=5.0)
    school.mark_asked(now=10.0)
    assert school.awaiting_answer(now=12.0)
    school.confirm_loud(now=13.0)
    assert not school.awaiting_answer(now=14.0)
