import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import browser as browser_module
from browser import BrowserError, BrowserManager, detect_page_wall
from tools import BrowserTools


def test_validate_url_accepts_http_urls() -> None:
    BrowserManager._validate_url("https://example.com/path")
    BrowserManager._validate_url("http://localhost:3000")


@pytest.mark.parametrize(
    "url",
    [
        "example.com",
        "file:///tmp/page.html",
        "javascript:alert(1)",
        "https://user:password@example.com",
    ],
)
def test_validate_url_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(BrowserError):
        BrowserManager._validate_url(url)


def test_consequential_controls_require_confirmation() -> None:
    assert BrowserTools._requires_confirmation("Submit order")
    assert BrowserTools._requires_confirmation("Delete account")
    assert not BrowserTools._requires_confirmation("Search")


def test_direct_navigation_is_prioritized_over_fallback_search() -> None:
    tools = BrowserTools(BrowserManager(headless=True)).tools

    assert [tool.id for tool in tools[:2]] == ["open_url", "search_the_web"]


def test_helper_and_tab_tools_are_registered() -> None:
    ids = [tool.id for tool in BrowserTools(BrowserManager(headless=True)).tools]
    for expected in (
        "open_helper_google",
        "read_helper",
        "close_helper",
        "list_tabs",
        "switch_tab",
        "dismiss_popups",
        "start_deep_research",
        "read_research_page",
        "close_deep_research",
    ):
        assert expected in ids


def test_research_manager_is_isolated_factory() -> None:
    main = BrowserManager(headless=True)
    research = BrowserManager.create_research_manager()
    assert research is not main
    assert research._headless is True
    assert research._profile_dir != main._profile_dir


