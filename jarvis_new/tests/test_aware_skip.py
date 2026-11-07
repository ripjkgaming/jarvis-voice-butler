"""Always Aware skip rules + sensitive matcher (PLAN.md §1.2), table-driven."""

import json

import pytest

from aware import skip


def ctx(**kw):
    base = {
        "env_ok": True,
        "killed": False,
        "state": {"enabled": True, "paused_until": 0},
        "now": 1000.0,
        "guest": False,
        "private_call": False,
        "locked": False,
        "watch_running": False,
        "visual_watch": False,
        "focus_active": False,
        "idle": "active",
        "foreground": {"app": "org.kde.konsole", "title": "pytest - konsole"},
        "foreground_url": "",
        "password_focused": False,
        "outputs": ["HDMI-A-2", "eDP-1", "DP-1"],
        "hud": "DP-1",
        "school": False,
    }
    base.update(kw)
    return skip.Context(**base)


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"env_ok": False}, "disabled"),
        ({"state": {"enabled": False}}, "disabled"),
        ({"state": {"enabled": True, "paused_until": 2000}}, "paused"),
        ({"killed": True}, "killed"),
        ({"guest": True}, "guest"),
        ({"private_call": True}, "private_call"),
        ({"locked": True}, "locked"),
        ({"watch_running": True}, "watch_mode"),
        ({"visual_watch": True}, "visual_watch"),
        ({"idle": "idle"}, "idle"),
        ({"idle": "unknown"}, "idle"),
        (
            {"foreground": {"app": "org.keepassxc.KeePassXC", "title": "db"}},
            "sensitive_foreground",
        ),
        (
            {
                "foreground": {
                    "app": "brave-browser",
                    "title": "Sign in - Google Accounts - Brave",
                }
            },
            "sensitive_foreground",
        ),
        (
            {
                "foreground": {"app": "brave-browser", "title": "x"},
                "foreground_url": "https://accounts.google.com/x",
            },
            "sensitive_foreground",
        ),
        (
            {
                "foreground": {"app": "brave-browser", "title": "x"},
                "foreground_url": "https://shop.example/checkout?id=1",
            },
            "sensitive_foreground",
        ),
        (
            {
                "foreground": {"app": "brave-browser", "title": "Settings"},
                "foreground_url": "brave://settings",
            },
            "sensitive_foreground",
        ),
        (
            {
                "foreground": {"app": "brave-browser", "title": "x"},
                "password_focused": True,
            },
            "password_focused",
        ),
        ({"outputs": ["DP-1"], "hud": "DP-1"}, "only_hud_monitor"),
        ({"outputs": [], "hud": None}, "no_monitors"),
    ],
)
def test_skip_reasons(kw, reason):
    verdict = skip.evaluate(ctx(**kw))
    assert verdict.skip and verdict.reason == reason


def test_first_hit_wins_order():
    v = skip.evaluate(ctx(env_ok=False, guest=True, idle="idle"))
    assert v.reason == "disabled"
    v = skip.evaluate(ctx(guest=True, idle="idle"))
    assert v.reason == "guest"


def test_password_focus_unknown_does_not_skip_non_brave():
    v = skip.evaluate(ctx(password_focused=None))
    assert not v.skip


def test_focus_and_school_are_log_only():
    v = skip.evaluate(ctx(focus_active=True))
    assert not v.skip and v.log_only and v.reason == "focus_mode"
    v = skip.evaluate(ctx(school=True))
    assert not v.skip and v.log_only and v.reason == "school_mode"


def test_clean_tick_runs_with_offers():
    v = skip.evaluate(ctx())
    assert not v.skip and not v.log_only and v.reason == ""


