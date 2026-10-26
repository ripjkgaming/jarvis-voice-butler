import json

import pytest

import youtube_resolve as yr

ID1, ID2, ID3 = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"


def entry(vid, title, duration=60, views=1000):
    return {"id": vid, "title": title, "duration": duration, "view_count": views}


def test_clean_query_strips_command_words():
    assert (
        yr.clean_query("download Tesla Roadster kickflip") == "Tesla Roadster kickflip"
    )
    assert yr.clean_query("get me the video of lofi beats as mp3") == "lofi beats"
    assert (
        yr.clean_query("please save the Tesla Roadster kickflip in 720p")
        == "Tesla Roadster kickflip"
    )
    assert yr.clean_query("download") == "download"


def test_simplify_query_drops_filler():
    assert yr.simplify_query("the official video of Tesla Roadster") == "Tesla Roadster"


def test_parse_video_default():
    r = yr.parse_request("download Tesla Roadster kickflip")
    assert (r.kind, r.query, r.url, r.quality, r.playlist) == (
        "video",
        "Tesla Roadster kickflip",
        None,
        1080,
        False,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Tesla Roadster kickflip as mp3",
        "download the song Tesla",
        "just the sound of X",
        "music only X",
    ],
)
def test_parse_audio_triggers(text):
    r = yr.parse_request(text)
    assert r.kind == "audio" and r.audio_format == "mp3"


def test_parse_m4a_and_quality_and_playlist():
    assert yr.parse_request("X as m4a").audio_format == "m4a"
    assert yr.parse_request("X in 720p").quality == 720
    assert yr.parse_request("X in 4k").quality == 2160
    assert yr.parse_request("X best").quality == 0
    assert yr.parse_request("download playlist X").playlist is True
    assert yr.parse_request("X").playlist is False


def test_parse_url_and_id_and_kind_override():
    r = yr.parse_request(
        "download https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc as mp3"
    )
    assert r.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ" and r.kind == "audio"
    assert (
        yr.parse_request("grab dQw4w9WgXcQ").url
        == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    assert yr.parse_request("Tesla Roadster").url is None
    assert yr.parse_request("X as mp3", kind="video").kind == "video"


def test_rank_filters_and_orders():
    res = [
        entry(ID1, "Tesla Roadster kickflip", 3),
        entry(ID2, "Tesla Roadster kickflip", 40 * 3600),
        entry(ID3, "Tesla Roadster kickflip reaction", 60, 10**9),
        entry("ddddddddddd", "Tesla Roadster kickflip", 60, 10),
    ]
    ranked = yr.rank_results("Tesla Roadster kickflip", res)
    assert [s.result["id"] for s in ranked] == ["ddddddddddd", ID3]
    assert len(yr.rank_results("Tesla Roadster kickflip", res, allow_long=True)) == 3


def test_rank_title_beats_views():
    res = [
        entry(ID1, "Cat video", views=10**9),
        entry(ID2, "Tesla Roadster kickflip", views=5),
    ]
    assert yr.rank_results("Tesla Roadster kickflip", res)[0].result["id"] == ID2


def test_decide():
    scored = yr.Scored
    assert yr.decide([]).action == "none"
    assert (
        yr.decide([scored(entry(ID1, "a"), 0.9), scored(entry(ID2, "b"), 0.5)]).action
        == "download"
    )
    close = yr.decide(
        [
            scored(entry(ID1, "a"), 0.9),
            scored(entry(ID2, "b"), 0.85),
            scored(entry(ID3, "c"), 0.8),
            scored(entry("ddddddddddd", "d"), 0.7),
        ]
    )
    assert close.action == "confirm" and len(close.candidates) == 3
    assert yr.decide([scored(entry(ID1, "a"), 0.3)]).action == "confirm"


def fake(queries_to_entries, seen):
    def runner(args, timeout):
        q = args[-1]
        seen.append(q)
        return json.dumps(
            {"entries": queries_to_entries.get(q.split(":", 1)[1], [])}
        ).encode()

    return runner


def test_search_argv_single_element(monkeypatch):
    seen = []
    monkeypatch.setattr(yr.shutil, "which", lambda n: "/bin/" + n)
    yr.search('x"; rm -rf /', runner=fake({}, seen))
    assert seen == ['ytsearch5:x"; rm -rf /']


def test_resolve_retries_simplified_then_raw(monkeypatch):
    monkeypatch.setattr(yr.shutil, "which", lambda n: "/bin/" + n)
    seen = []
    req = yr.parse_request("download the video of the official Tesla Roadster")
    runner = fake({"Tesla Roadster": [entry(ID1, "Tesla Roadster")]}, seen)
    d = yr.resolve_query(req, runner=runner)
    assert d.action == "download" and len(seen) == 2
    seen.clear()
    assert yr.resolve_query(req, runner=fake({}, seen)).action == "none"
    assert len(seen) == 3


@pytest.mark.parametrize(
    "msg,code",
    [
        (
            "Sign in to confirm your age. This video may be inappropriate",
            "age_restricted",
        ),
        ("Sign in to confirm you are not a bot", "authentication"),
        (
            "The uploader has not made this video available in your country",
            "region_blocked",
        ),
        ("Private video. Sign in", "private"),
        ("This video has been removed by the uploader", "removed"),
        ("Video unavailable", "unavailable"),
        ("ffmpeg not found", "no_ffmpeg"),
        ("<urlopen error Temporary failure in name resolution>", "network"),
        ("boom", "failed"),
    ],
)
def test_map_error(msg, code):
    assert yr.map_error(msg) == code
    assert yr.explain_error(msg) == yr.ERROR_SAY[code]
    assert "metadata" not in yr.ERROR_SAY[code]
