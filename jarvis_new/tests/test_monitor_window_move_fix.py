"""Window matching by app family, monitor names, and honest failure messages.

All KWin calls are mocked. The fake desktop mirrors what KRunner does live:
a probe matches a window when the probe text appears in its title or app
identity, so "terminal" finds nothing and only "konsole" finds the terminal.
"""
import copy

import pytest
from system import monitor_window_move as mm

KONSOLE = "{11111111-1111-4111-8111-111111111111}"
BRAVE = "{22222222-2222-4222-8222-222222222222}"
FIREFOX = "{33333333-3333-4333-8333-333333333333}"
CHATGPT = "{44444444-4444-4444-8444-444444444444}"


def win(caption, cls, name, desktop, x=0, y=0, pid=1, typ=0):
    return {"caption": caption, "resourceClass": cls, "resourceName": name,
            "desktopFile": desktop, "x": x, "y": y, "width": 1200,
            "height": 800, "pid": pid, "type": typ, "maximizeHorizontal": 0}


WINDOWS = {
    KONSOLE: win("ripjk : bash", "org.kde.konsole", "konsole", "org.kde.konsole",
                 x=0, y=0, pid=6118),
    BRAVE: win("The Real Food - YouTube - Brave", "brave-browser", "brave",
               "brave-browser", x=0, y=0, pid=7001),
    CHATGPT: win("ChatGPT", "Chatgpt", "chatgpt", "", x=0, y=0, pid=8001),
}


def fake_probe(text):
    """KRunner stand-in: match probe against caption / class / name."""
    needle = " ".join(text.lower().split())
    out = []
    for uuid, info in WINDOWS.items():
        hay = " ".join(str(info.get(k) or "") for k in
                       ("caption", "resourceClass", "resourceName", "desktopFile")).lower()
        if needle and needle.replace("-", " ") in hay.replace("-", " "):
            out.append((uuid, info["caption"]))
    return out


@pytest.fixture(autouse=True)
def _isolate_windows():
    saved = copy.deepcopy(WINDOWS)
    yield
    WINDOWS.clear()
    WINDOWS.update(saved)


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.setattr(mm, "_probe", fake_probe)
    monkeypatch.setattr(mm, "_info", lambda uuid: dict(WINDOWS[uuid], uuid=uuid)
                        if uuid in WINDOWS else None)
    monkeypatch.setattr(mm, "_active_pid", lambda: None)
    return WINDOWS


def screens_three():
    return [
        {"name": "HDMI-A-2", "x": 0, "y": 0, "w": 1920, "h": 1080, "priority": 1},
        {"name": "eDP-1", "x": 1920, "y": 0, "w": 1920, "h": 1080, "priority": 2},
        {"name": "DP-1", "x": 3840, "y": 0, "w": 1920, "h": 1080, "priority": 3},
    ]


# ------------------------------------------------------------ window matching

@pytest.mark.parametrize("query", ["terminal", "the terminal", "Terminal window",
                                   "konsole", "the konsole", "bash"])
def test_terminal_family_resolves_to_konsole(desktop, query):
    info = mm.kwin_info(query)
    assert info is not None, query
    assert info["uuid"] == KONSOLE
    assert info["caption"] == "ripjk : bash"


@pytest.mark.parametrize("query", ["browser", "the browser", "brave", "Brave Browser"])
def test_browser_family_resolves_to_brave(desktop, query):
    info = mm.kwin_info(query)
    assert info is not None, query
    assert info["uuid"] == BRAVE


def test_terminal_identity_beats_title_that_says_terminal(desktop):
    desktop[BRAVE] = win("How to open a terminal - Brave", "brave-browser", "brave",
                         "brave-browser", pid=7001)
    info = mm.kwin_info("terminal")
    assert info["uuid"] == KONSOLE


