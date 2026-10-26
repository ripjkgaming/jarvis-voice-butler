"""Isolated direct YouTube downloader; emit only safe lifecycle events.

The subprocess owns extraction, cookie loading and ffmpeg. Killing its process
group also stops child processes. Browser credentials never reach the bridge's
job files, activity log, progress events or a third-party converter.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path

PREPARE_TIMEOUT_S = 90.0
TRANSFER_TIMEOUT_S = 30 * 60.0


def progress_reporter(emit):
    started = False

    def report(progress):
        nonlocal started
        if started or progress.get("status") != "downloading":
            return
        if (progress.get("downloaded_bytes") or 0) <= 0:
            return
        started = True
        emit(
            {
                "event": "transfer",
                "title": str(
                    progress.get("info_dict", {}).get("title") or "YouTube video"
                )[:240],
            }
        )

    return report


def worker_options(staging: Path, hook) -> dict:
    node = shutil.which("node")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bv*[height>=1080]+ba[ext=m4a]/bv*[height>=1080]+ba/b[height>=1080]",
        "format_sort": ["res:1080", "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4",
        "outtmpl": str(staging / "video.%(ext)s"),
        "socket_timeout": 15,
        "retries": 2,
        "fragment_retries": 2,
        "concurrent_fragment_downloads": 1,
        "progress_hooks": [hook],
        "cachedir": False,
        "js_runtimes": {"node": {"path": node}} if node else {},
    }
    browser = os.environ.get("JARVIS_YOUTUBE_COOKIES_BROWSER", "brave").strip()
    if browser.lower() not in {"", "off", "none", "0"}:
        # yt-dlp reads the local cookie store in memory. No cookie export file.
        opts["cookiesfrombrowser"] = (browser,)
    return opts


def _error_code(message: str) -> str:
    lower = message.lower()
    if any(
        x in lower for x in ("sign in", "cookies", "decrypt", "not a bot", "keyring")
    ):
        return "authentication"
    if any(
        x in lower for x in ("unavailable", "private video", "removed", "not found")
    ):
        return "unavailable"
    return "failed"


def _worker(url: str, staging: Path) -> int:
    from yt_dlp import YoutubeDL

    from youtube_history import canonical_video_url

    def emit(event):
        print(json.dumps(event), flush=True)

    class QuietLogger:
        def debug(self, _message):
            pass

        def warning(self, _message):
            pass

        def error(self, _message):
            pass

    if canonical_video_url(url) != url:
        emit({"event": "error", "code": "unavailable"})
        return 1
    opts = worker_options(staging, progress_reporter(emit))
    opts["logger"] = QuietLogger()
    try:
        with YoutubeDL(opts) as downloader:
            info = downloader.extract_info(url, download=True)
        if not info or not (staging / "video.mp4").is_file():
            raise RuntimeError("No MP4 result")
        emit({"event": "done"})
        return 0
    except Exception as exc:
        # Do not print full exceptions: extractors may embed signed media URLs.
        emit({"event": "error", "code": _error_code(str(exc))})
        return 1


def worker_command(url: str, staging: Path) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        url,
        str(staging),
    ]


async def validate_media(path: Path) -> None:
    from youtube_download import DownloadError

    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        kinds = {s.get("codec_type") for s in json.loads(out).get("streams", [])}
        if process.returncode != 0 or not {"audio", "video"} <= kinds:
            raise DownloadError(
                "The downloaded MP4 is missing playable video or audio, Sir."
            )
    except (TimeoutError, ValueError) as exc:
        raise DownloadError("I couldn't validate the downloaded MP4, Sir.") from exc
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def _failure_say(code: str, *, started: bool = False) -> str:
    if code == "authentication":
        if started:
            return "YouTube stopped the download because it couldn't verify the Brave session, Sir. Open YouTube in Brave and sign in before retrying."
        return "YouTube couldn't verify the Brave session, Sir. Open YouTube in Brave and sign in, then try again. The download hasn't started."
    if code == "unavailable":
        return "YouTube says this video is unavailable, Sir."
    return "I couldn't finish the YouTube download, Sir. Please try again."


async def download_video_direct(
    url: str, directory: Path, title: str, on_started
) -> Path:
    from youtube_download import (
        DownloadError,
        _reservation,
        publish_download,
        reserve_target,
    )
    from youtube_history import canonical_video_url

    if canonical_video_url(url) != url:
        raise DownloadError("Please give me a valid YouTube video link, Sir.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise DownloadError("The YouTube downloader needs ffmpeg installed, Sir.")
    directory.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".jarvis-youtube-", dir=directory))
    process = None
    target = None
    completed = False
    try:
        process = await asyncio.create_subprocess_exec(
            *worker_command(url, staging),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        async with asyncio.timeout(PREPARE_TIMEOUT_S) as deadline:
            while line := await process.stdout.readline():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("event") == "error":
                    raise DownloadError(
                        _failure_say(
                            event.get("code", "failed"), started=target is not None
                        )
                    )
                if event.get("event") == "transfer" and target is None:
                    name = str(event.get("title") or title).removesuffix(" - YouTube")
                    target = reserve_target(directory, name + ".mp4", title)
                    deadline.reschedule(
                        asyncio.get_running_loop().time() + TRANSFER_TIMEOUT_S
                    )
                    result = on_started(target)
                    if inspect.isawaitable(result):
                        await result
                elif event.get("event") == "done":
                    completed = True
            await process.wait()
            if process.returncode or not completed or target is None:
                raise DownloadError("YouTube didn't complete the file transfer, Sir.")
            media = staging / "video.mp4"
            with media.open("rb") as content:
                header = content.read(32)
            if len(header) < 12 or header[4:8] != b"ftyp":
                raise DownloadError("The downloader returned an invalid MP4 file, Sir.")
            await validate_media(media)
            # Atomic and exclusive; never overwrite a file created during transfer.
            publish_download(media, target)
            return target
    except TimeoutError as exc:
        raise DownloadError("The YouTube download timed out, Sir.") from exc
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError("I couldn't save the YouTube download, Sir.") from exc
    finally:
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        if target is not None:
            _reservation(target).unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    raise SystemExit(_worker(sys.argv[2], Path(sys.argv[3])))
