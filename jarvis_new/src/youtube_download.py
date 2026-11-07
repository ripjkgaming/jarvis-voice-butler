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

from jarvis_rt import RunContext, function_tool
from jarvis_rt import ToolError
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


def downloads_directory(kind="video") -> Path:
    key = "JARVIS_YOUTUBE_AUDIO_DIR" if kind == "audio" else "JARVIS_YOUTUBE_VIDEO_DIR"
    override = os.environ.get(key, "").strip()
    if kind == "video":
        override = override or os.environ.get("JARVIS_DOWNLOADS_DIR", "").strip()
    return (
        Path(override).expanduser()
        if override
        else Path.home() / ("Music" if kind == "audio" else "Videos") / "Jarvis"
    )


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
    if Path(name).suffix.lower() not in {".mp4", ".mp3", ".m4a"}:
        raise DownloadError(
            "The downloader returned something other than MP4, MP3 or M4A, Sir."
        )
    suffix = Path(name).suffix.lower()
    stem = (
        Path(name).stem.encode("utf8")[:180].decode("utf8", "ignore").strip(" .")
        or "YouTube video"
    )
    for number in range(10_000):
        candidate = directory / f"{stem}{f' ({number})' if number else ''}{suffix}"
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


async def download_video(
    url: str, directory: Path, title: str, on_started, **options
) -> Path:
    if (
        not options
        and os.environ.get("JARVIS_YOUTUBE_BACKEND", "direct").strip().lower() == "yt1s"
    ):
        return await download_video_site(url, directory, title, on_started)
    from youtube_direct import download_video_direct

    try:
        return await download_video_direct(url, directory, title, on_started, **options)
    except DownloadError as exc:
        if getattr(exc, "code", "") not in {"failed", "network"}:
            raise
        await check_downloader_update()
        return await download_video_direct(url, directory, title, on_started, **options)


_update_checked = False
_update_lock = threading.Lock()


