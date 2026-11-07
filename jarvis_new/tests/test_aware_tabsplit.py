"""Always Aware tab split (PLAN.md §4.3): placement, split, hand-back, recovery.

Pure tests against a fake extension tab API and a fake KWin. No real Brave,
no real KWin, nothing on Sir's desktop is touched.
"""

import pytest

from aware import tabsplit as ts


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


def scr(name, x, w=2560, h=1440, prio=None):
    return {"name": name, "x": x, "y": 0, "w": w, "h": h, "priority": prio}


THREE = [
    scr("DP-1", 0, prio=1),
    scr("HDMI-A-2", 2560, prio=2),
    scr("DP-2", 5120, prio=3),
]


# ------------------------------------------------------------ placement


def test_placement_prefers_monitor_that_is_neither_main_nor_hud():
    p = ts.choose_placement(THREE, main="DP-1", hud="HDMI-A-2")
    assert p.mode == "monitor" and p.screen == "DP-2"


def test_placement_hud_fallback_falls_back_to_main_as_hud():
    # hud_screen() falls back to primary when the shell is missing
    p = ts.choose_placement(THREE, main="DP-1", hud="DP-1")
    assert p.mode == "monitor" and p.screen == "HDMI-A-2"


def test_placement_main_and_hud_only_uses_half_away_from_hud():
    two = THREE[:2]
    hud_rect = {"x": 2560, "y": 0, "w": 1000, "h": 1440}  # HUD on the left half
    p = ts.choose_placement(two, main="DP-1", hud="HDMI-A-2", hud_rect=hud_rect)
    assert p.mode == "hud_half" and p.screen == "HDMI-A-2"
    assert p.rect == {"x": 2560 + 1280, "y": 0, "w": 1280, "h": 1440}


def test_placement_hud_half_unknown_hud_rect_minimises():
    p = ts.choose_placement(THREE[:2], main="DP-1", hud="HDMI-A-2")
    assert p.mode == "minimise"


def test_placement_hud_half_too_small_minimises():
    two = [scr("DP-1", 0, prio=1), scr("HDMI-A-2", 2560, w=1920, prio=2)]
    hud_rect = {"x": 2560, "y": 0, "w": 900, "h": 1440}
    p = ts.choose_placement(two, main="DP-1", hud="HDMI-A-2", hud_rect=hud_rect)
    assert p.mode == "minimise"


def test_placement_single_monitor_minimises():
    p = ts.choose_placement(THREE[:1], main="DP-1", hud="DP-1")
    assert p.mode == "minimise" and p.screen is None


def test_placement_no_screens_minimises():
    assert ts.choose_placement([], main=None, hud=None).mode == "minimise"


# ------------------------------------------------------------ markers / mapping


def test_marker_add_and_strip_round_trip():
    assert ts.marked("Basket") == "⟦J⟧ Basket"
    assert ts.marked("⟦J⟧ Basket") == "⟦J⟧ Basket"
    assert ts.unmarked("⟦J⟧ Basket") == "Basket"
    assert ts.unmarked("Basket") == "Basket"


def test_resolve_tab_unique_marked_url():
    tabs = [
        {"id": 1, "url": "https://a/x", "title": "X"},
        {"id": 2, "url": "https://a/x", "title": "⟦J⟧ X"},
    ]
    assert ts.resolve_tab(tabs, "https://a/x") == 2


def test_resolve_tab_ambiguous_aborts():
    tabs = [
        {"id": 1, "url": "https://a/x", "title": "⟦J⟧ X"},
        {"id": 2, "url": "https://a/x", "title": "⟦J⟧ X"},
    ]
    with pytest.raises(ts.SplitError, match="ambiguous"):
        ts.resolve_tab(tabs, "https://a/x")


def test_resolve_tab_missing_aborts():
    with pytest.raises(ts.SplitError, match="not found"):
        ts.resolve_tab([{"id": 1, "url": "u", "title": "t"}], "u")


def test_no_focus_strips_activation_from_move_script():
    from system import monitor_window_move as mwm

    js = mwm.move_script("x", "DP-2", THREE, uuid="{abc}")
    assert "workspace.activeWindow" in js
    safe = ts.no_focus(js)
    assert "activeWindow" not in safe