def test_default_profile_dir_avoids_live_brave_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("JARVIS_BROWSER_PROFILE", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from browser import _default_profile_dir

    resolved = _default_profile_dir()
    assert resolved == tmp_path / "brave-profile"
    assert "BraveSoftware" not in str(resolved)


def test_profile_env_override_is_honored(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_BROWSER_PROFILE", str(tmp_path / "custom"))
    assert BrowserManager(headless=True)._profile_dir == tmp_path / "custom"


def test_browser_binary_prefers_brave(monkeypatch: pytest.MonkeyPatch) -> None:
    from browser import _discover_browser_binary

    monkeypatch.setattr(
        "browser.shutil.which",
        lambda name: "/usr/bin/brave-browser" if name == "brave-browser" else None,
    )
    assert _discover_browser_binary() == "/usr/bin/brave-browser"


def test_browser_binary_falls_back_without_brave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from browser import _discover_browser_binary

    monkeypatch.setattr(
        "browser.shutil.which",
        lambda name: "/usr/bin/google-chrome" if name == "google-chrome" else None,
    )
    assert _discover_browser_binary() == "/usr/bin/google-chrome"
    monkeypatch.setattr("browser.shutil.which", lambda name: None)
    assert _discover_browser_binary() is None


def test_explicit_binary_wins_over_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_BROWSER_BIN", "/opt/brave")
    assert BrowserManager(headless=True)._browser_bin == "/opt/brave"


@pytest.mark.asyncio
async def test_tabs_open_switch_and_close() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TestPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = BrowserManager(headless=True)

    try:
        url = f"http://127.0.0.1:{server.server_port}"
        await manager.open_url(url)
        second = await manager.new_tab(url)
        assert second["tab"].startswith("tab-")

        listed = await manager.list_tabs()
        assert listed["active"] == second["tab"]
        ids = [t["tab"] for t in listed["tabs"]]
        assert "main" in ids and second["tab"] in ids

        await manager.switch_tab("main")
        assert (await manager.list_tabs())["active"] == "main"

        helper = await manager.open_helper(url)
        assert helper["tab"] == "helper"
        text = await manager.read_page(tab="helper")
        assert "Search" in text["text"]

        # Tab-scoped interaction stays on the requested tab.
        await manager.type_text("Search", "LiveKit", tab="helper")

        closed = await manager.close_tab("helper")
        assert closed["closed"] == "helper"

        # Closing the only remaining tabs is refused.
        closed = await manager.close_tab(second["tab"])
        with pytest.raises(BrowserError, match="only open tab"):
            await manager.close_tab("main")
    finally:
        await manager.close()
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_type_text_targets_editable_not_labelled_decoys() -> None:
    """A labelled non-editable element (e.g. DuckDuckGo's aria-labelled
    "Search mode" toggle) must not steal type_text from the search box."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DecoySearchHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = BrowserManager(headless=True)

    try:
        url = f"http://127.0.0.1:{server.server_port}"
        await manager.open_url(url)

        result = await manager.type_text("search", "LiveKit")
        assert result["target"] == "search"

        value = await manager._page.locator("#q").input_value()
        assert value == "LiveKit"
    finally:
        await manager.close()
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_visible_browser_window_is_brought_to_front(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePage:
        def __init__(self) -> None:
            self.titles: list[str] = []
            self.was_brought_to_front = False

        async def title(self) -> str:
            return "Original title"

        async def evaluate(self, expression: str, title: str) -> None:
            self.titles.append(title)

        async def bring_to_front(self) -> None:
            self.was_brought_to_front = True

    focused_titles: list[str] = []
    monkeypatch.setattr(
        browser_module,
        "_focus_window_with_title",
        lambda title: focused_titles.append(title) or True,
    )
    page = FakePage()

    await browser_module._bring_page_window_to_front(page)

    assert page.was_brought_to_front
    assert len(focused_titles) == 1
    assert focused_titles[0].startswith("Jarvis Browser ")
    assert page.titles == [focused_titles[0], "Original title"]


class _TestPageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"""
        <html>
          <body>
            <label for="search">Search</label>
            <input id="search" type="search" placeholder="Search the site">
            <button>Go</button>
          </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format: str, *args: object) -> None:
        return


class _DecoySearchHandler(BaseHTTPRequestHandler):
    """Mimics DuckDuckGo: an aria-labelled toggle div shadows the search box."""

    def do_GET(self) -> None:
        body = b"""
        <html>
          <body>
            <div role="radiogroup" data-mode="search" aria-label="Search mode">
              <input type="radio" name="mode" value="web">Web</input>
              <input type="radio" name="mode" value="ai">AI</input>
            </div>
            <input id="q" type="search" placeholder="Search the web" aria-label="Search">
          </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format: str, *args: object) -> None:
        return


class _TallPageHandler(BaseHTTPRequestHandler):
    """5000px page so real scroll movement is observable via scrollY."""

    """5000px page so real scroll movement is observable via scrollY."""

    def do_GET(self) -> None:
        body = (
            b"""
        <html>
          <body>
            <div style="height:5000px">"""
            + b"scrollable content. " * 500
            + b"""</div>
          </body>
        </html>
        """
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format: str, *args: object) -> None:
        return


@pytest.mark.asyncio
async def test_auto_scroll_moves_page_and_reports() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TallPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = BrowserManager(headless=True)

    try:
        url = f"http://127.0.0.1:{server.server_port}"
        await manager.open_url(url)

        result = await manager.auto_scroll("down", rounds=2)
        assert result["direction"] == "down"
        assert result["rounds"] == 2
        assert result["moved"] is True
        assert result["url"].startswith(url)

        # Out-of-range rounds clamp instead of failing.
        assert (await manager.auto_scroll("down", rounds=99))["rounds"] == 5

        with pytest.raises(BrowserError, match="direction"):
            await manager.auto_scroll("sideways")  # type: ignore[arg-type]
    finally:
        await manager.close()
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_auto_scroll_tool_is_registered() -> None:
    ids = [tool.id for tool in BrowserTools(BrowserManager(headless=True)).tools]
    assert "auto_scroll" in ids


@pytest.mark.asyncio
async def test_browser_recovers_after_external_close() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TestPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = BrowserManager(headless=True)

    try:
        url = f"http://127.0.0.1:{server.server_port}"
        await manager.open_url(url)
        # Simulate the user closing the headed window or a browser crash:
        # the underlying browser dies without going through manager.close(),
        # leaving a stale page handle behind.
        assert manager._browser is not None
        await manager._browser.close()
        # The next operation must transparently relaunch instead of failing
        # with "Target page, context or browser has been closed".
        result = await manager.open_url(url)
        assert result["url"].startswith(url)
        text = await manager.read_page()
        assert "Search" in text["text"]
    finally:
        await manager.close()
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_inspect_and_interact_with_local_page() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TestPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = BrowserManager(headless=True)

    try:
        await manager.open_url(f"http://127.0.0.1:{server.server_port}")
        inspected = await manager.inspect_page()
        elements = inspected["elements"]

        assert isinstance(elements, list)
        assert any(element["name"] == "Search" for element in elements)
        assert any(element["name"] == "Go" for element in elements)

        await manager.type_text("Search", "LiveKit")
        await manager.click("Go")
    finally:
        await manager.close()
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_scroll_repeats_and_reports_times() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TestPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = BrowserManager(headless=True)

    try:
        url = f"http://127.0.0.1:{server.server_port}"
        await manager.open_url(url)

        result = await manager.scroll("down", times=3)
        assert result["direction"] == "down"
        assert result["times"] == 3
        assert result["url"].startswith(url)
        # Movement is verified and reported, like auto_scroll.
        assert isinstance(result["moved"], bool)
        assert isinstance(result["scrollY"], float)

        # Out-of-range counts clamp instead of failing.
        assert (await manager.scroll("up", times=99))["times"] == 5
        assert (await manager.scroll("up", times=0))["times"] == 1

        with pytest.raises(BrowserError, match="direction"):
            await manager.scroll("sideways")  # type: ignore[arg-type]
    finally:
        await manager.close()
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_profile_persists_logins_across_restarts(tmp_path) -> None:
    """localStorage set in one launch is still there after full close.

    This is the mechanism a one-time sign-in relies on: cookies and
    storage survive in the persistent profile.
    """
    profile = tmp_path / "persist-profile"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TestPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"

    try:
        first = BrowserManager(headless=True, profile_dir=profile)
        await first.open_url(url)
        page = await first._get_page(None)
        await page.evaluate("localStorage.setItem('jarvis-signin-probe', 'kept')")
        await first.close()

        second = BrowserManager(headless=True, profile_dir=profile)
        await second.open_url(url)
        kept = await (await second._get_page(None)).evaluate(
            "localStorage.getItem('jarvis-signin-probe')"
        )
        await second.close()
        assert kept == "kept"
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_start_reuses_restored_pages_without_closing_all(tmp_path) -> None:
    """Restart with leftover tabs reuses one as main instead of close-all.

    Closing every page before new_page() makes headed Chromium refuse
    new targets ("Failed to open a new tab"); reuse-first avoids the
    zero-page state entirely.
    """
    profile = tmp_path / "reuse-profile"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TestPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = BrowserManager(headless=True, profile_dir=profile)

    try:
        url = f"http://127.0.0.1:{server.server_port}"
        await manager.open_url(url)
        extra = await manager._context.new_page()
        assert len([p for p in manager._context.pages if not p.is_closed()]) == 2

        # Force a restart with tabs still open: must reuse, not close-all.
        manager._page = None
        await manager.start()
        live = [p for p in manager._context.pages if not p.is_closed()]
        assert len(live) == 1
        assert manager._active_tab == "main"
        assert extra.is_closed()
        # The reused main tab still works.
        result = await manager.open_url(url)
        assert result["url"].startswith(url)
    finally:
        await manager.close()
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_scroll_reports_movement_honestly() -> None:
    """Tall page moves (moved True); short page cannot (moved False)."""
    tall = ThreadingHTTPServer(("127.0.0.1", 0), _TallPageHandler)
    threading.Thread(target=tall.serve_forever, daemon=True).start()
    short = ThreadingHTTPServer(("127.0.0.1", 0), _TestPageHandler)
    threading.Thread(target=short.serve_forever, daemon=True).start()
    manager = BrowserManager(headless=True)

    try:
        await manager.open_url(f"http://127.0.0.1:{tall.server_port}")
        moved_result = await manager.scroll("down", times=3)
        assert moved_result["moved"] is True
        assert moved_result["scrollY"] > 0

        await manager.open_url(f"http://127.0.0.1:{short.server_port}")
        stuck_result = await manager.scroll("down", times=3)
        assert stuck_result["moved"] is False
    finally:
        await manager.close()
        tall.shutdown()
        short.shutdown()


def test_detect_page_wall_classifies_blocks() -> None:
    assert detect_page_wall("YouTube", "") == "empty"
    assert detect_page_wall("News", "independent reporting here") is None
    assert (
        detect_page_wall("Consent", "Before you continue, Accept all cookies")
        == "consent"
    )
    assert detect_page_wall("Hmm", "Sign in to confirm you're not a bot") == "bot-check"
    assert (
        detect_page_wall("", "You've been blocked by network security. File a ticket")
        == "bot-check"
    )
    assert detect_page_wall("Docs", "Unusual traffic from your network") == "bot-check"
    assert (
        detect_page_wall("App", "Please sign in to continue with your work") == "login"
    )


class _EmptyPageHandler(BaseHTTPRequestHandler):
    """Bot-shell stand-in: valid page, zero body text."""

    def do_GET(self) -> None:
        body = b"<html><head><title>Shell</title></head><body></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format: str, *args: object) -> None:
        return


class _ContentPageHandler(BaseHTTPRequestHandler):
    """Normal page with enough text to count as content."""

    def do_GET(self) -> None:
        body = b"""
        <html>
          <body>
            <h1>Daily news and reporting with plenty of readable text
            for the wall detector to chew on.</h1>
          </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format: str, *args: object) -> None:
        return


