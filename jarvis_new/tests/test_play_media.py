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


def test_youtube_url_uses_shared_metadata_match(monkeypatch) -> None:
    import media_selection
    monkeypatch.setattr(media_selection, "resolve", lambda *a, **k: media_selection.Selection("selected", "metadata_match", "bbbbbbbbbbb", 2))
    assert bridge._youtube_url("x") == "https://www.youtube.com/watch?v=bbbbbbbbbbb"


def test_youtube_url_no_match_opens_nothing(monkeypatch) -> None:
    import media_selection
    monkeypatch.setattr(media_selection, "resolve", lambda *a, **k: media_selection.Selection("unavailable", "no_supported_match"))
    assert bridge._youtube_url("grant wisler") is None


def test_play_media_allowed_for_guests() -> None:
    assert "play_media" in bridge._GUEST_VOICE_TOOLS
