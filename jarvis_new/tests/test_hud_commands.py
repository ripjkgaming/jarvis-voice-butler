import asyncio
import types

import pytest

from intent import hud_commands as hc


@pytest.mark.parametrize(
    "text,cmd",
    [
        ("Mute", "mute"),
        ("Jarvis, mute yourself", "mute"),
        ("hey jarvis mute please", "mute"),
        ("hide", "hide"),
        ("Jarvis hide", "hide"),
        ("hide yourself", "hide"),
        ("hide all", "hide_all"),
        ("Minimise all windows", "hide_all"),
        ("minimize everything", "hide_all"),
        ("show desktop", "hide_all"),
        ("stop", "stop"),
        ("Jarvis, stop!", "stop"),
        ("shut up", "stop"),
        ("that's enough", "stop"),
        ("unmute", "unmute"),
        ("get rid of the interface", "hide"),
        ("cut that off", "stop"),
        ("clean up the desktop", "hide_all"),
    ],
)
def test_grammar_hits(text, cmd):
    assert hc.parse(text) == cmd


@pytest.mark.parametrize(
    "text",
    [
        "show windows",
        "stop the music",
        "mute the volume and open spotify",
        "mute the mic",
        "go quiet",
        "what is the weather",
        "hide the evidence in my project folder please now",
        "",
    ],
)
def test_grammar_misses(text):
    assert hc.parse(text) is None


def test_needle_fallback_maps_and_gates():
    calls = []

    def route(t):
        calls.append(t)
        return {"action": "stop_speaking", "confidence": 0.93}

    assert hc.parse_needle("could you possibly cut that off", route) == "stop"
    # No trigger word: Needle is never consulted.
    assert hc.parse_needle("what time is it", route) is None
    assert calls == ["possibly cut that off"]


def test_needle_low_confidence_and_foreign_action_ignored():
    low = lambda t: {"action": "jarvis_hide", "confidence": 0.4}  # noqa: E731
    other = lambda t: {"action": "open_app", "confidence": 0.99}  # noqa: E731
    assert hc.parse_needle("please hide", low) is None
    assert hc.parse_needle("please hide", other) is None


def test_minimize_all_uses_kwin_script():
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="7\n")

    env = {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}
    assert hc.minimize_all(run, env) is True
    assert any("loadScript" in a[-3] or "loadScript" in " ".join(a) for a in seen)


def test_minimize_all_falls_back_to_wmctrl():
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        if argv[0].startswith("qdbus"):
            raise OSError
        if argv[:2] == ["wmctrl", "-l"]:
            return types.SimpleNamespace(
                returncode=0,
                stdout="0x01 0 host A\n0x02 -1 host panel\n0x03 1 host B\n",
            )
        return types.SimpleNamespace(returncode=0, stdout="")

    assert hc.minimize_all(run, {}) is True
    hidden = [a[3] for a in seen if a[:3] == ["wmctrl", "-i", "-r"]]
    assert hidden == ["0x01", "0x03"]


def test_stop_fades_then_interrupts():
    order = []

    class S:
        def fade_next_flush(self, ms):
            order.append(("fade", ms))

        async def interrupt(self, force=False):
            order.append(("interrupt", force))

    assert asyncio.run(hc.execute("stop", S())) is True
    assert order == [("fade", hc.FADE_MS), ("interrupt", True)]


def test_needle_hide_vs_hide_all_swapped_by_words():
    route = lambda t, *a: {"action": "hide_all_windows", "confidence": 0.62}  # noqa: E731
    assert hc.parse_needle("please dismiss the hud", route) == "hide"
    assert hc.parse_needle("please minimise every window", route) == "hide_all"


def test_needle_needs_corroborating_word():
    route = lambda t, *a: {"action": "stop_speaking", "confidence": 0.9}  # noqa: E731
    assert hc.parse_needle("hide the weather", route) is None  # no stop-word
    low_mute = lambda t, *a: {"action": "jarvis_mute", "confidence": 0.38}  # noqa: E731
    assert hc.parse_needle("make yourself quiet", low_mute) == "mute"


def test_breach_grammar():
    for phrase in ("system takeover", "red alert", "initiate takeover", "breach"):
        assert hc.parse(phrase) == "breach"
    for phrase in ("stand down", "all clear", "stop the takeover", "end breach"):
        assert hc.parse(phrase) == "stand_down"
    assert hc.parse("stop") == "stop"


def test_breach_state_keeps_mute(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert hc.set_hud_muted(True)
    assert hc.set_hud_breach(True)
    assert hc.hud_muted() and hc.hud_breach()
    assert hc.set_hud_breach(False)
    assert hc.hud_muted() and not hc.hud_breach()


def test_rescue_grammar():
    for phrase in ("rescue", "Jarvis take control", "restore balance"):
        assert hc.parse(phrase) == "rescue"


def test_ultron_screen_grammar():
    for phrase in ("ultron", "ultron online", "take over all screens"):
        assert hc.parse(phrase) == "ultron"
    for phrase in ("ultron offline", "deactivate ultron", "release the screens"):
        assert hc.parse(phrase) == "ultron_off"
