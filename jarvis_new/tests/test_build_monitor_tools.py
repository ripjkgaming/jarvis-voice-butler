import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import build_monitor as bm
import jobs

MODULE = Path(__file__).parents[1] / "src/system/build_monitor_tools.py"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("staged_build_tools", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_BUILD_TOOLS", "1")
    import phone_session
    import watch_mode

    monkeypatch.setattr(phone_session, "private", lambda: False)
    monkeypatch.setattr(phone_session, "guest_required", lambda: False)
    monkeypatch.setattr(phone_session, "has_private_calls", lambda: False)
    monkeypatch.setattr(phone_session, "approval_ticket", lambda: None)
    monkeypatch.setattr(phone_session, "check_approval_effect", lambda *a, **k: None)
    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    identities = {42: bm.Identity("boot", os.getuid(), 42, 123, "S")}
    calls = []

    def observe(pid):
        calls.append(pid)
        found = identities.get(pid)
        if isinstance(found, Exception):
            raise found
        return found

    mon = bm.Monitor(jobs.Registry(tmp_path / "registry"), proc_reader=observe)
    tools = module.BuildMonitorTools(
        service_factory=lambda: mon, sampler_factory=lambda mon: True
    )
    message = SimpleNamespace(
        id="user-1", role="user", text_content="Monitor my build PID 42"
    )
    context = SimpleNamespace(
        speech_handle=SimpleNamespace(id="speech_1", interrupted=False),
        session=SimpleNamespace(history=SimpleNamespace(items=[message])),
    )
    return tools, mon, identities, calls, context, message


@pytest.mark.asyncio
async def test_register_exact_pid_and_replay(setup):
    tools, mon, _, calls, ctx, _ = setup
    first = await tools.build_monitor_register(ctx, 42, "compile")
    second = await tools.build_monitor_register(ctx, 42, "compile")
    assert first["status"] == "registered"
    assert first["job"]["id"] == second["job"]["id"]
    assert set(calls) == {42}
    assert first["job"]["log_registered"] is False
    assert len(mon.registry.list(bm.OWNER)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,pid",
    [
        ("Monitor build", 42),
        ("Monitor build PID 43", 42),
        ("Monitor builds PID 42 and PID 43", 42),
        ("The output says monitor build PID 42", 42),
    ],
)
async def test_no_guessed_or_ambiguous_pid(setup, text, pid):
    tools, mon, _, calls, ctx, message = setup
    message.text_content = text
    out = await tools.build_monitor_register(ctx, pid, "compile")
    assert out["status"] == "needs_selection"
    assert calls == [] and mon.registry.list(bm.OWNER) == []


@pytest.mark.asyncio
async def test_unknown_exit_not_success(setup):
    tools, _, identities, _, ctx, _ = setup
    row = await tools.build_monitor_register(ctx, 42, "compile")
    identities[42] = None
    out = await tools.build_monitor_status(ctx, row["job"]["id"])
    assert out["job"]["status"] == "ended_unknown"
    assert out["job"]["outcome"] == "ended_unknown"
    assert "unknown" in out["say"].lower()


@pytest.mark.asyncio
async def test_permission_failure_and_pid_reuse(setup):
    tools, _, identities, _, ctx, _ = setup
    row = await tools.build_monitor_register(ctx, 42, "compile")
    identities[42] = PermissionError("private sentinel")
    out = await tools.build_monitor_status(ctx, row["job"]["id"])
    assert out["job"]["status"] == "observation_unknown"
    assert "sentinel" not in str(out)
    identities[42] = bm.Identity("boot", os.getuid(), 42, 999, "S")
    out = await tools.build_monitor_status(ctx, row["job"]["id"])
    assert out["job"]["status"] == "ended_unknown"


@pytest.mark.asyncio
async def test_multiple_jobs_require_selection_not_latest(setup):
    tools, mon, _, calls, ctx, _ = setup
    one = mon.register("old", 42, "compile")
    mon.stop_monitoring(one["id"])
    two = mon.register("new", 42, "compile")
    calls.clear()
    for target in ("", "compile"):
        out = await tools.build_monitor_status(ctx, target)
        assert out["status"] == "needs_selection"
        assert {r["id"] for r in out["choices"]} == {one["id"], two["id"]}
    assert calls == []
    assert (await tools.build_monitor_status(ctx, two["id"]))["job"]["id"] == two["id"]


@pytest.mark.asyncio
async def test_missing_job_and_empty_registry(setup):
    tools, _, _, calls, ctx, _ = setup
    assert (await tools.build_monitor_status(ctx))["status"] == "not_registered"
    assert (await tools.build_monitor_status(ctx, "job-" + "f" * 32))[
        "status"
    ] == "not_registered"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate", ["private", "guest", "active_private", "watch_guest", "disabled"]
)
async def test_authority_denial_prevents_observation(setup, monkeypatch, gate):
    tools, mon, _, calls, ctx, _ = setup
    import phone_session
    import watch_mode

    if gate == "disabled":
        monkeypatch.setenv("JARVIS_BUILD_TOOLS", "0")
    elif gate == "watch_guest":
        monkeypatch.setattr(watch_mode, "guest_required", lambda: True)
    else:
        monkeypatch.setattr(
            phone_session,
            {
                "private": "private",
                "guest": "guest_required",
                "active_private": "has_private_calls",
            }[gate],
            lambda: True,
        )
    assert (await tools.build_monitor_register(ctx, 42, "compile"))[
        "status"
    ] == "denied"
    assert (await tools.build_monitor_status(ctx))["status"] == "denied"
    assert calls == [] and mon.registry.list(bm.OWNER) == []


