"""Only fake input origins and temporary draft state; no actual effects."""

import asyncio
import contextvars
from types import SimpleNamespace

import pytest
import wa_approvals
from jarvis_rt import ToolError

import phone_session as ps
from system import LocalSystemError, desktop, whatsapp
from system.core import SystemTools
from system.muse_tools import MuseTools


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    scope = ps._Owner("owner-room", active=True)
    token = ps._OWNER.set(scope)
    try:
        yield scope
    finally:
        ps._OWNER.reset(token)


@pytest.mark.asyncio
async def test_private_legacy_whatsapp_and_muse_never_reach_sender(owner, monkeypatch):
    async def forbidden(*a, **k):
        pytest.fail("unapproved WhatsApp origin reached")

    monkeypatch.setattr(whatsapp, "_send_chat_unlocked", forbidden)
    assert not (await whatsapp.send_chat("Group", "message"))["ok"]
    tools = MuseTools(wa_sender=forbidden)
    tools._pending = ("whatsapp", "brief")
    with pytest.raises(ToolError, match="exact reviewed"):
        await MuseTools.confirm_muse_send(tools, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ["desktop_click", "desktop_type", "desktop_key", "run_command"]
)
async def test_generic_effects_refuse_while_review_active(owner, action, monkeypatch):
    owner.approval_gate = SimpleNamespace(transaction_active=True)

    async def forbidden(*a, **k):
        pytest.fail("generic effect origin reached")

    monkeypatch.setattr(desktop, "_desktop_size", forbidden)
    tools = SystemTools() if action == "run_command" else desktop.DesktopTools()
    tools._confirmed = "Send"
    tools._confirmed_command = "true"
    args = {
        "desktop_click": (10, 10),
        "desktop_type": ("text",),
        "desktop_key": ("Return",),
        "run_command": ("true",),
    }[action]
    with pytest.raises(ToolError, match="active WhatsApp"):
        await getattr(type(tools), action)(tools, None, *args)


@pytest.mark.asyncio
async def test_queued_desktop_action_before_review_epoch_cannot_replay_after_cancel(
    owner, monkeypatch
):
    started = asyncio.Event()
    resume = asyncio.Event()
    effects = []

    async def size():
        started.set()
        await resume.wait()
        return 100, 100

    class Input:
        def tap(self, code):
            effects.append(code)

        def close(self):
            pass

    monkeypatch.setattr(desktop, "_desktop_size", size)
    monkeypatch.setattr(desktop, "_make_input", lambda *a: Input())
    tools = desktop.DesktopTools()
    tools._confirmed = "Return"
    task = asyncio.create_task(desktop.DesktopTools.desktop_key(tools, None, "Return"))
    await started.wait()
    owner.approval_epoch += 1
    owner.approval_gate = SimpleNamespace(transaction_active=False)
    resume.set()
    with pytest.raises(ToolError, match="fresh action"):
        await task
    assert not effects
    tools._confirmed = "Return"
    assert (await desktop.DesktopTools.desktop_key(tools, None, "Return"))[
        "key"
    ] == "Return"
    assert effects == [28]


def test_copied_worker_context_rechecks_epoch_at_physical_effect(owner, monkeypatch):
    ticket = ps.approval_ticket()
    token = ps._APPROVAL_EFFECT.set(ticket)
    copied = contextvars.copy_context()
    ps._APPROVAL_EFFECT.reset(token)
    owner.approval_epoch += 1
    effects = []
    monkeypatch.setattr(desktop.os, "write", lambda *a: effects.append(a))
    device = object.__new__(desktop.UInputMouse)
    device._fd = 123
    with pytest.raises(LocalSystemError, match="fresh action"):
        copied.run(device._emit, desktop._EV_KEY, 28, 1)
    assert not effects
    # Releasing a key already down remains allowed even during cancellation.
    copied.run(device._emit, desktop._EV_KEY, 28, 0)
    assert len(effects) == 1


