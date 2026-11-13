from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from jarvis_rt import ToolError

import action_guard
import phone_session as ps
import visible_url as vu
from browser import BrowserManager
from tools import BrowserTools


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    action_guard.reset()

    # Google resolution is mocked below. Execute those fakes inline to keep
    # these interface tests bounded and independent of host executor shutdown.
    async def mocked_lookup(fn, *args, **kwargs):
        import contextvars

        return contextvars.copy_context().run(fn, *args, **kwargs)

    monkeypatch.setattr(vu.asyncio, "to_thread", mocked_lookup)
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.delenv("JARVIS_DESKTOP_SANDBOX", raising=False)
    monkeypatch.setattr(
        vu.shutil,
        "which",
        lambda name: "/usr/bin/brave-browser" if name == "brave-browser" else None,
    )
    monkeypatch.setattr(ps, "guest_required", lambda: False)


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/x",
        "javascript:alert(1)",
        "data:text/html,x",
        "https://user:pw@example.com",
        "https://example.com\n--x",
        "https://",
        "https://bad host/x",
    ],
)
def test_unsafe(url):
    with pytest.raises(ToolError):
        vu.normalize_url(url)


def test_quotes_spaces_and_case():
    assert (
        vu.normalize_url(' "https://example.com/Case Path?q=A B" ')
        == "https://example.com/Case%20Path?q=A%20B"
    )


async def test_visible_tool_routes_without_playwright(monkeypatch):
    launch = AsyncMock(
        return_value={"status": "launch_requested", "visually_confirmed": False}
    )
    monkeypatch.setattr(vu, "launch", launch)
    browser = BrowserManager(headless=True)
    monkeypatch.setattr(
        browser, "open_url", AsyncMock(side_effect=AssertionError("headless route"))
    )
    tools = BrowserTools(browser)
    assert "open_visible_url" in [t.id for t in tools.tools]
    result = await BrowserTools.open_visible_url(tools, None, "https://example.com")
    assert result["visually_confirmed"] is False
    launch.assert_awaited_once()


async def test_launch_success_duplicate_and_fresh_request(monkeypatch):
    spawn = AsyncMock(
        return_value=SimpleNamespace(pid=123, wait=AsyncMock(return_value=0))
    )
    monkeypatch.setattr(vu.asyncio, "create_subprocess_exec", spawn)
    with action_guard.request_scope("one"):
        result = await vu.launch("https://example.com/Case Path")
        duplicate = await vu.launch("https://example.com/Case Path")
    assert result["status"] == "launch_requested"
    assert result["visually_confirmed"] is False
    assert "not visually verified" in duplicate["say"]
    assert spawn.await_count == 1
    assert spawn.call_args.args == (
        "/usr/bin/brave-browser",
        "--new-tab",
        "https://example.com/Case%20Path",
    )
    with action_guard.request_scope("two"):
        await vu.launch("https://example.com")
    assert spawn.await_count == 2


async def test_failed_launch_allows_retry(monkeypatch):
    spawn = AsyncMock(
        return_value=SimpleNamespace(pid=123, wait=AsyncMock(return_value=1))
    )
    monkeypatch.setattr(vu.asyncio, "create_subprocess_exec", spawn)
    with action_guard.request_scope("failure"):
        with pytest.raises(ToolError, match="rejected"):
            await vu.launch("https://example.com")
        spawn.return_value.wait = AsyncMock(return_value=0)
        await vu.launch("https://example.com")
    assert spawn.await_count == 2