@pytest.mark.asyncio
async def test_sdk_registration_has_no_log_or_control_arguments(setup):
    tools, *_ = setup
    assert {t.id for t in tools.tools} == {
        "build_monitor_register",
        "build_monitor_status",
    }
    import inspect

    assert list(inspect.signature(tools.build_monitor_register).parameters) == [
        "context",
        "pid",
        "label",
        "window_name",
        "scope",
        "choice",
    ]


@pytest.mark.asyncio
async def test_interrupted_input_refuses_register(setup):
    tools, mon, _, calls, ctx, _ = setup
    ctx.speech_handle.interrupted = True
    assert (await tools.build_monitor_register(ctx, 42, "compile"))[
        "status"
    ] == "needs_selection"
    assert calls == [] and mon.registry.list(bm.OWNER) == []


@pytest.mark.asyncio
async def test_replayed_user_message_in_new_speech_cannot_reregister(setup):
    tools, mon, _, _, ctx, _ = setup
    first = await tools.build_monitor_register(ctx, 42, "compile")
    mon.stop_monitoring(first["job"]["id"])
    ctx.speech_handle.id = "speech_later"
    again = await tools.build_monitor_register(ctx, 42, "compile")
    assert again["job"]["id"] == first["job"]["id"]
    assert again["job"]["status"] == "monitoring_stopped"
    assert len(mon.registry.list(bm.OWNER)) == 1


@pytest.mark.asyncio
async def test_revocation_after_read_masks_receipt(setup, monkeypatch):
    tools, mon, _, _, ctx, _ = setup
    row = await tools.build_monitor_register(ctx, 42, "compile")
    original = mon.poll_once
    import phone_session

    def revoked():
        original()
        monkeypatch.setattr(phone_session, "guest_required", lambda: True)

    monkeypatch.setattr(mon, "poll_once", revoked)
    result = await tools.build_monitor_status(ctx, row["job"]["id"])
    assert result["status"] == "denied" and "job" not in result


@pytest.mark.asyncio
async def test_input_changed_before_worker_refuses_registration(setup, monkeypatch):
    tools, mon, _, calls, ctx, message = setup

    def factory():
        message.id = "different-turn"
        return mon

    tools._service_factory = factory
    assert (await tools.build_monitor_register(ctx, 42, "compile"))[
        "status"
    ] == "denied"
    assert calls == [] and mon.registry.list(bm.OWNER) == []


