"""Regression tests: existing window movement between KDE/Wayland monitors."""
import pytest
from system import monitor_window_move as mm


def test_other_monitor_prefers_other_external():
    screens = [
        {"name": "HDMI-A-2", "x": 0, "y": 0, "w": 1920, "h": 1080},
        {"name": "eDP-1", "x": 1920, "y": 0, "w": 1920, "h": 1080},
        {"name": "DP-1", "x": 3840, "y": 0, "w": 1920, "h": 1080},
    ]
    assert mm.pick_destination("other monitor", screens, "HDMI-A-2")["name"] == "DP-1"
    assert mm.pick_destination("other", screens, "DP-1")["name"] == "HDMI-A-2"
    assert mm.pick_destination("other", screens, "eDP-1")["name"] == "HDMI-A-2"
    assert mm.pick_destination("laptop", screens, "DP-1")["name"] == "eDP-1"
    assert mm.pick_destination("right monitor", screens, "HDMI-A-2")["name"] == "DP-1"
    assert mm.pick_destination("main", screens, "DP-1")["name"] == "HDMI-A-2"


def test_explicit_unknown_screen_does_not_silently_fallback():
    screens = [{"name": "HDMI-A-2", "x": 0, "y": 0, "w": 1920, "h": 1080}]
    with pytest.raises(ValueError):
        mm.pick_destination("DP-9", screens, "HDMI-A-2")


def test_move_script_moves_existing_window_and_retains_maximization():
    js = mm.move_script("ChatGPT", "DP-1", [{"name": "DP-1", "x": 3840, "y": 0, "w": 1920, "h": 1080}])
    assert "workspace.windowList()" in js
    assert "chatgpt" in js.lower()
    assert "setMaximize(false,false)" in js
    assert "setMaximize(true,true)" in js
    assert "3840" in js


def screens():
    return [
        {"name": "HDMI-A-2", "x": 0, "y": 0, "w": 1920, "h": 1080},
        {"name": "eDP-1", "x": 1920, "y": 0, "w": 1920, "h": 1080},
        {"name": "DP-1", "x": 3840, "y": 0, "w": 1920, "h": 1080},
    ]


def test_default_routing_excludes_ui_and_current():
    ss = screens()
    assert mm.select_destination("other", ss, "HDMI-A-2", "DP-1")["name"] == "eDP-1"
    assert mm.select_destination("other", ss, "eDP-1", "DP-1")["name"] == "HDMI-A-2"
    assert mm.select_destination("right", ss, "HDMI-A-2", "DP-1")["name"] == "DP-1"
    with pytest.raises(ValueError):
        mm.select_destination("other", ss[:2], "HDMI-A-2", "eDP-1")


def test_compositor_geometry_not_wrong_xwayland_coordinates():
    ss=screens()
    info={"x": 3840, "y": 0, "width": 1920, "height": 1030}
    assert mm.info_screen(info,ss) == "DP-1"
    assert mm.info_screen(None,ss) is None
    goal = mm.target_rect(info, ss[1])
    assert 1920 <= goal["x"] < 3840
    assert goal["w"] <= 1920


def test_move_validates_compositor_position(monkeypatch):
    ss=screens()
    before={"uuid": "{uuid}", "pid": 12, "caption": "ChatGPT",
            "x": 0, "y": 0, "width": 1200, "height": 720}
    after=dict(before,x=1920)
    state={"info": before, "calls": []}
    monkeypatch.setattr(mm, "monitors", lambda: ss)
    monkeypatch.setattr(mm, "kwin_info", lambda q: state["info"])
    monkeypatch.setattr(mm, "hud_screen", lambda s: "DP-1")
    monkeypatch.setattr(mm, "portal_available", lambda: False)
    def command(js):
        state["calls"].append(js)
        return True
    monkeypatch.setattr(mm, "run_move_script", command)
    monkeypatch.setattr(mm.time, "sleep", lambda sec: None)
    ok, message=mm.move_window("ChatGPT", "other")
    assert not ok and "verify" in message
    state["info"]=after
    ok, message=mm.move_window("ChatGPT", "eDP-1")
    assert ok and "already" in message
    state["info"]=before
    def successful(js):
        state["info"]=after
        return True
    monkeypatch.setattr(mm, "run_move_script", successful)
    ok, message=mm.move_window("ChatGPT", "other")
    assert ok and "eDP-1" in message


def test_portal_commits_geometry_before_animation_without_timing_race(monkeypatch):
    from system import cinematic
    ss=screens()
    before={"uuid":"{uuid}", "pid":12, "caption":"ChatGPT",
            "x":0, "y":0, "width":1200, "height":720}
    current=dict(before)
    actions=[]
    monkeypatch.setattr(mm,"monitors",lambda:ss)
    monkeypatch.setattr(mm,"kwin_info",lambda q:current)
    monkeypatch.setattr(mm,"hud_screen",lambda s:"DP-1")
    monkeypatch.setattr(mm,"portal_available",lambda:True)
    monkeypatch.setattr(cinematic,"mark_window",
                        lambda action,uuid,**kwargs:actions.append((action,uuid,kwargs)) or True)
    monkeypatch.setattr(mm.time,"sleep",lambda sec: actions.append(("sleep",sec)))
    def move(js):
        current["x"]=1920
        actions.append(("move",))
        return True
    monkeypatch.setattr(mm,"run_move_script",move)
    ok,message=mm.move_window("ChatGPT","other")
    assert ok and "Particle Portal" in message
    assert actions[0][:2] == ("portal","{uuid}")
    assert actions[0][2]["to"]["x"] >= 1920
    assert actions[1] == ("move",)
    assert actions[2][:2] == ("portal-commit", "{uuid}")
    assert not any(action[0] == "sleep" for action in actions)


@pytest.mark.parametrize("accepted", [False, True])
def test_failed_portal_move_releases_hidden_window(monkeypatch, accepted):
    from system import cinematic
    before={"uuid":"{uuid}", "x":0, "y":0, "width":1200, "height":720}
    actions=[]
    monkeypatch.setattr(mm, "monitors", screens)
    monkeypatch.setattr(mm, "kwin_info", lambda q: before)
    monkeypatch.setattr(mm, "hud_screen", lambda s: "DP-1")
    monkeypatch.setattr(mm, "portal_available", lambda: True)
    monkeypatch.setattr(cinematic, "mark_window", lambda action,*a,**k: actions.append(action) or True)
    monkeypatch.setattr(mm, "run_move_script", lambda js: accepted)
    monkeypatch.setattr(mm.time, "sleep", lambda sec: None)
    ok, _ = mm.move_window("ChatGPT", "other")
    assert not ok
    assert actions == ["portal", "portal-cancel"]