def test_active_window_wins_among_several_browsers(desktop):
    desktop[FIREFOX] = win("Mozilla Firefox", "firefox", "firefox", "firefox", pid=9009)
    mm_active = {"pid": 9009}
    orig = mm._active_pid
    try:
        mm._active_pid = lambda: mm_active["pid"]
        info = mm.kwin_info("browser")
        assert info["uuid"] == FIREFOX
        assert [o["uuid"] for o in info["_others"]] == [BRAVE]
    finally:
        mm._active_pid = orig


def test_several_apps_without_active_reports_the_others(desktop):
    desktop[FIREFOX] = win("Mozilla Firefox", "firefox", "firefox", "firefox", pid=9009)
    info = mm.kwin_info("browser")
    assert info["uuid"] == BRAVE  # match order: alias order, Brave is first
    assert [o["uuid"] for o in info["_others"]] == [FIREFOX]


def test_punctuation_and_case_are_ignored(desktop):
    assert mm.kwin_info("KON-SOLE")["uuid"] == KONSOLE
    assert mm.kwin_info("chat gpt")["uuid"] == CHATGPT


def test_unknown_query_has_no_match(desktop):
    assert mm.kwin_info("spotify") is None


def test_candidates_expand_generic_words():
    cands = mm.query_candidates("the terminal window")
    assert cands[0] == "terminal"
    assert "konsole" in cands and "kitty" in cands
    assert mm.query_candidates("  ") == []


# ------------------------------------------------------------ monitor names

def test_main_uses_kde_priority_not_list_order():
    ss = screens_three()
    ss_reversed = list(reversed(ss))  # DP-1 listed first, but priority 3
    assert mm.pick_destination("main", ss_reversed, None)["name"] == "HDMI-A-2"
    assert mm.pick_destination("primary monitor", ss_reversed, None)["name"] == "HDMI-A-2"
    assert mm.pick_destination("the main monitor", ss, None)["name"] == "HDMI-A-2"


@pytest.mark.parametrize("spec,expected", [
    ("DP-1", "DP-1"), ("dp1", "DP-1"), ("HDMI-A-2", "HDMI-A-2"),
    ("hdmi a 2", "HDMI-A-2"), ("eDP-1", "eDP-1"),
    ("laptop", "eDP-1"), ("built-in", "eDP-1"), ("laptop screen", "eDP-1"),
    ("left", "HDMI-A-2"), ("right", "DP-1"), ("rightmost", "DP-1"),
    ("second monitor", "DP-1"),
])
def test_monitor_names_and_synonyms(spec, expected):
    dest = mm.pick_destination(spec, screens_three(), "HDMI-A-2")
    assert dest["name"] == expected


def test_other_monitor_from_laptop_picks_external():
    ss = screens_three()
    assert mm.pick_destination("other", ss, "eDP-1")["name"] == "HDMI-A-2"


def test_single_external_resolves_for_external_word():
    ss = [s for s in screens_three() if s["name"] != "DP-1"]
    assert mm.pick_destination("external", ss, "eDP-1")["name"] == "HDMI-A-2"


def test_ambiguous_external_lists_choices():
    with pytest.raises(ValueError) as exc:
        mm.pick_destination("external monitor", screens_three(), "eDP-1")
    msg = str(exc.value)
    assert "More than one external" in msg and "HDMI-A-2" in msg and "DP-1" in msg


def test_missing_second_monitor_lists_monitors_found():
    one = [{"name": "eDP-1", "x": 0, "y": 0, "w": 1920, "h": 1080, "priority": 1}]
    with pytest.raises(ValueError) as exc:
        mm.pick_destination("second", one, "eDP-1")
    assert "no second monitor" in str(exc.value).lower()
    assert "eDP-1" in str(exc.value)


def test_unknown_monitor_lists_monitors_found():
    with pytest.raises(ValueError) as exc:
        mm.pick_destination("DP-9", screens_three(), "HDMI-A-2")
    msg = str(exc.value)
    assert "Unknown monitor 'DP-9'" in msg
    assert "Monitors found" in msg and "HDMI-A-2" in msg and "eDP-1" in msg