def test_no_focus_refuses_unknown_activation():
    with pytest.raises(ts.SplitError):
        ts.no_focus("var w; workspace.activeWindow = other;")


# ------------------------------------------------------------ fakes


class FakeTabs:
    """Simulates chrome.tabs / chrome.windows semantics we rely on."""

    def __init__(self):
        self.windows = {}  # id -> {"state":..., "tabs": [tab ids]}
        self.tabs = {}  # id -> {"title","url","active"}
        self.next = 100
        self.calls = []
        self.removed = []

    def add_window(self, wid, tabs, active=0):
        self.windows[wid] = {"state": "normal", "tabs": []}
        for i, (tid, title) in enumerate(tabs):
            self.tabs[tid] = {
                "title": title,
                "url": f"https://site/{tid}",
                "active": i == active,
            }
            self.windows[wid]["tabs"].append(tid)

    def _win_of(self, tid):
        return next(w for w, d in self.windows.items() if tid in d["tabs"])

    def _view(self, tid):
        w = self._win_of(tid)
        return {
            "id": tid,
            "windowId": w,
            "index": self.windows[w]["tabs"].index(tid),
            **self.tabs[tid],
        }

    async def query(self):
        return [self._view(t) for w in self.windows.values() for t in w["tabs"]]

    async def get_windows(self):
        return [
            {"id": w, "state": d["state"], "tabs": [self._view(t) for t in d["tabs"]]}
            for w, d in self.windows.items()
        ]

    def _detach(self, tid):
        w = self._win_of(tid)
        lst = self.windows[w]["tabs"]
        was_active = self.tabs[tid]["active"]
        idx = lst.index(tid)
        lst.remove(tid)
        self.tabs[tid]["active"] = False
        if was_active and lst:
            self.tabs[lst[min(idx, len(lst) - 1)]]["active"] = True
        if not lst:
            del self.windows[w]  # Chromium closes an emptied window

    async def create_window(self, tab_id, state="normal"):
        self.calls.append(("create_window", tab_id, state))
        self._detach(tab_id)
        wid = self.next
        self.next += 1
        self.windows[wid] = {"state": state, "tabs": [tab_id]}
        self.tabs[tab_id]["active"] = True
        return {"id": wid}

    async def move(self, tab_ids, window_id, index):
        self.calls.append(("move", list(tab_ids), window_id, index))
        for tid in tab_ids:
            self._detach(tid)
            lst = self.windows[window_id]["tabs"]
            i = len(lst) if index < 0 or index > len(lst) else index
            lst.insert(i, tid)
            index = i + 1 if index >= 0 else index

    async def activate(self, tab_id):
        w = self._win_of(tab_id)
        for t in self.windows[w]["tabs"]:
            self.tabs[t]["active"] = t == tab_id

    async def remove(self, tab_id):
        self.removed.append(tab_id)
        self._detach(tab_id)
        del self.tabs[tab_id]


class FakeKwin:
    def __init__(self, screens, jarvis_screen="DP-1", user_screen="DP-1"):
        self.screens = screens
        self.placed = []
        self.scripts = []
        self.minimised = []
        self.user_uuid = "{user}"
        self.jarvis_uuid = "{jarvis}"
        self.where = {self.user_uuid: user_screen, self.jarvis_uuid: jarvis_screen}
        self.fail_place = False

    def jarvis_window(self):
        return self.jarvis_uuid

    def window_for_caption(self, title):
        return self.user_uuid if title else None

    def screen_of(self, uuid):
        return self.where.get(uuid)

    def geometry(self, uuid):
        return {"x": 0, "y": 0, "w": 800, "h": 600, "screen": self.where.get(uuid)}

    def place(self, uuid, placement):
        self.placed.append((uuid, placement.mode, placement.screen))
        if self.fail_place:
            return False
        if placement.mode == "minimise":
            self.minimised.append(uuid)
        else:
            self.where[uuid] = placement.screen
        return True

    def move_to_screen(self, uuid, screen):
        self.placed.append((uuid, "monitor", screen))
        self.where[uuid] = screen
        return True

    def set_geometry(self, uuid, geom):
        self.placed.append((uuid, "restore", geom.get("screen")))
        self.where[uuid] = geom.get("screen")
        return True