@pytest.mark.asyncio
async def test_subprocess_origin_rechecks_ticket_after_awaited_preparation(
    owner, monkeypatch
):
    import system

    effects = []

    async def forbidden(*a, **k):
        effects.append(a)
        pytest.fail("stale subprocess created")

    monkeypatch.setattr(system.asyncio, "create_subprocess_exec", forbidden)
    token = ps._APPROVAL_EFFECT.set(ps.approval_ticket())
    owner.approval_epoch += 1
    try:
        with pytest.raises(LocalSystemError, match="fresh action"):
            await system.run_cmd("true")
    finally:
        ps._APPROVAL_EFFECT.reset(token)
    assert not effects


@pytest.mark.asyncio
async def test_real_gate_blocks_parallel_tools_then_fresh_actions_after_cancel(
    owner, tmp_path, monkeypatch
):
    class Session:
        def on(self, *a):
            pass

    gate = wa_approvals.SpokenApproval(Session(), owner.identity, ps.identity)
    ps.bind_approval_gate(gate)
    gate._pending = ("fake",)
    tools = desktop.DesktopTools()
    tools._confirmed = "Return"
    with pytest.raises(ToolError, match="active WhatsApp"):
        await desktop.DesktopTools.desktop_key(tools, None, "Return")
    gate.invalidate()
    assert not gate.transaction_active
    ps.check_approval_effect(ps.approval_ticket())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,args",
    [
        ("click", ("Send",)),
        ("type_text", ("Message", "hello")),
        ("press_key", ("Enter",)),
    ],
)
async def test_browser_tool_routes_block_during_review(owner, action, args):
    from tools import BrowserTools

    async def forbidden(*a, **k):
        pytest.fail("browser origin reached during review")

    owner.approval_gate = SimpleNamespace(transaction_active=True)
    browser = SimpleNamespace(
        **{name: forbidden for name in ("click", "type_text", "press_key")}
    )
    tools = BrowserTools(browser)
    tools._confirmed_target = "send"
    with pytest.raises(ToolError, match="active WhatsApp"):
        await getattr(BrowserTools, action)(tools, None, *args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,args",
    [
        ("click", ("Send",)),
        ("type_text", ("Message", "hello")),
        ("press_key", ("Enter",)),
    ],
)
async def test_browser_origins_recheck_after_await_and_cancel_requires_fresh_invocation(
    owner, monkeypatch, action, args
):
    from browser import BrowserManager
    from tools import BrowserTools

    started, resume = asyncio.Event(), asyncio.Event()
    effects = []
    manager = BrowserManager(headless=True)

    async def effect(*a, **k):
        effects.append(action)

    locator = SimpleNamespace(click=effect, fill=effect)
    page = SimpleNamespace(keyboard=SimpleNamespace(press=effect), url="https://mock")

    async def get_page(*a):
        started.set()
        await resume.wait()
        return page

    async def resolve(*a):
        return locator

    async def summary(*a):
        return {"ok": True}

    async def load(*a, **k):
        pass

    page.wait_for_load_state = load
    monkeypatch.setattr(manager, "_get_page", get_page)
    monkeypatch.setattr(manager, "_resolve_target", resolve)
    monkeypatch.setattr(manager, "_resolve_textbox", resolve)
    monkeypatch.setattr(manager, "_page_summary", summary)
    tools = BrowserTools(manager)
    tools._confirmed_target = "send"
    task = asyncio.create_task(getattr(BrowserTools, action)(tools, None, *args))
    await started.wait()
    owner.approval_epoch += 1
    owner.approval_gate = SimpleNamespace(transaction_active=False)
    resume.set()
    with pytest.raises(ToolError, match="fresh action"):
        await task
    assert not effects
    tools._confirmed_target = "send"
    await getattr(BrowserTools, action)(tools, None, *args)
    assert effects == [action]


@pytest.mark.asyncio
async def test_computer_use_route_and_direct_service_start_refuse_active_review(owner):
    from system.computer_use import ComputerUseService, ComputerUseTools

    owner.approval_gate = SimpleNamespace(transaction_active=True)
    service = ComputerUseService()
    assert service.start("open a view")["status"] == "unavailable"
    tools = ComputerUseTools(service)
    with pytest.raises(ToolError, match="active WhatsApp"):
        await ComputerUseTools.computer_use(tools, None, "open a view")


