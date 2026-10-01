#!/usr/bin/env python3
"""Headless STARK companion QA using synthetic data and a sealed network.

Run after the coordinator exports the frontend: python desktop/verify_frontend.py
Requires the installed Python Playwright package and cached Chromium. Nothing
is sent to the running bridge, native shell, mail service, or voice agent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PREVIEWS = ROOT / "desktop" / "previews"
STAMP = 1790857800
PROJECTS = [
    {
        "id": "fixture-solar",
        "kind": "research",
        "title": "Solar storage research — performance, safety, and long-term deployment notes",
        "status": "done",
        "created_at": STAMP,
        "updated_at": STAMP,
        "model": "research-engine",
        "summary": "A synthetic research dossier for visual verification only.",
    },
    {
        "id": "fixture-sensor",
        "kind": "research",
        "title": "Distributed sensor network calibration and field deployment",
        "status": "running",
        "created_at": STAMP,
        "updated_at": STAMP,
        "progress": 64,
        "stage": "Comparing calibrated measurements across deployment scenarios",
    },
] + [
    {
        "id": f"fixture-project-{n}",
        "kind": "code" if n % 2 else "research",
        "title": f"Archive project {n:02d} — measurement and validation report",
        "status": ["done", "failed", "cancelled"][n % 3],
        "created_at": STAMP - n * 3600,
        "updated_at": STAMP,
    }
    for n in range(3, 16)
]
REPORT = (
    "# Energy storage field report\n\n"
    + "\n\n".join(
        f"## Observation {n}\n\n"
        "This is synthetic preview content. The holographic document retains a readable "
        "body measure, clear headings, and a scrollable sheet when reports exceed the viewport. "
        "Measured capacity, temperature and cycle count remain visible without losing the archive index.\n\n"
        "- Capacity: **94.8%** of nominal\n- Temperature: **26.3°C**\n- Status: verified in fixture only"
        for n in range(1, 25)
    )
    + "\n\nEND OF SYNTHETIC PROJECT REPORT"
)
DETAIL = PROJECTS[0] | {
    "documents": [
        {"name": "report.md", "title": "Field report", "text": REPORT},
        {
            "name": "measurements.log",
            "title": "Measurement log",
            "text": "Fixture only",
        },
    ]
}
DRAFTS = [
    {
        "id": f"fixture-draft-{n}",
        "sender": f"Research Coordination {n:02d} <team{n}@example.test>",
        "to": "long.department.name.for.readability.review@synthetic-example.test",
        "subject": "Re: Deployment review — measurement results, design changes and next steps",
        "created": STAMP - n * 600,
        "status": "announced" if n == 1 else "pending",
        "priority": "high" if n in (1, 4) else "normal",
        "summary": "Please review the measurements and share the next revision when ready.",
        "body": "Hello team,\n\n"
        + "\n\n".join(
            f"Review note {p}: The measurements are ready for review. "
            "I will include the revised schedule and the validation summary in the next update. "
            "This entire message is synthetic preview content and cannot be sent."
            for p in range(1, 13)
        )
        + "\n\nEND OF SYNTHETIC DRAFT REPLY",
    }
    for n in range(1, 13)
]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def inspect_bounds(page):
    return page.evaluate("""() => ({
      viewport: [innerWidth, innerHeight],
      document: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
      sections: Array.from(document.querySelectorAll('main > section')).map(e => {
        const r = e.getBoundingClientRect();
        return {name: e.getAttribute('aria-label'), x:r.x,y:r.y,width:r.width,height:r.height,
          scrollWidth:e.scrollWidth,clientWidth:e.clientWidth};
      }),
      fontsReady: document.fonts.status === 'loaded',
      animations: document.getAnimations().filter(a => a.playState === 'running').length,
    })""")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export",
        "--export-root",
        dest="export",
        type=Path,
        default=ROOT / "frontend" / "out",
    )
    args = parser.parse_args()
    assert (args.export / "projects.html").is_file(), "Build/export frontend first"
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(args.export))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    summary = {
        "export_root": str(args.export.resolve()),
        "export_sha256": {
            name: hashlib.sha256((args.export / name).read_bytes()).hexdigest()
            for name in ("projects.html", "drafts.html", "404.html")
        },
        "network": {"mocked_reads": 0, "blocked": [], "writes": [], "websockets": []},
        "cases": [],
    }
    network = summary["network"]
    failures = []

    def check(condition, label):
        if not condition:
            failures.append(label)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--disable-background-networking"]
            )
            for route_name in ("projects", "drafts", "404"):
                for size in ((1440, 900), (640, 900), (390, 844)):
                    for reduced in [False, True] if size == (1440, 900) else [True]:
                        mode = "reduced" if reduced else "normal"
                        label = f"{route_name}-{size[0]}x{size[1]}-{mode}"
                        context = browser.new_context(
                            viewport={"width": size[0], "height": size[1]},
                            reduced_motion="reduce" if reduced else "no-preference",
                            color_scheme="dark",
                            locale="en-SG",
                            timezone_id="Asia/Singapore",
                            service_workers="block",
                        )
                        errors = []

                        def handle_request(route):
                            request = route.request
                            url = urlparse(request.url)
                            if request.method not in ("GET", "HEAD"):
                                network["writes"].append(
                                    {"url": request.url, "method": request.method}
                                )
                                route.abort()
                                return
                            if request.url.startswith(origin + "/"):
                                route.continue_()
                                return
                            if url.hostname == "127.0.0.1" and url.port == 4317:
                                payload = None
                                if url.path == "/projects":
                                    payload = {"ok": True, "projects": PROJECTS}
                                elif url.path == "/projects/fixture-solar":
                                    payload = {"ok": True, "project": DETAIL}
                                elif url.path == "/projects/ui":
                                    since = parse_qs(url.query).get("since", [None])[0]
                                    payload = {
                                        "ok": True,
                                        "seq": 0 if since is None else 2,
                                        "commands": [],
                                    }
                                    if since == "0":
                                        payload["commands"] = [
                                            {
                                                "seq": 1,
                                                "action": "select",
                                                "project_id": "fixture-solar",
                                            },
                                            {
                                                "seq": 2,
                                                "action": "open_document",
                                                "index": 1,
                                            },
                                        ]
                                elif url.path == "/drafts":
                                    payload = {"ok": True, "drafts": DRAFTS}
                                if payload is not None:
                                    network["mocked_reads"] += 1
                                    route.fulfill(
                                        status=200,
                                        content_type="application/json",
                                        headers={"Access-Control-Allow-Origin": "*"},
                                        body=json.dumps(payload),
                                    )
                                    return
                            network["blocked"].append(request.url)
                            route.abort()

                        def block_websocket(ws):
                            network["websockets"].append(ws.url)
                            ws.close(code=1000, reason="Isolated visual verification")

                        context.route("**/*", handle_request)
                        context.route_web_socket("**/*", block_websocket)
                        page = context.new_page()
                        page.on(
                            "pageerror",
                            lambda error, found=errors: found.append(str(error)),
                        )
                        page.goto(
                            f"{origin}/{route_name}.html", wait_until="networkidle"
                        )
                        if route_name == "projects":
                            page.get_by_role(
                                "heading", name="Energy storage field report"
                            ).wait_for(timeout=10000)
                        elif route_name == "drafts":
                            page.get_by_role(
                                "region", name="Draft reply text"
                            ).wait_for()
                            check(
                                page.get_by_text("NOT SENT", exact=True).is_visible(),
                                f"{label}: sent-state visible",
                            )
                            check(
                                page.get_by_text("send it", exact=True).is_visible(),
                                f"{label}: voice phrases visible",
                            )
                        else:
                            page.get_by_role(
                                "heading", name="View not found"
                            ).wait_for()
                        page.evaluate("document.fonts.ready")
                        metrics = inspect_bounds(page)
                        check(
                            metrics["document"][0] <= size[0],
                            f"{label}: horizontal document overflow",
                        )
                        for section in metrics["sections"]:
                            check(
                                section["x"] >= 0
                                and section["x"] + section["width"] <= size[0] + 1,
                                f"{label}: {section['name']} outside viewport",
                            )
                            check(
                                section["scrollWidth"] <= section["clientWidth"] + 1,
                                f"{label}: {section['name']} horizontal content overflow",
                            )
                        check(not errors, f"{label}: browser runtime errors {errors}")
                        check(metrics["fontsReady"], f"{label}: local fonts not loaded")
                        if reduced:
                            check(
                                metrics["animations"] == 0,
                                f"{label}: animations active under reduced motion",
                            )
                        page.screenshot(
                            path=str(PREVIEWS / f"frontend-{label}.png"), full_page=True
                        )
                        if route_name in ("projects", "drafts"):
                            selector = (
                                "[class*='project-archive_sheet__']"
                                if route_name == "projects"
                                else "article, [aria-label='Draft reply text']"
                            )
                            scroll = page.locator(selector).evaluate_all("""elements => elements.map(e => {
                              e.scrollTop = e.scrollHeight;
                              return {height:e.clientHeight, content:e.scrollHeight, top:e.scrollTop};
                            })""")
                            metrics["longContentScroll"] = scroll
                            check(
                                scroll[-1]["height"] >= 80,
                                f"{label}: readable document height below 80px",
                            )
                            check(
                                any(s["top"] > 0 for s in scroll),
                                f"{label}: long content cannot scroll",
                            )
                            end_bounds = page.evaluate(
                                """marker => {
                              const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                              while(walker.nextNode()) {
                                const node = walker.currentNode, index = node.textContent.indexOf(marker);
                                if(index < 0) continue;
                                const range = document.createRange();
                                range.setStart(node,index); range.setEnd(node,index+marker.length);
                                const r = range.getBoundingClientRect();
                                return {x:r.x,y:r.y,width:r.width,height:r.height};
                              }
                              return null;
                            }""",
                                "END OF SYNTHETIC PROJECT REPORT"
                                if route_name == "projects"
                                else "END OF SYNTHETIC DRAFT REPLY",
                            )
                            metrics["endMarker"] = end_bounds
                            check(
                                end_bounds
                                and end_bounds["y"] >= 0
                                and end_bounds["y"] + end_bounds["height"] <= size[1],
                                f"{label}: final content line not reachable in viewport",
                            )
                            page.screenshot(
                                path=str(PREVIEWS / f"frontend-{label}-end.png"),
                                full_page=True,
                            )
                        summary["cases"].append(
                            {"name": label, "metrics": metrics, "errors": errors}
                        )
                        context.close()
            browser.close()
        check(not network["writes"], "Unexpected bridge/action write attempted")
        summary["failures"] = failures
        summary["passed"] = not failures
        (PREVIEWS / "frontend-verification.json").write_text(
            json.dumps(summary, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "passed": summary["passed"],
                    "cases": len(summary["cases"]),
                    "network": network,
                    "failures": failures,
                },
                indent=2,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
