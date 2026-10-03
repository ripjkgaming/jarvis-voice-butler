"""Exercise the real headless browser with an isolated synthetic YT1s flow."""

from urllib.parse import urlparse

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

import youtube_download as yt
from browser import _discover_browser_binary

MP4 = b"\x00\x00\x00\x18ftypmp42synthetic-only-test-file"
VIDEO = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
FRAME = "https://embed.dlsrv.online/v1/full?videoId=aaaaaaaaaaa"


@pytest_asyncio.fixture
async def website():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, executable_path=_discover_browser_binary()
        )
        context = await browser.new_context(accept_downloads=True)
        requested = []
        mode = {"blocked": False, "two_phase": False}

        async def serve(route):
            url = route.request.url
            requested.append(url)
            parsed = urlparse(url)
            if url == yt.CONVERTER_URL:
                html = """<form onsubmit="event.preventDefault();location.href='/download/'">
                  <input placeholder="Search or paste Youtube link here"><button>Convert</button></form>"""
                await route.fulfill(content_type="text/html", body=html)
            elif parsed.hostname == "yt1s.com.co" and parsed.path == "/download/":
                await route.fulfill(
                    content_type="text/html", body=f'<iframe src="{FRAME}"></iframe>'
                )
            elif url == FRAME:
                script = (
                    "fetch('/api/verify').then(r=>{document.getElementById('error').textContent='Verification is taking longer than expected.'})"
                    if mode["blocked"]
                    else ""
                )
                html = (
                    """<a href="https://advertisement.example/setup.exe" target="_blank">Download Free Now</a>
                  <div id="error"></div><button onclick="document.querySelector('table').hidden=false">Video</button>
                  <table hidden><tbody>
                  <tr><td>2160p mp4</td><td><a href="https://advertisement.example/setup.exe">Install</a></td></tr>
                  <tr><td>1080p mp4</td><td><button onclick="location.href='/files/video.mp4'">Download</button></td></tr>
                  <tr><td>720p mp4</td><td><button onclick="location.href='/files/720.mp4'">Download</button></td></tr>
                  </tbody></table>"""
                    + f"<script>{script}</script>"
                )
                if mode["two_phase"]:
                    html = html.replace(
                        "location.href='/files/video.mp4'",
                        "document.getElementById('result').hidden=false",
                    )
                    html += '<div id="result" hidden><p>Conversion complete</p><button onclick="window.top.location.href=\'https://embed.dlsrv.online/files/video.mp4\'">Download Now</button></div>'
                await route.fulfill(content_type="text/html", body=html)
            elif (
                parsed.hostname == "embed.dlsrv.online" and parsed.path == "/api/verify"
            ):
                await route.fulfill(
                    status=403,
                    content_type="application/json",
                    body='{"error":"verification failed"}',
                )
            elif (
                parsed.hostname == "embed.dlsrv.online"
                and parsed.path == "/files/video.mp4"
            ):
                await route.fulfill(
                    content_type="video/mp4",
                    headers={
                        "Content-Disposition": 'attachment; filename="synthetic.mp4"'
                    },
                    body=MP4,
                )
            else:
                await route.abort()

        await context.route("**/*", serve)
        page = await context.new_page()
        try:
            yield page, requested, mode
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_converter_iframe_download_signal_and_saved_file(website, tmp_path):
    page, requests, _ = website
    starts = []
    download = await yt.run_site_flow(page, VIDEO)
    assert download.suggested_filename == "synthetic.mp4"
    target = await yt.save_download(
        download, tmp_path, "Synthetic", lambda path: starts.append(path)
    )
    assert target.read_bytes() == MP4
    assert starts == [target]
    assert any(url.endswith("/files/video.mp4") for url in requests)
    assert not any(
        "advertisement" in url or url.endswith("720.mp4") for url in requests
    )


@pytest.mark.asyncio
async def test_iframe_verification_403_never_starts_download(website):
    page, requests, mode = website
    mode["blocked"] = True
    transfers = []
    page.on("download", lambda item: transfers.append(item))
    with pytest.raises(yt.DownloadError, match="verification"):
        await yt.run_site_flow(page, VIDEO)
    assert not transfers
    assert not any("/files/" in url for url in requests)


@pytest.mark.asyncio
async def test_conversion_result_requires_separate_download_now_button(
    website, tmp_path
):
    page, requests, mode = website
    mode["two_phase"] = True
    transfers = []
    page.on("download", lambda item: transfers.append(item))
    download = await yt.run_site_flow(page, VIDEO)
    target = await yt.save_download(download, tmp_path, "Synthetic", lambda path: None)
    assert target.read_bytes() == MP4
    assert len(transfers) == 1
    assert sum(url.endswith("/files/video.mp4") for url in requests) == 1