async def test_revocation_and_review_block_before_effect(monkeypatch):
    scope = ps._Owner("test", active=True)
    token = ps._OWNER.set(scope)
    spawn = AsyncMock()
    monkeypatch.setattr(vu.asyncio, "create_subprocess_exec", spawn)
    try:
        ticket = ps.approval_ticket()
        scope.approval_epoch += 1
        with pytest.raises(ToolError, match="fresh action"):
            await vu.launch("https://example.com", ticket=ticket)
        scope.approval_gate = SimpleNamespace(transaction_active=True)
        with pytest.raises(ToolError, match="WhatsApp"):
            await vu.launch("https://example.com", ticket=ps.approval_ticket())
        scope.revoke()
        with pytest.raises(ToolError):
            await vu.launch("https://example.com", ticket=ps.approval_ticket())
        spawn.assert_not_awaited()
    finally:
        ps._OWNER.reset(token)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=offline",
        "https://youtu.be/offline",
        "https://example.com/Case Path",
    ],
)
async def test_registered_open_url_is_visible_not_backend(monkeypatch, url):
    browser = BrowserManager(headless=True)
    backend = AsyncMock(
        side_effect=AssertionError("User-facing open must not navigate Playwright")
    )
    monkeypatch.setattr(browser, "open_url", backend)
    launch = AsyncMock(
        return_value={"status": "launch_requested", "visually_confirmed": False}
    )
    monkeypatch.setattr(vu, "launch", launch)
    registry = {t.id: t for t in BrowserTools(browser).tools}
    result = await registry["open_url"](None, url)
    assert result["status"] == "launch_requested"
    assert result["visually_confirmed"] is False
    launch.assert_awaited_once()
    backend.assert_not_awaited()


async def test_registered_browse_url_remains_backend(monkeypatch):
    browser = BrowserManager(headless=True)
    backend = AsyncMock(return_value={"url": "https://example.com", "wall": None})
    monkeypatch.setattr(browser, "open_url", backend)
    launch = AsyncMock(side_effect=AssertionError("Reading must not launch desktop"))
    monkeypatch.setattr(vu, "launch", launch)
    tools = BrowserTools(browser).tools
    assert len({t.id for t in tools}) == len(tools)
    registry = {t.id: t for t in tools}
    result = await registry["browse_url"](None, "https://example.com", tab="helper")
    assert result["browser"] == "backend_automation"
    backend.assert_awaited_once_with("https://example.com", tab="helper")
    launch.assert_not_awaited()


async def test_registered_visible_aliases_share_duplicate_guard(monkeypatch):
    spawn = AsyncMock(return_value=SimpleNamespace(wait=AsyncMock(return_value=0)))
    monkeypatch.setattr(vu.asyncio, "create_subprocess_exec", spawn)
    browser = BrowserManager(headless=True)
    monkeypatch.setattr(
        browser,
        "open_url",
        AsyncMock(side_effect=AssertionError("No backend navigation")),
    )
    registry = {t.id: t for t in BrowserTools(browser).tools}
    with action_guard.request_scope("video-open"):
        first = await registry["open_url"](None, "https://youtu.be/offline")
        second = await registry["open_visible_url"](None, "https://youtu.be/offline")
    assert first["status"] == "launch_requested"
    assert second["status"] == "duplicate_request"
    assert second["visually_confirmed"] is False
    spawn.assert_awaited_once()


async def test_registered_open_url_preserves_private_review_gate(monkeypatch):
    scope = ps._Owner("test", active=True)
    scope.approval_gate = SimpleNamespace(transaction_active=True)
    owner_token = ps._OWNER.set(scope)
    spawn = AsyncMock()
    monkeypatch.setattr(vu.asyncio, "create_subprocess_exec", spawn)
    try:
        browser = BrowserManager(headless=True)
        monkeypatch.setattr(
            browser,
            "open_url",
            AsyncMock(side_effect=AssertionError("No backend navigation")),
        )
        registry = {t.id: t for t in BrowserTools(browser).tools}
        with pytest.raises(ToolError, match="WhatsApp"):
            await registry["open_url"](None, "https://youtu.be/offline")
        spawn.assert_not_awaited()
    finally:
        ps._OWNER.reset(owner_token)


async def test_google_file_open_uses_visible_launch_evidence(monkeypatch):
    import google_apps

    monkeypatch.setattr(
        google_apps,
        "resolve",
        lambda *a: {
            "url": "https://docs.google.com/document/d/offline",
            "title": "Notes",
            "say": "Opening Notes, Sir.",
            "status": "resolved",
        },
    )
    browser = BrowserManager(headless=True)
    backend = AsyncMock(side_effect=AssertionError("No backend navigation"))
    monkeypatch.setattr(browser, "open_url", backend)
    launch = AsyncMock(
        return_value={
            "say": "Requested; not visually verified.",
            "status": "launch_requested",
            "visually_confirmed": False,
        }
    )
    monkeypatch.setattr(vu, "launch", launch)
    result = await BrowserTools.open_google_app(
        BrowserTools(browser), None, "docs", "Notes"
    )
    assert result["status"] == "launch_requested"
    assert result["visually_confirmed"] is False
    assert result["say"] == "Requested; not visually verified."
    assert result["title"] == "Notes"
    backend.assert_not_awaited()
    launch.assert_awaited_once()