def make(screens=THREE, main="DP-1", hud="HDMI-A-2", user_screen="DP-1", active=None):
    chrome = FakeTabs()
    chrome.add_window(1, [(10, "Mail"), (11, "Basket"), (12, "News")], active=1)
    kwin = FakeKwin(screens, user_screen=user_screen)
    seq = iter(active or [{"id": "A"}] * 10)
    split = ts.TabSplit(
        chrome,
        kwin,
        screens=lambda: screens,
        main=lambda s: main,
        hud=lambda s: (hud, None),
        active=lambda: next(seq),
        browser_id=lambda: "browser-1",
    )
    return chrome, kwin, split


# ------------------------------------------------------------ split


async def test_split_moves_only_task_tab_to_side_monitor_without_focus():
    chrome, kwin, split = make()
    j = await split.split("r1", [11])
    assert chrome.calls[0] == ("create_window", 11, "normal")
    jw = j["jarvis_window"]
    assert chrome.windows[jw]["tabs"] == [11]
    assert chrome.windows[1]["tabs"] == [10, 12]  # Sir keeps his other tabs
    assert (kwin.jarvis_uuid, "monitor", "DP-2") in kwin.placed
    assert j["tabs"][0]["orig_window"] == 1 and j["tabs"][0]["orig_index"] == 1
    assert j["tabs"][0]["left_neighbour"] == 10
    assert j["state"] == "split"
    assert ts.load_journal("r1")["jarvis_window"] == jw


async def test_split_multi_tab_follows_into_jarvis_window():
    chrome, _kwin, split = make()
    j = await split.split("r1", [11, 12])
    assert chrome.windows[j["jarvis_window"]]["tabs"] == [11, 12]
    assert chrome.windows[1]["tabs"] == [10]


async def test_split_single_monitor_creates_minimised():
    chrome, _kwin, split = make(screens=THREE[:1], hud="DP-1")
    j = await split.split("r1", [11])
    assert chrome.calls[0] == ("create_window", 11, "minimized")
    assert j["placement"]["mode"] == "minimise"


async def test_split_moves_sirs_window_to_main_when_not_there():
    _chrome, kwin, split = make(user_screen="DP-2")
    j = await split.split("r1", [11])
    assert kwin.where[kwin.user_uuid] == "DP-1"
    assert j["moved_user_window"] is True


async def test_split_focus_change_undoes_and_raises():
    chrome, _kwin, split = make(active=[{"id": "A"}, {"id": "B"}])
    with pytest.raises(ts.SplitError, match="window_split"):
        await split.split("r1", [11])
    assert chrome.windows[1]["tabs"] == [10, 11, 12]  # back where it was
    assert ts.load_journal("r1")["state"] == "handed_back"


async def test_split_placement_failure_undoes():
    chrome, kwin, split = make()
    kwin.fail_place = True
    with pytest.raises(ts.SplitError):
        await split.split("r1", [11])
    assert chrome.windows[1]["tabs"] == [10, 11, 12]


# ------------------------------------------------------------ hand-back


async def test_handback_returns_to_original_index_unactivated():
    chrome, _kwin, split = make()
    await split.split("r1", [11])
    active_before = [
        t
        for t, d in chrome.tabs.items()
        if d["active"] and t in chrome.windows[1]["tabs"]
    ]
    out = await split.handback("r1")
    assert chrome.windows[1]["tabs"] == [10, 11, 12]
    assert not chrome.tabs[11]["active"]
    assert [
        t for t in chrome.windows[1]["tabs"] if chrome.tabs[t]["active"]
    ] == active_before
    assert out["neighbour_title"] == "Mail"
    assert ts.load_journal("r1")["state"] == "handed_back"
    assert len(chrome.windows) == 1  # empty Jarvis window closed itself


async def test_handback_follows_neighbour_when_sir_closed_tabs():
    chrome, _kwin, split = make()
    await split.split("r1", [11])
    await chrome.remove(10)  # Sir closed "Mail"
    chrome.removed.clear()
    await split.handback("r1")
    assert chrome.windows[1]["tabs"] == [12, 11] or chrome.windows[1]["tabs"] == [
        11,
        12,
    ]
    assert 11 in chrome.windows[1]["tabs"]


