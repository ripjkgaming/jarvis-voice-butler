"""Call agent (W3): CallSession state machine for inbound and outbound calls.

Everything external is injected so tests run with fakes:
  llm(system, history) -> str          history = [(role, text)], role caller|jarvis|owner
  speaker(text, persona) -> None       TTS + play into the call (async or sync)
  driver                                W2 WhatsAppCallDriver (accept/decline/hangup/dial)
  bridge (optional)                     W2 call_bridge (flush() for instant stop)
  stores                                W1 modules: contacts, rules, calllog, memory_bridge
  settings(name, default) -> value      disclose_ai (default True), record_calls
                                        (default False), persona, max_call_s
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from calls import events, policy

log = logging.getLogger("calls.agent")

STATES = ("ringing", "handling", "hold", "owner", "ended")
DEFAULT_MAX_CALL_S = 600.0
MAX_TURNS_CONTEXT = 24


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def default_settings(name: str, default: Any = None) -> Any:
    if name == "disclose_ai":
        return _env_bool("JARVIS_CALL_DISCLOSE_AI", True)
    if name == "record_calls":
        return _env_bool("JARVIS_CALL_RECORD", False)
    if name == "persona":
        return os.environ.get("JARVIS_CALL_PERSONA", "") or default
    if name == "max_call_s":
        try:
            return float(os.environ.get("JARVIS_CALL_MAX_S", ""))
        except ValueError:
            return default
    return default


def load_stores() -> SimpleNamespace:
    """Import W1 modules lazily so this package imports before W1 lands."""
    import importlib

    ns = {}
    for n in ("contacts", "rules", "calllog", "memory_bridge"):
        try:
            ns[n] = importlib.import_module(f"calls.{n}")
        except Exception:
            log.warning("calls.%s unavailable", n)
            ns[n] = None
    return SimpleNamespace(**ns)


async def _maybe(res: Any) -> Any:
    if inspect.isawaitable(res):
        return await res
    return res


@dataclass
class Turn:
    speaker: str
    text: str


class CallSession:
    def __init__(
        self,
        *,
        direction: str,
        number: str,
        llm: Callable,
        speaker: Callable,
        driver: Any,
        stores: Any = None,
        bridge: Any = None,
        settings: Callable = default_settings,
        goal: str = "",
        owner_name: str = "the owner",
        clock: Callable[[], float] = time.time,
        calendar: Any = None,
    ) -> None:
        self.calendar = calendar  # optional calls.calendar_tools.CalendarTools
        self.intent_state: Any = None  # calls.intent.State, built on first use
        self.direction = direction
        self.number = number
        self.goal = goal
        self.llm = llm
        self.speaker = speaker
        self.driver = driver
        self.stores = stores if stores is not None else load_stores()
        self.bridge = bridge
        self.settings = settings
        self.owner_name = owner_name
        self.clock = clock

        self.state = "ringing"
        self.call_id: Any = None
        self.contact: Any = None
        self.decision: Any = None
        self.turns: list[Turn] = []
        self.messages: list[str] = []
        self.started_at = clock()
        self.ai_disclosed = False
        self.recording = False
        self.persona = "jarvis"
        self.outcome = ""
        self.summary = ""
        self.take_message_mode = False
        self._rule_message = ""
        self._recall = ""
        self._watchdog: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._ended = asyncio.Event()

    # ---------- helpers ----------
    def _store(self, name: str) -> Any:
        return getattr(self.stores, name, None)

    def _audit(self, kind: str, **detail: Any) -> None:
        cl = self._store("calllog")
        if cl is None:
            return
        try:
            cl.audit(kind, {"call_id": self.call_id, "number": self.number, **detail})
        except Exception:
            log.exception("audit failed")

    def _caller_info(self) -> dict | None:
        c = self.contact
        if c is None:
            return (
                {
                    "name": "Unknown",
                    "number": self.number,
                    "trust": "unknown",
                    "relationship": "",
                    "notes": "",
                }
                if self.number
                else None
            )
        return {
            "name": c.name,
            "number": c.number,
            "trust": c.trust,
            "relationship": c.relationship,
            "notes": c.notes,
        }

    def _emit_state(self, state: str | None = None) -> None:
        if state:
            self.state = state
        events.emit(
            events.state_event(
                self.call_id,
                self.state,
                self.direction,
                self._caller_info(),
                self.goal,
                self.started_at,
                "owner_voice" if self.persona == "owner" else "jarvis",
                self.ai_disclosed,
                self.recording,
            )
        )

    def _record_turn(self, speaker: str, text: str, partial: bool = False) -> None:
        events.emit(
            events.transcript_event(self.call_id, speaker, text, partial, self.clock())
        )
        if partial:
            return
        self.turns.append(Turn(speaker, text))
        cl = self._store("calllog")
        if cl is not None and self.call_id is not None:
            try:
                cl.add_turn(self.call_id, speaker, text)
            except Exception:
                log.exception("add_turn failed")

    async def _say(self, text: str, speaker: str = "jarvis") -> None:
        if self.state in ("ended", "owner"):
            return
        self._record_turn(speaker, text)
        events.emit(events.speaking_event(self.call_id, 0.6))
        try:
            await _maybe(self.speaker(text, self.persona))
        finally:
            events.emit(events.speaking_event(self.call_id, 0.0))

    async def _flush_audio(self) -> None:
        if self.bridge is not None:
            try:
                await _maybe(self.bridge.flush())
            except Exception:
                log.exception("flush failed")

    def _begin(self) -> None:
        self.persona = (
            (
                self.contact.policy.get("persona")
                if self.contact and self.contact.policy
                else None
            )
            or self.settings("persona", None)
            or "jarvis"
        )
        self.recording = bool(self.settings("record_calls", False))
        cl = self._store("calllog")
        if cl is not None:
            try:
                self.call_id = cl.start_call(
                    self.direction,
                    self.number,
                    getattr(self.contact, "id", None),
                    self.goal,
                )
            except Exception:
                log.exception("start_call failed")
        if self.call_id is None:
            self.call_id = f"mem-{int(self.started_at * 1000)}"
        mb = self._store("memory_bridge")
        if mb is not None and self.contact is not None:
            try:
                self._recall = mb.recall_for_call(self.contact) or ""
            except Exception:
                log.exception("recall failed")
        events.set_active(self)

    def _start_watchdog(self) -> None:
        limit = self.settings("max_call_s", None) or DEFAULT_MAX_CALL_S
        self._max_s = float(limit)
        self._watchdog = asyncio.ensure_future(self._watch(self._max_s))

    async def _watch(self, limit: float) -> None:
        try:
            await asyncio.sleep(limit)
        except asyncio.CancelledError:
            return
        if self.state != "ended":
            self._audit("max_duration", seconds=limit)
            if self.state != "owner":
                await self._say("I have to wrap up now. Goodbye.")
            await self.end("max_duration")

    async def _opening(self) -> None:
        parts = []
        if self.settings("disclose_ai", True):
            parts.append(policy.DISCLOSURE)
            self.ai_disclosed = True
        if self.recording:
            parts.append(policy.RECORDING_NOTICE)
        if self.direction == "out":
            parts.append(
                f"I'm calling about this: {self.goal}."
                if self.goal
                else "I'm calling on the owner's behalf."
            )
        else:
            parts.append("Who is calling, and what is it about?")
        self._emit_state()
        await self._say(" ".join(parts))

    # ---------- inbound ----------
    async def start_inbound(self) -> str:
        """Ringing call arrived. Returns the action taken."""
        self.direction = "in"
        c = self._store("contacts")
        if c is not None:
            try:
                self.contact = c.get_by_number(self.number)
            except Exception:
                log.exception("contact lookup failed")
        action, reason, rule_id = "owner", "no rules module", None
        rl = self._store("rules")
        if rl is not None:
            try:
                d = rl.decide(self.number, self.clock())
                self.decision = d
                action, reason = d.action, d.reason
                rule_id = getattr(d, "rule_id", None)
                self.contact = self.contact or getattr(d, "contact", None)
                self._rule_message = getattr(d, "message", "") or ""
                if not self._rule_message and rule_id is not None:
                    for r in rl.active_rules(self.clock()):
                        if r.id == rule_id:
                            self._rule_message = r.message or ""
            except Exception:
                log.exception("decide failed; defaulting to owner")
                action, reason = "owner", "decide error"
        self._begin()
        self._audit("decision", action=action, reason=reason, rule_id=rule_id)
        self._emit_state("ringing")

        if action == "reject":
            await _maybe(self.driver.decline())
            await self.end("rejected")
        elif action == "owner":
            events.emit(
                events.escalation_event(self.call_id, "incoming call needs owner")
            )
            # stay ringing: owner says accept / decline / take_message / takeover
        elif action == "message":
            await _maybe(self.driver.accept())
            self.take_message_mode = True
            self._start_watchdog()
            await self._opening_message()
        else:  # answer
            await _maybe(self.driver.accept())
            self._start_watchdog()
            self._emit_state("handling")
            await self._opening()
        return action

    async def _opening_message(self) -> None:
        self._emit_state("handling")
        text = self._rule_message or (
            f"{self.owner_name} can't take calls right now. "
            "Please leave your name and message after this."
        )
        if self.settings("disclose_ai", True):
            text = policy.DISCLOSURE + " " + text
            self.ai_disclosed = True
        if self.recording:
            text += " " + policy.RECORDING_NOTICE
        self._emit_state()
        await self._say(text)

    # ---------- outbound ----------
    async def start_outbound(self) -> None:
        self.direction = "out"
        c = self._store("contacts")
        if c is not None:
            try:
                self.contact = c.get_by_number(self.number)
            except Exception:
                log.exception("contact lookup failed")
        self._begin()
        self._audit("dial", goal=self.goal)
        self._emit_state("ringing")
        try:
            await _maybe(self.driver.dial(self.number))
        except Exception as exc:
            self._audit("dial_failed", error=str(exc))
            await self.end("failed")
            return
        self._start_watchdog()
        self._emit_state("handling")
        await self._opening()

    # ---------- conversation ----------
    async def on_caller_text(self, text: str, partial: bool = False) -> None:
        text = (text or "").strip()
        if not text or self.state == "ended":
            return
        if partial:
            events.emit(
                events.transcript_event(
                    self.call_id, "caller", text, True, self.clock()
                )
            )
            return
        self._record_turn("caller", text)
        if self.state in ("owner", "hold", "ringing"):
            return  # owner talks / paused / not answered: agent stays silent
        async with self._lock:
            if self.state != "handling":
                return
            if self.take_message_mode:
                self.messages.append(text)
            guard = policy.check_incoming(text)
            if not guard.allowed:
                if guard.reason.startswith("prompt injection"):
                    self._audit("prompt_injection_blocked", reason=guard.reason)
                else:
                    await self._escalate(guard.reason)
                await self._say(guard.replacement)
                return
            if self.calendar is not None and self.direction == "in":
                said = self._calendar_intent(text)
                if said is not None:
                    await self._say_checked(said)
                    return
            history = [(t.speaker, t.text) for t in self.turns[-MAX_TURNS_CONTEXT:]]
            system = policy.build_system_prompt(
                direction=self.direction,
                contact=self.contact,
                goal=self.goal,
                recall=self._recall,
                persona=self.persona,
                owner_name=self.owner_name,
                record=self.recording,
                disclose_ai=self.ai_disclosed,
                rule_message=self._rule_message,
                max_minutes=getattr(self, "_max_s", DEFAULT_MAX_CALL_S) / 60,
            )
            if self.calendar is not None:
                from calls import calendar_hook

                calendar_hook.bind(self.calendar, self)
                system += "\n" + calendar_hook.prompt_lines(self.calendar)
            try:
                reply = await _maybe(self.llm(system, history))
            except Exception:
                log.exception("llm failed")
                await self._escalate("llm failure")
                await self._say(
                    "Sorry, I'm having trouble. I'll pass this to the owner."
                )
                return
            if self.state != "handling":  # takeover/hangup while thinking
                return
            await self._handle_reply(str(reply or ""))

    def _calendar_intent(self, text: str) -> str | None:
        """Code-level booking/check/remind/cancel intent (see calls.intent)."""
        from calls import calendar_hook, intent

        calendar_hook.bind(self.calendar, self)
        if self.intent_state is None:
            self.intent_state = intent.State()
        return calendar_hook.handle_caller_text(
            self.calendar, self.intent_state, text, self.owner_name
        )

    async def _say_checked(self, text: str) -> None:
        """Say code-generated text, still through the outgoing guard."""
        g = policy.check_outgoing(text)
        if not g.allowed:
            await self._escalate(g.reason)
            text = g.replacement
        await self._say(text)

    async def _handle_reply(self, reply: str) -> None:
        if self.calendar is not None:
            from calls import calendar_hook

            calendar_hook.bind(self.calendar, self)
            reply, results = calendar_hook.apply_tokens(
                self.calendar, reply, self.owner_name
            )
            if calendar_hook.false_confirmation(reply, bool(results)):
                await self._escalate("model claimed a booking without a tool")
                reply = calendar_hook.FALSE_CONFIRM_FIX
            elif results:
                reply = " ".join([reply, *results]).strip()
        handover = "HANDOVER" in reply
        escalate = "ESCALATE" in reply
        end = bool(re.search(r"\bEND\b", reply))
        clean = reply
        for tok in ("HANDOVER", "ESCALATE", "END"):
            clean = clean.replace(tok, "")
        clean = " ".join(clean.split())
        if escalate:
            await self._escalate("model flagged request needing owner")
        g = policy.check_outgoing(clean)
        if not g.allowed:
            await self._escalate(g.reason)
            clean = g.replacement
            handover = False
        if handover:
            await self._escalate("caller asked for the owner")
            clean = clean or f"One moment, I'll let {self.owner_name} know."
        if clean:
            await self._say(clean)
        if end and not handover:
            await self.end("completed")

    async def _escalate(self, reason: str) -> None:
        self._audit("escalation", reason=reason)
        events.emit(events.escalation_event(self.call_id, reason))

    # ---------- commands ----------
    async def handle_command(self, name: str) -> dict:
        self._audit("command", name=name, state=self.state)
        if self.state == "ended":
            return {"ok": False, "detail": "call ended"}
        if name == "takeover":
            await self._flush_audio()  # instant: before any state work
            self._emit_state("owner")
            return {"ok": True, "detail": "owner has the call"}
        if name == "hold":
            await self._flush_audio()
            self._emit_state("hold")
            return {"ok": True, "detail": "on hold"}
        if name == "resume":
            if self.state not in ("hold", "owner"):
                return {"ok": False, "detail": f"cannot resume from {self.state}"}
            self._emit_state("handling")
            return {"ok": True, "detail": "resumed"}
        if name == "hangup":
            await self._flush_audio()
            await _maybe(self.driver.hangup())
            await self.end("hangup")
            return {"ok": True, "detail": "hung up"}
        if name == "accept":
            if self.state != "ringing" or self.direction != "in":
                return {"ok": False, "detail": "nothing to accept"}
            await _maybe(self.driver.accept())
            self._start_watchdog()
            self._emit_state("handling")
            await self._opening()
            return {"ok": True, "detail": "accepted"}
        if name == "decline":
            if self.state != "ringing":
                return {"ok": False, "detail": "nothing to decline"}
            await _maybe(self.driver.decline())
            await self.end("declined")
            return {"ok": True, "detail": "declined"}
        if name == "take_message":
            if self.state == "ringing":
                await _maybe(self.driver.accept())
                self._start_watchdog()
            self.take_message_mode = True
            await self._opening_message()
            return {"ok": True, "detail": "taking a message"}
        return {"ok": False, "detail": f"unsupported {name}"}

    # ---------- end ----------
    async def end(self, outcome: str) -> None:
        if self.state == "ended":
            return
        prev = self.state
        self.state = "ended"
        self.outcome = outcome
        if self._watchdog is not None and self._watchdog is not asyncio.current_task():
            self._watchdog.cancel()
        await self._flush_audio()
        if outcome not in ("hangup", "rejected", "declined"):
            try:
                if await _maybe(self.driver.is_in_call()):
                    await _maybe(self.driver.hangup())
            except Exception:
                log.exception("hangup on end failed")
        self.summary = await self._summarise(prev)
        cl = self._store("calllog")
        if cl is not None and self.call_id is not None:
            try:
                cl.end_call(self.call_id, outcome, self.summary)
            except Exception:
                log.exception("end_call failed")
        mb = self._store("memory_bridge")
        if mb is not None and self.contact is not None and self.summary:
            try:
                mb.remember_person(self.contact.id, f"Call ({outcome}): {self.summary}")
            except Exception:
                log.exception("remember failed")
        self._emit_state("ended")
        events.emit(events.summary_event(self.call_id, self.summary, outcome))
        self._audit("ended", outcome=outcome)
        if events.get_active() is self:
            events.set_active(None)
        self._ended.set()

    async def _summarise(self, prev_state: str) -> str:
        if not self.turns:
            return f"No conversation ({self.outcome})."
        history = [(t.speaker, t.text) for t in self.turns[-MAX_TURNS_CONTEXT:]]
        system = (
            "Summarise this phone call in two short sentences: who, what "
            "they wanted, any message or follow-up for the owner. No "
            "private data beyond what was said."
        )
        try:
            s = await _maybe(self.llm(system, history))
            s = " ".join(str(s).split())
            if s:
                return s
        except Exception:
            log.exception("summary llm failed")
        caller = [t.text for t in self.turns if t.speaker == "caller"]
        return (
            "Caller said: " + " / ".join(caller)[:400]
            if caller
            else "No caller speech."
        )

    async def wait_ended(self) -> None:
        await self._ended.wait()