async def test_google_file_choice_does_not_launch(monkeypatch):
    import google_apps

    monkeypatch.setattr(
        google_apps,
        "resolve",
        lambda *a: {
            "status": "needs_choice",
            "say": "Which Notes file?",
            "candidates": [
                {"title": "Notes", "url": "https://docs.google.com/document/d/offline"}
            ],
        },
    )
    browser = BrowserManager(headless=True)
    monkeypatch.setattr(
        browser,
        "open_url",
        AsyncMock(side_effect=AssertionError("No backend navigation")),
    )
    spawn = AsyncMock()
    monkeypatch.setattr(vu.asyncio, "create_subprocess_exec", spawn)
    result = await BrowserTools.open_google_app(
        BrowserTools(browser), None, "docs", "Notes"
    )
    assert result["status"] == "needs_choice"
    assert result["visually_confirmed"] is False
    spawn.assert_not_awaited()


@pytest.mark.parametrize("revoke", [False, True])
async def test_google_file_lookup_preserves_invocation_epoch(monkeypatch, revoke):
    import google_apps

    owner = ps._Owner("lookup-owner", active=True)
    owner_token = ps._OWNER.set(owner)

    def resolve(*args):
        if revoke:
            owner.revoke()
        else:
            owner.approval_epoch += 1
        return {
            "status": "resolved",
            "title": "Notes",
            "url": "https://docs.google.com/document/d/offline",
            "say": "Opening Notes",
        }

    monkeypatch.setattr(google_apps, "resolve", resolve)
    browser = BrowserManager(headless=True)
    monkeypatch.setattr(
        browser,
        "open_url",
        AsyncMock(side_effect=AssertionError("No backend navigation")),
    )
    spawn = AsyncMock()
    monkeypatch.setattr(vu.asyncio, "create_subprocess_exec", spawn)
    try:
        with pytest.raises(ToolError, match="WhatsApp"):
            await BrowserTools.open_google_app(
                BrowserTools(browser), None, "docs", "Notes"
            )
        spawn.assert_not_awaited()
    finally:
        ps._OWNER.reset(owner_token)


async def test_google_active_review_blocks_lookup(monkeypatch):
    import google_apps

    owner = ps._Owner("google-review", active=True)
    owner.approval_gate = SimpleNamespace(transaction_active=True)
    owner_token = ps._OWNER.set(owner)
    lookup = Mock(side_effect=AssertionError("No lookup during review"))
    monkeypatch.setattr(google_apps, "resolve", lookup)
    try:
        with pytest.raises(ToolError, match="WhatsApp"):
            await BrowserTools.open_google_app(
                BrowserTools(BrowserManager(headless=True)), None, "drive", "Notes"
            )
        lookup.assert_not_called()
    finally:
        ps._OWNER.reset(owner_token)


async def test_google_lookup_passes_original_ticket_and_request_identity(monkeypatch):
    import google_apps

    owner = ps._Owner("google-owner", active=True)
    owner_token = ps._OWNER.set(owner)
    monkeypatch.setattr(
        google_apps,
        "resolve",
        lambda *a: {
            "status": "resolved",
            "title": "Notes",
            "url": "https://docs.google.com/document/d/offline",
        },
    )
    launch = AsyncMock(
        return_value={
            "say": "Requested; not verified.",
            "status": "launch_requested",
            "visually_confirmed": False,
        }
    )
    monkeypatch.setattr(vu, "launch", launch)
    try:
        with action_guard.request_scope("google-request"):
            await BrowserTools.open_google_app(
                BrowserTools(BrowserManager(headless=True)), None, "docs", "Notes"
            )
        assert launch.call_args.kwargs["ticket"] == (owner, 0)
        assert launch.call_args.kwargs["identity"] == "google-request"
    finally:
        ps._OWNER.reset(owner_token)


