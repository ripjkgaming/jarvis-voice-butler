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
import time
from pathlib import Path

PREPARE_TIMEOUT_S = 90.0
TRANSFER_TIMEOUT_S = 30 * 60.0


def progress_reporter(emit):
    started = False
    last_progress = 0.0

    def report(progress):
        nonlocal started, last_progress
        if progress.get("status") != "downloading":
            return
        if (progress.get("downloaded_bytes") or 0) <= 0:
            return
        total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
        if started:
            if total and time.monotonic() - last_progress >= 1:
                last_progress = time.monotonic()
                emit(
                    {
                        "event": "progress",
                        "percent": min(
                            99,
                            max(0, 100 * progress.get("downloaded_bytes", 0) / total),
                        ),
                    }
                )
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


def worker_options(
    staging: Path,
    hook,
    *,
    kind="video",
    audio_format="mp3",
    quality=1080,
    playlist=False,
) -> dict:
    node = shutil.which("node")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": not playlist,
        "format": video_selector(quality),
        "format_sort": [
            f"res:{quality}" if quality else "res",
            "vcodec:h264",
            "acodec:aac",
        ],
        "merge_output_format": "mp4",
        "outtmpl": str(
            staging / ("audio.%(ext)s" if kind == "audio" else "video.%(ext)s")
        ),
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
    if kind == "audio":
        opts.update(
            format="bestaudio[ext=m4a]/bestaudio/best",
            writethumbnail=True,
            postprocessors=[
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": "192",
                },
                {"key": "FFmpegMetadata"},
            ],
        )
    return opts


def video_selector(quality):
    cap = f"[height<={quality}]" if quality else ""
    return f"bestvideo{cap}[ext=mp4]+bestaudio[ext=m4a]/bestvideo{cap}+bestaudio/best{cap}[ext=mp4]/best{cap}"


def _error_code(message: str) -> str:
    from youtube_resolve import map_error

    return map_error(message)


def _worker(url: str, staging: Path, **options) -> int:
    import zipfile

    binary = shutil.which("yt-dlp")
    if binary and zipfile.is_zipfile(binary):
        sys.path.insert(0, binary)
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
    opts = worker_options(staging, progress_reporter(emit), **options)
    opts["logger"] = QuietLogger()
    try:
        with YoutubeDL(opts) as downloader:
            if options.get("kind", "video") == "audio":
                from yt_dlp.postprocessor.embedthumbnail import EmbedThumbnailPP

                class OptionalThumbnailPP(EmbedThumbnailPP):
                    def run(self, info):
                        try:
                            return super().run(info)
                        except Exception:
                            return [], info

                downloader.add_post_processor(
                    OptionalThumbnailPP(downloader, already_have_thumbnail=False)
                )
            info = downloader.extract_info(url, download=True)
        if not info:
            raise RuntimeError("No download result")
        if options.get("kind", "video") == "video":
            normalize_video(staging)
        emit({"event": "done"})
        return 0
    except Exception as exc:
        # Do not print full exceptions: extractors may embed signed media URLs.
        emit({"event": "error", "code": _error_code(str(exc))})
        return 1


def normalize_video(staging: Path) -> None:
    """Enforce the container and H.264/AAC contract, even for fallback streams."""
    import subprocess

    source = next(
        (p for p in staging.glob("video.*") if p.suffix in {".mp4", ".mkv", ".webm"}),
        None,
    )
    if source is None:
        raise RuntimeError("No video result")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(source)],
        capture_output=True,
        check=True,
        timeout=15,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next((x for x in streams if x.get("codec_type") == "video"), {})
    audio = next((x for x in streams if x.get("codec_type") == "audio"), {})
    if (
        source.suffix == ".mp4"
        and video.get("codec_name") == "h264"
        and audio.get("codec_name") == "aac"
    ):
        return
    target = staging / "normalized.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "copy" if video.get("codec_name") == "h264" else "libx264",
            "-c:a",
            "copy" if audio.get("codec_name") == "aac" else "aac",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
        timeout=TRANSFER_TIMEOUT_S,
    )
    source.unlink()
    target.rename(staging / "video.mp4")


def worker_command(url: str, staging: Path, **options) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        url,
        str(staging),
        json.dumps(options),
    ]


async def validate_media(path: Path, kind="video") -> None:
    from youtube_download import DownloadError

    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type:stream_disposition=attached_pic",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        kinds = {
            s.get("codec_type")
            for s in json.loads(out).get("streams", [])
            if not s.get("disposition", {}).get("attached_pic")
        }
        if (
            process.returncode != 0
            or (kind == "video" and not {"audio", "video"} <= kinds)
            or (kind == "audio" and ("audio" not in kinds or "video" in kinds))
        ):
            raise DownloadError(
                "The downloaded MP4 is missing playable video or audio, Sir."
                if kind == "video"
                else "The downloaded audio file is missing playable audio, Sir."
            )
    except (TimeoutError, ValueError) as exc:
        raise DownloadError("I couldn't validate the downloaded media, Sir.") from exc
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
    from youtube_resolve import ERROR_SAY

    return ERROR_SAY.get(code, ERROR_SAY["failed"])


async def download_video_direct(
    url: str, directory: Path, title: str, on_started, on_progress=None, **options
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
    kind = options.get("kind", "video")
    extension = options.get("audio_format", "mp3") if kind == "audio" else "mp4"
    directory.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".jarvis-youtube-", dir=directory))
    process = None
    target = None
    completed = False
    try:
        process = await asyncio.create_subprocess_exec(
            *worker_command(url, staging, **options),
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
                if event.get("event") == "progress" and on_progress:
                    value = event.get("percent")
                    if isinstance(value, (int, float)):
                        on_progress(min(99, max(0, value)))
                if event.get("event") == "error":
                    error = DownloadError(
                        _failure_say(
                            event.get("code", "failed"), started=target is not None
                        )
                    )
                    error.code = event.get("code", "failed")
                    raise error
                if event.get("event") == "transfer" and target is None:
                    name = str(event.get("title") or title).removesuffix(" - YouTube")
                    target = reserve_target(directory, name + "." + extension, title)
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
            media = staging / (("audio." if kind == "audio" else "video.") + extension)
            with media.open("rb") as content:
                header = content.read(32)
            if kind == "video" and (len(header) < 12 or header[4:8] != b"ftyp"):
                raise DownloadError("The downloader returned an invalid MP4 file, Sir.")
            await validate_media(media) if kind == "video" else await validate_media(
                media, kind
            )
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
    if len(sys.argv) != 5 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    options = json.loads(sys.argv[4])
    if (
        not isinstance(options, dict)
        or set(options) - {"kind", "audio_format", "quality", "playlist"}
        or options.get("kind", "video") not in {"video", "audio"}
        or options.get("audio_format", "mp3") not in {"mp3", "m4a"}
        or type(options.get("quality", 1080)) is not int
        or not 0 <= options.get("quality", 1080) <= 4320
        or type(options.get("playlist", False)) is not bool
    ):
        raise SystemExit(2)
    raise SystemExit(_worker(sys.argv[2], Path(sys.argv[3]), **options))
