"""Exercise the exported calendar dashboard against isolated bridge fixtures."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:4012/calendar.html")
parser.add_argument(
    "--screenshots", type=Path, default=Path("/tmp/jarvis-calendar-review")
)
args = parser.parse_args()
args.screenshots.mkdir(parents=True, exist_ok=True)
fixture = {
    "ok": True,
    "enabled": True,
    "timezone": "Asia/Singapore",
    "whatsapp": {
        "enabled": True,
        "connected": True,
        "detail": "Authenticated browser scan completed.",
    },
    "calendar": {
        "configured": True,
        "connected": True,
        "account": "primary",
        "calendar_id": "primary",
        "detail": "Calendar event access verified.",
    },
    "presence": {
        "state": "input",
        "detail": "Reminders wait for sustained keyboard or mouse activity.",
    },
    "reminders": {"deferred": 2, "detail": "Queued reminders survive restarts."},
    "pending": [
        {
            "id": "wa:review:1",
            "title": "Tentative team dinner",
            "date": "2026-11-09",
            "start": "19:00",
            "source": "whatsapp",
            "sender": "Alex",
            "status": "pending",
            "reason": "A tentative plan needs your review.",
        }
    ],
    "retry": [
        {
            "id": "wa:retry:1",
            "title": "Dentist appointment",
            "date": "2026-11-10",
            "start": "10:30",
            "status": "retry",
            "source": "whatsapp",
        }
    ],
    "recent": [
        {
            "id": "wa:added:1",
            "title": "Project review",
            "when": "Monday 9 November 3 PM",
            "status": "added",
            "google_id": "fixture-calendar-event-123",
            "source": "whatsapp",
            "sender": "Maya",
        }
    ],
}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1360, "height": 1600})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    state = {"offline": False, "reject": True, "writes": 0, "denied": False}
    data = copy.deepcopy(fixture)

    def bridge(route):
        if route.request.method == "OPTIONS":
            return route.fulfill(
                status=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                },
            )
        if state["offline"]:
            route.abort()
        elif state["denied"]:
            route.fulfill(
                headers={"Access-Control-Allow-Origin": "*"},
                status=403,
                json={
                    "ok": False,
                    "error": "Calendar activity requires the owner session.",
                },
            )
        elif route.request.method == "POST":
            assert route.request.url.endswith("/auto-calendar/review")
            assert route.request.post_data_json == {
                "id": "wa:review:1",
                "action": "dismiss",
            }
            state["writes"] += 1
            if state["reject"]:
                route.fulfill(
                    headers={"Access-Control-Allow-Origin": "*"},
                    status=404,
                    json={
                        "ok": False,
                        "error": "This item is no longer pending. Refresh the calendar activity.",
                    },
                )
            else:
                data["pending"] = []
                route.fulfill(
                    headers={"Access-Control-Allow-Origin": "*"}, json={"ok": True}
                )
        else:
            route.fulfill(headers={"Access-Control-Allow-Origin": "*"}, json=data)

    page.route("http://127.0.0.1:4317/**", bridge)
    page.goto(args.url)
    page.get_by_role("heading", name="Tentative team dinner").wait_for()
    page.get_by_text("Calendar event access verified.").wait_for()
    page.get_by_role("heading", name="Waiting to retry").wait_for()
    assert page.get_by_role("button", name="Dismiss Dentist appointment").count() == 0

    def no_overflow():
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator("main").evaluate("(el) => el.scrollWidth <= el.clientWidth")

    no_overflow()
    page.screenshot(path=str(args.screenshots / "desktop.png"))
    # Keyboard can reach and activate the real dismiss handler.
    button = page.get_by_role("button", name="Dismiss Tentative team dinner")
    button.focus()
    page.keyboard.press("Enter")
    page.get_by_role("status").filter(has_text="no longer pending").wait_for()
    assert state["writes"] == 1
    assert button.is_enabled()
    state["reject"] = False
    button.focus()
    page.keyboard.press("Enter")
    page.get_by_text("Suggestion dismissed.", exact=True).wait_for()
    page.get_by_role("heading", name="All clear").wait_for()
    assert state["writes"] == 2
    page.set_viewport_size({"width": 390, "height": 844})
    no_overflow()
    page.screenshot(path=str(args.screenshots / "mobile.png"))
    page.locator("main").evaluate("e => e.scrollTop = e.scrollHeight")
    page.screenshot(path=str(args.screenshots / "mobile-details.png"))
    state["offline"] = True
    page.get_by_text("Bridge unavailable", exact=True).wait_for(timeout=12000)
    page.get_by_text("Showing the last received activity;", exact=False).wait_for()
    page.screenshot(path=str(args.screenshots / "offline.png"))
    assert page.get_by_role("heading", name="Project review").is_visible()
    state["offline"] = False
    state["denied"] = True
    page.get_by_text(
        "Calendar activity requires the owner session.", exact=False
    ).wait_for(timeout=12000)
    assert page.get_by_role("heading", name="Project review").count() == 0, (
        "Owner activity leaked into guest state"
    )
    assert page.get_by_text("Bridge unavailable", exact=True).count() == 0
    page.screenshot(path=str(args.screenshots / "owner-required.png"))
    assert not errors, errors
    browser.close()
print(
    json.dumps(
        {
            "checks": [
                "desktop render",
                "narrow render without overflow",
                "keyboard dismiss",
                "stale review error",
                "successful dismiss refresh",
                "retry has no dismiss",
                "offline retains audit data",
            ],
            "screenshots": str(args.screenshots),
        },
        indent=2,
    )
)
