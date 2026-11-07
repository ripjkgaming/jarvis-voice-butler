"""Always Aware tab split (docs/always-aware/PLAN.md §4.3).

On a web takeover only the task tab(s) move, without a reload, into a
dedicated "Jarvis window" on a monitor that is neither main nor the HUD
monitor. Sir's other tabs stay in his window on main. Moves go through the
locke-ext extension APIs (``chrome.windows.create({tabId})`` and
``chrome.tabs.move``), which keep page state, form input and media.
Placement goes through KWin (Brave runs on native Wayland), never with
activation. Everything is journaled under ``~/.jarvis/aware/brave/`` so a
bridge crash can hand the tabs back on the next start.

Hard rules kept here, in code:
  * every create/move is ``focused: false``; KWin scripts never activate;
  * a tab is only ever closed if Jarvis created it in this browser session;
  * hand-back never activates the returned tab.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from dataclasses import asdict, dataclass

from aware import state as st

MARK = "⟦J⟧ "
MIN_HALF_W = 1200
JOURNAL_DIR = "brave"


class SplitError(RuntimeError):
    """The split cannot be done safely; the caller reports ``stuck``."""


# ------------------------------------------------------------------ pure


@dataclass
class Placement:
    mode: str  # monitor | hud_half | minimise
    screen: str | None = None
    rect: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _overlap(a: dict, b: dict) -> int:
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return max(0, w) * max(0, h)


def choose_placement(
    screens: list[dict],
    main: str | None,
    hud: str | None,
    hud_rect: dict | None = None,
    min_half_w: int = MIN_HALF_W,
) -> Placement:
    """Where the Jarvis window goes. Pure.

    First output that is neither main nor HUD; else the half of the HUD
    monitor away from the HUD window (only when that half is wide enough and
    the HUD window's rectangle is known); else minimised.
    """
    others = [s for s in screens if s.get("name") not in (main, hud)]
    if others:
        return Placement("monitor", others[0]["name"])
    hs = next((s for s in screens if s.get("name") == hud), None)
    if hs and hud != main and hud_rect and hs["w"] // 2 >= min_half_w:
        half_w = hs["w"] // 2
        halves = [
            {"x": hs["x"], "y": hs["y"], "w": half_w, "h": hs["h"]},
            {"x": hs["x"] + half_w, "y": hs["y"], "w": hs["w"] - half_w, "h": hs["h"]},
        ]
        best = min(halves, key=lambda r: _overlap(r, hud_rect))
        if _overlap(best, hud_rect) == 0:
            return Placement("hud_half", hud, best)
    return Placement("minimise")


def marked(title: str) -> str:
    title = title or ""
    return title if title.startswith(MARK) else MARK + title


def unmarked(title: str) -> str:
    title = title or ""
    return title[len(MARK) :] if title.startswith(MARK) else title


def resolve_tab(tabs: list[dict], url: str) -> int:
    """The single marked extension tab for a CDP page url, else SplitError."""
    hits = [
        t
        for t in tabs
        if str(t.get("title", "")).startswith(MARK) and t.get("url") == url
    ]
    if not hits:
        raise SplitError("task tab not found among extension tabs")
    if len(hits) > 1:
        raise SplitError("task tab is ambiguous; nothing was moved")
    return int(hits[0]["id"])


_ACTIVATE = re.compile(r"workspace\.activeWindow\s*=\s*w\s*;")


def no_focus(js: str) -> str:
    """Strip the mover's activation line; refuse any other activation."""
    out = _ACTIVATE.sub("", js)
    if "activeWindow" in out:
        raise SplitError("KWin script would change focus")
    return out


# ------------------------------------------------------------------ journal


def journal_path(run_id: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(run_id))[:80]
    return st.sub(JOURNAL_DIR) / f"{safe}.json"


def load_journal(run_id: str) -> dict | None:
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(journal_path(run_id).read_text())
        return data if isinstance(data, dict) else None
    return None


def save_journal(j: dict) -> None:
    j["updated"] = time.time()
    if not st.write_private(journal_path(j["run_id"]), json.dumps(j, sort_keys=True)):
        raise SplitError("tab journal not writable")


def open_journals() -> list[dict]:
    out = []
    for p in sorted(st.sub(JOURNAL_DIR).glob("*.json")):
        with contextlib.suppress(OSError, ValueError):
            j = json.loads(p.read_text())
            if isinstance(j, dict) and j.get("state") in ("splitting", "split"):
                out.append(j)
    return out


# ------------------------------------------------------------------ extension API


class TabApi:
    """chrome.tabs / chrome.windows through an extension context.

    ``evaluate(expression, arg)`` runs a JS function expression in the
    locke-ext service worker (or the jarvis-tabs.html fallback page). Only
    the calls below exist: no navigation, no reload, no update of URL.
    """

    def __init__(self, evaluate):
        self._eval = evaluate

    async def query(self) -> list[dict]:
        return await self._eval(
            "()=>chrome.tabs.query({}).then(ts=>ts.map(t=>({id:t.id,"
            "windowId:t.windowId,index:t.index,active:t.active,title:t.title,"
            "url:t.url,incognito:t.incognito})))",
            None,
        )

    async def get_windows(self) -> list[dict]:
        return await self._eval(
            "()=>chrome.windows.getAll({populate:true}).then(ws=>ws.map(w=>({"
            "id:w.id,state:w.state,type:w.type,incognito:w.incognito,"
            "tabs:(w.tabs||[]).map(t=>({id:t.id,index:t.index,active:t.active,"
            "title:t.title,url:t.url}))})))",
            None,
        )

    async def create_window(self, tab_id: int, state: str = "normal") -> dict:
        if state not in ("normal", "minimized"):
            raise SplitError(f"window state {state!r} refused")
        return await self._eval(
            "([id,st])=>chrome.windows.create({tabId:id,focused:false,state:st})"
            ".then(w=>({id:w.id}))",
            [int(tab_id), state],
        )

    async def move(self, tab_ids: list[int], window_id: int, index: int) -> None:
        await self._eval(
            "([ids,w,i])=>chrome.tabs.move(ids,{windowId:w,index:i}).then(()=>true)",
            [[int(t) for t in tab_ids], int(window_id), int(index)],
        )

    async def activate(self, tab_id: int) -> None:
        """Make a tab active *within its own window* (never focuses a window)."""
        await self._eval(
            "(id)=>chrome.tabs.update(id,{active:true}).then(()=>true)", int(tab_id)
        )

    async def remove(self, tab_id: int) -> None:
        await self._eval("(id)=>chrome.tabs.remove(id).then(()=>true)", int(tab_id))


# ------------------------------------------------------------------ KWin


class KwinPlacer:
    """Real KWin placement by uuid; every script is passed through no_focus()."""

    def __init__(self, screens_fn=None):
        from system import monitor_window_move as mwm

        self._m = mwm
        self._screens = screens_fn or mwm.monitors

    def _brave(self, uuid: str) -> dict | None:
        info = self._m._info(uuid)
        cls = str((info or {}).get("resourceClass", "")).lower()
        return info if info and "brave" in cls else None

    def jarvis_window(self) -> str | None:
        from system import kwin_windows

        hits = []
        for query in ("⟦J⟧", "J⟧"):
            for uuid, title in kwin_windows.find(query):
                if title.startswith(MARK.strip()) and self._brave(uuid):
                    hits.append(uuid)
            if hits:
                break
        return hits[0] if len(set(hits)) == 1 else None

    def window_for_caption(self, title: str) -> str | None:
        from system import kwin_windows

        if not title:
            return None
        hits = {
            u
            for u, cap in kwin_windows.find(title[:60])
            if cap.startswith(title[:60]) and self._brave(u)
        }
        return hits.pop() if len(hits) == 1 else None

    def screen_of(self, uuid: str) -> str | None:
        return self._m.info_screen(self._m._info(uuid), self._screens())

    def geometry(self, uuid: str) -> dict | None:
        info = self._m._info(uuid)
        if not info:
            return None
        return {
            "x": int(info["x"]),
            "y": int(info["y"]),
            "w": int(info["width"]),
            "h": int(info["height"]),
            "screen": self._m.info_screen(info, self._screens()),
        }

    def _run(self, js: str) -> bool:
        return self._m.run_move_script(no_focus(js))

    def move_to_screen(self, uuid: str, screen: str) -> bool:
        screens = self._screens()
        if not self._run(self._m.move_script("", screen, screens, uuid=uuid)):
            return False
        for _ in range(12):
            if self.screen_of(uuid) == screen:
                return True
            time.sleep(0.1)
        return False

    def _by_uuid(self, uuid: str, body: str) -> str:
        want = json.dumps(uuid.strip().lower())
        return (
            "var m=workspace.windowList().filter(function(w){"
            f"return String(w.internalId).toLowerCase()==={want};}});"
            f"if(m.length){{var w=m[0];{body}}}"
        )

    def set_geometry(self, uuid: str, g: dict) -> bool:
        body = (
            "try{w.setMaximize(false,false);}catch(e){}"
            f"w.frameGeometry={{x:{int(g['x'])},y:{int(g['y'])},"
            f"width:{int(g['w'])},height:{int(g['h'])}}};"
        )
        return self._run(self._by_uuid(uuid, body))

    def place(self, uuid: str, placement: Placement) -> bool:
        if placement.mode == "minimise":
            return self._run(self._by_uuid(uuid, "w.minimized=true;"))
        if placement.mode == "monitor":
            return self.move_to_screen(uuid, placement.screen)
        if placement.mode == "hud_half" and placement.rect:
            screens = self._screens()
            target = next(s for s in screens if s["name"] == placement.screen)
            pos = json.dumps(target["name"])
            body = (
                "var o=(workspace.screens||[]).find(function(v){return v.name==="
                f"{pos};}});if(o&&w.output!==o){{try{{workspace.sendClientToScreen(w,o);}}"
                "catch(e){}}"
            )
            r = placement.rect
            ok = self._run(self._by_uuid(uuid, body)) and self.set_geometry(uuid, r)
            return ok and self.screen_of(uuid) == placement.screen
        return False


# ------------------------------------------------------------------ split


def _default_main(screens):
    from system import monitor_window_move as mwm

    return mwm._primary(screens)["name"] if screens else None


def _default_hud(screens):
    """(hud screen name, HUD window rect or None)."""
    from system import monitor_window_move as mwm

    info = mwm.kwin_info("Jarvis")
    rect = None
    if info and str(info.get("resourceClass", "")).lower() == "jarvis-shell":
        rect = {
            "x": int(info["x"]),
            "y": int(info["y"]),
            "w": int(info["width"]),
            "h": int(info["height"]),
        }
    return mwm.hud_screen(screens), rect


def _default_active():
    import active_window

    return active_window.active()


def _focus_key(win: dict | None):
    if not win:
        return None
    for key in ("internalId", "uuid", "id"):
        if win.get(key):
            return str(win[key])
    return str(win.get("pid") or "")


class TabSplit:
    def __init__(
        self,
        api,
        kwin,
        *,
        screens=None,
        main=None,
        hud=None,
        active=None,
        browser_id=None,
        unmark=None,
    ):
        self.api = api
        self.kwin = kwin
        self.screens = screens or (
            lambda: __import__(
                "system.monitor_window_move", fromlist=["monitors"]
            ).monitors()
        )
        self.main = main or _default_main
        self.hud = hud or _default_hud
        self.active = active or _default_active
        self.browser_id = browser_id or (lambda: "")
        self.unmark = unmark  # async (tab_id) -> None, strips the title marker

    # -- helpers

    async def _tab_map(self) -> dict[int, dict]:
        return {int(t["id"]): t for t in await self.api.query()}

    def note_created(self, run_id: str, tab_id: int, role: str = "scratch") -> None:
        """Record a tab Jarvis opened himself (scratch: closed at hand-back,
        result: joins Sir's window). Only these may ever be closed."""
        j = load_journal(run_id)
        if j is None:
            return
        j.setdefault("created", [])
        j["created"] = [c for c in j["created"] if c["tab_id"] != int(tab_id)]
        j["created"].append({"tab_id": int(tab_id), "role": role})
        save_journal(j)

    # -- split

    async def split(self, run_id: str, tab_ids: list[int]) -> dict:
        if not tab_ids:
            raise SplitError("no task tab")
        tabs = await self._tab_map()
        for tid in tab_ids:
            if tid not in tabs:
                raise SplitError("task tab not found")
            if tabs[tid].get("incognito"):
                raise SplitError("private window tabs are never taken over")
        first = tabs[tab_ids[0]]
        user_win = int(first["windowId"])
        windows = {int(w["id"]): w for w in await self.api.get_windows()}
        source_tabs_before = {
            int(tabs[tid]["windowId"]): [
                t["id"] for t in windows[int(tabs[tid]["windowId"])]["tabs"]
            ]
            for tid in tab_ids
        }
        user_tabs_before = source_tabs_before[user_win]

        screens = self.screens()
        main = self.main(screens)
        hud, hud_rect = self.hud(screens)
        placement = choose_placement(screens, main, hud, hud_rect)
        focus_before = _focus_key(self.active())

        rows = []
        for tid in tab_ids:
            t = tabs[tid]
            win_tabs = list(windows[int(t["windowId"])]["tabs"])
            idx = int(t["index"])
            left = next((x for x in win_tabs if x["index"] == idx - 1), None)
            rows.append(
                {
                    "tab_id": tid,
                    "orig_window": int(t["windowId"]),
                    "orig_index": idx,
                    "was_active": bool(t.get("active")),
                    "left_neighbour": int(left["id"]) if left else None,
                    "left_title": unmarked(left["title"]) if left else "",
                    "url": t.get("url", ""),
                    "title": unmarked(t.get("title", "")),
                }
            )
        user_active = next(
            (x for x in windows[user_win]["tabs"] if x.get("active")), None
        )
        j = {
            "run_id": run_id,
            "browser_id": self.browser_id(),
            "created_ts": time.time(),
            "state": "splitting",
            "tabs": rows,
            "user_window": user_win,
            "user_tabs_before": user_tabs_before,
            "source_tabs_before": source_tabs_before,
            "jarvis_window": None,
            "placement": placement.to_dict(),
            "moved_user_window": False,
            "user_geometry_before": None,
            "user_geometry_after": None,
            "user_uuid": None,
            "created": [],
        }
        save_journal(j)
        try:
            state = "minimized" if placement.mode == "minimise" else "normal"
            win = await self.api.create_window(tab_ids[0], state)
            j["jarvis_window"] = int(win["id"])
            save_journal(j)
            if len(tab_ids) > 1:
                await self.api.move(tab_ids[1:], j["jarvis_window"], -1)
            juuid = self.kwin.jarvis_window()
            if not juuid and placement.mode != "minimise":
                raise SplitError("Jarvis window not found in KWin")
            if juuid and not self.kwin.place(juuid, placement):
                raise SplitError("Jarvis window placement failed")
            j["jarvis_uuid"] = juuid
            # Sir's window to main, only if it is not already there.
            new_active = None
            for w in await self.api.get_windows():
                if int(w["id"]) == user_win:
                    new_active = next((x for x in w["tabs"] if x.get("active")), None)
            title = unmarked((new_active or user_active or {}).get("title", ""))
            uuuid = self.kwin.window_for_caption(title) if title else None
            j["user_uuid"] = uuuid
            remaining = [t for t in user_tabs_before if t not in tab_ids]
            if remaining and screens:
                if not uuuid or not main:
                    raise SplitError("Sir's window placement cannot be verified")
                if self.kwin.screen_of(uuuid) != main:
                    j["user_geometry_before"] = self.kwin.geometry(uuuid)
                    # Save intent before moving so recovery can inspect the result.
                    save_journal(j)
                    moved = self.kwin.move_to_screen(uuuid, main)
                    j["user_geometry_after"] = self.kwin.geometry(uuuid)
                    j["moved_user_window"] = (
                        j["user_geometry_before"] != j["user_geometry_after"]
                    )
                    save_journal(j)
                    if not moved or self.kwin.screen_of(uuuid) != main:
                        raise SplitError("Sir's window could not be placed on main")
            save_journal(j)
            # Verify: Sir's window lost exactly the moved tabs; focus unchanged.
            after = {int(w["id"]): w for w in await self.api.get_windows()}
            for wid, before in source_tabs_before.items():
                moved_here = {r["tab_id"] for r in rows if r["orig_window"] == wid}
                want = [t for t in before if t not in moved_here]
                actual = [t["id"] for t in after.get(wid, {}).get("tabs", [])]
                if actual != want:
                    raise SplitError("window_split: Sir's tab set changed")
            moved_actual = [
                t["id"] for t in after.get(j["jarvis_window"], {}).get("tabs", [])
            ]
            if moved_actual != tab_ids:
                raise SplitError("window_split: task tab set changed")
            if _focus_key(self.active()) != focus_before:
                raise SplitError("window_split: focus changed")
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self.handback(run_id)
            if isinstance(exc, SplitError):
                raise
            raise SplitError(f"window_split: {exc}") from exc
        j["state"] = "split"
        save_journal(j)
        return j

    async def adopt(self, run_id: str, tab_id: int, placement: Placement) -> dict:
        """Journal a window Jarvis opened himself (new-tab mode) and place it."""
        tabs = await self._tab_map()
        if int(tab_id) not in tabs:
            raise SplitError("Jarvis's tab not found")
        jwin = int(tabs[int(tab_id)]["windowId"])
        others = [
            w
            for w in await self.api.get_windows()
            if int(w["id"]) != jwin
            and w.get("type", "normal") == "normal"
            and not w.get("incognito")
        ]
        j = {
            "run_id": run_id,
            "browser_id": self.browser_id(),
            "created_ts": time.time(),
            "state": "splitting",
            "tabs": [],
            "user_window": int(others[0]["id"]) if others else None,
            "user_tabs_before": [],
            "jarvis_window": jwin,
            "placement": placement.to_dict(),
            "moved_user_window": False,
            "user_geometry_before": None,
            "user_geometry_after": None,
            "user_uuid": None,
            "created": [{"tab_id": int(tab_id), "role": "scratch"}],
        }
        save_journal(j)
        juuid = self.kwin.jarvis_window()
        if not juuid and placement.mode != "minimise":
            raise SplitError("Jarvis window not found in KWin")
        if juuid and not self.kwin.place(juuid, placement):
            raise SplitError("Jarvis window placement failed")
        if not juuid:
            windows = await self.api.get_windows()
            if not any(
                int(w["id"]) == jwin and w.get("state") == "minimized" for w in windows
            ):
                raise SplitError("Jarvis window minimised placement not verified")
        j["jarvis_uuid"] = juuid
        j["state"] = "split"
        save_journal(j)
        return j

    # -- hand-back

    async def handback(
        self, run_id: str, *, verified: bool = False, leave_there: bool = False
    ) -> dict:
        try:
            return await self._handback(
                run_id, verified=verified, leave_there=leave_there
            )
        except Exception as exc:
            j = load_journal(run_id)
            if j is not None:
                j["state"] = "splitting"
                j.setdefault("notes", []).append(f"hand-back incomplete: {exc}")
                save_journal(j)
            raise

    async def _handback(
        self, run_id: str, *, verified: bool, leave_there: bool
    ) -> dict:
        j = load_journal(run_id)
        out = {"neighbour_title": "", "returned": [], "notes": []}
        if j is None or j.get("state") in ("handed_back", "left_there"):
            return out
        verified = verified or j.get("handback_verified", False)
        if verified:
            j["handback_verified"] = True
            save_journal(j)
        leave_there = leave_there or j.get("leave_there_requested", False)
        if leave_there:
            j["leave_there_requested"] = True
            save_journal(j)
        same_browser = j.get("browser_id", "") == self.browser_id()
        if not same_browser:
            # Brave restarted: tab ids are stale and the markers died with
            # the pages. Nothing can be moved or closed by id safely.
            j["state"] = "handed_back"
            j["notes"] = ["browser restarted; nothing to return"]
            save_journal(j)
            out["notes"] = j["notes"]
            return out
        tabs = await self._tab_map()
        own = {int(r["tab_id"]) for r in j["tabs"]}
        marker_failed = False
        for tid in own | {int(c["tab_id"]) for c in j.get("created", [])}:
            if tid in tabs and self.unmark:
                try:
                    await self.unmark(tid)
                except Exception:
                    marker_failed = True
                    out["notes"].append(f"tab {tid} title marker could not be removed")
        if leave_there:
            j["state"] = "splitting" if marker_failed else "left_there"
            j["notes"] = out["notes"]
            save_journal(j)
            return out
        windows = {int(w["id"]): w for w in await self.api.get_windows()}
        recreated = {int(k): int(v) for k, v in j.get("recreated_windows", {}).items()}
        for row in sorted(j["tabs"], key=lambda r: r["orig_index"]):
            tid = int(row["tab_id"])
            if tid not in tabs:
                out["notes"].append(f"tab {tid} is gone")
                continue
            original = int(row["orig_window"])
            dest = recreated.get(original, original)
            if dest not in windows:
                # Moving the sole task tab closes its original Chrome window.
                # Recreate that window around the existing page, without reload.
                new_window = await self.api.create_window(tid, "normal")
                dest = int(new_window["id"])
                recreated[original] = dest
                j["recreated_windows"] = recreated
                if original == j.get("user_window"):
                    j["user_window"] = dest
                j.setdefault("placement_pending", []).append(tid)
                save_journal(j)
                windows = {int(w["id"]): w for w in await self.api.get_windows()}
                tabs = await self._tab_map()
                out["notes"].append(f"original window {original} recreated as {dest}")
            if tid in j.get("placement_pending", []):
                title = tabs[tid].get("title", "")
                uuid = self.kwin.window_for_caption(title)
                main = self.main(self.screens())
                if (
                    not uuid
                    or not main
                    or not self.kwin.move_to_screen(uuid, main)
                    or self.kwin.screen_of(uuid) != main
                ):
                    raise SplitError("recreated user window placement on main failed")
                j["placement_pending"].remove(tid)
                save_journal(j)
                out["returned"].append(tid)
                continue
            if int(tabs[tid]["windowId"]) == dest and j.get("state") == "splitting":
                continue
            wtabs = [x for x in windows[dest]["tabs"] if int(x["id"]) != tid]
            idx = min(int(row["orig_index"]), len(wtabs))
            left = row.get("left_neighbour")
            match = next((x for x in wtabs if int(x["id"]) == left), None)
            if match is not None:
                idx = wtabs.index(match) + 1
            elif left is not None:
                idx = min(int(row["orig_index"]), len(wtabs))
            await self.api.move([tid], dest, idx)
            out["returned"].append(tid)
            if not out["neighbour_title"]:
                out["neighbour_title"] = unmarked(
                    match["title"] if match else row.get("left_title", "")
                )
            windows = {int(w["id"]): w for w in await self.api.get_windows()}
        # Tabs Jarvis created: results join Sir's window, scratch is closed.
        tabs = await self._tab_map()
        user_win = j.get("user_window")
        user_win = int(user_win) if user_win is not None else -1
        for c in j.get("created", []):
            tid = int(c["tab_id"])
            if tid in own or tid not in tabs:
                continue  # never close a tab that was Sir's
            if c.get("role") == "result" and verified and user_win in windows:
                wt = windows[user_win]["tabs"]
                act = next((x for x in wt if x.get("active")), None)
                idx = (wt.index(act) + 1) if act else -1
                await self.api.move([tid], user_win, idx)
            else:
                await self.api.remove(tid)
            windows = {int(w["id"]): w for w in await self.api.get_windows()}
        # Sir's window geometry back, only if Jarvis moved it and he hasn't since.
        if j.get("moved_user_window") and j.get("user_uuid"):
            now = self.kwin.geometry(j["user_uuid"])
            if (
                now
                and now == j.get("user_geometry_after")
                and j.get("user_geometry_before")
            ):
                before = j["user_geometry_before"]
                if before.get("screen") and before["screen"] != now.get("screen"):
                    self.kwin.move_to_screen(j["user_uuid"], before["screen"])
                self.kwin.set_geometry(j["user_uuid"], before)
        j["state"] = "splitting" if marker_failed else "handed_back"
        j["notes"] = out["notes"]
        j["returned"] = out["returned"]
        save_journal(j)
        return out

    async def recover(self) -> list[str]:
        """Sidecar start: hand back every unfinished split. Never raises."""
        done = []
        for j in open_journals():
            with contextlib.suppress(Exception):
                await self.handback(j["run_id"])
                recovered = load_journal(j["run_id"]) or {}
                if recovered.get("state") in ("handed_back", "left_there"):
                    done.append(j["run_id"])
        return done
