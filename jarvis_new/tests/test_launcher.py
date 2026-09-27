"""Universal launcher: app/site/search resolution, fully injectable."""

from system.launcher import (
    AppEntry,
    _parse_desktop,
    _score,
    lucky_url,
    resolve_launch,
)

APPS = [
    AppEntry(name="OBS Studio", argv=["obs"], keywords="streaming recording"),
    AppEntry(
        name="LibreOffice Writer",
        argv=["libreoffice", "--writer"],
        keywords="word document",
    ),
    AppEntry(name="Dolphin", argv=["dolphin"], keywords="file manager"),
    AppEntry(name="Spotify", argv=["spotify"], keywords="music"),
    AppEntry(name="Konsole", argv=["konsole"], keywords="terminal"),
]

KNOWN = {"youtube": "https://www.youtube.com", "github": "https://github.com"}


def _no_llm(query, options):
    return None  # arbiter unavailable -> highest-confidence candidate wins


def _no_web(query, **_):
    return f"https://www.google.com/search?q={query}"


def test_strong_app_match_beats_everything():
    d = resolve_launch(
        "obs", apps=APPS, history=[], known_sites=KNOWN, llm=_no_llm, lucky=_no_web
    )
    assert d.kind == "app" and d.target == "OBS Studio" and d.argv == ["obs"]


def test_dolphin_does_not_become_spotify():
    d = resolve_launch(
        "dolphin", apps=APPS, history=[], known_sites=KNOWN, llm=_no_llm, lucky=_no_web
    )
    assert d.target == "Dolphin"


def test_fuzzy_app_name():
    d = resolve_launch(
        "libre office",
        apps=APPS,
        history=[],
        known_sites=KNOWN,
        llm=_no_llm,
        lucky=_no_web,
    )
    assert d.kind == "app" and d.target.startswith("LibreOffice")


def test_visited_site_when_no_app():
    hist = [("Notion", "https://www.notion.so/home", 0.95)]
    d = resolve_launch(
        "notion", apps=APPS, history=hist, known_sites=KNOWN, llm=_no_llm, lucky=_no_web
    )
    assert d.kind == "url" and "notion" in d.target


def test_known_site_without_history():
    d = resolve_launch(
        "youtube", apps=APPS, history=[], known_sites=KNOWN, llm=_no_llm, lucky=_no_web
    )
    assert d.kind == "url" and d.target == "https://www.youtube.com"


def test_web_search_fallback():
    d = resolve_launch(
        "quixotic widget factory",
        apps=APPS,
        history=[],
        known_sites=KNOWN,
        llm=_no_llm,
        lucky=_no_web,
    )
    assert d.kind == "url" and d.reason == "web-search"


def test_arbiter_breaks_app_vs_history_tie():
    # "photoshop" fuzzily matches the app "Photopea" (mid score) AND a
    # visited photoshop site (mid score): both land in the ambiguous
    # zone, so the arbiter decides. It picks index 1 (the site).
    hist = [("Photoshop on the web", "https://photoshop.adobe.com", 0.75)]
    picked = {}

    def llm(query, options):
        picked["options"] = options
        return 1

    apps = [AppEntry(name="Photopea", argv=["photopea"], keywords="image editor")]
    d = resolve_launch(
        "photoshop", apps=apps, history=hist, known_sites={}, llm=llm, lucky=_no_web
    )
    assert d.kind == "url" and d.target == "https://photoshop.adobe.com"
    assert len(picked["options"]) == 2


def test_arbiter_fallback_to_confidence_when_offline():
    hist = [("Photoshop on the web", "https://photoshop.adobe.com", 0.72)]
    apps = [AppEntry(name="Photopea", argv=["photopea"], keywords="image editor")]
    d = resolve_launch(
        "photoshop", apps=apps, history=hist, known_sites={}, llm=_no_llm, lucky=_no_web
    )
    # Arbiter offline -> highest-confidence candidate (the site) wins.
    assert d.kind == "url"


def test_parse_desktop_flatpak():
    text = (
        "[Desktop Entry]\nType=Application\nName=WhatSie\n"
        "Exec=flatpak run --branch=stable com.ktechpit.whatsie %U\n"
    )
    entry = _parse_desktop(
        text, which=lambda n: "/usr/bin/flatpak" if n == "flatpak" else None
    )
    assert entry and entry.argv == ["flatpak", "run", "com.ktechpit.whatsie"]


def test_parse_desktop_skips_nodisplay():
    text = (
        "[Desktop Entry]\nType=Application\nName=Hidden\nExec=hidden\nNoDisplay=true\n"
    )
    assert _parse_desktop(text, which=lambda n: "/usr/bin/hidden") is None


def test_lucky_unwraps_google_redirect():
    class FakeResp:
        def __init__(self, url, headers):
            self._url = url
            self.headers = headers

        def geturl(self):
            return self._url

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=0):
        return FakeResp("https://www.google.com/url?q=https://obsproject.com/", {})

    assert lucky_url("obs studio", opener=opener) == "https://obsproject.com/"


def test_score_monotonic():
    assert _score("obs", "OBS Studio") > _score("obs", "Spotify")


def test_strip_filler_drops_location_and_politeness() -> None:
    from system.launcher import strip_filler

    assert strip_filler("sober on the laptop") == "sober"
    assert strip_filler("Sober on my laptop please.") == "Sober"
    assert strip_filler("spotify for me") == "spotify"
    assert strip_filler("steam") == "steam"
    # Never strips the whole query away.
    assert strip_filler("please") == "please"
