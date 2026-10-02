"""Headless Insights integration checks with synthetic data and mocked native IPC.

uv run --no-sync python frontend/tests/insights-smoke.py --url http://127.0.0.1:4061
Never opens a real desktop window or contacts the live bridge/tracker.
"""

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

SPEC = importlib.util.spec_from_file_location(
    "suit_smoke", Path(__file__).with_name("suit-diagnostics-smoke.py")
)
SUIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUIT)


def usage(period):
    totals = {
        "total_tokens": 12345678,
        "estimated_cost_usd": 42.123456789,
        "input_tokens": 2000000,
        "output_tokens": 1000000,
        "cache_read_tokens": 8000000,
        "cache_write_tokens": 500000,
        "reported_token_subtotal": 11500000,
        "unattributed_tokens": 845678,
        "missing_pricing": False,
        "unpriced_models": [],
    }
    return {
        "source_mode": "ccusage",
        "updated_at": "2026-10-02T01:30:00Z",
        "timezone": "Asia/Singapore",
        "range": {
            "key": period,
            "label": f"Synthetic {period} report",
            "start": "2026-09-26",
            "end": "2026-10-03",
        },
        "source_info": {
            "name": "ccusage",
            "version": "20.0.26",
            "pricing_mode": "DEFAULT",
            "timezone": "Asia/Singapore",
            "status": "ready",
            "snapshot_at": "2026-10-02T01:29:00Z",
        },
        "summary": totals,
        "apps": [dict(totals, name="Codex", app="codex")],
        "models": [
            dict(totals, name="gpt-6-astra", model="gpt-6-astra", total_tokens=None),
            dict(
                totals,
                name="unknown",
                model="unknown",
                total_tokens=None,
                estimated_cost_usd=None,
            ),
        ],
        "timeline": [
            dict(
                totals,
                date=f"2026-09-{day}",
                total_tokens=800000 + (day - 26) * 300000,
                estimated_cost_usd=4 + (day - 26) * 1.7,
            )
            for day in range(26, 31)
        ]
        + [
            dict(totals, date="2026-10-01"),
            dict(totals, date="2026-10-02", total_tokens=5000000),
        ],
        "warnings": [
            "Synthetic fixture. Model totals are unavailable; daily totals remain authoritative."
        ],
        "accounting_note": "Preserve reported totals independently of category attribution.",
    }


class Desktop(SUIT.Desktop):
    def __init__(self, page):
        super().__init__(page)
        self.usage_requests = []
        self.paper_requests = 0
        self.offline = False

    async def bridge(self, route):
        parsed = urlparse(route.request.url)
        if parsed.path == "/insights/usage":
            self.usage_requests.append(parsed.query)
            period = parse_qs(parsed.query)["range"][0]
            return await route.fulfill(
                status=503 if self.offline else 200,
                json={"ok": False} if self.offline else usage(period),
                headers={"Access-Control-Allow-Origin": "*"},
            )
        if parsed.path == "/paper-market":
            self.paper_requests += 1
            return await route.fulfill(
                status=503,
                json={"ok": False},
                headers={"Access-Control-Allow-Origin": "*"},
            )
        return await super().bridge(route)