def test_priority_parsed_from_kscreen_output():
    text = (
        "\x1b[01;32mOutput: \x1b[0;0m1 HDMI-A-2 c8ced468\n\tpriority 1\n"
        "Output: 2 DP-1 fd2bf7ef\n\tpriority 3\n"
        "Output: 3 eDP-1 96fee68d\n\tpriority 2\n"
    )
    assert mm._priorities(text) == {"HDMI-A-2": 1, "DP-1": 3, "eDP-1": 2}


# ------------------------------------------------------------ move flow

def _move_harness(monkeypatch, *, lands_on="DP-1", accept=True, ss=None):
    ss = ss or screens_three()
    state = {"x": {KONSOLE: 0}, "js": []}

    def info(uuid):
        base = dict(WINDOWS[uuid], uuid=uuid)
        base["x"] = state["x"].get(uuid, base["x"])
        return base if uuid in WINDOWS else None

    def run(js):
        state["js"].append(js)
        if accept:
            # Park the konsole on the destination's origin, as KWin would.
            dest = next(s for s in ss if s["name"] == lands_on)
            state["x"][KONSOLE] = dest["x"]
        return accept

    monkeypatch.setattr(mm, "monitors", lambda: ss)
    monkeypatch.setattr(mm, "_info", info)
    monkeypatch.setattr(mm, "_probe", fake_probe)
    monkeypatch.setattr(mm, "_active_pid", lambda: None)
    monkeypatch.setattr(mm, "hud_screen", lambda s: "HDMI-A-2")
    monkeypatch.setattr(mm, "portal_available", lambda: False)
    monkeypatch.setattr(mm, "run_move_script", run)
    monkeypatch.setattr(mm.time, "sleep", lambda sec: None)
    return state


def test_move_terminal_resolves_and_reports_the_window(monkeypatch):
    state = _move_harness(monkeypatch, lands_on="DP-1")
    ok, msg = mm.move_window("terminal", "DP-1")
    assert ok, msg
    assert "ripjk : bash" in msg and "DP-1" in msg
    # The script targets the exact konsole uuid, not a name substring.
    js = state["js"][0]
    assert KONSOLE.lower() in js.lower()
    assert "internalId" in js


def test_move_verifies_and_refuses_when_window_did_not_move(monkeypatch):
    _move_harness(monkeypatch, lands_on="DP-1")
    # Script claims success but the window never leaves the laptop screen.
    monkeypatch.setattr(mm, "run_move_script", lambda js: True)
    ok, msg = mm.move_window("konsole", "DP-1")
    assert ok is False
    assert "Could not verify" in msg and "no success was assumed" in msg


def test_move_no_match_message_lists_open_windows(monkeypatch):
    _move_harness(monkeypatch)
    # No konsole open, but a Brave tab title says "terminal": must not match.
    del WINDOWS[KONSOLE]
    WINDOWS[BRAVE] = win("How to open a terminal - Brave", "brave-browser", "brave",
                         "brave-browser", pid=7001)
    ok, msg = mm.move_window("terminal", "other")
    assert ok is False
    assert msg.startswith("No open window matches 'terminal'.")
    assert "Open windows I can see" in msg
    assert "How to open a terminal - Brave (brave)" in msg
    assert "No open window matching" not in msg


def test_move_bad_monitor_reports_monitors_instead_of_failing_silently(monkeypatch):
    _move_harness(monkeypatch)
    ok, msg = mm.move_window("terminal", "DP-9")
    assert ok is False
    assert "Unknown monitor 'DP-9'" in msg and "Monitors found" in msg


def test_move_script_targets_uuid_when_given():
    ss = screens_three()
    js = mm.move_script("terminal", "DP-1", ss, uuid=KONSOLE)
    assert KONSOLE.lower() in js.lower()
    assert "needle" not in js
    legacy = mm.move_script("ChatGPT", "DP-1", ss)
    assert "chatgpt" in legacy and "needle" in legacy
