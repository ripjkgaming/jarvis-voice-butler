import asyncio
from pathlib import Path

import pytest

import shopping
from system.inbox import InboxTools
from tools import BrowserTools

ROOT = Path(__file__).resolve().parents[1]


def test_shopping_is_browser_tool_without_hud():
    assert "shopping_search" in {t.info.name for t in BrowserTools(None).tools}
    assert "shopping_search" not in {t.info.name for t in InboxTools().tools}
    assert (
        "ShoppingSection"
        not in (ROOT / "frontend/components/hud/hud-shell.tsx").read_text()
    )
    assert "bridgeShopping" not in (ROOT / "frontend/lib/bridge.ts").read_text()


@pytest.mark.asyncio
async def test_playwright_prices_and_blocks(monkeypatch):
    monkeypatch.setattr(shopping, "public_url", lambda url: url)
    monkeypatch.setattr(shopping, "robots_allowed", lambda url: True)

    class Response:
        status = 200

    class Page:
        url = "https://shop.example/product"
        closed = False
        wall = ""

        async def route(self, pattern, handler):
            pass

        async def goto(self, url, **kwargs):
            self.url = url
            return Response()

        async def title(self):
            return "Product"

        def locator(self, selector):
            return self

        async def inner_text(self, **kwargs):
            return self.wall or "Headphones available"

        async def content(self):
            return '<script type="application/ld+json">{"@type":"Product","name":"Headphones","offers":{"@type":"Offer","price":"42.50","priceCurrency":"USD","availability":"https://schema.org/InStock"}}</script>'

        async def close(self):
            self.closed = True

    page = Page()

    class Context:
        async def new_page(self):
            return page

    class Browser:
        _context = Context()
        _lock = asyncio.Lock()

        async def _get_page(self):
            return page

    result = await shopping.search_products_browser(
        "https://shop.example/product", Browser()
    )
    product = result["results"][0]["products"][0]
    assert product["price"] == "42.50" and product["currency"] == "USD"
    assert product["availability"] == "InStock" and product["checked_at"]
    assert product["url"] == page.url and page.closed
    page.wall = "Verify you are human captcha"
    result = await shopping.search_products_browser(page.url, Browser())
    assert result["results"][0]["status"] == "blocked"
    assert result["results"][0]["products"] == []


@pytest.mark.asyncio
async def test_tool_uses_shared_browser_without_opening(monkeypatch):
    calls = []
    browser = object()

    async def search(query, manager):
        calls.append((query, manager))
        return {"results": []}

    monkeypatch.setattr(shopping, "search_products_browser", search)
    tool = BrowserTools(browser)
    result = await tool.shopping_search(None, "headphones")
    assert result == {"results": []}
    assert calls == [("headphones", browser)]
