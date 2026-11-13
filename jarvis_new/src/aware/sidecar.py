"""Always Aware sidecar: scheduler -> skip -> capture -> vision -> log (§1-§3).

Runs as a bridge sidecar thread (``bridge.SIDECAR_THREADS`` entry
``("aware.sidecar", "JARVIS_AWARE")``). Ships OFF: nothing is captured
until ``state.json.enabled`` is true (voice: "always aware on"), and
``JARVIS_AWARE=0`` or ``~/.jarvis/aware/KILL`` beat everything.

Every machine touch goes through ``Deps`` so the whole tick is tested with
fakes (no real screens are ever captured in tests). Opportunities go to the
P2 gate and offers modules, and accepted offers to the P3 takeover runner,
all imported lazily: a module that is not there yet means "log only".
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from aware import capture, ledger, scheduler, skip, spans, vision
from aware import log as alog
from aware import state as st

PHASE_HOLD_S = 30.0  # how long a "skipped" glyph stays lit
FEED_EVERY_S = 3600.0
HISTORY_N = 5  # recent tick summaries sent as context
WATCH_TTL_S = 10 * 60  # an unconfirmed opportunity is forgotten after this
PRUNE_EVERY_S = 6 * 3600.0
_LISTEN: list[Callable[[str], None]] = []


def set_listener(fn: Callable[[str], None] | None) -> None:
    """Bridge hook: open one listening turn seeded with an offer line."""
    _LISTEN.clear()
    if fn is not None:
        _LISTEN.append(fn)


# ------------------------------------------------------------------ live deps


def _live_screens() -> list[dict]:
    from system import monitor_window_move as mwm

    return mwm.monitors()


def _live_hud(screens: list[dict]) -> str | None:
    from system import monitor_window_move as mwm

    try:
        info = mwm.kwin_info("Jarvis")
    except Exception:
        info = None
    if not info or str(info.get("resourceClass", "")).lower() != "jarvis-shell":
        return None  # unknown: capture_set excludes the primary (privacy-safe)
    return mwm.info_screen(info, screens)


def _live_tabs() -> list[dict]:
    from aware import cdp

    try:
        return [t for t in cdp.targets(timeout=1.5) if t.get("type") == "page"]
    except Exception:
        return []


def _live_ask(clips: list[dict], ctx: dict) -> dict:
    import focus

    key = focus.google_key()
    if not key:
        raise vision.VisionError("no key")
    return vision.ask(clips, ctx, key=key, models=focus.gemini_models())


def _live_active() -> dict | None:
    import active_window

    return active_window.active()


async def _live_speak(text: str) -> None:
    import speak

    await asyncio.to_thread(speak.speak, text, source="aware", min_gap_s=0)


async def _live_listen(offer_id: str, text: str) -> None:
    """After an offer is spoken: open one listening turn for the yes/no."""
    for fn in list(_LISTEN):
        with contextlib.suppress(Exception):
            await asyncio.to_thread(fn, text)


def _in_call() -> bool | None:
    try:
        import speak

        status = speak.wake_status()
    except Exception:
        return None
    return bool(status.get("in_call")) if status else None


@dataclass
class Deps:
    now: Callable[[], float] = time.time
    signals: Callable[[], scheduler.Signals] = scheduler.read_signals
    screens: Callable[[], list] = _live_screens
    hud: Callable[[list], str | None] = _live_hud
    tabs: Callable[[], list] = _live_tabs
    active: Callable[[], dict | None] = _live_active
    gather: Callable[..., skip.Context] = skip.gather
    presence: Callable[[float], str] = skip.presence
    windows: Callable[[], list | None] = capture.live_windows
    shoot: Callable[[], Any] = capture.shoot_desktop
    workdir: Callable[[], Any] = capture.private_workdir
    encode: Callable[..., Any] = capture.encode
    ask: Callable[[list, dict], dict] = _live_ask
    sleep: Callable[[float], None] = time.sleep
    speak: Callable[[str], Any] = _live_speak
    listen: Callable[[str, str], Any] = _live_listen
    in_call: Callable[[], bool | None] = _in_call
    modules: Callable[[str], Any] = None  # name -> module or None
    run_takeover: Callable[..., Any] = None  # (plan) -> None; default: thread
    extra: dict = field(default_factory=dict)


def _import(name: str):
    try:
        import importlib

        return importlib.import_module(f"aware.{name}")
    except ImportError:
        return None


# ------------------------------------------------------------------ helpers


def set_phase(phase: str, now: float, hold: float = 0.0, **fields) -> None:
    st.update(phase=phase, phase_until=now + hold if hold else 0.0, **fields)


def active_monitor(windows: list, active: dict | None, screens: list) -> str | None:
    """Output holding the centre of the active window. Pure."""
    if not active or not windows:
        return None
    ids = {str(active.get(k)) for k in ("internalId", "uuid", "id") if active.get(k)}
    win = next((w for w in windows if str(w.get("id")) in ids), None)
    if win is None:
        title = active.get("title")
        win = next((w for w in windows if title and w.get("title") == title), None)
    if win is None:
        return None
    cx, cy = win["x"] + win["w"] / 2, win["y"] + win["h"] / 2
    for s in screens:
        if s["x"] <= cx < s["x"] + s["w"] and s["y"] <= cy < s["y"] + s["h"]:
            return s["name"]
    return None


def window_labels(windows: list, screen: dict, rules, tabs: list) -> list[str]:
    out = []
    for w in windows:
        if w.get("minimized") or not capture._on(
            (w["x"], w["y"], w["w"], w["h"]), screen
        ):
            continue
        if capture.sensitive_window(w, tabs, rules):
            out.append("[hidden]")
        else:
            out.append(f"{w.get('app', '')}: {w.get('title', '')}"[:120])
    return out


def opportunity_from(raw: dict, entry: dict, idx: int, ctx_flags: dict):
    """vision opportunity -> aware.types.Opportunity with sidecar evidence."""
    from aware.types import Opportunity

    opp = Opportunity.from_vision(
        raw, subject=entry.get("subject", ""), tick_id=entry["tick_id"], idx=idx
    )
    opp.evidence.update(
        {
            "vision_kind": raw.get("kind", ""),
            "subject": entry.get("subject", ""),
            "app": entry.get("app", ""),
            "url_host": entry.get("url_host", ""),
            "tick_id": entry["tick_id"],
            "window_title": entry.get("window_title", ""),
            "summary": entry.get("summary", ""),
            "vision": raw,
            "context": ctx_flags,
        }
    )
    if entry.get("url_host"):
        opp.evidence.setdefault("url", "https://" + entry["url_host"])
    if entry.get("tab_id"):
        opp.evidence.setdefault("tab_id", entry["tab_id"])
    return opp


# ------------------------------------------------------------------ runner


class Runner:
    def __init__(self, deps: Deps | None = None):
        self.deps = deps or Deps()
        if self.deps.modules is None:
            self.deps.modules = _import
        self.sched = scheduler.Scheduler()
        self.prev: dict = {}  # monitor -> {hash, windows} of the last upload
        self.prev_summary = ""
        self.history: list[str] = []  # "HH:MM summary", oldest first
        self.watching: dict[str, float] = {}  # opportunity key -> first seen
        self.parked = False
        self.last_interval = 0.0
        self.last_feed = 0.0
        self.last_prune = 0.0
        self.takeovers: list[threading.Thread] = []
        self._speaker_set = False
        self.coordinator = None

    # -- opportunity pipeline ------------------------------------------------

    def _wire_speaker(self, offers) -> None:
        if self._speaker_set or offers is None:
            return
        with contextlib.suppress(Exception):
            offers.set_speaker(self.deps.speak)
            if hasattr(offers, "set_listener"):
                offers.set_listener(self.deps.listen)
            self._speaker_set = True

    async def _handle_opps(self, opps: list, log_only: bool) -> list[dict]:
        gate = self.deps.modules("gate")
        offers = self.deps.modules("offers")
        self._wire_speaker(offers)
        tags = []
        for opp in opps:
            tag = {
                "kind": opp.task_kind,
                "title": opp.title,
                "self_feasibility": opp.self_feasibility,
                "gate": "pending",
            }
            tags.append(tag)
            if (
                offers is not None
                and getattr(offers, "is_assist", None)
                and offers.is_assist(opp)
            ):
                tag["gate"] = "assist"
                if not log_only:
                    try:
                        outcome = await offers.maybe_assist(opp)
                    except Exception as exc:
                        tag["offer"] = f"error:{type(exc).__name__}"[:80]
                        continue
                    status = getattr(outcome, "status", outcome)
                    tag["offer"] = str(getattr(status, "value", status))
                continue
            if gate is None:
                continue
            try:
                result = await gate.evaluate(opp)
            except Exception as exc:
                tag["gate"] = f"fail:error:{type(exc).__name__}"[:80]
                continue
            tag["gate"] = (
                result.log_tag()
                if hasattr(result, "log_tag")
                else ("pass" if result.passed else f"fail:{result.stage_failed}")
            )
            if not result.passed or log_only or offers is None:
                if result.passed and log_only:
                    tag["offer"] = "held:log_only"
                continue
            set_phase("offer", self.deps.now(), hold=90.0)
            try:
                outcome = await offers.maybe_offer(opp, result)
            except Exception as exc:
                tag["offer"] = f"error:{type(exc).__name__}"
                continue
            status = getattr(outcome, "status", outcome)
            status = str(getattr(status, "value", status))
            tag["offer"] = status
            if status == "accepted" and result.plan:
                self.start_takeover(result.plan, getattr(outcome, "offer_id", ""))
        return tags

    def start_takeover(self, plan, offer_id: str = "") -> None:
        if self.deps.run_takeover is not None:
            self.deps.run_takeover(plan, offer_id)
            return
        t = threading.Thread(
            target=run_takeover_blocking,
            args=(plan, self.deps.speak, offer_id),
            name="aware-takeover",
            daemon=True,
        )
        t.start()
        self.takeovers.append(t)

    # -- one tick --------------------------------------------------------------

    def skip(self, reason: str, now: float, log_it: bool = True) -> dict:
        if not log_it:  # off / killed: touch no files at all
            return {"outcome": "skip", "reason": reason}
        set_phase("skipped", now, PHASE_HOLD_S, last_reason=reason)
        return alog.skip(reason, now)

    def tick(self) -> dict:
        if self.coordinator is not None:
            from aware.coordinator import allowed

            if not allowed(self.deps.now()):
                return self.skip("controls", self.deps.now(), log_it=False)
            return self.coordinator.poll()
        return self._screen_tick()

    def _screen_tick(self) -> dict:
        d = self.deps
        now = d.now()
        state = st.load()
        if not st.env_allows() or not state.get("enabled"):
            return self.skip("disabled", now, log_it=False)
        if st.killed():
            return self.skip("killed", now, log_it=False)
        if st.paused(state, now):
            return self.skip("paused", now)
        backoff = self.sched.backoff_reason(now)
        if backoff:
            return self.skip(backoff, now)
        sig = d.signals()
        reason = scheduler.guard(sig)
        if reason:
            return self.skip(reason, now)
        budget = ledger.level(now)
        if budget == "stop":
            return self.skip("budget", now)
        try:
            screens = d.screens() or []
        except Exception:
            screens = []
        hud = None
        with contextlib.suppress(Exception):
            hud = d.hud(screens)
        tabs = d.tabs() or []
        front_tab = tabs[0] if tabs else None
        hud_unknown = hud is None and bool(screens)
        if hud_unknown:
            # Shell window not found: treat the primary as the HUD monitor
            # (the shell's default), which is the privacy-safe choice.
            kept = capture.capture_set(screens, None)
            hud = next((s["name"] for s in screens if s not in kept), None)
        ctx = d.gather([s["name"] for s in screens], hud, now, front_tab)
        verdict = skip.evaluate(ctx)
        if verdict.skip:
            if verdict.reason in ("disabled", "killed"):
                return self.skip(verdict.reason, now, log_it=False)
            return self.skip(verdict.reason, now)
        self.sched.last_tick = now
        row = self._see(screens, hud, tabs, front_tab, ctx, verdict, budget, now)
        if hud_unknown and isinstance(row, dict):
            row["hud_unknown"] = True
        return row

    def _see(self, screens, hud, tabs, front_tab, ctx, verdict, budget, now) -> dict:
        d = self.deps
        tick_id = uuid.uuid4().hex[:10]
        wins = d.windows()
        if wins is None:
            set_phase("skipped", now, PHASE_HOLD_S, last_reason="redaction_unavailable")
            return alog.skip("redaction_unavailable", now)
        active = None
        with contextlib.suppress(Exception):
            active = d.active()
        act_mon = active_monitor(wins, active, screens)
        targets = capture.capture_set(screens, hud)
        if act_mon is None and targets:
            act_mon = targets[0]["name"]
        set_phase("capturing", now, capture.BURST_S + 2, last_tick=now)
        workdir = d.workdir()
        b = capture.burst(
            shoot=d.shoot,
            screens=screens,
            hud=hud,
            windows_fn=lambda: wins,
            tabs=tabs,
            active_monitor=act_mon,
            workdir=workdir,
            sleep=d.sleep,
            rules=ctx.rules,
        )
        if b.workdir is None:
            b.workdir = workdir
        try:
            return self._after_burst(
                b,
                screens,
                wins,
                tabs,
                front_tab,
                active,
                act_mon,
                ctx,
                verdict,
                budget,
                tick_id,
                now,
            )
        finally:
            b.cleanup()

    def _after_burst(
        self,
        b,
        screens,
        wins,
        tabs,
        front_tab,
        active,
        act_mon,
        ctx,
        verdict,
        budget,
        tick_id,
        now,
    ) -> dict:
        d = self.deps
        if not b.ok:
            if b.reason == "capture":
                self.sched.note_error("capture", now)
                set_phase("idle", now, last_reason="error:capture")
                return alog.error("capture", now, tick_id=tick_id)
            set_phase("skipped", now, PHASE_HOLD_S, last_reason=b.reason)
            return alog.skip(b.reason, now)
        changed = {}
        for name, mon in b.monitors.items():
            h = mon.get("hash6", mon.get("hash1", 0))
            if capture.monitor_changed(name, h, mon["windows"], self.prev):
                changed[name] = mon
        if not changed and self.sched.study and b.monitors:
            # Watching for a bit: a still screen is the point (Sir is stuck),
            # so look again even though nothing moved.
            name = act_mon if act_mon in b.monitors else sorted(b.monitors)[0]
            changed = {name: b.monitors[name]}
        if budget == "active_only":
            changed = {k: v for k, v in changed.items() if k == act_mon}
        if not changed:
            self.sched.after_tick(changed=False, importance=0, opportunity=False)
            set_phase("idle", now, last_reason="unchanged")
            row = alog.write({"outcome": "unchanged", "tick_id": tick_id}, now)
            spans.update(row, now)
            return row
        by_name = {s["name"]: s for s in screens}
        clips, labels = [], []
        for i, (name, mon) in enumerate(sorted(changed.items())):
            out = b.workdir / f"{mon['key']}.mp4"
            path = d.encode(b.workdir, mon["key"], out)
            if path is None:
                continue
            label = f"M{i + 1} {name}" + (
                ", active window here" if name == act_mon else ""
            )
            clips.append(
                {"label": label, "mp4": path.read_bytes(), "active": name == act_mon}
            )
            labels.append(name)
        if not clips:
            self.sched.note_error("capture", now)
            set_phase("idle", now, last_reason="error:encode")
            return alog.error("encode", now, tick_id=tick_id)
        url_host = (
            skip.host_path((front_tab or {}).get("url", ""))
            if ctx.foreground_url
            else ""
        )
        vctx = {
            "time": time.strftime("%A %H:%M", time.localtime(now)),
            "monitors": [
                {
                    "label": name,
                    "windows": window_labels(wins, by_name[name], ctx.rules, tabs),
                }
                for name in labels
                if name in by_name
            ],
            "active": (
                "[hidden]"
                if active
                and skip.is_sensitive(active.get("app", ""), active.get("title", ""))
                else f"{(active or {}).get('app', '')}: {(active or {}).get('title', '')}"
            ),
            "url": url_host,
            "previous": self.prev_summary,
            "history": list(self.history),
        }
        if not st.load().get("enabled") or st.killed() or st.paused(st.load(), d.now()):
            return self.skip("controls", d.now(), log_it=False)
        try:
            res = d.ask(clips, vctx)
        except vision.QuotaError:
            self.sched.note_error("quota", now)
            set_phase("idle", now, last_reason="backoff_quota")
            return alog.error("quota", now, tick_id=tick_id)
        except Exception as exc:
            self.sched.note_error("outage", now)
            set_phase("idle", now, last_reason="error:vision")
            return alog.error(f"vision:{str(exc)[:80]}", now, tick_id=tick_id)
        if (
            not st.env_allows()
            or not st.load().get("enabled")
            or st.killed()
            or st.paused(st.load(), d.now())
        ):
            return self.skip("controls", d.now(), log_it=False)
        ledger.record(res.get("tokens", 0), now)
        for name in labels:
            mon = changed[name]
            self.prev[name] = {
                "hash": mon.get("hash6", mon.get("hash1", 0)),
                "windows": mon["windows"],
            }
        self._audit_upload(clips, res, active, now)
        obs = res["obs"]
        entry = alog.tick_entry(
            tick_id=tick_id,
            interval_s=self.last_interval,
            obs=obs,
            app=(active or {}).get("app", ""),
            title=(active or {}).get("title", ""),
            url_host=url_host,
            model=res.get("model", ""),
            tokens=res.get("tokens", 0),
            bytes_up=res.get("bytes_up", 0),
            latency_ms=res.get("latency_ms", 0),
            monitors=labels,
        )
        if front_tab and url_host:
            entry["tab_id"] = front_tab.get("id", "")
        tags = []
        study = False
        if obs["opportunities"] and not obs.get("sensitive"):
            ready, waiting = self._confirm(obs["opportunities"], active, now)
            study = bool(waiting)
            tags.extend({**o, "gate": "watching"} for o in waiting)
            obs = {**obs, "opportunities": ready}
        if obs["opportunities"] and not obs.get("sensitive"):
            flags = {
                "guest": ctx.guest,
                "private": ctx.private_call,
                "locked": ctx.locked,
                "focus": ctx.focus_active,
                "school": ctx.school,
            }
            with contextlib.suppress(Exception):
                import input_idle

                flags["idle_s"] = input_idle.idle_seconds()
            with contextlib.suppress(Exception):
                flags["in_call"] = d.in_call()
            try:
                # The real window title stays in memory (the log keeps a hash).
                src = {**entry, "window_title": (active or {}).get("title", "")}
                opps = [
                    opportunity_from(raw, src, i, flags)
                    for i, raw in enumerate(obs["opportunities"])
                ]
                tags += asyncio.run(self._handle_opps(opps, verdict.log_only))
            except Exception as exc:
                tags += [
                    {**o, "gate": f"fail:error:{type(exc).__name__}"}
                    for o in obs["opportunities"]
                ]
        entry["opportunities"] = [
            {
                k: t.get(k)
                for k in ("kind", "title", "self_feasibility", "gate", "offer")
                if k in t
            }
            for t in tags
        ] or entry["opportunities"]
        if verdict.log_only:
            entry["log_only"] = verdict.reason
        entry.pop("tab_id", None)
        row = alog.write(entry, now)
        spans.update(row, now)
        self.prev_summary = obs.get("summary", "")
        if self.prev_summary:
            stamp = time.strftime("%H:%M", time.localtime(now))
            self.history = ([*self.history, f"{stamp} {self.prev_summary}"])[
                -HISTORY_N:
            ]
        self.sched.after_tick(
            changed=True,
            importance=obs.get("importance", 0),
            opportunity=bool(obs["opportunities"]) or study,
            activity=obs.get("activity", ""),
            study=study,
        )
        phase = st.load().get("phase")
        if phase != "offer":
            set_phase("idle", now, last_reason="seen")
        return row

    def _confirm(self, raws: list, active, now: float) -> tuple[list, list]:
        """Watch before offering: an opportunity is handed on only when the
        next look (STUDY_S later) sees it again. Returns (ready, waiting)."""
        self.watching = {
            k: t for k, t in self.watching.items() if now - t < WATCH_TTL_S
        }
        title = str((active or {}).get("title", "")).strip().lower()[:80]
        ready, waiting = [], []
        for raw in raws:
            key = f"{raw.get('kind', '')}|{title}"
            if key in self.watching:
                self.watching.pop(key, None)
                ready.append(raw)
            else:
                self.watching[key] = now
                waiting.append(raw)
        return ready, waiting

    def _audit_upload(self, clips, res, active, now) -> None:
        from aware import transactions

        for c in clips:
            transactions.audit(
                {
                    "event": "upload",
                    "sha256": hashlib.sha256(c["mp4"]).hexdigest()[:16],
                    "bytes": len(c["mp4"]),
                    "model": res.get("model", ""),
                    "app": (active or {}).get("app", "")[:40],
                    "reason": "tick",
                },
                now,
            )

    # -- housekeeping + loop -------------------------------------------------

    def housekeeping(self, now: float) -> None:
        if now - self.last_feed >= FEED_EVERY_S:
            self.last_feed = now
            with contextlib.suppress(Exception):
                from aware import memory_feed

                memory_feed.feed(now)
        if now - self.last_prune >= PRUNE_EVERY_S:
            self.last_prune = now
            with contextlib.suppress(Exception):
                alog.prune(now)
                spans.prune(now)

    def next_delay(self) -> float:
        try:
            sig = self.deps.signals()
        except Exception:
            sig = scheduler.Signals()
        delay = self.sched.next_delay(sig)
        return min(delay, 180.0) if self.coordinator is not None else delay

    def loop(self, stop: threading.Event, first_delay: float = 60.0) -> None:
        # Crash recovery runs even when Always Aware is off: a run that was
        # interrupted by a bridge restart must still hand Sir's tabs back.
        with contextlib.suppress(Exception):
            back = recover(self.deps.modules)
            if back:
                alog.write({"outcome": "seen", "event": "recovered", "runs": back})
        for line in recover_runs(self.deps.modules):
            with contextlib.suppress(Exception):
                asyncio.run(self.deps.speak(line))
        if stop.wait(first_delay):
            return
        lock = st.aware_dir() / "tick.lock"
        while not stop.is_set():
            now = self.deps.now()
            row = None
            with scheduler.tick_lock(lock) as got:
                if got and (self.coordinator is not None or self.sched.may_tick(now)):
                    with contextlib.suppress(Exception):
                        row = self.tick()
            with contextlib.suppress(Exception):
                self.housekeeping(self.deps.now())
            if (
                self.coordinator is None
                and isinstance(row, dict)
                and row.get("reason") == "idle"
            ):
                # No input and nothing playing: park until Sir is back.
                with contextlib.suppress(Exception):
                    st.update(next_tick=0.0, parked=True)
                if not self.park(stop):
                    return
                with contextlib.suppress(Exception):
                    st.update(parked=False)
                delay = scheduler.RESUME_S
            else:
                delay = self.next_delay()
            self.last_interval = delay
            if st.env_allows() and st.load().get("enabled"):
                st.update(next_tick=self.deps.now() + delay)
            if stop.wait(delay):
                return

    def park(self, stop: threading.Event) -> bool:
        """Sleep until input or media resumes (or Always Aware is switched
        off). False when the thread is stopping. Costs a file read per poll."""
        self.parked = True
        try:
            while not stop.wait(scheduler.PARK_POLL_S):
                if not (st.env_allows() and st.load().get("enabled")):
                    return True
                try:
                    back = self.deps.presence(self.deps.now()) == "active"
                except Exception:
                    back = False
                if back:
                    self.sched.resume()
                    alog.write({"outcome": "seen", "event": "resumed"}, self.deps.now())
                    return True
            return False
        finally:
            self.parked = False


def run_takeover_blocking(plan, speak_fn, offer_id: str = "", modules=None) -> None:
    """Run an accepted plan through P3's takeover.run, speak its one honest
    line (TakeoverResult.spoken) and tell offers.py how it ended."""
    modules = modules or _import
    takeover = modules("takeover")
    if takeover is None:
        return
    now = time.time()
    pending_lines: list[str] = []
    tasks: list = []

    def progress(frac, note):
        with contextlib.suppress(Exception):
            st.update(phase="takeover", phase_until=time.time() + 150)
        note = str(note or "")
        if note.startswith("paused:"):
            # P4: Sir touched the Jarvis tab; say the hand-over line once.
            line = note.removeprefix("paused:").strip()
            with contextlib.suppress(Exception):
                tasks.append(asyncio.get_running_loop().create_task(speak_fn(line)))
                return
            pending_lines.append(line)

    async def go():
        st.update(phase="takeover", phase_until=time.time() + 150)
        result = await takeover.run(plan, progress=progress)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for line in pending_lines:
            await speak_fn(line)
        line = getattr(result, "spoken", "") or ""
        if line:
            await speak_fn(line)
        return result

    try:
        result = asyncio.run(go())
        status = getattr(result, "status", "")
        status = str(getattr(status, "value", status))
        alog.write(
            {
                "outcome": "seen",
                "event": "takeover",
                "plan": getattr(plan, "id", ""),
                "status": status,
            },
            time.time(),
        )
        offers = modules("offers")
        if offers is not None and offer_id:
            with contextlib.suppress(Exception):
                offers.record_outcome(offer_id, status)
    except Exception as exc:
        alog.error(f"takeover:{type(exc).__name__}", now)
    finally:
        st.update(phase="idle", phase_until=0.0)


def recover_runs(modules=None) -> list[str]:
    """Sidecar start: P3 reverts runs a dead bridge left open. Returns the
    honest one-liners to speak (one per interrupted run)."""
    modules = modules or _import
    takeover = modules("takeover")
    fn = getattr(takeover, "recover", None) if takeover is not None else None
    if fn is None:
        return []
    try:
        return [
            getattr(r, "spoken", "") for r in fn() or [] if getattr(r, "spoken", "")
        ]
    except Exception:
        return []


def recover(modules=None) -> list:
    """Sidecar start: P4 returns any tabs a crashed run left marked."""
    modules = modules or _import
    brave = modules("brave_live")
    fn = getattr(brave, "recover", None) if brave is not None else None
    if fn is None:
        return []
    try:
        out = fn()
        if asyncio.iscoroutine(out):
            out = asyncio.run(out)
        return list(out or [])
    except Exception:
        return []


# ------------------------------------------------------------------ status


def status(now: float | None = None) -> dict:
    """HUD/phone payload: {state, next_tick_s, last_reason, calls_today, ...}."""
    now = time.time() if now is None else now
    s = st.load()
    word = st.status_word(s, now)
    phase = s.get("phase") or "idle"
    held = phase in ("capturing", "skipped", "offer", "takeover") and (
        phase == "takeover" or float(s.get("phase_until") or 0) > now
    )
    if word != "on":
        state = word
    elif held:
        state = phase
    else:
        state = "armed"
    from aware.coordinator import status as poll_status

    budget = ledger.summary(now)
    nxt = float(s.get("next_tick") or 0)
    return {
        "ok": True,
        "state": state,
        "enabled": bool(s.get("enabled")),
        "shadow": bool(s.get("shadow")),
        "next_tick_s": max(0, round(nxt - now)) if word == "on" and nxt else None,
        "parked": bool(s.get("parked")) and word == "on",
        "last_reason": s.get("last_reason") or "",
        "calls_today": budget["calls"],
        "tokens_today": budget["tokens"],
        "budget": budget["level"],
        "paused_until": float(s.get("paused_until") or 0) or None,
        "poll": poll_status(),
        "exercise_mode": "observation_only",
        "whatsapp_marks_read": True,
        "day": dt.date.fromtimestamp(now).isoformat(),
    }


_THREAD: list = []


def start_thread(first_delay: float = 60.0):
    """Bridge sidecar entry. The loop idles cheaply while disabled."""
    if _THREAD:
        return _THREAD[0]
    stop = threading.Event()
    runner = Runner()
    from aware.coordinator import live

    runner.coordinator = live(
        runner._screen_tick, speak=lambda line: asyncio.run(runner.deps.speak(line))
    )
    thread = threading.Thread(
        target=runner.loop, args=(stop, first_delay), name="aware", daemon=True
    )
    thread.start()
    _THREAD.append((thread, stop, runner))
    return thread, stop
