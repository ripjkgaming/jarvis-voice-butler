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


# --- regressions from the live sweep (2026-09-27) ---------------------

LIVE_APPS = [
    AppEntry(name="Steam", argv=["steam"]),
    AppEntry(name="LACT", argv=["lact"], keywords="GPU Control Application"),
    AppEntry(name="ZCode", argv=["zcode"]),
    AppEntry(name="Visual Studio Code", argv=["code"], keywords="Text Editor vscode"),
    AppEntry(name="Google Play Store", argv=["gps"]),
    AppEntry(name="Discord", argv=["discord"]),
    AppEntry(name="OBS Studio", argv=["obs"]),
    AppEntry(name="Jarvis Projects", argv=["jp"]),
    AppEntry(name="ChatGPT", argv=["chatgpt"]),
]
LIVE_KNOWN = {"google": "https://www.google.com", "chatgpt": "https://chat.openai.com"}


def _live(q, history=()):
    return resolve_launch(
        q,
        apps=LIVE_APPS,
        history=list(history),
        known_sites=LIVE_KNOWN,
        llm=_no_llm,
        lucky=_no_web,
    )


def test_uninstalled_app_is_not_swapped_for_a_lookalike():
    # "telegram" ~ "steam" and "slack" ~ "lact" only by letters: search instead.
    for q in ("telegram", "slack"):
        d = _live(q)
        assert d.kind == "url" and d.reason == "web-search", (q, d)


def test_spaced_nickname_hits_compact_keyword():
    d = _live("vs code")
    assert d.kind == "app" and d.target == "Visual Studio Code"


def test_known_site_beats_partial_app_and_history():
    hist = [("translate - Google Search", "https://www.google.com/search?q=x", 1.0)]
    d = _live("google", history=hist)
    assert d.kind == "url" and d.target == "https://www.google.com"


def test_exact_app_name_still_beats_known_site():
    d = _live("chatgpt")
    assert d.kind == "app" and d.target == "ChatGPT"


def test_leading_verbs_and_wakeword_are_stripped():
    for q, want in (
        ("open discord", "Discord"),
        ("fire up obs", "OBS Studio"),
        ("jarvis open discord", "Discord"),
        ("Hey Jarvis, please launch the steam app", "Steam"),
    ):
        d = _live(q)
        assert d.kind == "app" and d.target == want, (q, d)


def test_strip_filler_leading() -> None:
    from system.launcher import strip_filler

    assert strip_filler("jarvis, open spotify") == "spotify"
    assert strip_filler("could you bring up my files") == "files"
    # A bare verb is kept rather than emptied.
    assert strip_filler("open") == "open"


def test_history_site_named_by_a_query_word(tmp_path):
    import sqlite3

    from system.launcher import history_sites

    db = tmp_path / "History"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE urls (url TEXT, title TEXT, visit_count INT)")
    con.execute(
        "INSERT INTO urls VALUES ('https://classroom.google.com/h', 'Classroom', 40)"
    )
    con.commit()
    con.close()
    hits = history_sites("google classroom", paths=(db,))
    assert hits and hits[0][1] == "https://classroom.google.com/h"
    # Beats a weak app lookalike when the arbiter is offline.
    apps = [AppEntry(name="Google Chrome", argv=["chrome"], keywords="Web Browser")]
    d = resolve_launch(
        "google classroom",
        apps=apps,
        history=hits,
        known_sites={},
        llm=_no_llm,
        lucky=_no_web,
    )
    assert d.kind == "url" and "classroom" in d.target


# --- owner's priority apps (always win, on every launch path) ---------

