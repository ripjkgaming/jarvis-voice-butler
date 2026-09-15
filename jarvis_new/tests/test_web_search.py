import pytest

from tools import KNOWN_SITES, duckduckgo_search_url, google_search_url, resolve_url


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
async def test_search_the_web_falls_back_to_helper_google_on_wall() -> None:
    from tools import BrowserTools

    browser = _WalledBrowser()
    tools = BrowserTools(browser)  # type: ignore[arg-type]
    result = await tools.search_the_web(None, query="tallest mountain")  # type: ignore[arg-type]
    kinds = [c[0] for c in browser.calls]
    assert kinds == ["open_url", "open_helper", "read_page"]
    assert "Everest" in str(result)
    assert result.get("fallback") == "helper-google"


@pytest.mark.asyncio
async def test_search_the_web_skips_fallback_when_clean() -> None:
    from tools import BrowserTools

    browser = _CleanBrowser()
    tools = BrowserTools(browser)  # type: ignore[arg-type]
    result = await tools.search_the_web(None, query="capital of France")  # type: ignore[arg-type]
    assert [c[0] for c in browser.calls] == ["open_url"]
    assert "Paris" in str(result)