class _BannerPageHandler(BaseHTTPRequestHandler):
    """Cookie banner over content: safe Reject button must be clicked."""

    def do_GET(self) -> None:
        body = b"""
        <html>
          <body>
            <div id="banner"><p>We value your privacy</p>
            <button>Reject all</button></div>
            <h1>Article text lives under the banner.</h1>
          </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format: str, *args: object) -> None:
        return


class _DangerPageHandler(BaseHTTPRequestHandler):
    """Only a consequential button: dismiss must touch nothing."""

    def do_GET(self) -> None:
        body = b"""
        <html>
          <body>
            <button>Delete account</button>
          </body>
        </html>
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format: str, *args: object) -> None:
        return


def _serve(handler) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.mark.asyncio
async def test_open_url_reports_wall_flag() -> None:
    content_server, content_thread = _serve(_ContentPageHandler)
    empty_server, empty_thread = _serve(_EmptyPageHandler)
    manager = BrowserManager(headless=True)

    try:
        ok = await manager.open_url(f"http://127.0.0.1:{content_server.server_port}")
        assert ok["wall"] is None

        shell = await manager.open_url(f"http://127.0.0.1:{empty_server.server_port}")
        assert shell["wall"] == "empty"
    finally:
        await manager.close()
        content_server.shutdown()
        empty_server.shutdown()
        content_thread.join(timeout=2)
        empty_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_dismiss_popups_clicks_safe_button_only() -> None:
    banner_server, banner_thread = _serve(_BannerPageHandler)
    danger_server, danger_thread = _serve(_DangerPageHandler)
    manager = BrowserManager(headless=True)

    try:
        await manager.open_url(f"http://127.0.0.1:{banner_server.server_port}")
        clicked = await manager.dismiss_popups()
        assert clicked["dismissed"] == "Reject all"

        await manager.open_url(f"http://127.0.0.1:{danger_server.server_port}")
        untouched = await manager.dismiss_popups()
        assert untouched["dismissed"] is None
    finally:
        await manager.close()
        banner_server.shutdown()
        danger_server.shutdown()
        banner_thread.join(timeout=2)
        danger_thread.join(timeout=2)
