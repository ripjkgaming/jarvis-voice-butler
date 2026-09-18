import pytest

from tools import KNOWN_SITES, duckduckgo_search_url, google_search_url, resolve_url


@pytest.fixture(autouse=True)
def _scrub_search_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """No provider keys in tests: keyed providers miss before networking."""
    for var in ("YDC_API_KEY", "YOU_API_KEY", "BRAVE_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_duckduckgo_search_url_encodes_query() -> None:
    assert (
        duckduckgo_search_url("  current weather in New York  ")
        == "https://duckduckgo.com/?q=current+weather+in+New+York"
    )


def test_duckduckgo_search_url_rejects_empty_query() -> None:
    with pytest.raises(ValueError, match="query cannot be empty"):
        duckduckgo_search_url("  ")


def test_google_search_url_encodes_query() -> None:
    assert (
        google_search_url("  livekit agents handoff  ")
        == "https://www.google.com/search?q=livekit+agents+handoff"
    )


def test_google_search_url_rejects_empty_query() -> None:
    with pytest.raises(ValueError, match="query cannot be empty"):
        google_search_url("  ")


@pytest.mark.parametrize(
    ("destination", "expected"),
    [
        ("youtube", "https://www.youtube.com"),
        ("YouTube", "https://www.youtube.com"),
        ("open youtube", "https://www.youtube.com"),
        ("youtube shorts", "https://www.youtube.com/shorts"),
        ("open youtube shorts", "https://www.youtube.com/shorts"),
        ("gmail", "https://mail.google.com"),
        ("amazon", "https://www.amazon.com"),
        ("youtube.com", "https://youtube.com"),
        ("https://example.com/path", "https://example.com/path"),
    ],
)
def test_resolve_url_knows_sites_on_its_own(destination: str, expected: str) -> None:
    assert resolve_url(destination) == expected


def test_known_sites_covers_common_destinations() -> None:
    for site in ("google", "youtube", "amazon", "gmail", "reddit", "wikipedia"):
        assert site in KNOWN_SITES


class _WalledBrowser:
    """DuckDuckGo serves a bot wall; helper Google has real results."""

    def __init__(self) -> None:
        self.calls: list = []

    async def open_url(self, url: str, **kwargs):
        self.calls.append(("open_url", url))
        return {"title": "DDG", "text": "", "url": url, "wall": "bot-check"}

    async def open_helper(self, url: str):
        self.calls.append(("open_helper", url))
        return {"tab": "helper", "url": url}

    async def read_page(self, **kwargs):
        self.calls.append(("read_page", kwargs.get("tab")))
        return {"text": "Mount Everest is the tallest mountain.", "url": "google"}


class _CleanBrowser(_WalledBrowser):
    async def open_url(self, url: str, **kwargs):
        self.calls.append(("open_url", url))
        return {
            "title": "DDG",
            "text": "Paris is the capital.",
            "url": url,
            "wall": None,
        }


@pytest.mark.asyncio
async def test_search_the_web_falls_back_to_helper_google_on_wall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools as tools_mod
    from tools import BrowserTools

    async def _no_api(*args, **kwargs):
        return None

    monkeypatch.setattr(tools_mod, "brave_api_search", _no_api)
    monkeypatch.setattr(tools_mod, "ddg_lite_search", _no_api)
    browser = _WalledBrowser()
    tools = BrowserTools(browser)  # type: ignore[arg-type]
    result = await tools.search_the_web(None, query="tallest mountain")  # type: ignore[arg-type]
    kinds = [c[0] for c in browser.calls]
    assert kinds == ["open_url", "open_helper", "read_page"]
    assert "Everest" in str(result)
    assert result.get("fallback") == "helper-google"


@pytest.mark.asyncio
async def test_search_the_web_skips_fallback_when_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools as tools_mod
    from tools import BrowserTools

    async def _no_api(*args, **kwargs):
        return None

    monkeypatch.setattr(tools_mod, "brave_api_search", _no_api)
    monkeypatch.setattr(tools_mod, "ddg_lite_search", _no_api)
    browser = _CleanBrowser()
    tools = BrowserTools(browser)  # type: ignore[arg-type]
    result = await tools.search_the_web(None, query="capital of France")  # type: ignore[arg-type]
    assert [c[0] for c in browser.calls] == ["open_url"]
    assert "Paris" in str(result)


@pytest.mark.asyncio
async def test_api_parallel_prefers_brave_over_lite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools as tools_mod
    from tools import api_search_parallel

    async def _brave(query: str, **kwargs):
        return {"say": "b", "text": "b", "url": "u", "fallback": "brave-api"}

    async def _lite(query: str):
        return {"say": "l", "text": "l", "url": "u", "fallback": "ddg-lite"}

    monkeypatch.setattr(tools_mod, "brave_api_search", _brave)
    monkeypatch.setattr(tools_mod, "ddg_lite_search", _lite)
    out = await api_search_parallel("q")
    assert out is not None and out.get("fallback") == "brave-api"


@pytest.mark.asyncio
async def test_api_parallel_falls_through_to_lite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools as tools_mod
    from tools import api_search_parallel

    async def _none(*args, **kwargs):
        return None

    async def _lite(query: str):
        return {"say": "l", "text": "l", "url": "u", "fallback": "ddg-lite"}

    monkeypatch.setattr(tools_mod, "brave_api_search", _none)
    monkeypatch.setattr(tools_mod, "ddg_lite_search", _lite)
    out = await api_search_parallel("q")
    assert out is not None and out.get("fallback") == "ddg-lite"


@pytest.mark.asyncio
async def test_api_parallel_none_when_all_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools as tools_mod
    from tools import api_search_parallel

    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(tools_mod, "brave_api_search", _none)
    monkeypatch.setattr(tools_mod, "ddg_lite_search", _none)
    assert await api_search_parallel("q") is None


@pytest.mark.asyncio
async def test_api_parallel_survives_provider_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools as tools_mod
    from tools import api_search_parallel

    async def _boom(*args, **kwargs):
        raise RuntimeError("provider down")

    async def _lite(query: str):
        return {"say": "l", "text": "l", "url": "u", "fallback": "ddg-lite"}

    monkeypatch.setattr(tools_mod, "brave_api_search", _boom)
    monkeypatch.setattr(tools_mod, "ddg_lite_search", _lite)
    out = await api_search_parallel("q")
    assert out is not None and out.get("fallback") == "ddg-lite"


@pytest.mark.asyncio
async def test_search_the_web_prefers_api_over_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools as tools_mod
    from tools import BrowserTools

    async def _lite(query: str):
        return {
            "say": "lite says hi",
            "text": "lite says hi",
            "url": "u",
            "fallback": "ddg-lite",
        }

    async def _no_api(*args, **kwargs):
        return None

    monkeypatch.setattr(tools_mod, "brave_api_search", _no_api)
    monkeypatch.setattr(tools_mod, "ddg_lite_search", _lite)
    browser = _CleanBrowser()
    tools = BrowserTools(browser)  # type: ignore[arg-type]
    result = await tools.search_the_web(None, query="anything")  # type: ignore[arg-type]
    assert browser.calls == []
    assert result.get("fallback") == "ddg-lite"


def test_clean_text_strips_tags() -> None:
    from tools import _clean_text

    assert _clean_text("<b>Everest</b> &amp; K2") == "Everest & K2"


def test_format_results_numbers_hits() -> None:
    from tools import _format_results

    out = _format_results("q", [("T", "S", "https://u")])
    assert "1. T" in out["text"] and out["url"] == "https://u"


@pytest.mark.asyncio
async def test_brave_api_search_needs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import brave_api_search

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert await brave_api_search("q") is None


class _FakeBraveResp:
    def raise_for_status(self) -> None:
        pass

    def json(self):
        return {
            "web": {
                "results": [
                    {"title": "T1", "description": "D1", "url": "https://a.example"},
                    {"title": "T2", "url": "https://b.example"},
                ]
            }
        }


class _FakeBraveClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        return _FakeBraveResp()


@pytest.mark.asyncio
async def test_brave_api_search_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools as tools_mod
    from tools import brave_api_search

    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", _FakeBraveClient)
    out = await brave_api_search("q", api_key="k")
    assert out is not None
    assert out.get("fallback") == "brave-api"
    assert "T1" in out["text"] and "https://a.example" in out["text"]


class _FakeLiteResp:
    text = (
        '<a rel="nofollow" href="https://c.example">CT</a>'
        "<td class='result-snippet'>CS</td>"
    )

    def raise_for_status(self) -> None:
        pass


class _FakeLiteClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return _FakeLiteResp()


@pytest.mark.asyncio
async def test_ddg_lite_search_parses_results(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools as tools_mod
    from tools import ddg_lite_search

    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", _FakeLiteClient)
    out = await ddg_lite_search(" q ")
    assert out is not None
    assert out.get("fallback") == "ddg-lite"
    assert "CT" in out["text"] and "https://c.example" in out["text"]


@pytest.mark.asyncio
async def test_ddg_lite_search_rejects_empty() -> None:
    from tools import ddg_lite_search

    assert await ddg_lite_search("   ") is None


@pytest.mark.asyncio
async def test_you_search_needs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import you_search

    monkeypatch.delenv("YDC_API_KEY", raising=False)
    monkeypatch.delenv("YOU_API_KEY", raising=False)
    assert await you_search("q") is None
    assert await you_search("   ", api_key="k") is None


class _FakeYouResp:
    def raise_for_status(self) -> None:
        pass

    def json(self):
        return {
            "results": {
                "web": [
                    {
                        "title": "YT",
                        "url": "https://y.example",
                        "snippets": ["S1", "S2"],
                    },
                    {"title": "NoURL"},
                ]
            }
        }


class _FakeYouClient:
    def __init__(self, *args, **kwargs):
        self.seen = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        self.seen = {"url": url, "key": kwargs["headers"].get("X-API-Key")}
        return _FakeYouResp()


@pytest.mark.asyncio
async def test_you_search_posts_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools as tools_mod
    from tools import you_search

    client_holder: dict = {}

    class _CaptureClient(_FakeYouClient):
        async def __aenter__(self):
            client_holder["client"] = self
            return self

    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", _CaptureClient)
    out = await you_search(" q ", api_key="k")
    assert out is not None
    assert out.get("fallback") == "you-search"
    assert "YT" in out["text"] and "S1 S2" in out["text"]
    assert client_holder["client"].seen["url"] == "https://ydc-index.io/v1/search"
    assert client_holder["client"].seen["key"] == "k"


@pytest.mark.asyncio
async def test_api_parallel_prefers_you_over_brave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools as tools_mod
    from tools import api_search_parallel

    async def _you(query: str, **kwargs):
        return {"say": "y", "text": "y", "url": "u", "fallback": "you-search"}

    async def _brave(query: str, **kwargs):
        return {"say": "b", "text": "b", "url": "u", "fallback": "brave-api"}

    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(tools_mod, "you_search", _you)
    monkeypatch.setattr(tools_mod, "brave_api_search", _brave)
    monkeypatch.setattr(tools_mod, "ddg_lite_search", _none)
    out = await api_search_parallel("q")
    assert out is not None and out.get("fallback") == "you-search"