@pytest.mark.asyncio
async def test_background_computer_task_captures_invocation_epoch(owner, monkeypatch):
    from system import computer_use

    started, resume = asyncio.Event(), asyncio.Event()
    effects = []
    lease = SimpleNamespace(release=lambda: None)
    monkeypatch.setattr(computer_use, "acquire_control", lambda: lease)
    service = computer_use.ComputerUseService()

    async def run(*a):
        started.set()
        await resume.wait()
        ps.check_approval_effect()
        effects.append("input")

    monkeypatch.setattr(service, "_run", run)
    assert service.start("open a view")["status"] == "running"
    await started.wait()
    owner.approval_epoch += 1
    resume.set()
    with pytest.raises(LocalSystemError, match="fresh action"):
        await service._task
    assert not effects


def test_computer_surface_worker_rechecks_ticket_at_injection_origin(owner):
    import threading
    import time

    from system.computer_surface import ComputerSurface

    surface = object.__new__(ComputerSurface)
    surface._check_target = lambda: None
    surface._device = SimpleNamespace(
        tap=lambda *a: pytest.fail("stale physical input")
    )
    token = ps._APPROVAL_EFFECT.set(ps.approval_ticket())
    owner.approval_epoch += 1
    try:
        with pytest.raises(LocalSystemError, match="fresh action"):
            surface._inject(
                {"type": "press", "key": "Return"},
                100,
                100,
                threading.Event(),
                time.monotonic(),
            )
    finally:
        ps._APPROVAL_EFFECT.reset(token)


@pytest.mark.asyncio
async def test_popup_dismiss_tool_refuses_while_review_active(owner):
    from tools import BrowserTools

    async def forbidden(*a, **k):
        pytest.fail("popup click reached")

    owner.approval_gate = SimpleNamespace(transaction_active=True)
    tools = BrowserTools(SimpleNamespace(dismiss_popups=forbidden))
    with pytest.raises(ToolError, match="active WhatsApp"):
        await BrowserTools.dismiss_popups(tools, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,args",
    [
        ("click", ("Send",)),
        ("type_text", ("Message", "hello")),
        ("press_key", ("Enter",)),
        ("dismiss_popups", ()),
    ],
)
async def test_browser_origins_without_ticket_cannot_mutate_active_review(
    owner, monkeypatch, action, args
):
    from browser import BrowserError, BrowserManager

    manager = BrowserManager(headless=True)
    owner.approval_gate = SimpleNamespace(transaction_active=True)
    effects = []

    async def effect(*a, **k):
        effects.append(action)

    locator = SimpleNamespace(click=effect, fill=effect)
    page = SimpleNamespace(keyboard=SimpleNamespace(press=effect), url="https://mock")

    async def get_page(*a):
        return page

    async def resolve(*a):
        return locator

    async def summary(*a):
        return {"ok": True}

    monkeypatch.setattr(manager, "_get_page", get_page)
    monkeypatch.setattr(manager, "_resolve_target", resolve)
    monkeypatch.setattr(manager, "_resolve_textbox", resolve)
    monkeypatch.setattr(manager, "_page_summary", summary)
    assert ps._APPROVAL_EFFECT.get() is None
    if action == "dismiss_popups":
        assert (await manager.dismiss_popups())["dismissed"] is None
    else:
        with pytest.raises(BrowserError, match="active WhatsApp"):
            await getattr(manager, action)(*args)
    assert not effects


def test_computer_surface_without_ticket_refuses_active_review(owner):
    import threading
    import time

    from system.computer_surface import ComputerSurface

    owner.approval_gate = SimpleNamespace(transaction_active=True)
    surface = object.__new__(ComputerSurface)
    surface._check_target = lambda: None
    surface._device = SimpleNamespace(tap=lambda *a: pytest.fail("unapproved input"))
    assert ps._APPROVAL_EFFECT.get() is None
    with pytest.raises(LocalSystemError, match="active WhatsApp"):
        surface._inject(
            {"type": "press", "key": "Return"},
            100,
            100,
            threading.Event(),
            time.monotonic(),
        )