async def test_google_unsafe_resolved_target_cannot_spawn(monkeypatch):
    import google_apps

    monkeypatch.setattr(
        google_apps,
        "resolve",
        lambda *a: {
            "status": "resolved",
            "title": "Notes",
            "url": "javascript:alert(1)",
        },
    )
    spawn = AsyncMock()
    monkeypatch.setattr(vu.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(ToolError):
        await BrowserTools.open_google_app(
            BrowserTools(BrowserManager(headless=True)), None, "docs", "Notes"
        )
    spawn.assert_not_awaited()


async def test_google_fallback_reports_launch_only(monkeypatch):
    import google_apps

    monkeypatch.setattr(
        google_apps,
        "resolve",
        lambda *a: {
            "status": "fallback_search",
            "title": "Search: Notes",
            "url": "https://drive.google.com/drive/search?q=Notes",
            "say": "The fallback is a Drive search.",
        },
    )
    launch = AsyncMock(
        return_value={
            "say": "Requested; not visually verified.",
            "status": "launch_requested",
            "visually_confirmed": False,
        }
    )
    monkeypatch.setattr(vu, "launch", launch)
    result = await BrowserTools.open_google_app(
        BrowserTools(BrowserManager(headless=True)), None, "drive", "Notes"
    )
    assert result["resolution"] == "fallback_search"
    assert result["status"] == "launch_requested"
    assert result["visually_confirmed"] is False
    assert (
        result["say"]
        == "The fallback is a Drive search. Requested; not visually verified."
    )


async def test_guest_missing_desktop_and_missing_brave(monkeypatch):
    monkeypatch.setattr(ps, "guest_required", lambda: True)
    with pytest.raises(ToolError, match="Guest"):
        await vu.launch("https://example.com")
    monkeypatch.setattr(ps, "guest_required", lambda: False)
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(ToolError, match="desktop"):
        await vu.launch("https://example.com")
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setattr(vu.shutil, "which", lambda _: None)
    with pytest.raises(ToolError, match="Brave"):
        await vu.launch("https://example.com")


async def test_backend_navigation_reports_separate_visibility(monkeypatch):
    browser = BrowserManager(headless=True)
    monkeypatch.setattr(
        browser,
        "open_url",
        AsyncMock(return_value={"url": "https://example.com", "wall": None}),
    )
    result = await BrowserTools.open_url(
        BrowserTools(browser), None, "https://example.com"
    )
    assert result["browser"] == "backend_automation"
    assert result["visibility"] == "not_verified_on_desktop"


async def test_missing_binary_launch_error(monkeypatch):
    monkeypatch.setattr(
        vu.asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=FileNotFoundError("Brave missing")),
    )
    with action_guard.request_scope("missing"):
        with pytest.raises(ToolError, match="Brave missing"):
            await vu.launch("https://example.com")
        assert action_guard.launch_for_request("missing") is None


async def test_long_lived_launcher_is_only_requested(monkeypatch):
    import asyncio

    done = asyncio.Event()

    async def wait():
        await done.wait()
        return 0

    monkeypatch.setattr(
        vu.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=SimpleNamespace(wait=wait)),
    )
    result = await vu.launch("https://example.com")
    assert result["launcher_exit_code"] is None
    assert result["visually_confirmed"] is False
    done.set()
    await asyncio.sleep(0)


async def test_revocation_during_resolution(monkeypatch):
    scope = ps._Owner("test", active=True)
    token = ps._OWNER.set(scope)
    spawn = AsyncMock()
    monkeypatch.setattr(vu.asyncio, "create_subprocess_exec", spawn)

    def resolve(name):
        scope.revoke()
        return "/usr/bin/brave-browser"

    monkeypatch.setattr(vu.shutil, "which", resolve)
    try:
        with pytest.raises(ToolError, match="WhatsApp"):
            await vu.launch("https://example.com", ticket=ps.approval_ticket())
        spawn.assert_not_awaited()
    finally:
        ps._OWNER.reset(token)
