"""Bridge "play X" -> YouTube routing (no real browser or network)."""

import pytest

import bridge


@pytest.mark.parametrize(
    ("text", "tool", "query"),
    [
        ("play a video by grant wisler", "play_media", "grant wisler"),
        ("Play a video by Grant Wisler.", "play_media", "grant wisler"),
        ("play grant wisler on youtube", "play_media", "grant wisler"),
        ("play some music", "play_media", ""),
        ("play a video", "play_media", ""),
        ("play a video about lock picking", "play_media", "lock picking"),
    ],
)
def test_play_phrases_route_to_youtube(text, tool, query) -> None:
    got = bridge._match_voice_tool(text)
    assert got is not None
    assert got[0] == tool
    assert got[1] == {"query": query}


@pytest.mark.parametrize(
    ("text", "tool"),
    [
        ("play", "media_play_pause"),
        ("pause", "media_play_pause"),
        ("play it", "media_play_pause"),
        ("play next song", "media_next"),
        ("play the next video", "media_next"),
        ("lock the computer", "lock"),
    ],
)
def test_media_controls_unchanged(text, tool) -> None:
    assert bridge._match_voice_tool(text)[0] == tool


def test_youtube_url_picks_first_video() -> None:
    html = 'junk "videoId":"dQw4w9WgXcQ" more "videoId":"aaaaaaaaaaa"'
    url = bridge._youtube_url("x", fetch=lambda _u: html)
    assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_youtube_url_falls_back_to_results() -> None:
    url = bridge._youtube_url("grant wisler", fetch=lambda _u: "no ids here")
    assert url == "https://www.youtube.com/results?search_query=grant+wisler"


def test_play_media_allowed_for_guests() -> None:
    assert "play_media" in bridge._GUEST_VOICE_TOOLS