@pytest.mark.parametrize(
    "app,title,url,hit",
    [
        ("org.kde.konsole", "vim notes.md", "", False),
        ("Bitwarden", "Vault", "", True),
        ("1Password", "x", "", True),
        ("org.kde.kwalletmanager", "KWallet", "", True),
        (
            "org.kde.polkit-kde-authentication-agent-1",
            "Authentication Required",
            "",
            True,
        ),
        ("pinentry-qt", "PIN", "", True),
        ("ksshaskpass", "x", "", True),
        ("code", "Enter your passphrase", "", True),
        ("brave-browser", "Two-factor 2FA", "", True),
        ("brave-browser", "Enter verification code", "", True),
        ("brave-browser", "Log in to Twitter", "", True),
        ("brave-browser", "New Private Tab - Brave", "", True),
        ("brave-browser", "Private Window with Tor - Brave", "", True),
        ("brave-browser", "News", "https://www.barclays.co.uk/x", True),
        ("brave-browser", "News", "https://online.hsbc.co.uk/", True),
        ("brave-browser", "Docs", "https://example.com/signin", True),
        ("brave-browser", "Docs", "https://example.com/payment/step", True),
        ("brave-browser", "Docs", "https://example.com/docs/payments-api", False),
        ("brave-browser", "GitHub", "https://github.com/a/b", False),
        ("brave-browser", "ext", "chrome-extension://abc/popup.html", True),
    ],
)
def test_sensitive_matcher(app, title, url, hit):
    assert skip.is_sensitive(app, title, url) is hit


def test_deny_json_extends_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "aware").mkdir()
    (tmp_path / "aware" / "deny.json").write_text(
        json.dumps(
            {
                "apps": ["signal"],
                "titles": ["secret project"],
                "hosts": ["mychart.example"],
                "paths": ["/admin"],
            }
        )
    )
    rules = skip.load_rules()
    assert skip.is_sensitive("org.signal.Signal", "chat", "", rules)
    assert skip.is_sensitive("kate", "Secret Project notes", "", rules)
    assert skip.is_sensitive("brave", "x", "https://app.mychart.example/a", rules)
    assert skip.is_sensitive("brave", "x", "https://ok.example/admin/users", rules)
    assert not skip.is_sensitive("brave", "x", "https://ok.example/home", rules)


def test_bad_deny_json_keeps_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "aware").mkdir()
    (tmp_path / "aware" / "deny.json").write_text("{nope")
    rules = skip.load_rules()
    assert skip.is_sensitive("keepassxc", "x", "", rules)


def test_strip_url_drops_query_and_fragment():
    assert skip.host_path("https://a.example/p/q?token=1#x") == "a.example/p/q"
    assert skip.host_path("not a url") == ""


def test_password_probe_uses_cdp_eval():
    calls = []

    def fake_eval(ws, expr):
        calls.append(expr)
        return "password"

    tab = {"webSocketDebuggerUrl": "ws://x"}
    assert skip.password_focused(tab, evaluate=fake_eval) is True
    assert "activeElement" in calls[0]
    assert skip.password_focused(tab, evaluate=lambda w, e: "text") is False
    assert skip.password_focused(None, evaluate=fake_eval) is None

    def boom(ws, expr):
        raise OSError

    assert skip.password_focused(tab, evaluate=boom) is None


# ------------------------------------------------------------- media presence
def test_media_playing_counts_as_present(monkeypatch):
    import input_idle

    monkeypatch.setattr(input_idle, "read_state", lambda: "idle")
    monkeypatch.setattr(skip, "media_playing", lambda now=None: True)
    assert skip.presence(0.0) == "active"
    monkeypatch.setattr(skip, "media_playing", lambda now=None: False)
    assert skip.presence(0.0) == "idle"


def test_media_playing_reads_players_and_fails_soft():
    assert skip.media_playing(1.0, players=lambda: [("brave", "Playing")]) is True
    assert skip.media_playing(2.0, players=lambda: [("vlc", "Paused")]) is False

    def boom():
        raise RuntimeError("no playerctl")

    assert skip.media_playing(3.0, players=boom) is False
