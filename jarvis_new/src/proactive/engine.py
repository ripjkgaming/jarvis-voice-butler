"""Proactive engine: sources -> one decision per signal -> say/toast/hud/drop.

IRONMAN_SPEC §1.1-1.2. Every ~30 s the engine polls the event sources in
proactive/sources/, drops signals it already delivered (by key), runs the
pure decide() policy on the rest, delivers, and logs each decision to
actions.log as ``proactive <kind> <decision> <reason>``.

decide() rules, first match wins:
1. critical -> say, always (safety beats quiet hours, DnD, focus and the cap).
2. low -> hud (never interrupts).
3. quiet hours / DnD / focus -> toast (+ queued for the next natural pause).
4. ignored the last 3 spoken signals of this kind -> toast (learned penalty).
5. daily speech cap reached -> toast.
6. high -> say.
7. normal -> say only when Sir is at a natural pause (no input for 30 s to
   10 min: at the desk but not typing); otherwise toast.

"Ignored": a spoken signal that Sir did not answer (no voice turn) within
ACK_WINDOW_S. note_user_turn() is called by the agent on every user turn.
State (delivered keys, today's count, streaks) lives in
~/.jarvis/proactive_engine.json. JARVIS_PROACTIVE_ENGINE=0 turns it off.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from proactive import policy as quiet_policy
from proactive.signals import Signal, rank

SWITCH_ENV = "JARVIS_PROACTIVE_ENGINE"
CAP_ENV = "JARVIS_PROACTIVE_SPEAK_CAP"
DEFAULT_CAP = 12
PAUSE_MIN_S = 30.0
PAUSE_MAX_S = 600.0
IGNORE_STREAK = 3
ACK_WINDOW_S = 120.0
KEYS_KEEP = 600


def _source_name(source) -> str:
    """'mail_source.poll' instead of a bare 'poll' (every source is one)."""
    module = str(getattr(source, "__module__", "") or "").rsplit(".", 1)[-1]
    name = str(getattr(source, "__name__", source))
    return f"{module}.{name}" if module else name


@dataclass
class Context:
    quiet: bool = False  # quiet hours (idle-gated, like notify/dnd)
    dnd: bool = False
    focus: bool = False
    idle_s: float | None = None  # seconds since last input, None = unknown
    spoken_today: int = 0
    cap: int = DEFAULT_CAP
    ignored_streak: int = 0  # for this signal's kind


@dataclass
class Decision:
    action: str  # "say" | "toast" | "hud" | "drop"
    reason: str


def decide(signal: Signal, ctx: Context) -> Decision:
    """The §1.2 policy. Pure."""
    r = rank(signal.urgency)
    if signal.urgency == "critical":
        return Decision("say", "critical")
    if r == 0:
        return Decision("hud", "low-urgency")
    if ctx.quiet:
        return Decision("toast", "quiet-hours")
    if ctx.dnd:
        return Decision("toast", "dnd")
    if ctx.focus:
        return Decision("toast", "focus-mode")
    if ctx.ignored_streak >= IGNORE_STREAK:
        return Decision("toast", f"ignored-{ctx.ignored_streak}x")
    if ctx.spoken_today >= ctx.cap:
        return Decision("toast", "daily-cap")
    if signal.urgency == "high":
        return Decision("say", "high")
    if ctx.idle_s is not None and PAUSE_MIN_S <= ctx.idle_s <= PAUSE_MAX_S:
        return Decision("say", "natural-pause")
    if ctx.idle_s is not None and ctx.idle_s > PAUSE_MAX_S:
        return Decision("toast", "away")
    return Decision("toast", "busy-typing")


# --- state ---


def _home() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return Path(h) if h else Path.home() / ".jarvis"


def state_path() -> Path:
    return _home() / "proactive_engine.json"


def load_state() -> dict:
    try:
        data = json.loads(state_path().read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def save_state(state: dict) -> None:
    with contextlib.suppress(OSError):
        state_path().parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, state_path())


def note_user_turn(now: float | None = None) -> None:
    """Sir spoke to Jarvis: recent spoken signals count as acknowledged."""
    now = time.time() if now is None else now
    state = load_state()
    state["last_user_ts"] = now
    save_state(state)


def settle_streaks(state: dict, now: float) -> None:
    """Resolve spoken signals older than ACK_WINDOW_S into ignored/answered."""
    last_user = float(state.get("last_user_ts") or 0)
    streaks = state.setdefault("streaks", {})
    keep = []
    for said in state.get("pending_acks") or []:
        ts, kind = float(said.get("ts") or 0), str(said.get("kind") or "")
        if ts <= last_user <= ts + ACK_WINDOW_S:
            streaks[kind] = 0
        elif now - ts > ACK_WINDOW_S:
            streaks[kind] = int(streaks.get(kind) or 0) + 1
        else:
            keep.append(said)
    state["pending_acks"] = keep


def speak_cap() -> int:
    try:
        return max(0, int(os.environ.get(CAP_ENV, DEFAULT_CAP)))
    except ValueError:
        return DEFAULT_CAP


def live_context(state: dict, kind: str, now: float) -> Context:
    """Build a Context from the real sensors. Every probe is fail-soft."""
    idle_s = None
    with contextlib.suppress(Exception):
        import input_idle

        idle_s = input_idle.idle_seconds(now=now)
    dnd_on = False
    with contextlib.suppress(Exception):
        import dnd

        dnd_on = bool(dnd.active(now))
    in_focus = False
    with contextlib.suppress(Exception):
        import focus

        in_focus = focus.load_state() is not None
    quiet = quiet_policy.in_quiet_hours(
        datetime.datetime.fromtimestamp(now), quiet_policy.quiet_hours()
    ) and (idle_s is None or idle_s >= 30 * 60)
    day = datetime.date.fromtimestamp(now).isoformat()
    spoken = int(state.get("spoken", {}).get(day) or 0)
    return Context(
        quiet=quiet,
        dnd=dnd_on,
        focus=in_focus,
        idle_s=idle_s,
        spoken_today=spoken,
        cap=speak_cap(),
        ignored_streak=int((state.get("streaks") or {}).get(kind) or 0),
    )


# --- delivery ---


def _say(signal: Signal) -> bool:
    import speak

    return bool(
        speak.speak(signal.title, source=f"proactive-{signal.kind}", title="Jarvis")
    )


def _toast(signal: Signal) -> bool:
    """Silent notification now, spoken later at the next natural pause."""
    import notify

    notify.send(
        signal.detail or signal.title,
        title=f"Jarvis - {signal.kind}",
        kind=f"proactive-{signal.kind}",
        source="proactive",
        allow_speech=False,
        fingerprint=f"proactive:{signal.key}",
    )
    return notify.enqueue(
        {
            "ts": signal.ts or time.time(),
            "kind": f"proactive-{signal.kind}",
            "source": "proactive",
            "title": f"Jarvis - {signal.kind}",
            "text": signal.detail or signal.title,
            "speak_text": signal.title,
            "urgency": signal.urgency,
        }
    )


def _hud(signal: Signal) -> bool:
    import activity

    activity.start(
        "note",
        signal.title[:80],
        detail=signal.detail[:120] or signal.kind,
        source="jarvis",
        item_id=f"proactive-{signal.key}"[:64],
    )
    activity.finish(f"proactive-{signal.key}"[:64], ok=True)
    return True


class ProactiveEngine:
    """Poll sources, decide, deliver. All I/O injectable for tests."""

    def __init__(
        self,
        sources: list[Callable[[float], list[Signal]]] | None = None,
        *,
        context_fn: Callable[[dict, str, float], Context] = live_context,
        say: Callable[[Signal], bool] = _say,
        toast: Callable[[Signal], bool] = _toast,
        hud: Callable[[Signal], bool] = _hud,
        log: Callable[[str, str], None] | None = None,
    ) -> None:
        if sources is None:
            from proactive.sources import default_sources

            sources = default_sources()
        self.sources = sources
        self.context_fn = context_fn
        self.deliver = {"say": say, "toast": toast, "hud": hud}
        if log is None:
            from system import log_action

            log = log_action
        self.log = log

    def poll(self, now: float) -> list[Signal]:
        out: list[Signal] = []
        for source in self.sources:
            try:
                out.extend(source(now) or [])
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self.log(
                        "proactive",
                        f"source-error {_source_name(source)}: {type(exc).__name__}: {str(exc)[:80]}",
                    )
        return out

    def run_once(self, now: float | None = None) -> list[tuple[Signal, Decision]]:
        """One pass. Returns what was decided. Never raises."""
        now = time.time() if now is None else now
        state = load_state()
        settle_streaks(state, now)
        delivered = dict(state.get("delivered") or {})
        day = datetime.date.fromtimestamp(now).isoformat()
        spoken = {day: int((state.get("spoken") or {}).get(day) or 0)}
        state["spoken"] = spoken
        results = []
        for signal in self.poll(now):
            if signal.key in delivered:
                continue
            ctx = self.context_fn(state, signal.kind, now)
            decision = decide(signal, ctx)
            ok = False
            if decision.action in self.deliver:
                try:
                    ok = bool(self.deliver[decision.action](signal))
                except Exception:
                    ok = False
                if decision.action == "say" and not ok:
                    # Speaker busy or gone: never lose it, fall back to a toast.
                    with contextlib.suppress(Exception):
                        ok = bool(self.deliver["toast"](signal))
                    decision = Decision("toast", f"{decision.reason}+speaker-failed")
            if decision.action == "say":
                spoken[day] += 1
                state.setdefault("pending_acks", []).append(
                    {"ts": now, "kind": signal.kind}
                )
            delivered[signal.key] = now
            with contextlib.suppress(Exception):
                self.log(
                    "proactive",
                    f"{signal.kind} {decision.action} {decision.reason} key={signal.key[:60]}",
                )
            results.append((signal, decision))
        state["delivered"] = dict(
            sorted(delivered.items(), key=lambda kv: kv[1])[-KEYS_KEEP:]
        )
        save_state(state)
        return results


def enabled() -> bool:
    return os.environ.get(SWITCH_ENV, "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def start_thread(interval: float = 30.0, first_delay: float = 45.0):
    """Run the engine inside the Jarvis service (bridge sidecar)."""
    import threading

    stop = threading.Event()

    def _loop() -> None:
        if stop.wait(first_delay):
            return
        engine = None
        while not stop.is_set():
            with contextlib.suppress(Exception):
                if enabled():
                    engine = engine or ProactiveEngine()
                    engine.run_once()
            if stop.wait(interval):
                return

    thread = threading.Thread(target=_loop, name="proactive-engine", daemon=True)
    thread.start()
    return thread, stop