PRI_APPS = [
    AppEntry(name="Sober", argv=["flatpak", "run", "org.vinegarhq.Sober"]),
    AppEntry(
        name="DaVinci Resolve",
        argv=["/usr/bin/env", "QT_QPA_PLATFORM=xcb", "/opt/resolve/bin/resolve"],
    ),
    AppEntry(name="Dolphin", argv=["/usr/bin/dolphin"], keywords="File Manager"),
    AppEntry(name="Konsole", argv=["/usr/bin/konsole"], keywords="Terminal"),
    AppEntry(name="wdb terminal", argv=["/usr/bin/konsole", "-e", "python3", "x.py"]),
    AppEntry(name="Brave Web Browser", argv=["/usr/bin/brave-browser-stable"]),
    AppEntry(name="Brave", argv=["flatpak", "run", "com.brave.Browser"]),
    AppEntry(name="Spotify", argv=["spotify"], keywords="music"),
]


def test_priority_apps_resolve_first():
    from system.launcher import priority_app

    cases = {
        "sober": ["flatpak", "run", "org.vinegarhq.Sober"],
        "Sober.": ["flatpak", "run", "org.vinegarhq.Sober"],
        "roblox": ["flatpak", "run", "org.vinegarhq.Sober"],
        "sober on the laptop please": ["flatpak", "run", "org.vinegarhq.Sober"],
        "davinci resolve": [
            "/usr/bin/env",
            "QT_QPA_PLATFORM=xcb",
            "/opt/resolve/bin/resolve",
        ],
        "resolve": ["/usr/bin/env", "QT_QPA_PLATFORM=xcb", "/opt/resolve/bin/resolve"],
        "files": ["/usr/bin/dolphin"],
        "dolphin": ["/usr/bin/dolphin"],
        "file manager": ["/usr/bin/dolphin"],
        "konsole": ["/usr/bin/konsole"],
        "terminal": ["/usr/bin/konsole"],
        "brave": ["/usr/bin/brave-browser-stable"],
        "brave browser": ["/usr/bin/brave-browser-stable"],
    }
    for q, argv in cases.items():
        d = priority_app(q, apps=PRI_APPS, running=lambda d: False)
        assert d is not None and d.kind == "app" and d.argv == argv, (q, d)
        assert d.reason == "priority"
    assert priority_app("spotify", apps=PRI_APPS) is None


def test_claude_runs_in_konsole():
    from system.launcher import priority_app

    for q in ("claude", "claude code", "Claude."):
        d = priority_app(q, apps=PRI_APPS, which=lambda b: f"/usr/bin/{b}")
        assert d is not None and d.argv[:1] == ["/usr/bin/konsole"], (q, d)
        assert d.argv[-2:] == ["-e", "/usr/bin/claude"]
        assert d.target == "Claude"


def test_resolve_launch_uses_priority_before_fuzzy():
    # "terminal" used to hit the "wdb terminal" script entry.
    d = resolve_launch(
        "terminal",
        apps=PRI_APPS,
        history=[],
        known_sites={},
        llm=_no_llm,
        lucky=_no_web,
    )
    assert d.argv == ["/usr/bin/konsole"]


def test_single_instance_priority_app_already_running():
    # Sober pops a "Crash: already running" dialog on a second launch.
    from system.launcher import priority_app

    d = priority_app("sober", apps=PRI_APPS, running=lambda d: True)
    assert d is not None and d.argv == [] and "already running" in d.say
    d = priority_app("resolve", apps=PRI_APPS, running=lambda d: True)
    assert d is not None and d.argv == []
    # Multi-window apps always launch a fresh window.
    d = priority_app("konsole", apps=PRI_APPS, running=lambda d: True)
    assert d is not None and d.argv == ["/usr/bin/konsole"]


def test_is_running_checks(monkeypatch):
    from system import launcher

    monkeypatch.setattr(
        launcher,
        "_run_quiet",
        lambda argv: "org.vinegarhq.Sober\ncom.x.Y\n" if argv[0] == "flatpak" else "",
    )
    assert launcher.is_running(["flatpak", "run", "org.vinegarhq.Sober"]) is True
    assert launcher.is_running(["flatpak", "run", "com.other.App"]) is False
    monkeypatch.setattr(
        launcher, "_run_quiet", lambda argv: "4242\n" if argv[0] == "pgrep" else ""
    )
    assert launcher.is_running(["/usr/bin/env", "A=1", "/opt/resolve/bin/resolve"])
