"""Bridge-owned, headless MP4 downloads from YouTube.

Conversion is preparation. Only Playwright's real download event marks the
job as downloading. Jobs outlive a voice room, and every outcome reaches the
activity feed and notification system. Direct downloading is the default;
the original YT1s flow remains available as an explicit backend option.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from youtube_history import YouTubeHistory, _atomic_json, foreground_video

CONVERTER_URL = "https://yt1s.com.co/en228/youtube-to-mp4/"
CONVERTER_FRAME_HOST = "embed.dlsrv.online"
PREPARE_TIMEOUT_S = 90.0
TRANSFER_TIMEOUT_S = 30 * 60.0
VOICE_POLL_S = 0.75
VOICE_WAIT_S = PREPARE_TIMEOUT_S + 10.0
JOB_ID = re.compile(r"^youtube-[a-f0-9]{12}$")
ACTIVE_STATUSES = {"preparing", "downloading"}


class DownloadError(Exception):
    """A truthful, user-facing download failure."""


def choose_mp4_row(rows: list[dict]) -> int | None:
    """Prefer 1080p/720p MP4 with audio; never choose the site's software ad."""
    candidates = []
    for row in rows:
        text = str(row.get("text", "")).lower()
        if not re.search(r"\bmp4\b", text) or "download" not in text:
            continue
        if any(
            word in text
            for word in ("video only", "no audio", "without audio", "muted")
        ):
            continue
        resolution = re.search(r"\b(\d{3,4})p\b", text)
        height = int(resolution[1]) if resolution else 0
        # Avoid huge 4K transfers by default; prefer the best <= 1080p.
        rank = (0, -height) if 0 < height <= 1080 else (1, height or 9999)
        candidates.append((rank, int(row["index"])))
    return min(candidates)[1] if candidates else None


def classify_site_error(text: str, statuses: set[int]) -> str | None:
    body = str(text or "").lower()
    if (
        any(
            phrase in body
            for phrase in (
                "verification is taking longer",
                "verify you are human",
                "verify that you are human",
                "captcha",
                "verification failed",
            )
        )
        or 403 in statuses
    ):
        return (
            "YT1s couldn't complete its verification, Sir. The download hasn't started."
        )
    if any(
        phrase in body
        for phrase in (
            "video unavailable",
            "video is unavailable",
            "private video",
            "video not found",
            "removed by",
        )
    ):
        return "YT1s says this video is unavailable."
    if any(
        phrase in body
        for phrase in ("conversion failed", "failed to convert", "unable to convert")
    ):
        return "YT1s couldn't convert this video, Sir. The download hasn't started."
    return None