async def test_handback_is_idempotent():
    chrome, _kwin, split = make()
    await split.split("r1", [11])
    await split.handback("r1")
    calls = len(chrome.calls)
    await split.handback("r1")
    assert len(chrome.calls) == calls


async def test_handback_closes_only_jarvis_scratch_and_returns_result_tab():
    chrome, _kwin, split = make()
    j = await split.split("r1", [11])
    jw = j["jarvis_window"]
    # Jarvis opened two tabs himself in his window
    chrome.tabs[50] = {"title": "scratch", "url": "u", "active": False}
    chrome.tabs[51] = {"title": "Order confirmed", "url": "u2", "active": False}
    chrome.windows[jw]["tabs"] += [50, 51]
    split.note_created("r1", 50, role="scratch")
    split.note_created("r1", 51, role="result")
    await split.handback("r1", verified=True)
    assert chrome.removed == [50]
    assert 51 in chrome.windows[1]["tabs"]
    assert not chrome.tabs[51]["active"]


async def test_handback_never_closes_sir_tab_even_if_noted_as_created():
    chrome, _kwin, split = make()
    await split.split("r1", [11])
    split.note_created("r1", 11, role="scratch")  # bogus: 11 is Sir's
    await split.handback("r1")
    assert 11 not in chrome.removed and 11 in chrome.windows[1]["tabs"]


async def test_leave_there_keeps_window_and_unmarks():
    chrome, _kwin, split = make()
    j = await split.split("r1", [11])
    await split.handback("r1", leave_there=True)
    assert chrome.windows[j["jarvis_window"]]["tabs"] == [11]
    assert ts.load_journal("r1")["state"] == "left_there"


async def test_handback_restores_sir_window_geometry_only_if_jarvis_moved_it():
    _chrome, kwin, split = make(user_screen="DP-2")
    await split.split("r1", [11])
    await split.handback("r1")
    assert kwin.where[kwin.user_uuid] == "DP-2"


async def test_handback_skips_geometry_restore_if_sir_moved_it_since():
    _chrome, kwin, split = make(user_screen="DP-2")
    await split.split("r1", [11])
    kwin.where[kwin.user_uuid] = "HDMI-A-2"  # Sir moved it
    await split.handback("r1")
    assert kwin.where[kwin.user_uuid] == "HDMI-A-2"


async def test_adopt_own_window_result_joins_sir_window_when_verified():
    chrome, kwin, split = make()
    chrome.add_window(7, [(70, "Jarvis lookup")])
    j = await split.adopt("r2", 70, ts.Placement("monitor", "DP-2"))
    assert j["jarvis_window"] == 7 and j["user_window"] == 1
    assert (kwin.jarvis_uuid, "monitor", "DP-2") in kwin.placed
    split.note_created("r2", 70, role="result")
    await split.handback("r2", verified=True)
    assert 70 in chrome.windows[1]["tabs"] and not chrome.tabs[70]["active"]
    assert 7 not in chrome.windows


async def test_adopt_own_window_closed_when_not_verified():
    chrome, _kwin, split = make()
    chrome.add_window(7, [(70, "Jarvis lookup")])
    await split.adopt("r2", 70, ts.Placement("minimise"))
    split.note_created("r2", 70, role="result")
    await split.handback("r2", verified=False)
    assert chrome.removed == [70]
    assert chrome.windows[1]["tabs"] == [10, 11, 12]


# ------------------------------------------------------------ recovery


async def test_recover_returns_tabs_from_unfinished_journal():
    chrome, kwin, split = make()
    await split.split("r1", [11])
    # bridge restarted: a fresh TabSplit over the same browser
    _, _, fresh = make()
    fresh.api, fresh.kwin = chrome, kwin
    done = await fresh.recover()
    assert done == ["r1"]
    assert chrome.windows[1]["tabs"] == [10, 11, 12]


async def test_recover_after_browser_restart_never_closes_by_stale_id():
    chrome, kwin, split = make()
    await split.split("r1", [11])
    split.note_created("r1", 12, role="scratch")  # stale id after restart
    _, _, fresh = make()
    fresh.api, fresh.kwin = chrome, kwin
    fresh.browser_id = lambda: "browser-2"
    await fresh.recover()
    assert chrome.removed == []
    assert ts.load_journal("r1")["state"] == "handed_back"