async def check_downloader_update():
    """Only standalone installs support self-update; never mutate pip packages."""
    global _update_checked
    with _update_lock:
        if _update_checked:
            return
        _update_checked = True
    binary = shutil.which("yt-dlp")
    if not binary:
        return
    try:
        import zipfile

        if not zipfile.is_zipfile(binary) and Path(binary).read_bytes()[:2] == b"#!":
            return
        process = await asyncio.create_subprocess_exec(
            binary,
            "-U",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), 60)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    except (OSError, TimeoutError):
        pass


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
        self._runners = {}
        self._cancelled = set()

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
                if (
                    job.get("url") == resolved["url"]
                    and job.get("kind", "video") == resolved.get("kind", "video")
                    and job.get("audio_format", "mp3")
                    == resolved.get("audio_format", "mp3")
                ):
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
                "kind": resolved.get("kind", "video"),
                "quiet": bool(resolved.get("quiet", False)),
                "entries": resolved.get("entries", []),
                "audio_format": resolved.get("audio_format", "mp3"),
                "quality": resolved.get("quality", 1080),
                "playlist": resolved.get("playlist", False),
                "channel": str(resolved.get("channel") or "")[:240],
                "created_at": time.time(),
                "updated_at": time.time(),
                "say": f"I'm preparing {title.removesuffix(' - YouTube')}, Sir. I'll tell you when the file starts downloading.",
            }
            job["say"] = job_say(job)
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

        def output(function, *args, **kwargs):
            if not job.get("quiet"):
                return function(*args, **kwargs)

        output(
            activity.start,
            "download",
            job["title"],
            detail=f"Preparing YouTube {job.get('audio_format', 'mp3').upper() if job.get('kind') == 'audio' else 'MP4'}",
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
            output(
                activity.update,
                job_id,
                detail=f"Downloading {target.name}",
                meta={"filename": target.name, "path": str(target)},
            )
            output(log_action, "youtube-download", f"started {job_id} {target.name}")
            output(
                notify.send,
                f"Your YouTube {job.get('kind', 'video')} is downloading, Sir: {target.name}. It will be saved in {downloads_directory(job.get('kind', 'video'))}.",
                title="YouTube download started",
                source="youtube",
                kind="download-started",
                fingerprint=f"{job_id}:start",
                allow_speech=False,
                queue=False,
            )

        try:

            async def transfer():
                loop = asyncio.get_running_loop()
                task = asyncio.current_task()
                with self._lock:
                    self._runners[job_id] = (loop, task)
                    if job_id in self._cancelled:
                        raise asyncio.CancelledError
                options = {
                    k: job[k]
                    for k in ("kind", "audio_format", "quality", "playlist")
                    if k in job
                }

                def progress(percent):
                    self._change(job_id, progress=percent)
                    output(activity.update, job_id, progress=percent)

                entries = job.get("entries") or [
                    {"url": job["url"], "title": job["title"]}
                ]
                files = []
                for entry in entries:
                    entry_url = (
                        f"https://www.youtube.com/watch?v={entry['id']}"
                        if entry.get("id")
                        else entry["url"]
                    )
                    item_options = dict(options, playlist=False, on_progress=progress)
                    target = await download_video(
                        entry_url,
                        downloads_directory(job.get("kind", "video")),
                        entry.get("title") or job["title"],
                        started,
                        **item_options,
                    )
                    files.append(str(target))
                    self._change(job_id, files=files)
                return target

            target = asyncio.run(transfer())
            self._change(
                job_id,
                status="done",
                filename=target.name,
                path=str(target),
                completed_at=time.time(),
                progress=100.0,
            )
            output(activity.finish, job_id, detail=f"Saved to {target}")
            output(log_action, "youtube-download", f"saved {job_id} {target.name}")
            output(
                notify.send,
                f"Saved {target.name}, Sir, in {target.parent}.",
                title="YouTube file saved",
                source="youtube",
                kind="download-complete",
                fingerprint=f"{job_id}:complete",
                allow_speech=False,
                queue=False,
            )
        except asyncio.CancelledError:
            self._change(job_id, status="cancelled")
            output(
                activity.finish,
                job_id,
                ok=False,
                detail="Download cancelled; partial files removed.",
            )
        except Exception as exc:
            error = (
                str(exc)
                if isinstance(exc, DownloadError)
                else "The YouTube download failed, Sir."
            )
            self._change(job_id, status="failed", error=error, failed_at=time.time())
            output(activity.finish, job_id, ok=False, detail=error)
            output(log_action, "youtube-download", f"failed {job_id} {error}")
            output(
                notify.send,
                error,
                title="YouTube download failed",
                source="youtube",
                kind="download-failed",
                fingerprint=f"{job_id}:failure",
                allow_speech=False,
                queue=False,
            )
        finally:
            with self._lock:
                self._runners.pop(job_id, None)
                self._threads.pop(job_id, None)
                self._cancelled.discard(job_id)
                self._launched.discard(job_id)

    def cancel(self, job_id=None):
        from system import require_local

        require_local()
        with self._lock:
            active = [self.get(p.stem) for p in self.directory.glob("youtube-*.json")]
            active = [
                j
                for j in active
                if j
                and j.get("status") in ACTIVE_STATUSES
                and (not job_id or j["id"] == job_id)
            ]
            if not active:
                raise DownloadError(
                    "There is no active YouTube download to cancel, Sir."
                )
            job = max(active, key=lambda j: j.get("created_at", 0))
            self._cancelled.add(job["id"])
            runner = self._runners.get(job["id"])
            if runner:
                runner[0].call_soon_threadsafe(runner[1].cancel)
            thread = self._threads.get(job["id"])
        if thread:
            thread.join(timeout=20)
            if thread.is_alive():
                raise DownloadError(
                    "The download is still stopping, Sir. Check its status in a moment."
                )
        current = self.get(job["id"])
        if current and current.get("status") == "done":
            return current
        self._change(job["id"], status="cancelled")
        return self.get(job["id"])


_jobs: DownloadJobs | None = None
_jobs_lock = threading.Lock()


def job_say(job: dict) -> str:
    status = job.get("status")
    if status == "confirm":
        return job["say"]
    if status == "cancelled":
        return "YouTube download cancelled, Sir. Partial files have been removed."
    if status == "failed":
        return job.get("error") or "The YouTube download failed, Sir."
    folder = str(downloads_directory(job.get("kind", "video")))
    if status == "downloading":
        return f"Your YouTube file is downloading to {folder}, Sir: {job.get('filename', 'the file')}."
    if status == "done":
        return f"Saved {job.get('filename', 'the file')} in {folder}, Sir." + (
            f" {len(job['files'])} playlist files saved."
            if len(job.get("files", [])) > 1
            else ""
        )
    title = str(job.get("title") or "this YouTube video").removesuffix(" - YouTube")
    channel = f" by {job['channel']}" if job.get("channel") else ""
    return f"I'm preparing {title}{channel}, Sir. I'll tell you when it is saved."


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


_choices = {}
_choices_lock = threading.Lock()


def start_download(
    url: str = "", *, confirmation_id="", confirm=None, quiet=False
) -> dict:
    from system import require_local
    from youtube_resolve import (
        YouTubeSearchError,
        parse_request,
        playlist_entries,
        playlist_url,
        resolve_query,
        video_details,
    )

    require_local()
    if confirmation_id:
        with _choices_lock:
            choice = _choices.pop(confirmation_id, None)
        if not choice or choice[0] < time.monotonic():
            raise DownloadError(
                "That choice expired, Sir. Please ask for the download again."
            )
        if confirm is not True:
            return {
                "status": "cancelled",
                "say": "I won't download that one, Sir. Try different words.",
            }
        return jobs().start(choice[1])
    request = parse_request(url)
    options = {
        k: getattr(request, k) for k in ("kind", "audio_format", "quality", "playlist")
    }
    options["quiet"] = quiet
    collection = playlist_url(url) if request.playlist else None
    if re.search(r"https?://", url, re.I) and not request.url and not collection:
        raise DownloadError(
            "Please give me a YouTube video link, Sir, or explicitly ask to download a playlist."
        )
    if collection:
        try:
            entries = playlist_entries(collection)
        except YouTubeSearchError as exc:
            raise DownloadError(str(exc)) from exc
        from youtube_history import VIDEO_ID

        entries = [e for e in entries if VIDEO_ID.fullmatch(str(e.get("id") or ""))]
        if not entries:
            raise DownloadError(
                "That playlist has no available videos, Sir. Try a public playlist."
            )
        resolved = {
            "url": collection,
            "title": f"Playlist ({len(entries)} videos)",
            "entries": entries,
        }
    elif (
        request.url
        or not url.strip()
        or request.query.casefold()
        in {
            "this video",
            "that video",
            "this",
            "that",
            "current video",
            "recent video",
            "last video",
            "this song",
            "this audio",
            "download",
            "save",
        }
    ):
        resolved = YouTubeHistory().resolve(foreground_video(), request.url or "")
        try:
            details = video_details(resolved["url"])
        except YouTubeSearchError as exc:
            raise DownloadError(str(exc)) from exc
        resolved.update(
            title=details.get("title") or resolved.get("title"),
            channel=details.get("channel") or details.get("uploader") or "",
        )
    else:
        try:
            decision = resolve_query(request)
        except YouTubeSearchError as exc:
            raise DownloadError(str(exc)) from exc
        if decision.action == "none":
            raise DownloadError(
                f'Nothing found for "{request.query}", Sir. Shall we try different words?'
            )
        top = decision.candidates[0].result
        resolved = {
            "url": f"https://www.youtube.com/watch?v={top['id']}",
            "title": top.get("title"),
            "channel": top.get("channel") or top.get("uploader"),
            "source": "search",
        }
        if decision.action == "confirm":
            token = uuid.uuid4().hex
            with _choices_lock:
                for key, value in list(_choices.items()):
                    if value[0] < time.monotonic():
                        _choices.pop(key, None)
                _choices[token] = (time.monotonic() + 120, dict(resolved, **options))
            return {
                "status": "confirm",
                "confirmation_id": token,
                "title": resolved["title"],
                "channel": resolved["channel"],
                "candidates": [
                    {"title": c.result.get("title"), "channel": c.result.get("channel")}
                    for c in decision.candidates
                ],
                "say": f"{resolved['title']} by {resolved['channel'] or 'YouTube'}, Sir — this one?",
            }
    return jobs().start(dict(resolved, **options))


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
            response = urllib.request.urlopen(request, timeout=110)
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
        return [
            self.download_youtube_video,
            self.confirm_youtube_download,
            self.cancel_youtube_download,
        ]

    def _report_start(self, context: RunContext, job_id: str) -> None:
        session = getattr(context, "session", None)
        if session is None or job_id in self._watched:
            return
        self._watched.add(job_id)

        async def watch() -> None:
            deadline = time.monotonic() + PREPARE_TIMEOUT_S + TRANSFER_TIMEOUT_S + 60
            while time.monotonic() < deadline:
                await asyncio.sleep(VOICE_POLL_S)
                result = await asyncio.to_thread(
                    _bridge_call, "GET", f"/youtube/download/{job_id}"
                )
                job = (result or {}).get("job")
                if not isinstance(job, dict) or job.get("status") in ACTIVE_STATUSES:
                    continue
                if job_id not in self._watched:
                    return
                with contextlib.suppress(Exception):
                    await session.say(job_say(job))
                return

        from hud_voice_state import execution_scope

        # Acquire before returning the tool result; release even if the watcher
        # is cancelled before its first coroutine step or fails while polling.
        work = execution_scope(session)
        work.__enter__()
        try:
            task = asyncio.create_task(watch())
        except BaseException:
            work.__exit__(None, None, None)
            raise
        task.add_done_callback(lambda _: work.__exit__(None, None, None))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @function_tool()
    async def download_youtube_video(self, context: RunContext, url: str = "") -> dict:
        """Download YouTube from a URL, video ID or complete spoken search request.

        Pass the full request, including audio/MP3/M4A and quality words, in url.
        Empty url selects the current/recent video. Default MP4 up to 1080p;
        audio defaults to MP3 192 kbps. Files stay on the desktop.
        Speak the returned say. On confirm ask exactly that short question,
        then call confirm_youtube_download with the token and the user's yes/no.
        Use cancel_youtube_download when asked to cancel a download.
        Personal use only; private/paywalled content is not bypassed.
        """
        from system import LocalSystemError, require_local

        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if not isinstance(url, str) or len(url) > 2048:
            raise ToolError("Please give me a valid YouTube video link, Sir.")
        import phone_session

        body = {"url": url}
        if phone_session.private():
            body["quiet"] = True
        result = await asyncio.to_thread(
            _bridge_call, "POST", "/youtube/download", body
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
        return self._watch_result(context, result["job"])

    def _watch_result(self, context, job):
        if job.get("status") in ACTIVE_STATUSES:
            self._latest = job["id"]
            self._report_start(context, job["id"])
        return dict(job, say=job_say(job))

    @function_tool()
    async def confirm_youtube_download(
        self, context: RunContext, confirmation_id: str, confirmed: bool
    ) -> dict:
        """Accept or reject the last YouTube title, using its confirmation_id and Sir's yes/no."""
        result = await asyncio.to_thread(
            _bridge_call,
            "POST",
            "/youtube/download",
            {"confirmation_id": confirmation_id, "confirm": confirmed},
        )
        if not result or not result.get("ok"):
            raise ToolError(
                (result or {}).get("error")
                or "The download service isn't available, Sir."
            )
        return self._watch_result(context, result["job"])

    @function_tool()
    async def cancel_youtube_download(
        self, context: RunContext, job_id: str = ""
    ) -> dict:
        """Cancel the given YouTube download, or the most recent active download."""
        watched = self._watched.copy()
        self._watched.discard(job_id or getattr(self, "_latest", ""))
        result = await asyncio.to_thread(
            _bridge_call, "POST", "/youtube/download/cancel", {"job_id": job_id or None}
        )
        if not result or not result.get("ok"):
            self._watched.update(watched)
            raise ToolError(
                (result or {}).get("error") or "I couldn't cancel that download, Sir."
            )
        job = result["job"]
        # The tool response speaks cancellation; suppress the completion watcher.
        self._watched.discard(job.get("id"))
        return dict(job, say=job_say(job))
