"""Bounded metadata selection. A match never proves frames, playback or hearing.

Only public YouTube metadata is requested; no user profile, cookies or model.
Both bridge and SDK paths use this policy. Provider text is untrusted data.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import selectors
import shutil
import subprocess
import time
from dataclasses import dataclass

MAX_CANDIDATES = 5
MAX_BYTES = 512_000
DEADLINE = 20.0
_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_NOISE = {
    "play",
    "show",
    "me",
    "a",
    "an",
    "the",
    "of",
    "on",
    "in",
    "at",
    "from",
    "for",
    "by",
    "about",
    "video",
    "videos",
    "footage",
    "live",
    "hd",
    "uhd",
    "resolution",
    "high",
    "definition",
    "livestream",
    "stream",
    "current",
    "now",
    "right",
    "please",
    "sir",
    "youtube",
    "music",
    "over",
    "longer",
    "than",
    "minutes",
    "minute",
    "min",
    "hours",
    "hour",
    "some",
    "something",
}
_BAD_FOOTAGE = re.compile(
    r"watch\s*along|commentary|reaction|podcast|audio.only|live timing|no (?:race |video )?footage|cannot show|simulation|gameplay",
    re.I,
)
_FIREWORKS = re.compile(
    r"fireworks? (?:display|show|footage)|(?:display|show) of fireworks?", re.I
)
_NAMED_LOCATION = re.compile(
    r"\b(?:sydney|london|dubai|singapore|paris|tokyo|hong kong|new york|san francisco|los angeles|marina bay)\b",
    re.I,
)
_GENERIC_LOCATION = {
    "waterfront",
    "harbour",
    "harbor",
    "river",
    "city",
    "venue",
    "downtown",
    "spectacular",
    "beautiful",
    "amazing",
    "fireworks",
    "firework",
    "display",
    "show",
    "footage",
    "live",
}


@dataclass(frozen=True)
class Selection:
    decision: str
    reason: str
    video_id: str | None = None
    candidates_checked: int = 0

    @property
    def url(self):
        return (
            f"https://www.youtube.com/watch?v={self.video_id}"
            if self.video_id
            else None
        )

    @property
    def say(self):
        if self.decision == "selected":
            return "Requested the metadata-matched video in desktop Brave; playback is not verified."
        if self.reason == "location_required":
            return "Which city or venue should the fireworks video show?"
        if self.reason == "ambiguous_matches":
            return "Several videos match. Please name the broadcaster or give the exact video link."
        if self.reason == "time_required":
            return (
                "Please give the event date and location, or the exact live video link."
            )
        if self.reason == "query_too_long":
            return "Please shorten the request or give the exact video link; I have not opened a substitute."
        return "I could not establish a matching video from its metadata. Please give the exact event, broadcaster or video link."

    def summary(self):
        # Do not send arbitrary provider description/title into speech or model context.
        return {
            "decision": self.decision,
            "reason": self.reason,
            "video_id": self.video_id,
            "candidates_checked": self.candidates_checked,
            "basis": "provider_metadata_only",
        }


def _text(value):
    return value[:16_000].casefold() if isinstance(value, str) else ""


def _words(text):
    text = re.sub(r"\bformula\s*(?:one|1)\b", "f1", text.casefold())
    words = set(re.findall(r"[^\W_]+", text)) - _NOISE
    return words - {"f1"} if {"grand", "prix"} <= words else words


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def preflight(query):
    if not isinstance(query, str) or len(query) > 200:
        return Selection("clarify", "query_too_long")
    query = query.strip().casefold()
    if "://" in query:
        return Selection("clarify", "explicit_url_use_open_url")
    if re.search(r"\b(no|not|except|without|exclude|excluding)\b", query):
        return Selection("clarify", "unsupported_constraint")
    if re.search(r"\b(today|tonight|tomorrow|new year|new year's)\b", query):
        return Selection("clarify", "time_required")
    words = _words(query)
    if re.search(r"\bfireworks?\b", query):
        # A preposition alone is not geographic evidence (at midnight / in 4K).
        # Unsupported bare place names must clarify rather than infer a city.
        location = re.search(r"\b(?:city|venue)\s+(?:of\s+)?(.+)", query)
        explicit = _words(location[1]) - _GENERIC_LOCATION if location else set()
        explicit = {term for term in explicit if term[0].isalpha()}
        if not _NAMED_LOCATION.search(query) and not explicit:
            return Selection("clarify", "location_required")
    if not words:
        return Selection("clarify", "event_required")
    return None


def select(query, candidates, *, now=None):
    refusal = preflight(query)
    if refusal:
        return refusal
    now = time.time() if now is None else now
    q = query.casefold()
    live = bool(re.search(r"\b(live|livestream|current|right now)\b", q))
    commentary = bool(re.search(r"\b(commentary|watchalong|podcast|reaction)\b", q))
    fireworks = bool(re.search(r"\bfireworks?\b", q))
    race = bool(re.search(r"\bf1\b|formula\s*(one|1)|grand prix", q))
    footage = bool(re.search(r"\bfootage\b|video feed", q)) or race or fireworks
    duration = re.search(r"(?:over|longer than)\s+(\d+)\s*(minutes?|mins?|hours?)", q)
    terms = _words(q)
    if duration:
        terms -= {duration[1]}
    accepted = []
    seen = set()
    checked = 0
    for candidate in list(candidates)[:MAX_CANDIDATES]:
        checked += 1
        if not isinstance(candidate, dict):
            continue
        vid = candidate.get("id")
        if not isinstance(vid, str) or not _ID.fullmatch(vid) or vid in seen:
            continue
        seen.add(vid)
        title, desc = _text(candidate.get("title")), _text(candidate.get("description"))
        source = _text(candidate.get("channel"))
        if (
            not title
            or not desc
            or not source
            or not _text(candidate.get("channel_id"))
        ):
            continue
        if footage and re.search(
            r"\b(no|unavailable|cannot|can't|won't|unable|excluded)\b", desc
        ):
            continue
        # Unstructured negation cannot safely establish which constraint it denies.
        if re.search(r"\b(not|except|without|exclude|excluding)\b", desc):
            continue
        combined = _words(title + " " + desc + " " + source)
        if not terms <= combined:
            continue
        if live:
            stamp = candidate.get("checked_at")
            release = candidate.get("release_timestamp")
            if (
                candidate.get("is_live") is not True
                or candidate.get("live_status") != "is_live"
                or candidate.get("was_live") is True
                or not _number(stamp)
                or not 0 <= now - stamp <= 30
                or (release is not None and (not _number(release) or release > now))
            ):
                continue
            if not commentary and re.search(
                r"\breplay\b|\barchive[ds]?\b|\brecorded\b|pre.recorded|\bhighlights\b|\bpremiere\b",
                title + " " + desc,
            ):
                continue
        if commentary:
            if not re.search(r"commentary|watch\s*along|podcast|reaction", desc):
                continue
        elif (footage and _BAD_FOOTAGE.search(title + " " + desc)) or (
            footage
            and not re.search(
                r"\bfootage\b|video feed|live feed|video of|live coverage", desc
            )
        ):
            continue
        if race and not commentary:
            if _BAD_FOOTAGE.search(title + " " + desc) or not re.search(
                r"(?:race|trackside|onboard|racing) (?:video |live )?(?:footage|feed|coverage)",
                desc,
            ):
                continue
            # Location/event qualifiers must occur in description too, so a misleading
            # title cannot override a description about a different Grand Prix.
            qualifiers = terms - {"f1", "grand", "prix", "race", "racing"}
            if not qualifiers <= _words(desc):
                continue
        if fireworks:
            if not _FIREWORKS.search(desc) or re.search(
                r"motor race|grand prix|watchalong|commentary|simulation", desc
            ):
                continue
            qualifiers = terms - {"firework", "fireworks", "display"}
            if not qualifiers <= _words(desc):
                continue
        if duration:
            seconds = candidate.get("duration")
            threshold = int(duration[1]) * (
                3600 if duration[2].startswith("hour") else 60
            )
            if not _number(seconds) or seconds <= threshold:
                continue
        # Require independent descriptor corroboration for general queries too.
        if not (terms & _words(desc)):
            continue
        accepted.append((vid, len(terms & _words(title)), len(_words(title) - terms)))
    if len(accepted) == 1:
        return Selection("selected", "metadata_match", accepted[0][0], checked)
    if (
        accepted
        and not (live or footage or commentary)
        and re.search(r"\b(music|lofi|jazz|ambient|chill|classical|piano)\b", q)
    ):
        # Broad music requests permit any fully qualifying clip. Compare all
        # candidates and prefer title coverage/specificity, with stable ID tie-break.
        chosen = sorted(accepted, key=lambda item: (-item[1], item[2], item[0]))[0]
        return Selection("selected", "metadata_music_match", chosen[0], checked)
    return Selection(
        "clarify" if accepted else "unavailable",
        "ambiguous_matches" if accepted else "no_supported_match",
        candidates_checked=checked,
    )


def _args(binary, target, *, search=False):
    return [
        binary,
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-cache-dir",
        "--skip-download",
        "--dump-single-json",
        "--socket-timeout",
        "4",
        "--retries",
        "0",
        "--extractor-retries",
        "0",
        "--flat-playlist" if search else "--no-playlist",
        target,
    ]


def _decode(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError("metadata_limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("metadata_shape")
    return value


def _ids(data):
    entries = data.get("entries")
    if not isinstance(entries, list):
        return []
    return list(
        dict.fromkeys(
            entry["id"]
            for entry in entries[:MAX_CANDIDATES]
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and _ID.fullmatch(entry["id"])
        )
    )


def _run(argv, timeout):
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    ) as proc:
        try:
            end = time.monotonic() + timeout
            out = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while True:
                    remaining = end - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError()
                    chunk = os.read(
                        proc.stdout.fileno(), min(8192, MAX_BYTES + 1 - len(out))
                    )
                    if not chunk:
                        break
                    out.extend(chunk)
                    if len(out) > MAX_BYTES:
                        raise ValueError("metadata_limit")
            proc.wait(timeout=max(0.001, end - time.monotonic()))
            if proc.returncode != 0:
                raise ValueError("metadata_failed")
            return bytes(out)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


async def _run_async(argv, timeout):
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:

        async def collect():
            out = bytearray()
            while True:
                chunk = await proc.stdout.read(min(8192, MAX_BYTES + 1 - len(out)))
                if not chunk:
                    break
                out.extend(chunk)
                if len(out) > MAX_BYTES:
                    raise ValueError("metadata_limit")
            await proc.wait()
            return bytes(out)

        out = await asyncio.wait_for(collect(), timeout)
        if proc.returncode != 0:
            raise ValueError("metadata_failed")
        return out
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            # Shield reap so a second cancellation cannot abandon the owned child.
            task = asyncio.create_task(proc.wait())
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()


def resolve(query, *, runner=None, binary=None, guard=lambda: None):
    refusal = preflight(query)
    if refusal:
        return refusal
    binary = binary or shutil.which("yt-dlp")
    if not binary:
        return Selection("unavailable", "metadata_unavailable")
    runner = runner or _run
    end = time.monotonic() + DEADLINE

    def fetch(target, search=False):
        guard()
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        return _decode(runner(_args(binary, target, search=search), min(6, remaining)))

    try:
        ids = _ids(fetch("ytsearch5:" + query, True))
        candidates = []
        for vid in ids:
            detail = fetch("https://www.youtube.com/watch?v=" + vid)
            if detail.get("id") == vid:
                detail["checked_at"] = time.time()
                candidates.append(detail)
        return select(query, candidates)
    except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired):
        return Selection("unavailable", "metadata_unavailable")


async def resolve_async(query, *, runner=None, binary=None, guard=lambda: None):
    refusal = preflight(query)
    if refusal:
        return refusal
    binary = binary or shutil.which("yt-dlp")
    if not binary:
        return Selection("unavailable", "metadata_unavailable")
    runner = runner or _run_async
    end = time.monotonic() + DEADLINE

    async def fetch(target, search=False):
        guard()
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        return _decode(
            await runner(_args(binary, target, search=search), min(6, remaining))
        )

    try:
        ids = _ids(await fetch("ytsearch5:" + query, True))
        candidates = []
        for vid in ids:
            detail = await fetch("https://www.youtube.com/watch?v=" + vid)
            if detail.get("id") == vid:
                detail["checked_at"] = time.time()
                candidates.append(detail)
        return select(query, candidates)
    except (OSError, ValueError, TimeoutError):
        return Selection("unavailable", "metadata_unavailable")


def receipt(selection=None, *, launched=False, reason=""):
    from action_outcome import ActionOutcome, Dispatch, OutcomeState

    outcome = ActionOutcome(
        "url.launch",
        OutcomeState.LAUNCH_REQUESTED if launched else OutcomeState.BLOCKED,
        dispatch=Dispatch.STARTED if launched else Dispatch.NOT_STARTED,
        surface="owner_desktop",
        reason=reason or (selection.reason if selection else "playlist"),
    )
    result = outcome.result()
    if selection is not None:
        result.update(
            selection=selection.summary(),
            say=selection.say if launched or not reason else outcome.say,
        )
    return result
