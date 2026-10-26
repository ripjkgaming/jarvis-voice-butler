"""Public product-page prices. No estimates, checkout, or access-control bypass."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from decimal import Decimal, InvalidOperation

MAX_BYTES = 2_000_000


def public_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Only public HTTPS product pages are allowed")
    for result in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(result[4][0]).is_global:
            raise ValueError("Private network addresses are blocked")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, "")
    )


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirect requires a fresh product-page URL and robots check")


def fetch(url: str) -> str:
    url = public_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "JarvisShopping/1.0"})
    with urllib.request.build_opener(PublicRedirect()).open(req, timeout=6) as response:
        if "text/" not in response.headers.get(
            "Content-Type", ""
        ) and "json" not in response.headers.get("Content-Type", ""):
            raise ValueError("Not a public HTML product page")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Page exceeds size limit")
        return data.decode("utf-8", errors="replace")


def parse_prices(page: str, url: str) -> list[dict]:
    """Only explicit Product/Offer JSON-LD with price AND ISO currency."""
    found = []

    def visit(node, title=""):
        if isinstance(node, list):
            for child in node:
                visit(child, title)
        elif isinstance(node, dict):
            kind = node.get("@type", "")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Product" in kinds:
                title = str(node.get("name", "")).strip()[:240]
                visit(node.get("offers", []), title)
            elif "Offer" in kinds and title:
                price = node.get(
                    "price",
                    node.get("priceSpecification", {}).get("price")
                    if isinstance(node.get("priceSpecification"), dict)
                    else None,
                )
                currency = str(node.get("priceCurrency", "")).upper()
                try:
                    amount = Decimal(str(price))
                    valid = (
                        amount.is_finite()
                        and amount >= 0
                        and re.fullmatch("[A-Z]{3}", currency)
                    )
                except InvalidOperation:
                    valid = False
                if valid:
                    found.append(
                        {
                            "title": title,
                            "price": str(amount),
                            "currency": currency,
                            "url": url,
                            "source": urllib.parse.urlsplit(url).hostname,
                            "availability": str(
                                node.get("availability", "not stated")
                            ).rsplit("/", 1)[-1],
                            "warning": "Page-listed price; shipping, tax and variants may differ.",
                        }
                    )
            for key in ("@graph", "mainEntity", "itemListElement", "item"):
                if key in node:
                    visit(node[key], title)

    for raw in re.findall(
        r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        re.I | re.S,
    ):
        try:
            visit(json.loads(raw))
        except (ValueError, TypeError):
            continue
    return found[:5]


def inspect_product(url: str) -> dict:
    checked = dt.datetime.now(dt.timezone.utc).isoformat()
    result = {
        "url": url,
        "checked_at": checked,
        "products": [],
        "status": "unavailable",
    }
    try:
        url = public_url(url)
        origin = urllib.parse.urlsplit(url)
        robots = urllib.robotparser.RobotFileParser()
        try:
            robots.parse(fetch(f"https://{origin.netloc}/robots.txt").splitlines())
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            robots.parse([])
        if not robots.can_fetch("JarvisShopping", url):
            result.update(
                status="blocked", warning="Site robots policy disallows this page."
            )
            return result
        page = fetch(url)
        products = parse_prices(page, url)
        for p in products:
            p["checked_at"] = checked
        result.update(
            products=products,
            status="verified" if products else "unavailable",
            warning=""
            if products
            else "No explicit product price and currency could be verified on this page.",
        )
    except urllib.error.HTTPError as exc:
        result.update(
            status="blocked" if exc.code in (401, 403, 429) else "unavailable",
            warning=f"Site returned HTTP {exc.code}; access restrictions respected.",
        )
    except Exception:
        result["warning"] = (
            "Page or robots policy unavailable, unsupported, or unsafe; no price inferred."
        )
    return result


def search_products(query: str) -> dict:
    query = query.strip()[:240]
    checked = dt.datetime.now(dt.timezone.utc).isoformat()
    if not query:
        return {
            "query": query,
            "checked_at": checked,
            "results": [],
            "warning": "Enter a product or public HTTPS product URL.",
        }
    try:
        if query.startswith("https://"):
            urls = [query]
        else:
            search = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode(
                {"q": query + " buy price"}
            )
            # Respect discovery provider robots policy too.
            robots = urllib.robotparser.RobotFileParser()
            robots.parse(fetch("https://html.duckduckgo.com/robots.txt").splitlines())
            if not robots.can_fetch("JarvisShopping", search):
                raise ValueError("Search access blocked")
            page = fetch(search)
            urls = []
            for href in re.findall(
                r'<a\b[^>]*class=["\'][^"\']*result__a[^"\']*["\'][^>]*href=["\']([^"\']+)',
                page,
                re.I,
            ):
                target = html.unescape(href)
                if target.startswith("//"):
                    target = "https:" + target
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)
                target = params.get("uddg", [target])[0]
                if target.startswith("https://") and target not in urls:
                    urls.append(target)
                if len(urls) == 4:
                    break
            if not urls:
                raise ValueError("No public search results")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(inspect_product, urls))
        return {
            "query": query,
            "checked_at": checked,
            "results": results,
            "warning": "Prices are page observations, not live checkout guarantees.",
        }
    except Exception:
        return {
            "query": query,
            "checked_at": checked,
            "results": [],
            "warning": "Search unavailable or blocked. Try a public product page URL; no prices were invented.",
        }


def open_product_source(url: str, confirmed: bool, opener=None) -> dict:
    """Open only after explicit user confirmation; no checkout operation."""
    if confirmed is not True:
        return {"ok": False, "error": "Confirm opening this source first."}
    try:
        import subprocess

        from system import require_local
        from system.launcher import require_launch_allowed

        require_local()
        url = public_url(url)
        argv = ["xdg-open", url]
        require_launch_allowed(argv)
        runner = opener or subprocess.run
        result = runner(
            argv, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if result.returncode != 0:
            return {"ok": False, "error": "Browser could not open the source."}
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": "Source opening unavailable or URL blocked."}


def robots_allowed(url: str) -> bool:
    origin = urllib.parse.urlsplit(public_url(url))
    robots = urllib.robotparser.RobotFileParser()
    try:
        robots.parse(fetch(f"https://{origin.netloc}/robots.txt").splitlines())
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        robots.parse([])
    return robots.can_fetch("JarvisShopping", url)


async def search_products_browser(query: str, browser) -> dict:
    """Read JSON-LD using the existing Playwright context, without visual models.

    Temporary backend tab only; never opens a product in the user's system browser.
    No login, challenge solving, checkout, or fallback around access blocks.
    """
    import asyncio

    from browser import detect_page_wall

    query = query.strip()[:240]
    checked = dt.datetime.now(dt.timezone.utc).isoformat()
    reply = {
        "query": query,
        "checked_at": checked,
        "results": [],
        "warning": "Prices are page observations; tax, shipping and variants may differ. Offer to open a source only after explicit user approval.",
    }
    if not query:
        reply["warning"] = "Provide a product name or public HTTPS product URL."
        return reply
    page = None
    try:
        async with browser._lock:
            await browser._get_page()
            page = await browser._context.new_page()

            async def guard(route):
                try:
                    await asyncio.to_thread(public_url, route.request.url)
                    if (
                        route.request.is_navigation_request()
                        and not await asyncio.to_thread(
                            robots_allowed, route.request.url
                        )
                    ):
                        await route.abort()
                        return
                    await route.continue_()
                except Exception:
                    await route.abort()

            await page.route("**/*", guard)

            async def read(url):
                observation = {
                    "url": url,
                    "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "products": [],
                    "status": "unavailable",
                }
                try:
                    await asyncio.to_thread(public_url, url)
                    if not await asyncio.to_thread(robots_allowed, url):
                        observation.update(
                            status="blocked",
                            warning="Site robots policy disallows this page.",
                        )
                        return observation
                    response = await page.goto(
                        url, wait_until="domcontentloaded", timeout=15000
                    )
                    final = await asyncio.to_thread(public_url, page.url)
                    wall = detect_page_wall(
                        await page.title(),
                        await page.locator("body").inner_text(timeout=5000),
                    )
                    if (response and response.status >= 400) or wall:
                        observation.update(
                            status="blocked",
                            warning=f"Access blocked: {wall or response.status}; no bypass attempted.",
                        )
                        return observation
                    content = await page.content()
                    if len(content.encode()) > MAX_BYTES:
                        raise ValueError("Page exceeds size limit")
                    products = parse_prices(content, final)
                    for product in products:
                        product["checked_at"] = observation["checked_at"]
                    observation.update(
                        url=final,
                        products=products,
                        status="verified" if products else "unavailable",
                        warning=""
                        if products
                        else "No explicit product price and currency verified.",
                    )
                except Exception:
                    observation["warning"] = (
                        "Page or robots policy unavailable or unsafe; no price inferred."
                    )
                return observation

            if query.startswith("https://"):
                urls = [query]
            else:
                discovery = (
                    "https://html.duckduckgo.com/html/?"
                    + urllib.parse.urlencode({"q": query + " buy price"})
                )
                observed = await read(discovery)
                if observed["status"] == "blocked" or observed.get(
                    "warning", ""
                ).startswith("Page or"):
                    reply["results"] = [observed]
                    return reply
                hrefs = await page.locator("a.result__a").evaluate_all(
                    "nodes => nodes.map(n => n.href).slice(0, 4)"
                )
                urls = []
                for href in hrefs:
                    target = urllib.parse.parse_qs(
                        urllib.parse.urlsplit(href).query
                    ).get("uddg", [href])[0]
                    if target.startswith("https://") and target not in urls:
                        urls.append(target)
            for url in urls[:4]:
                reply["results"].append(await read(url))
            if not reply["results"]:
                reply["warning"] = (
                    "No accessible public product results; no prices invented."
                )
    except Exception:
        reply["warning"] = "Playwright browser unavailable; no prices invented."
    finally:
        if page is not None:
            import contextlib

            with contextlib.suppress(Exception):
                await page.close()
    return reply