def downloads_directory() -> Path:
    override = os.environ.get("JARVIS_DOWNLOADS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    # Honor the desktop's relocated XDG Downloads folder without executing it.
    config = (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "user-dirs.dirs"
    )
    with contextlib.suppress(OSError):
        match = re.search(r'^XDG_DOWNLOAD_DIR="([^"\n]+)"$', config.read_text(), re.M)
        if match:
            value = match[1].replace("$HOME", str(Path.home()))
            if "$" not in value and Path(value).is_absolute():
                return Path(value)
    return Path.home() / "Downloads"


def _reservation(target: Path) -> Path:
    return target.with_name(f".{target.name}.jarvis-reservation")


def publish_download(source: Path, target: Path) -> None:
    """Publish exclusively and notify Downloads watchers of a finished write.

    A hard link alone emits IN_CREATE, which the existing Resolve converter
    doesn't watch. Closing a writable descriptor emits IN_CLOSE_WRITE without
    changing any bytes, after the complete validated file is already in place.
    """
    os.link(source, target)
    fd = os.open(target, os.O_WRONLY | os.O_APPEND)
    os.close(fd)


def reserve_target(directory: Path, suggested: str, fallback: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = str(suggested or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', "_", name).strip(" .")
    if not name:
        name = re.sub(r"[^\w .-]", "_", fallback).strip(" .")[:150] + ".mp4"
    if Path(name).suffix.lower() != ".mp4":
        raise DownloadError("YT1s returned something other than an MP4 video, Sir.")
    stem = (
        Path(name).stem.encode("utf8")[:180].decode("utf8", "ignore").strip(" .")
        or "YouTube video"
    )
    for number in range(10_000):
        candidate = directory / f"{stem}{f' ({number})' if number else ''}.mp4"
        if candidate.exists() or candidate.with_suffix(".mov").exists():
            continue
        try:
            fd = os.open(
                _reservation(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            os.close(fd)
            if candidate.exists() or candidate.with_suffix(".mov").exists():
                _reservation(candidate).unlink(missing_ok=True)
                continue
            return candidate
        except FileExistsError:
            continue
    raise DownloadError("I couldn't choose a free filename in Downloads, Sir.")


async def save_download(download, directory: Path, title: str, on_started) -> Path:
    """Called only with an actual browser Download; preserve existing files."""
    target = reserve_target(directory, download.suggested_filename, title)
    partial = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.partial")
    try:
        result = on_started(target)
        if inspect.isawaitable(result):
            await result
        async with asyncio.timeout(TRANSFER_TIMEOUT_S):
            await download.save_as(str(partial))
            failure = await download.failure()
        if failure:
            raise DownloadError(
                f"The YouTube download failed, Sir: {str(failure)[:160]}."
            )
        with partial.open("rb") as content:
            header = content.read(32)
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise DownloadError("YT1s returned an invalid MP4 file, Sir.")
        # link() is exclusive: a file created since reservation is never overwritten.
        publish_download(partial, target)
        partial.unlink()
        return target
    except TimeoutError as exc:
        raise DownloadError("The YouTube download timed out, Sir.") from exc
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(
            f"I couldn't save the YouTube video, Sir: {str(exc)[:160]}."
        ) from exc
    finally:
        partial.unlink(missing_ok=True)
        _reservation(target).unlink(missing_ok=True)


async def run_site_flow(page, url: str):
    """Operate only the converter form, video tab and table Download control."""
    blocked_statuses: set[int] = set()

    def response_seen(response) -> None:
        parsed = urlparse(response.url)
        if (
            parsed.hostname == CONVERTER_FRAME_HOST
            and parsed.path == "/api/verify"
            and response.status >= 400
        ):
            blocked_statuses.add(response.status)

    page.on("response", response_seen)

    # Ads use popups. They are never download targets or followed by automation.
    async def close_popup(popup) -> None:
        with contextlib.suppress(Exception):
            await popup.close()

    page.on("popup", lambda popup: asyncio.create_task(close_popup(popup)))
    await page.goto(CONVERTER_URL, wait_until="domcontentloaded", timeout=20_000)
    with contextlib.suppress(PlaywrightTimeoutError):
        await page.wait_for_load_state("networkidle", timeout=5_000)
    field = page.get_by_placeholder("Search or paste Youtube link here", exact=True)
    await field.fill(url, timeout=10_000)
    try:
        await page.get_by_role("button", name="Convert", exact=True).click(
            timeout=10_000
        )
    except PlaywrightTimeoutError:
        # Gatsby navigation sometimes detaches the button after submission.
        # Accept only the expected converter destination, never another site.
        if (
            urlparse(page.url).hostname != "yt1s.com.co"
            or urlparse(page.url).path != "/download/"
        ):
            raise
    iframe = await page.wait_for_selector(
        f'iframe[src^="https://{CONVERTER_FRAME_HOST}/v1/full?"]', timeout=20_000
    )
    converter = await iframe.content_frame()
    if converter is None:
        raise DownloadError("YT1s didn't load its video converter, Sir.")
    await converter.get_by_role("button", name="Video", exact=True).click(
        timeout=20_000
    )
    rows = converter.locator("table tbody tr")
    deadline = time.monotonic() + 35
    choice = None
    while time.monotonic() < deadline:
        error = classify_site_error(
            await converter.locator("body").inner_text(timeout=1_000), blocked_statuses
        )
        if error:
            raise DownloadError(error)
        data = await rows.evaluate_all(
            "rows => rows.map((row, index) => ({index, text: row.innerText}))"
        )
        choice = choose_mp4_row(data)
        if choice is not None:
            break
        await asyncio.sleep(0.4)
    if choice is None:
        raise DownloadError("YT1s didn't offer an MP4 video with audio, Sir.")
    button = rows.nth(choice).get_by_role("button", name="Download", exact=True)
    # A conversion API response alone must never count as a file download.
    received = asyncio.get_running_loop().create_future()

    def download_seen(download) -> None:
        if not received.done():
            received.set_result(download)

    page.on("download", download_seen)
    try:
        async with asyncio.timeout(35):
            await button.click(timeout=10_000)
            final_clicked = False
            final_button = converter.get_by_role(
                "button", name="Download Now", exact=True
            )
            while not received.done():
                error = classify_site_error(
                    await converter.locator("body").inner_text(timeout=1_000),
                    blocked_statuses,
                )
                if error:
                    raise DownloadError(error)
                # The website converts first, then exposes a result-modal
                # button that navigates the top page to the actual file.
                if (
                    not final_clicked
                    and await final_button.is_visible()
                    and await final_button.is_enabled()
                ):
                    await final_button.click(timeout=10_000)
                    final_clicked = True
                if not received.done():
                    await asyncio.sleep(0.2)
            return received.result()
    except (PlaywrightTimeoutError, TimeoutError) as exc:
        error = classify_site_error(
            await converter.locator("body").inner_text(timeout=1_000), blocked_statuses
        )
        raise DownloadError(
            error or "YT1s didn't start the file download, Sir."
        ) from exc
    finally:
        page.remove_listener("download", download_seen)
        if not received.done():
            received.cancel()


async def download_video(url: str, directory: Path, title: str, on_started) -> Path:
    if os.environ.get("JARVIS_YOUTUBE_BACKEND", "direct").strip().lower() == "yt1s":
        return await download_video_site(url, directory, title, on_started)
    from youtube_direct import download_video_direct

    return await download_video_direct(url, directory, title, on_started)


async def download_video_site(
    url: str, directory: Path, title: str, on_started
) -> Path:
    from browser import _discover_browser_binary

    directory.mkdir(parents=True, exist_ok=True)
    # Browser transfer data stays under Downloads and is removed after save/failure.
    staging = Path(tempfile.mkdtemp(prefix=".jarvis-youtube-", dir=directory))
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                executable_path=_discover_browser_binary(),
                downloads_path=str(staging),
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=["--enable-automation"],
            )
            try:
                context = await browser.new_context(accept_downloads=True)
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
                )
                page = await context.new_page()
                async with asyncio.timeout(PREPARE_TIMEOUT_S):
                    download = await run_site_flow(page, url)
                return await save_download(download, directory, title, on_started)
            finally:
                await browser.close()
    except TimeoutError as exc:
        raise DownloadError(
            "YT1s took too long to prepare this video, Sir. The download hasn't started."
        ) from exc
    except DownloadError:
        raise
    except Exception as exc:
        # Browser tracebacks/headers are diagnostics, never spoken wholesale.
        raise DownloadError(
            "I couldn't start the download through YT1s, Sir. Please try again later."
        ) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


class DownloadJobs:
    def __init__(self, directory: Path | None = None) -> None:
        base = Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")
        self.directory = (
            Path(directory) if directory is not None else base / "youtube" / "jobs"
        )
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._launched: set[str] = set()

    def get(self, job_id: str) -> dict | None:
        if not JOB_ID.fullmatch(job_id):
            return None
        try:
            data = json.loads((self.directory / f"{job_id}.json").read_text())
            if isinstance(data, dict):
                # The user's external Resolve watcher replaces completed MP4s
                # with same-named MOV files. Follow that result on status reads.
                if data.get("status") == "done" and data.get("path"):
                    original = Path(data["path"])
                    converted = original.with_suffix(".mov")
                    if (
                        original.suffix.lower() == ".mp4"
                        and not original.exists()
                        and converted.is_file()
                    ):
                        data.update(path=str(converted), filename=converted.name)
                data["say"] = job_say(data)
                return data
            return None
        except (OSError, ValueError):
            return None

    def _change(self, job_id: str, **updates) -> dict | None:
        with self._lock:
            job = self.get(job_id)
            if job is None:
                return None
            job.update(updates, updated_at=time.time())
            job["say"] = job_say(job)
            _atomic_json(self.directory / f"{job_id}.json", json.dumps(job))
            return job

    def start(self, resolved: dict) -> dict:
        from system import require_local

        require_local()
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            running = 0
            for path in self.directory.glob("youtube-*.json"):
                job = self.get(path.stem)
                if not job or job.get("status") not in ACTIVE_STATUSES:
                    continue
                if job["id"] not in self._launched:
                    self._change(
                        job["id"],
                        status="failed",
                        error="Jarvis restarted before this download finished.",
                    )
                    continue
                if job.get("url") == resolved["url"]:
                    return job
                running += 1
            if running >= 2:
                raise DownloadError(
                    "Two YouTube downloads are already running, Sir. Please wait for one to finish."
                )
            job_id = "youtube-" + uuid.uuid4().hex[:12]
            title = str(resolved.get("title") or "YouTube video")[:240]
            job = {
                "id": job_id,
                "status": "preparing",
                "url": resolved["url"],
                "title": title,
                "source": resolved.get("source", "explicit"),
                "created_at": time.time(),
                "updated_at": time.time(),
                "say": f"I'm preparing {title.removesuffix(' - YouTube')}, Sir. I'll tell you when the file starts downloading.",
            }
            _atomic_json(self.directory / f"{job_id}.json", json.dumps(job))
            self._launched.add(job_id)
            try:
                self._spawn(job)
            except Exception:
                self._change(
                    job_id,
                    status="failed",
                    error="I couldn't start the background download, Sir.",
                )
                raise DownloadError(
                    "I couldn't start the background download, Sir."
                ) from None
            return job

    def _spawn(self, job: dict) -> None:
        thread = threading.Thread(
            target=self._run, args=(job,), name=job["id"], daemon=True
        )
        self._threads[job["id"]] = thread
        thread.start()

    def _run(self, job: dict) -> None:
        import activity
        import notify
        from system import log_action

        job_id = job["id"]
        activity.start(
            "download",
            job["title"],
            detail="Preparing YouTube MP4",
            source="youtube",
            item_id=job_id,
        )

        def started(target: Path) -> None:
            self._change(
                job_id,
                status="downloading",
                filename=target.name,
                path=str(target),
                started_at=time.time(),
            )
            activity.update(
                job_id,
                detail=f"Downloading {target.name}",
                meta={"filename": target.name, "path": str(target)},
            )
            log_action("youtube-download", f"started {job_id} {target.name}")
            notify.send(
                f"Your YouTube video is downloading, Sir: {target.name}. It will be saved in Downloads.",
                title="YouTube download started",
                source="youtube",
                kind="download-started",
                fingerprint=f"{job_id}:start",
                allow_speech=False,
                queue=False,
            )

        try:
            target = asyncio.run(
                download_video(job["url"], downloads_directory(), job["title"], started)
            )
            self._change(
                job_id,
                status="done",
                filename=target.name,
                path=str(target),
                completed_at=time.time(),
            )
            activity.finish(job_id, detail=f"Saved to {target}")
            log_action("youtube-download", f"saved {job_id} {target.name}")
            notify.send(
                f"Your YouTube video is saved in Downloads, Sir: {target.name}.",
                title="YouTube video saved",
                source="youtube",
                kind="download-complete",
                fingerprint=f"{job_id}:complete",
                allow_speech=False,
                queue=False,
            )
        except Exception as exc:
            error = (
                str(exc)
                if isinstance(exc, DownloadError)
                else "The YouTube download failed, Sir."
            )
            self._change(job_id, status="failed", error=error, failed_at=time.time())
            activity.finish(job_id, ok=False, detail=error)
            log_action("youtube-download", f"failed {job_id} {error}")
            notify.send(
                error,
                title="YouTube download failed",
                source="youtube",
                kind="download-failed",
                fingerprint=f"{job_id}:failure",
                allow_speech=False,
                queue=False,
            )
        finally:
            self._threads.pop(job_id, None)


_jobs: DownloadJobs | None = None
_jobs_lock = threading.Lock()


def job_say(job: dict) -> str:
    if job.get("status") == "downloading":
        return f"Your YouTube video is downloading to Downloads, Sir: {job.get('filename', 'the MP4 video')}."
    if job.get("status") == "done":
        return f"Your YouTube video has been saved in Downloads, Sir: {job.get('filename', 'the MP4 video')}."
    if job.get("status") == "failed":
        return job.get("error") or "The YouTube download failed, Sir."
    title = str(job.get("title") or "this YouTube video").removesuffix(" - YouTube")
    return (
        f"I'm preparing {title}, Sir. I'll tell you when the file starts downloading."
    )


def jobs() -> DownloadJobs:
    global _jobs
    with _jobs_lock:
        directory = (
            Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")
            / "youtube"
            / "jobs"
        )
        if _jobs is None or _jobs.directory != directory:
            _jobs = DownloadJobs(directory)
        return _jobs


def start_download(url: str = "") -> dict:
    resolved = YouTubeHistory().resolve(foreground_video(), url)
    return jobs().start(resolved)


def _bridge_call(method: str, path: str, body: dict | None = None) -> dict | None:
    from wizard.gate import BRIDGE_DEFAULT_PORT, bridge_host, bridge_token

    try:
        port = int(os.environ.get("JARVIS_BRIDGE_PORT", str(BRIDGE_DEFAULT_PORT)))
        request = urllib.request.Request(
            f"http://{bridge_host()}:{port}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
        )
        request.add_header("Content-Type", "application/json")
        if token := bridge_token():
            request.add_header("Authorization", f"Bearer {token}")
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return json.loads(response.read())
    except Exception:
        return None


class YouTubeDownloadTools:
    """One direct tool; website jobs are hosted by the bridge, outside the call."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._watched: set[str] = set()

    @property
    def tools(self) -> list:
        return [self.download_youtube_video]

    def _report_start(self, context: RunContext, job_id: str) -> None:
        session = getattr(context, "session", None)
        if session is None or job_id in self._watched:
            return
        self._watched.add(job_id)

        async def watch() -> None:
            deadline = time.monotonic() + VOICE_WAIT_S
            while time.monotonic() < deadline:
                await asyncio.sleep(VOICE_POLL_S)
                result = await asyncio.to_thread(
                    _bridge_call, "GET", f"/youtube/download/{job_id}"
                )
                job = (result or {}).get("job")
                if not isinstance(job, dict) or job.get("status") == "preparing":
                    continue
                with contextlib.suppress(Exception):
                    await session.say(job_say(job))
                return

        task = asyncio.create_task(watch())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @function_tool()
    async def download_youtube_video(self, context: RunContext, url: str = "") -> dict:
        """Download this YouTube video as MP4 with audio, headlessly, to Downloads.

        Use immediately for 'download this YouTube video' or 'save this video'.
        With no URL, use the user's currently viewed YouTube video, otherwise
        the most recent foreground YouTube video (including a closed tab).
        The tool tracks conversion separately from downloading and announces
        when the actual file transfer starts. Never use open_url for this task.

        Args:
            url: Optional explicit YouTube video URL; empty means this/recent video.
        """
        from system import LocalSystemError, require_local

        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if not isinstance(url, str) or len(url) > 2048:
            raise ToolError("Please give me a valid YouTube video link, Sir.")
        result = await asyncio.to_thread(
            _bridge_call, "POST", "/youtube/download", {"url": url}
        )
        if (
            not result
            or not result.get("ok")
            or not isinstance(result.get("job"), dict)
        ):
            raise ToolError(
                (result or {}).get("error")
                or "The download service isn't available, Sir."
            )
        job = result["job"]
        if job.get("status") == "preparing":
            self._report_start(context, job["id"])
        return {
            "job_id": job["id"],
            "status": job["status"],
            "url": job["url"],
            "say": job_say(job),
        }