async def check(browser, url, output, school=False, width=1600):
    page = await browser.new_page(
        viewport={"width": 1600, "height": 64 if school else 900},
        screen={"width": 1600, "height": 900},
        reduced_motion="reduce",
    )
    page.set_default_timeout(20000)
    desktop = Desktop(page)
    desktop.mode = "school" if school else "normal"
    await page.add_init_script(SUIT.PHYSICAL_SCREEN)
    await desktop.install()
    await page.goto(url, wait_until="domcontentloaded")
    if school:
        await page.get_by_role("button", name="Applications", exact=True).click()
        await page.get_by_role("menu", name="Apps", exact=True).wait_for()
    else:
        await desktop.ready()
        if width != 1600:
            await page.set_viewport_size({"width": width, "height": 900})
    assert not desktop.usage_requests and not desktop.paper_requests, (
        "Closed panels fetched data"
    )
    await page.locator('[data-insights-launch="usage"]').click()
    dialog = page.get_by_role("dialog", name="Insights", exact=True)
    await dialog.wait_for()
    await page.get_by_text("REPORT READY", exact=True).wait_for()
    assert await page.get_by_text("12,345,678", exact=True).count() >= 1
    assert await page.get_by_text("Reported: 42.123456789 USD", exact=True).count() == 1
    assert await page.get_by_text("unknown", exact=True).count() >= 1
    assert await page.locator(".sbar-root" if school else ".hud").evaluate(
        "e => e.inert"
    )
    assert not desktop.paper_requests
    bounds = await dialog.bounding_box()
    assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
    if school:
        await page.wait_for_function("innerHeight > 600")
        await asyncio.wait_for(desktop.focus_acquired.wait(), 10)
        assert desktop.focus_lease is not None
    await page.screenshot(
        path=str(output / f"insights-{'school' if school else width}.png")
    )

    # Disclosure controls remain in natural tab order before the trap wraps.
    disclosures = page.locator('[role="dialog"] summary')
    assert await disclosures.count() >= 2
    await disclosures.nth(0).focus()
    await page.keyboard.press("Tab")
    assert (
        await page.evaluate("document.activeElement?.getAttribute('aria-label')")
        != "Close Insights"
    )
    await disclosures.last.focus()
    await page.keyboard.press("Enter")
    assert await disclosures.last.evaluate("e => e.parentElement.open")
    await page.keyboard.press("Tab")
    assert await page.get_by_role("button", name="Close Insights", exact=True).evaluate(
        "e => e === document.activeElement"
    )

    await page.get_by_role("button", name="Weekly", exact=True).click()
    await page.get_by_text(
        "Synthetic week report · Asia/Singapore", exact=True
    ).wait_for()
    assert "range=week" in desktop.usage_requests[-1]
    for name in ("Monthly", "All time"):
        await page.get_by_role("button", name=name, exact=True).click()
        await page.get_by_text("REPORT READY", exact=True).wait_for()
    before = len(desktop.usage_requests)
    await page.get_by_role(
        "tab", name="02 Paper Market Portfolio & performance", exact=True
    ).click()
    await page.get_by_text(
        "Paper market service is unavailable. Retrying automatically.", exact=True
    ).wait_for()
    assert len(desktop.usage_requests) == before
    assert 1 <= desktop.paper_requests <= 2  # Dev StrictMode may replay the mount.
    # Returning to the inactive tab mounts a fresh reader; failure is not $0.
    desktop.offline = True
    await page.get_by_role(
        "tab", name="01 AI Usage Tokens & estimated spend", exact=True
    ).click()
    await page.get_by_text("Usage report unavailable", exact=True).wait_for()
    assert await page.get_by_text("12,345,678", exact=True).count() == 0
    await page.screenshot(
        path=str(output / f"insights-offline-{'school' if school else width}.png")
    )

    if school:
        # Voice suit request while Insights holds a lease keeps the native
        # surface expanded and the taskbar inert until the suit is dismissed.
        count = len(desktop.calls)
        await desktop.suit("open")
        await dialog.wait_for(state="detached")
        assert await page.locator(".sbar-root").evaluate("e => e.inert")
        assert not any(
            name == "school_menu" and args.get("extra") == 0
            for name, args in desktop.calls[count:]
        )
        await desktop.suit("close")
        await page.wait_for_function("innerHeight === 64")
        assert not await page.locator(".sbar-root").evaluate("e => e.inert")
        assert desktop.focus_lease is None
    else:
        await page.keyboard.press("Escape")
        await dialog.wait_for(state="detached")
        assert not await page.locator(".hud").evaluate("e => e.inert")
        assert not any(name == "hide_overlay" for name, _ in desktop.calls)
        assert await page.locator('[data-insights-launch="usage"]').evaluate(
            "e => e === document.activeElement"
        )
    frozen = (len(desktop.usage_requests), desktop.paper_requests)
    await page.wait_for_timeout(1200)
    assert frozen == (len(desktop.usage_requests), desktop.paper_requests)
    assert not desktop.errors, desktop.errors
    await page.close()
    return {
        "school": school,
        "width": width,
        "usage_requests": frozen[0],
        "paper_requests": frozen[1],
        "errors": desktop.errors,
    }


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            result = [
                await check(browser, args.url, args.output),
                await check(browser, args.url, args.output, width=520),
                await check(browser, args.url, args.output, school=True),
            ]
        finally:
            await browser.close()
    (args.output / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("/tmp/jarvis-insights-review")
    )
    asyncio.run(main(parser.parse_args()))
