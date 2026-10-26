"""Synthetic provider metadata only; no browser, network, audio or models."""

import asyncio
import json

import pytest

from media_selection import resolve, resolve_async, select


def video(vid="aaaaaaaaaaa", **fields):
    return dict(
        id=vid,
        title="British Grand Prix race feed",
        description="British Grand Prix live race footage at Silverstone",
        channel="Fixture broadcaster",
        channel_id="fixture-source",
        live_status="is_live",
        is_live=True,
        was_live=False,
        checked_at=100,
        **fields,
    )


def test_compare_footage_not_first_commentary():
    bad = video()
    bad.update(description="British Grand Prix watchalong commentary. No race footage.")
    good = video("bbbbbbbbbbb")
    assert (
        select("live footage of F1 British Grand Prix", [bad, good], now=100).video_id
        == good["id"]
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"live_status": "was_live", "is_live": False},
        {"live_status": "is_upcoming", "is_live": True},
        {"checked_at": 0},
        {"release_timestamp": 101},
        {"was_live": True},
    ],
)
def test_live_is_positive_and_fresh(fields):
    candidate = video()
    candidate.update(fields)
    assert (
        select("live F1 British Grand Prix", [candidate], now=100).decision
        != "selected"
    )


def test_missing_location_clarifies_before_fetch():
    def forbidden(*args):
        raise AssertionError("no metadata request")

    assert resolve("live fireworks", runner=forbidden).decision == "clarify"


def test_fireworks_not_race_or_other_city():
    race = video()
    race.update(
        title="Sydney fireworks Grand Prix",
        description="Sydney motor race. Fireworks is our channel name.",
    )
    other = video("bbbbbbbbbbb")
    other.update(
        title="London fireworks", description="London fireworks display over river"
    )
    good = video("ccccccccccc")
    good.update(
        title="Sydney fireworks", description="Sydney Harbour fireworks display footage"
    )
    assert (
        select("Sydney fireworks footage", [race, other, good], now=100).video_id
        == good["id"]
    )


def test_tie_does_not_launch_first():
    assert (
        select(
            "F1 British Grand Prix footage", [video(), video("bbbbbbbbbbb")], now=100
        ).decision
        == "clarify"
    )


def test_title_only_and_duration_unknown_refuse():
    candidate = video()
    candidate["description"] = ""
    assert select("F1 British Grand Prix", [candidate], now=100).decision != "selected"
    candidate = video()
    assert (
        select("F1 British Grand Prix over 15 minutes", [candidate], now=100).decision
        != "selected"
    )


def test_no_constraint_truncation_or_non_ascii_id():
    assert select("x" * 201, [video()], now=100).decision == "clarify"
    candidate = video()
    candidate["id"] = "é" * 11
    assert select("F1 British Grand Prix", [candidate], now=100).decision != "selected"


def test_resolver_bounds_and_same_id_details():
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        if argv[-1].startswith("ytsearch5:"):
            return json.dumps(
                {"entries": [{"id": "aaaaaaaaaaa"}, {"id": "bbbbbbbbbbb"}]}
            ).encode()
        candidate = video(argv[-1].split("=")[-1])
        candidate["description"] = "watchalong commentary"
        return json.dumps(candidate).encode()

    result = resolve(
        "live F1 British Grand Prix", runner=runner, binary="fixture-ytdlp"
    )
    assert result.decision == "unavailable"
    assert len(calls) == 3
    assert all(
        "--ignore-config" in call and "--no-plugin-dirs" in call for call in calls
    )


def test_async_resolver_never_swallows_cancellation():
    async def runner(argv, timeout):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            resolve_async("F1 British Grand Prix", runner=runner, binary="fixture")
        )


def test_resolution_is_not_a_fireworks_location():
    assert (
        select("live fireworks in 4K", [video()], now=100).reason == "location_required"
    )


def test_generic_footage_needs_positive_descriptor():
    candidate = video()
    candidate.update(
        title="Olympics opening ceremony LIVE",
        description="Olympics opening ceremony schedule and ticket information",
    )
    assert (
        select("live Olympics opening ceremony footage", [candidate], now=100).decision
        != "selected"
    )


def test_broad_music_compares_multiple_qualifying_clips():
    first = video()
    first.update(title="Jazz music ten hour concert", description="Jazz music concert")
    second = video("bbbbbbbbbbb")
    second.update(title="Jazz music", description="Jazz music performance")
    result = select("jazz music", [first, second], now=100)
    assert result.video_id == second["id"] and result.candidates_checked == 2


def test_detail_id_cannot_replace_requested_candidate():
    def runner(argv, timeout):
        if argv[-1].startswith("ytsearch5:"):
            return json.dumps({"entries": [{"id": "aaaaaaaaaaa"}]}).encode()
        return json.dumps(video("bbbbbbbbbbb")).encode()

    assert (
        resolve("F1 British Grand Prix", runner=runner, binary="fixture").decision
        == "unavailable"
    )


def test_oversized_async_metadata_is_killed_and_reaped(monkeypatch):
    import media_selection

    class Process:
        returncode = None
        killed = waited = False

        @property
        def stdout(self):
            return self

        async def read(self, size):
            return b"x" * size

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True
            return self.returncode

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(ValueError, match="metadata_limit"):
        asyncio.run(media_selection._run_async(["fixture"], 1))
    assert process.killed and process.waited
