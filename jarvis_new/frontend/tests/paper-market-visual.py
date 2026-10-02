"""Headless checks for the isolated, explicitly synthetic React panel bundle."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--directory", required=True, type=Path)
output = parser.parse_args().directory.resolve()
report = {"fixture": "SYNTHETIC QA FIXTURE — NOT LIVE PERFORMANCE", "checks": []}


def check(page, label):
    body = page.locator("body").inner_text()
    assert "SYNTHETIC QA FIXTURE — NOT LIVE PERFORMANCE" in body
    assert "NaN" not in body and "undefined" not in body and "Infinity" not in body
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), label
    # Full table width deliberately scrolls inside its own viewport on mobile.
    overflowing = page.locator(
        "#root *"
    ).evaluate_all("""elements => elements.filter(el => {
      const rect = el.getBoundingClientRect();
      return rect.width && (rect.left < -1 || rect.right > innerWidth + 1) && !el.closest('.tableScroll');
    }).map(el => ({tag:el.tagName, class:el.className, text:el.textContent.slice(0,90)}))""")
    assert not overflowing, overflowing
    report["checks"].append(label)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(
        viewport={"width": 1240, "height": 1080},
        device_scale_factor=1,
        timezone_id="Asia/Singapore",
    )
    errors = []
    network = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: network.append(request.url))
    page.route(
        "**/*",
        lambda route: (
            route.continue_()
            if route.request.url.startswith("file://")
            else route.abort()
        ),
    )
    page.add_init_script("""{
      const ActualDate = Date;
      const fixed = ActualDate.parse('2026-10-05T21:00:00Z');
      window.Date = class extends ActualDate {
        constructor(...args) { super(...(args.length ? args : [fixed])); }
        static now() { return fixed; }
      };
    }""")
    page.goto((output / "index.html").as_uri())
    page.get_by_role("heading", name="Paper market").wait_for()
    page.get_by_text("3d 17h remaining", exact=True).wait_for()
    assert page.locator("tbody tr").count() == 3
    assert page.locator(".decisions > li").count() == 3
    assert page.locator(".fills > li").count() == 5
    assert (
        page.locator(".valuationWarning")
        .inner_text()
        .startswith("Last observed valuation")
    )
    assert "$1,022.00" in page.locator(".equityMetric").inner_text()
    assert "+$22.00" in page.locator(".metrics").inner_text()
    assert "-$15.00" in page.locator("tbody").inner_text()
    assert "7h ago" in page.locator("tbody").inner_text()
    assert "Unknown" in page.locator(".quote").filter(has_text="GOOGL").inner_text()
    check(page, "desktop_equity_populated")
    page.screenshot(path=str(output / "desktop-equity.png"), full_page=True)

    # Exercise the actual React click handler, rather than replacing chart markup.
    page.get_by_role("button", name="P&L", exact=True).click()
    assert (
        page.get_by_role("button", name="P&L", exact=True).get_attribute("aria-pressed")
        == "true"
    )
    page.get_by_role("heading", name="Total profit / loss", exact=True).wait_for()
    values = page.locator(".chartPoint title").all_text_contents()
    assert len(values) == 6, values
    assert [text.split(" · ")[-1] for text in values] == [
        "$0.00",
        "$0.00",
        "+$8.00",
        "+$16.00",
        "+$11.00",
        "+$22.00",
    ]
    assert page.locator(".chartLine").count() == 2, (
        "Missing valuation must split the line"
    )
    assert [
        len(points.split())
        for points in page.locator(".chartLine").evaluate_all(
            "lines => lines.map(line => line.getAttribute('points'))"
        )
    ] == [3, 3]
    report["pnl_observed_values"] = values
    check(page, "desktop_real_button_pnl_and_missing_mark_gap")
    page.screenshot(path=str(output / "desktop-pnl.png"), full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    check(page, "mobile_390px_populated_no_page_overflow")
    page.screenshot(path=str(output / "mobile-pnl.png"), full_page=True)
    assert page.locator(".tableScroll").evaluate(
        "el => el.scrollWidth > el.clientWidth"
    )
    page.locator(".tableScroll").evaluate("el => el.scrollLeft = el.scrollWidth")
    last_column = page.locator("tbody tr td:last-child")
    assert last_column.all_text_contents() == ["+$20.00", "-$15.00", "+$12.00"]
    assert last_column.evaluate_all("""cells => cells.every(cell => {
      const r = cell.getBoundingClientRect();
      const viewport = cell.closest('.tableScroll').getBoundingClientRect();
      return r.left >= viewport.left - 1 && r.right <= viewport.right + 1;
    })"""), "Final P&L column is unreachable"
    check(page, "mobile_final_holdings_column_reachable_after_scroll")
    page.screenshot(path=str(output / "mobile-holdings-scrolled.png"), full_page=True)

    # Remove one mark without changing the known cash or fabricating totals.
    page.evaluate("""() => {
      const fixture = structuredClone(window.__PAPER_MARKET_QA__);
      Object.assign(fixture.account, {equity_usd:null,total_pnl_usd:null,total_return_pct:null,unrealized_pnl_usd:null,valuation_complete:false});
      Object.assign(fixture.holdings[1], {mark_price_usd:null,market_value_usd:null,unrealized_pnl_usd:null,quote_at:null});
      window.setPaperSnapshot(fixture);
    }""")
    page.get_by_text("Valuation incomplete", exact=False).wait_for()
    assert "Unknown" in page.locator(".equityMetric").inner_text()
    assert "$425.00" in page.locator(".metrics").inner_text()
    assert "$0.00" not in page.locator(".metrics").inner_text()
    assert (
        page.locator("tbody tr").nth(1).locator("td:last-child").inner_text()
        == "Unknown"
    )
    check(page, "mobile_missing_holding_mark_stays_unknown")
    page.screenshot(path=str(output / "mobile-unknown.png"), full_page=True)
    assert not errors, errors
    assert all(url.startswith("file://") for url in network), network
    report["console_errors"] = errors
    report["network_requests"] = network
    report["screenshots"] = [str(path) for path in sorted(output.glob("*.png"))]
    browser.close()

(output / "report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