async def test_split_refuses_failed_move_of_sirs_window_to_main():
    chrome, kwin, split = make(user_screen="DP-2")
    kwin.move_to_screen = lambda uuid, screen: False
    with pytest.raises(ts.SplitError, match="main"):
        await split.split("r1", [11])
    assert chrome.windows[1]["tabs"] == [10, 11, 12]


async def test_split_refuses_unknown_sirs_window_placement():
    chrome, kwin, split = make()
    kwin.window_for_caption = lambda title: None
    with pytest.raises(ts.SplitError, match="Sir"):
        await split.split("r1", [11])
    assert chrome.windows[1]["tabs"] == [10, 11, 12]


async def test_split_checks_tab_sets_in_every_source_window():
    chrome, _kwin, split = make()
    chrome.add_window(2, [(20, "Docs"), (21, "Search")])
    move = chrome.move

    async def disturb(tab_ids, window_id, index):
        await move(tab_ids, window_id, index)
        if tab_ids == [21]:
            await chrome.remove(20)

    chrome.move = disturb
    with pytest.raises(ts.SplitError, match="tab set"):
        await split.split("r1", [11, 21])


@pytest.mark.parametrize("missing", [False, True])
async def test_adopt_refuses_failed_or_missing_visible_placement(missing):
    chrome, kwin, split = make()
    chrome.add_window(7, [(70, "Jarvis lookup")])
    kwin.fail_place = not missing
    if missing:
        kwin.jarvis_uuid = None
    with pytest.raises(ts.SplitError, match=r"placement|KWin"):
        await split.adopt("r2", 70, ts.Placement("monitor", "DP-2"))
    assert ts.load_journal("r2")["state"] == "splitting"
    assert await split.recover() == ["r2"]
    assert chrome.removed == [70]


async def test_handback_recreates_lone_original_window_without_reload():
    chrome, _kwin, split = make()
    chrome.windows[1]["tabs"] = [11]
    chrome.tabs = {11: chrome.tabs[11]}
    await split.split("r1", [11])
    out = await split.handback("r1")
    assert out["returned"] == [11]
    assert len(chrome.windows) == 1
    assert chrome.removed == []
    assert ts.load_journal("r1")["state"] == "handed_back"
    assert out["notes"]


async def test_handback_unmark_failure_stays_recoverable_with_notes():
    _chrome, _kwin, split = make()
    await split.split("r1", [11])

    async def fail_unmark(tid):
        raise RuntimeError("unavailable")

    split.unmark = fail_unmark
    out = await split.handback("r1")
    j = ts.load_journal("r1")
    assert j["state"] == "splitting"
    assert j["notes"] == out["notes"]
    assert await split.recover() == []
    split.unmark = None
    assert await split.recover() == ["r1"]


async def test_recreated_window_placement_failure_retries_and_keeps_evidence():
    chrome, kwin, split = make()
    chrome.windows[1]["tabs"] = [11]
    chrome.tabs = {11: chrome.tabs[11]}
    await split.split("r1", [11])
    move = kwin.move_to_screen
    kwin.move_to_screen = lambda uuid, screen: False
    with pytest.raises(ts.SplitError, match="main"):
        await split.handback("r1")
    j = ts.load_journal("r1")
    assert j["state"] == "splitting"
    assert "main" in j["notes"][-1]
    assert await split.recover() == []
    kwin.move_to_screen = move
    assert await split.recover() == ["r1"]
    assert len(chrome.windows) == 1


async def test_leave_there_marker_retry_does_not_return_tabs():
    chrome, _kwin, split = make()
    j = await split.split("r1", [11])

    async def fail_unmark(tid):
        raise RuntimeError("unavailable")

    split.unmark = fail_unmark
    await split.handback("r1", leave_there=True)
    split.unmark = None
    assert await split.recover() == ["r1"]
    assert chrome.windows[j["jarvis_window"]]["tabs"] == [11]
    assert ts.load_journal("r1")["state"] == "left_there"


async def test_headless_minimise_does_not_require_main_monitor_placement():
    chrome, kwin, split = make(screens=[], main=None, hud=None)
    kwin.window_for_caption = lambda title: None
    j = await split.split("headless", [11])
    assert j["placement"]["mode"] == "minimise"
    assert chrome.windows[j["jarvis_window"]]["state"] == "minimized"
