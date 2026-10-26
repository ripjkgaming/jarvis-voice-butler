"""Turn a spoken YouTube download request into a concrete video choice.

Pure parsing, ranking and error mapping, plus one flat yt-dlp search. Flat
search needs no per-video fetch, so it never trips YouTube's bot check the way
the metadata-matching play path does.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from youtube_history import VIDEO_ID, canonical_video_url

SEARCH_N = 5
MIN_SECONDS = 5
MAX_SECONDS = 10 * 3600
DOWNLOAD_SCORE = 0.6
CLEAR_MARGIN = 0.15

ERROR_SAY = {
    "authentication": "YouTube wants a signed-in session, Sir. Open YouTube in Brave and sign in, then try again.",
    "age_restricted": "That video is age-restricted, Sir. Sign in to YouTube in Brave with an adult account and try again.",
    "region_blocked": "That video is blocked in your region, Sir.",
    "private": "That video is private, Sir.",
    "removed": "That video was removed, Sir.",
    "unavailable": "YouTube says that video is unavailable, Sir.",
    "no_ffmpeg": "The downloader needs ffmpeg installed, Sir.",
    "network": "I couldn't reach YouTube, Sir. Check the network connection.",
    "no_results": "YouTube returned nothing for that search, Sir.",
    "failed": "I couldn't finish the YouTube download, Sir. Please try again.",
}

_URL = re.compile(r"https?://\S+", re.I)
_AUDIO = re.compile(
    r"\b(audio|mp3|m4a|song|music only|just the (?:sound|audio|music)|sound only)\b",
    re.I,
)
_QUALITY = re.compile(r"\b(?:(\d{3,4})p|(4k)|(best))\b", re.I)
_COMMAND = re.compile(
    r"\b(?:jarvis|please|download|get me|get|save|grab|fetch|the video of|"
    r"the video|a video of|from youtube|on youtube|youtube|"
    r"(?:as|in|to|into)\s+(?:an?\s+)?(?:mp3|m4a|mp4|audio|video|song)"
    r"(?:\s+(?:file|format))?|(?:in|at)\s+(?:\d{3,4}p|4k|best)(?:\s+quality)?|"
    r"mp3|m4a|mp4|audio only|music only|just the (?:sound|audio|music)|"
    r"sound only|as a playlist|the playlist|playlist)\b",
    re.I,
)
_FILLER = {
    "the",
    "a",
    "an",
    "of",
    "by",
    "video",
    "official",
    "please",
    "some",
    "that",
    "this",
    "one",
    "for",
    "me",
}
_NOISE = re.compile(r"reaction|compilation|#shorts|\bshorts\b|react\b", re.I)


class YouTubeSearchError(Exception):
    """Search failed; message is a user-facing sentence."""


@dataclass(frozen=True)
class Request:
    query: str
    raw_query: str
    url: str | None
    kind: str
    audio_format: str
    quality: int
    playlist: bool


@dataclass(frozen=True)
class Scored:
    result: dict
    score: float


@dataclass(frozen=True)
class Decision:
    action: str
    candidates: list


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", str(text or "").casefold())


def clean_query(text: str) -> str:
    raw = " ".join(str(text or "").split())
    cleaned = _COMMAND.sub(" ", raw)
    cleaned = re.sub(
        r"\btesla\s+(?:roaster|road star)\b", "Tesla Roadster", cleaned, flags=re.I
    )
    cleaned = re.sub(r"\bkick flip\b", "kickflip", cleaned, flags=re.I)
    cleaned = re.sub(r"^\s*(?:the|a|an)\s+", "", cleaned.strip(), flags=re.I)
    cleaned = re.sub(
        r"^(?:song|audio|video)\s+|\s+(?:audio|video|best|\d{3,4}p|4k)$",
        " ",
        cleaned.strip(),
        flags=re.I,
    )
    cleaned = " ".join(cleaned.replace(" ,", ",").split()).strip(" ,.?!")
    return cleaned or raw


def simplify_query(query: str) -> str:
    kept = [w for w in str(query or "").split() if w.casefold() not in _FILLER]
    return " ".join(kept) or str(query or "").strip()


def parse_request(text: str, *, kind: str | None = None) -> Request:
    raw = " ".join(str(text or "").split())
    url = None
    stripped = raw
    match = _URL.search(raw)
    if match:
        url = canonical_video_url(match.group(0).rstrip(".,)"))
        stripped = raw.replace(match.group(0), " ")
    else:
        for token in raw.split():
            if VIDEO_ID.fullmatch(token) and (
                len(raw.split()) == 1
                or any(c.isdigit() or c in "-_" for c in token)
                or token != token.lower()
            ):
                url = f"https://www.youtube.com/watch?v={token}"
                stripped = raw.replace(token, " ")
                break
    if url is None and VIDEO_ID.fullmatch(clean_query(stripped)):
        url = f"https://www.youtube.com/watch?v={clean_query(stripped)}"
    lower = raw.casefold()
    if kind not in {"video", "audio"}:
        kind = "audio" if _AUDIO.search(stripped) else "video"
    audio_format = "m4a" if re.search(r"\bm4a\b", lower) else "mp3"
    quality = 1080
    q = _QUALITY.search(stripped)
    if q:
        if q.group(1):
            quality = int(q.group(1))
        elif q.group(2):
            quality = 2160
        else:
            quality = 0
    playlist = bool(re.search(r"\bplaylist\b", lower))
    return Request(
        query=clean_query(stripped) if url is None else "",
        raw_query=raw,
        url=url,
        kind=kind,
        audio_format=audio_format,
        quality=quality,
        playlist=playlist,
    )


def rank_results(query: str, results: list, *, allow_long: bool = False) -> list:
    qwords = set(_words(query))
    scored = []
    for result in results:
        if not isinstance(result, dict) or not VIDEO_ID.fullmatch(
            str(result.get("id") or "")
        ):
            continue
        duration = result.get("duration")
        if isinstance(duration, (int, float)) and (
            duration < MIN_SECONDS or (duration >= MAX_SECONDS and not allow_long)
        ):
            continue
        title = str(result.get("title") or "")
        twords = set(_words(title))
        overlap = len(qwords & twords) / len(qwords) if qwords else 0.0
        views = result.get("view_count")
        bonus = (
            min(math.log10(views), 8) / 8 * 0.1
            if isinstance(views, (int, float)) and views > 0
            else 0.0
        )
        penalty = 0.2 if _NOISE.search(title) and not _NOISE.search(query) else 0.0
        scored.append(Scored(result, round(overlap + bonus - penalty, 4)))
    return sorted(scored, key=lambda s: -s.score)


def decide(scored: list) -> Decision:
    if not scored:
        return Decision("none", [])
    top = scored[0].score
    if top >= DOWNLOAD_SCORE and (
        len(scored) == 1 or top - scored[1].score >= CLEAR_MARGIN
    ):
        return Decision("download", scored[:1])
    return Decision("confirm", scored[:3])


def map_error(message: str) -> str:
    m = str(message or "").lower()
    rules = (
        ("age_restricted", ("confirm your age", "age-restricted", "age restricted")),
        (
            "authentication",
            ("not a bot", "sign in to confirm", "cookies", "decrypt", "keyring"),
        ),
        (
            "region_blocked",
            ("in your country", "geo-restrict", "geo restrict", "geoblock"),
        ),
        ("private", ("private video",)),
        ("removed", ("removed", "terminated", "copyright")),
        ("no_ffmpeg", ("ffmpeg", "ffprobe")),
        ("unavailable", ("unavailable", "not found", "404")),
        (
            "network",
            (
                "timed out",
                "temporary failure",
                "connection",
                "network is unreachable",
                "getaddrinfo",
                "urlopen error",
                "http error 5",
            ),
        ),
    )
    for code, needles in rules:
        if any(n in m for n in needles):
            return code
    return "failed"


def explain_error(message: str) -> str:
    return ERROR_SAY[map_error(message)]


def _default_runner(args: list, timeout: float) -> bytes:
    done = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    if done.returncode != 0:
        raise YouTubeSearchError(explain_error(done.stderr.decode("utf8", "ignore")))
    return done.stdout


def search(
    query: str, n: int = SEARCH_N, *, runner=None, binary=None, timeout: float = 30.0
) -> list:
    return _flat_target(
        f"ytsearch{int(n)}:{query}", runner=runner, binary=binary, timeout=timeout
    )


def playlist_url(text):
    from urllib.parse import parse_qs, urlparse

    match = _URL.search(text)
    if not match:
        return None
    parts = urlparse(match.group().rstrip(".,)"))
    if (
        parts.scheme not in {"https", "http"}
        or parts.hostname
        not in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
        or parts.username
        or parts.password
    ):
        return None
    value = parse_qs(parts.query).get("list", [""])[0]
    return (
        f"https://www.youtube.com/playlist?list={value}"
        if re.fullmatch(r"[A-Za-z0-9_-]+", value)
        else None
    )


def video_details(url, *, runner=None):
    entries = _flat_target(url, runner=runner, timeout=60)
    return entries[0] if entries else {}


def playlist_entries(url, *, runner=None):
    return _flat_target(url, runner=runner, timeout=60)


def _flat_target(target, *, runner=None, binary=None, timeout=30):
    binary = binary or shutil.which("yt-dlp")
    if not binary:
        raise YouTubeSearchError("yt-dlp isn't installed, Sir.")
    args = [
        binary,
        "--ignore-config",
        "--flat-playlist",
        "--dump-single-json",
        "--no-cache-dir",
    ]
    browser = os.environ.get("JARVIS_YOUTUBE_COOKIES_BROWSER", "brave").strip()
    if browser.lower() not in {"", "off", "none", "0"}:
        args += ["--cookies-from-browser", browser]
    if node := shutil.which("node"):
        args += ["--js-runtimes", f"node:{node}"]
    args.append(target)
    try:
        data = json.loads((runner or _default_runner)(args, timeout))
    except subprocess.TimeoutExpired as exc:
        raise YouTubeSearchError(ERROR_SAY["network"]) from exc
    except (ValueError, OSError) as exc:
        raise YouTubeSearchError(ERROR_SAY["failed"]) from exc
    entries = (
        data.get("entries", [data] if data.get("id") else [])
        if isinstance(data, dict)
        else None
    )
    return [e for e in entries or [] if isinstance(e, dict)]


def resolve_query(request: Request, *, runner=None) -> Decision:
    tried = []
    for query in (request.query, simplify_query(request.query), request.raw_query):
        if not query or query in tried:
            continue
        tried.append(query)
        ranked = rank_results(
            query,
            search(query, runner=runner),
            allow_long=bool(
                re.search(
                    r"\b(?:long|stream|hours?|marathon)\b", request.raw_query, re.I
                )
            ),
        )
        if ranked:
            return decide(ranked)
    return Decision("none", [])
