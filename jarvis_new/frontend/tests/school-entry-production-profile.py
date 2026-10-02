"""Profile an explicitly supplied private production export with mocked IPC.

Build the isolated source with the direct Next CLI, never the repository's build
wrapper. This runner serves only the supplied export, launches headless Chromium,
and shuts down its private server/browser after the unrecorded timing samples.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from playwright.async_api import async_playwright

SPEC = importlib.util.spec_from_file_location(
    "cinematic_evidence", Path(__file__).with_name("school-entry-cinematic-evidence.py")
)
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    directories = {args.label: args.directory}
    if args.reference_directory:
        assert args.label != "baseline"
        directories = {"baseline": args.reference_directory, **directories}
    for directory in directories.values():
        assert (directory / "index.html").is_file(), directory
    servers = {
        label: ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(EVIDENCE.ENTRY.SCHOOL.QuietHandler, directory=str(directory)),
        )
        for label, directory in directories.items()
    }
    for server in servers.values():
        Thread(target=server.serve_forever, daemon=True).start()
    urls = {
        label: f"http://127.0.0.1:{server.server_port}"
        for label, server in servers.items()
    }
    origins = {urlparse(url).netloc for url in urls.values()}
    original_install = EVIDENCE.ENTRY.EvidenceDesktop.install

    async def install(desktop):
        async def route_request(route):
            if urlparse(route.request.url).netloc in origins:
                await route.continue_()
            else:
                await route.abort()

        await desktop.page.route("**/*", route_request)
        await original_install(desktop)
        await desktop.page.add_init_script(EVIDENCE.COST_PROBE)

    EVIDENCE.ENTRY.EvidenceDesktop.install = install
    report = {
        "label": args.label,
        "exports": {label: str(path.resolve()) for label, path in directories.items()},
        "urls": urls,
        "environment": "isolated production static export; headless Chromium; mocked native and bridge",
        "method": "Separate unrecorded contexts; 400ms settle after hydration; three cases; paired source order alternates by repetition and case",
        "limitations": [
            "Synthetic Chromium scheduling does not establish native WebKit/KWin FPS.",
            "The cross-monitor fixture includes a 900ms delayed window move.",
            "Entry callback cost is CPU-side submission, not GPU completion or compositor cost.",
            "The shared host retains normal background system/application load.",
        ],
        "results": [],
    }

    def save(result):
        report["results"].append(result)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(result), flush=True)

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                report["browser"] = {"name": "Chromium", "version": browser.version}
                for url in urls.values():
                    context, _, _ = await EVIDENCE.ENTRY.create_page(browser, url)
                    await context.close()
                for repetition in range(1, args.repetitions + 1):
                    for case, (dpr, cross) in enumerate(
                        ((1, False), (2, False), (1, True))
                    ):
                        order = list(urls)
                        if (repetition + case) % 2 == 0:
                            order.reverse()
                        for label in order:
                            result = await EVIDENCE.performance(
                                browser, urls[label], dpr, cross, repetition
                            )
                            result["source"] = label
                            save(result)
                url = urls[args.label]
                if args.lifecycle:
                    for job in (
                        lambda: EVIDENCE.ENTRY.lifecycle(browser, url),
                        lambda: EVIDENCE.ENTRY.lifecycle(browser, url, hidden=True),
                        lambda: EVIDENCE.ENTRY.reversal(browser, url, "compress"),
                        lambda: EVIDENCE.ENTRY.reversal(browser, url, "forge"),
                        lambda: EVIDENCE.ENTRY.return_reentry(browser, url),
                        lambda: EVIDENCE.ENTRY.reduced(browser, url),
                        lambda: EVIDENCE.ENTRY.delayed_dock(browser, url),
                        lambda: EVIDENCE.ENTRY.reveal_curve(browser, url),
                    ):
                        save(await job())
                if args.media:
                    media = args.output / "media"
                    media.mkdir(exist_ok=True)
                    save(await EVIDENCE.ENTRY.video(browser, url, media))
                    for dpr in (1, 2):
                        save(await EVIDENCE.ENTRY.capture(browser, url, media, dpr))
                    fractions = args.output / "fractions"
                    fractions.mkdir(exist_ok=True)
                    save(
                        await EVIDENCE.fraction_stills(
                            browser,
                            argparse.Namespace(
                                stage="after", url=url, output=fractions
                            ),
                        )
                    )
            finally:
                await browser.close()
    finally:
        for server in servers.values():
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--reference-directory", type=Path)
    parser.add_argument("--lifecycle", action="store_true")
    parser.add_argument("--media", action="store_true")
    asyncio.run(main(parser.parse_args()))