@pytest.mark.asyncio
async def test_tool_output_cannot_supply_pid_authority(setup):
    tools, mon, _, calls, ctx, message = setup
    message.role = "tool"
    assert (await tools.build_monitor_register(ctx, 42, "compile"))[
        "status"
    ] == "needs_selection"
    assert calls == [] and mon.registry.list(bm.OWNER) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pid,label", [(True, "build"), (0, "build"), (42, ""), (42, "x" * 81)]
)
async def test_invalid_parameters_never_observe(setup, pid, label):
    tools, mon, _, calls, ctx, _ = setup
    assert (await tools.build_monitor_register(ctx, pid, label))[
        "status"
    ] == "needs_selection"
    assert calls == [] and mon.registry.list(bm.OWNER) == []


def test_agent_keeps_new_tools_registered_and_always_available():
    import ast

    source = (Path(__file__).parents[1] / "src/agent.py").read_text()
    tree = ast.parse(source)
    never = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_never_demote"
    )
    names = {
        node.value
        for node in ast.walk(never)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert {"build_monitor_register", "build_monitor_status", "librarian_find"} <= names
    assert "self.build_monitor_tools = BuildMonitorTools()" in source
    assert "*self.build_monitor_tools.tools" in source


@pytest.mark.asyncio
async def test_old_speech_cannot_register_when_current_speech_changed(setup):
    tools, mon, _, calls, ctx, _ = setup
    ctx.session.current_speech = SimpleNamespace(id="new-speech")
    assert (await tools.build_monitor_register(ctx, 42, "compile"))[
        "status"
    ] == "needs_selection"
    assert calls == [] and mon.registry.list(bm.OWNER) == []


@pytest.mark.asyncio
async def test_actual_sdk_chat_messages_and_tool_schema(setup):
    tools, _, _, _, ctx, _ = setup
    from jarvis_rt import ChatContext, get_function_info, tool_schema

    ctx.session.history = ChatContext()
    ctx.session.history.add_message(role="user", content="Monitor my build PID 42")
    assert (await tools.build_monitor_register(ctx, 42, "compile"))[
        "status"
    ] == "registered"
    registered = next(t for t in tools.tools if t.id == "build_monitor_register")
    schema = tool_schema(registered)
    assert set(schema["parameters"]["properties"]) == {
        "pid",
        "label",
        "window_name",
        "scope",
        "choice",
    }
    assert get_function_info(registered).name == "build_monitor_register"


@pytest.mark.asyncio
async def test_installed_runcontext_registration_without_model(setup):
    tools, _, _, _, _, _ = setup
    from jarvis_rt import ChatContext, FunctionCall, RunContext, SpeechHandle

    speech = SpeechHandle.create()
    history = ChatContext()
    history.add_message(role="user", content="Monitor my build PID 42")
    session = SimpleNamespace(
        history=history, current_speech=speech, _global_run_state=None
    )
    context = RunContext(
        session=session,
        speech_handle=speech,
        function_call=FunctionCall(
            call_id="test-call", name="build_monitor_register", arguments="{}"
        ),
    )
    assert (await tools.build_monitor_register(context, 42, "compile"))[
        "status"
    ] == "registered"


@pytest.mark.asyncio
async def test_query_one_or_exact_label_never_reads_log(setup, monkeypatch):
    tools, mon, _, _, ctx, _ = setup
    row = await tools.build_monitor_register(ctx, 42, "compile")

    def no_logs(*a, **k):
        raise AssertionError("Voice status has no log authority")

    monkeypatch.setattr(mon, "tail", no_logs)
    for target in ("", "compile", row["job"]["id"]):
        out = await tools.build_monitor_status(ctx, target)
        assert out["job"]["id"] == row["job"]["id"]
        assert out["job"]["status"] == "running"


@pytest.mark.asyncio
async def test_cancel_before_registration_stops_worker_before_metadata_write(setup):
    import asyncio
    import threading

    tools, mon, _, calls, ctx, _ = setup
    entered = threading.Event()
    release = threading.Event()
    drained = threading.Event()

    def factory():
        entered.set()
        try:
            release.wait(1)
        finally:
            drained.set()
        return mon

    tools._service_factory = factory
    task = asyncio.create_task(tools.build_monitor_register(ctx, 42, "compile"))
    assert await asyncio.to_thread(entered.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert await asyncio.to_thread(drained.wait, 1)
    # Let the registry worker observe its cooperative cancellation check.
    await asyncio.to_thread(lambda: None)
    assert calls == [] and mon.registry.list(bm.OWNER) == []


@pytest.mark.asyncio
async def test_job_errors_do_not_echo_exception_content(setup, monkeypatch):
    tools, mon, _, _, ctx, _ = setup

    def refused(*a, **k):
        raise jobs.JobError("private-sentinel", 503)

    monkeypatch.setattr(mon, "register", refused)
    out = await tools.build_monitor_register(ctx, 42, "compile")
    assert out["status"] == "unavailable"
    assert "private-sentinel" not in str(out)


def named_setup(base):
    import window_monitor as wm

    tools, mon, identities, calls, ctx, message = base
    wid = "{11111111-1111-1111-1111-111111111111}"
    window = wm.Window(wid, "compile — VS Code", "code", 42, ":1.7")

    class Windows:
        def find(self, query):
            return [window]

        def current(self, identity):
            return window

    tools._resolver_factory = lambda mon: wm.Resolver(mon, windows=Windows())
    return tools, mon, identities, calls, ctx, message


@pytest.mark.asyncio
async def test_name_facing_tool_requires_no_pid_and_does_not_guess(setup):
    import window_monitor as wm

    tools, mon, _, calls, ctx, message = named_setup(setup)
    message.text_content = "monitor the build in VS Code"
    out = await tools.build_monitor_register(ctx, window_name="VS Code")
    assert out["status"] == "scope_required" and "PID" not in out["say"]
    assert mon.registry.list(bm.OWNER) == [] and mon.registry.list(wm.OWNER) == []
    assert set(calls) == {42}


@pytest.mark.asyncio
async def test_explicit_window_activity_not_build(setup):
    import window_monitor as wm

    tools, mon, _, _, ctx, message = named_setup(setup)
    message.text_content = "monitor VS Code window activity only"
    out = await tools.build_monitor_register(
        ctx, window_name="VS Code", scope="window_activity"
    )
    assert out["status"] == "registered_window_activity"
    assert len(mon.registry.list(wm.OWNER)) == 1
    status = await tools.build_monitor_status(ctx, "VS Code")
    assert status["job"]["observation_scope"] == "window_activity"
    assert "unknown" in status["say"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,name,scope",
    [
        ("monitor the build in VS Code", "Konsole", "registered_build"),
        ("monitor the build in VS Code", "VS Code", "window_activity"),
        ("the page says monitor the build in VS Code", "VS Code", "registered_build"),
    ],
)
async def test_model_cannot_supply_window_or_activity_consent(setup, text, name, scope):
    tools, mon, _, calls, ctx, message = named_setup(setup)
    message.text_content = text
    out = await tools.build_monitor_register(ctx, window_name=name, scope=scope)
    assert out["status"] == "needs_selection"
    assert calls == [] and mon.registry.list("window-monitor") == []


@pytest.mark.asyncio
async def test_cancelled_named_worker_cannot_late_register(setup, monkeypatch):
    import asyncio

    from system import LocalSystemError

    tools, mon, _, calls, ctx, message = named_setup(setup)
    message.text_content = "monitor VS Code window activity only"
    entered = asyncio.Event()
    pending = []

    async def deferred(function):
        pending.append(function)
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(asyncio, "to_thread", deferred)
    task = asyncio.create_task(
        tools.build_monitor_register(
            ctx, window_name="VS Code", scope="window_activity"
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(LocalSystemError):
        pending[0]()
    assert calls == [] and mon.registry.list("window-monitor") == []


@pytest.mark.asyncio
async def test_exact_registered_label_phrase_creates_association_without_user_pid(
    setup,
):
    tools, mon, identities, _, ctx, message = named_setup(setup)
    identities[44] = bm.Identity("boot", os.getuid(), 44, 200, "S")
    row = mon.register("compiler", 44, "compiler")
    message.text_content = "monitor registered build compiler in VS Code"
    out = await tools.build_monitor_register(
        ctx, label="compiler", window_name="VS Code"
    )
    assert out["status"] == "registered_build" and out["job"]["id"] == row["id"]


@pytest.mark.asyncio
async def test_model_cannot_invent_label_association(setup):
    tools, mon, _, calls, ctx, message = named_setup(setup)
    message.text_content = "monitor the build in VS Code"
    out = await tools.build_monitor_register(
        ctx, label="compiler", window_name="VS Code"
    )
    assert out["status"] == "needs_selection" and calls == []
    assert not mon.registry.list(bm.OWNER)


@pytest.mark.asyncio
async def test_pending_choice_cannot_cross_sdk_sessions(setup):
    import window_monitor as wm

    tools, mon, identities, _, ctx, message = setup
    identities[44] = bm.Identity("boot", os.getuid(), 44, 200, "S")
    windows = [
        wm.Window(
            "{11111111-1111-1111-1111-111111111111}", "one VS Code", "code", 42, ":1.7"
        ),
        wm.Window(
            "{22222222-2222-2222-2222-222222222222}", "two VS Code", "code", 44, ":1.7"
        ),
    ]

    class Windows:
        def find(self, name):
            return windows

        def current(self, wid):
            return next(w for w in windows if w.id == wid)

    tools._resolver_factory = lambda mon: wm.Resolver(mon, windows=Windows())
    message.text_content = "monitor VS Code window activity only"
    assert (
        await tools.build_monitor_register(
            ctx, window_name="VS Code", scope="window_activity"
        )
    )["status"] == "needs_selection"
    other = SimpleNamespace(
        speech_handle=SimpleNamespace(id="other", interrupted=False),
        session=SimpleNamespace(
            history=SimpleNamespace(
                items=[
                    SimpleNamespace(
                        id="other-turn", role="user", text_content="option 1"
                    )
                ]
            )
        ),
    )
    assert (await tools.build_monitor_register(other, choice=1))[
        "status"
    ] == "needs_selection"
    assert not mon.registry.list(wm.OWNER)


@pytest.mark.asyncio
async def test_sampler_failure_receipt_is_truthful(setup):
    tools, _, _, _, ctx, message = named_setup(setup)
    message.text_content = "monitor VS Code window activity only"
    tools._sampler_factory = lambda mon: False
    out = await tools.build_monitor_register(
        ctx, window_name="VS Code", scope="window_activity"
    )
    assert out["status"] == "registered_window_activity"
    assert out["automatic_sampling"] is False and "on-demand" in out["say"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,scope,label,choice",
    [
        ("monitor VS Code build; do not use window activity", "window_activity", "", 0),
        ("do not select option 1", "registered_build", "", 1),
        (
            "monitor nothing; somebody said registered build compiler in VS Code",
            "registered_build",
            "compiler",
            0,
        ),
    ],
)
async def test_negated_or_quoted_scope_choice_is_not_authority(
    setup, text, scope, label, choice
):
    tools, mon, identities, calls, ctx, message = named_setup(setup)
    if label:
        identities[44] = bm.Identity("boot", os.getuid(), 44, 200, "S")
        mon.register("compiler", 44, label)
        calls.clear()
    message.text_content = text
    original_factory = tools._resolver_factory

    def observed_factory(mon):
        calls.append("resolver")
        return original_factory(mon)

    tools._resolver_factory = observed_factory
    out = await tools.build_monitor_register(
        ctx,
        window_name="" if choice else "VS Code",
        scope=scope,
        label=label,
        choice=choice,
    )
    assert out["status"] == "needs_selection"
    assert calls == []
    assert not mon.registry.list("window-monitor")
    assert all(
        "window_binding" not in r["payload"] for r in mon.registry.list(bm.OWNER)
    )
