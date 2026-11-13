"""Voice tools and intents for phone-call handling.

``CallTools().tools`` follows the same pattern as ``system.schedule_tools``:
register with ``*self.call_tools.tools``. W1 stores (``calls.contacts``,
``calls.rules``, ``calls.calllog``) and the W3 event handler are imported lazily
so this module loads without them.

Dialing is injected with ``set_dialer(fn)``; ``fn(job: dict) -> receipt`` where
job has ``contact_name``, ``number``, ``goal`` and receipt is ``True`` or
``{"status": "queued|started|blocked|failed", "reason": str}``. It may be
async.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import inspect
import re
import time
from collections.abc import Callable

from calls import rules_parser, scheduler
from jarvis_rt import RunContext, ToolError, function_tool
from scheduled_tasks import parse_when

CONTROL_COMMANDS = (
    "takeover",
    "hold",
    "resume",
    "hangup",
    "accept",
    "decline",
    "take_message",
    "stop_all",
)
_ALIASES = {
    "take_over": "takeover",
    "hang_up": "hangup",
    "end_call": "hangup",
    "pick_up": "accept",
    "answer": "accept",
    "reject": "decline",
    "take_a_message": "take_message",
    "stop": "stop_all",
}
TRUSTS = ("family", "work", "unknown", "blocked")

_dialer: Callable | None = None


def set_dialer(fn: Callable | None) -> None:
    global _dialer
    _dialer = fn


def _guard():
    """Owner-only: no guests, local or authenticated private session."""
    import phone_session

    if phone_session.guest_required():
        raise ToolError("Call handling is available only to the authenticated owner.")
    if phone_session.private():
        if not phone_session.identity():
            raise ToolError("The private owner session has ended.")
    else:
        from system import LocalSystemError, require_local

        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc


def _mods():
    try:
        from calls import calllog, contacts, rules
    except ImportError as exc:  # W1 not installed
        raise ToolError("The call store is unavailable.") from exc
    return contacts, rules, calllog


def _audit(kind, detail):
    with contextlib.suppress(Exception):
        _mods()[2].audit(kind, detail)


def _resolve(contacts, name):
    name = " ".join(str(name or "").split())
    if not name:
        raise ToolError("Which contact?")
    found = contacts.find(name)
    exact = [c for c in found if c.name.lower() == name.lower()]
    if exact:
        found = exact
    if not found:
        raise ToolError(f"No contact named {name}.")
    if len(found) > 1:
        names = ", ".join(f"{c.name} ({c.relationship or c.trust})" for c in found[:5])
        raise ToolError(f"More than one match: {names}. Which one?")
    return found[0]


def _fmt_time(ts):
    return dt.datetime.fromtimestamp(ts, scheduler.timezone()).strftime(
        "%a %d %b %H:%M %Z"
    )


def _describe_rule(r):
    if r.daily_from:
        when = f"daily {r.daily_from}-{r.daily_to}"
    elif r.end:
        when = f"until {_fmt_time(r.end)}"
    else:
        when = "until stopped"
    return f"#{r.id} {r.kind.replace('_', ' ')} {when}" + (
        f', saying "{r.message}"' if r.message else ""
    )


def _describe_job(j):
    return (
        f"{j['id']}: call {j['contact_name']} at {_fmt_time(j['due'])}"
        + (f" ({j['goal']})" if j["goal"] else "")
        + f", {j['status']}"
    )


def _err(exc):
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, (scheduler.ScheduleError, rules_parser.ParseError, ValueError)):
        return ToolError(str(exc))
    return ToolError("That call request could not be completed.")


async def _dial(job):
    if _dialer is None:
        return {"status": "failed", "reason": "No dialer configured"}
    res = _dialer(job)
    if inspect.isawaitable(res):
        res = await res
    return res


def dial_sync(job):
    """Sync adapter for the scheduler thread (runs async dialers on a fresh loop)."""
    if _dialer is None:
        return {"status": "failed", "reason": "No dialer configured"}
    res = _dialer(job)
    if inspect.isawaitable(res):
        res = asyncio.run(res)
    return res


# ------------------------------------------------------------ utterances

_NUMBER_IS = re.compile(
    r"^(?P<name>.+?)(?:'s|s'| his| her| their)?\s+(?:phone |mobile |cell )?number\s+is\s+(?P<num>\+?[\d][\d\s\-().]{5,})$",
    re.I,
)
_MARK = re.compile(
    r"^(?:mark|set|flag|tag)\s+(?P<name>.+?)\s+as\s+(?:a\s+)?(?P<trust>family|work|unknown|blocked)$",
    re.I,
)


def parse_contact_phrase(text):
    """Returns ('number', name, number) / ('mark', name, trust) / None."""
    t = " ".join(str(text or "").replace("\u2019", "'").split()).strip(" .")
    if m := _NUMBER_IS.match(t):
        return ("number", m["name"].strip(" ,"), m["num"].strip())
    if m := _MARK.match(t):
        return ("mark", m["name"].strip(" ,"), m["trust"].lower())
    return None


class CallTools:
    @property
    def tools(self):
        return [
            self.call_contact,
            self.schedule_call,
            self.set_auto_answer,
            self.cancel_auto_answer,
            self.list_call_rules,
            self.list_scheduled_calls,
            self.cancel_scheduled_call,
            self.add_contact,
            self.update_contact,
            self.find_contact,
            self.delete_contact,
            self.call_control,
        ]

    # ------------------------------------------------------------ outbound

    @function_tool()
    async def call_contact(
        self, context: RunContext, name: str, goal: str = ""
    ) -> dict[str, str]:
        """Phone a saved contact now on Sir's behalf (outbound call by Jarvis).

        Use for 'call Mum and ask about dinner'. Never invent a goal; ask if
        Sir gave none and the purpose is unclear. Do not use for 'call me'.

        Args:
            name: Contact name as spoken.
            goal: What the call should achieve, in Sir's words.
        """
        _guard()
        try:
            contacts, _, _ = _mods()
            c = await asyncio.to_thread(_resolve, contacts, name)
            if c.trust == "blocked":
                raise ToolError(f"{c.name} is blocked. Unblock them first.")
            job = {
                "contact_id": c.id,
                "contact_name": c.name,
                "number": c.number,
                "goal": " ".join(goal.split()),
            }
            receipt = await _dial(job)
        except Exception as exc:
            raise _err(exc) from exc
        if receipt is True:
            receipt = {"status": "queued"}
        status = (receipt or {}).get("status", "failed")
        _audit("call_requested", {**job, "status": status})
        if status in ("queued", "started"):
            return {"say": f"Calling {c.name} now.", "status": status}
        reason = (receipt or {}).get("reason") or "the call could not start"
        raise ToolError(f"Could not call {c.name}: {reason}.")

    @function_tool()
    async def schedule_call(
        self, context: RunContext, contact: str, when: str, goal: str = ""
    ) -> dict[str, str]:
        """Schedule Jarvis to phone a saved contact later.

        Args:
            contact: Contact name.
            when: 6pm, tomorrow at 9am, in 20 minutes, or YYYY-MM-DD HH:MM.
            goal: What the call should achieve.
        """
        _guard()
        try:
            import action_guard

            contacts, _, _ = _mods()
            due = parse_when(when)
            c = await asyncio.to_thread(_resolve, contacts, contact)
            if c.trust == "blocked":
                raise ToolError(f"{c.name} is blocked.")
            rid = action_guard.request_id(context)
            if rid:
                rid = f"{rid}|{when}"
            job = await asyncio.to_thread(_store_create, c, goal, due, rid)
        except Exception as exc:
            raise _err(exc) from exc
        _audit("call_scheduled", {"id": job["id"], "contact": c.name, "due": due})
        return {
            "say": f"Scheduled {_describe_job(job)}.",
            "job_id": job["id"],
            "status": job["status"],
        }

    @function_tool()
    async def list_scheduled_calls(self, context: RunContext) -> dict[str, str]:
        """List pending and recent scheduled outbound calls."""
        _guard()
        try:
            rows = await asyncio.to_thread(lambda: scheduler.Store().list())
        except Exception as exc:
            raise _err(exc) from exc
        return {
            "say": "; ".join(_describe_job(j) for j in rows)[:3500]
            if rows
            else "No scheduled calls."
        }

    @function_tool()
    async def cancel_scheduled_call(
        self, context: RunContext, job_id: str = "", contact: str = ""
    ) -> dict[str, str]:
        """Cancel a scheduled call by exact ID, or all pending ones for a contact.

        Args:
            job_id: Exact call- ID from list_scheduled_calls.
            contact: Contact name, cancels all their pending scheduled calls.
        """
        _guard()
        if not job_id and not contact:
            raise ToolError("Give the call ID or the contact name.")
        try:
            st = scheduler.Store()
            if job_id:
                n = int(await asyncio.to_thread(st.cancel, job_id))
            else:
                n = await asyncio.to_thread(st.cancel_for_contact, contact)
        except Exception as exc:
            raise _err(exc) from exc
        _audit("call_schedule_cancel", {"job_id": job_id, "contact": contact, "n": n})
        return {
            "say": f"Cancelled {n} scheduled call{'s' if n != 1 else ''}."
            if n
            else "Nothing pending to cancel."
        }

    # ------------------------------------------------------------ rules

    @function_tool()
    async def set_auto_answer(
        self, context: RunContext, request: str
    ) -> dict[str, str]:
        """Set an auto-answer window for incoming calls from Sir's own words.

        Use for 'for the next 3 hours answer all calls and say I'm in a
        meeting' or 'between 9am and 5pm handle calls from unknown numbers'.
        Pass Sir's request unchanged. Read the returned summary back.

        Args:
            request: Sir's request in natural language.
        """
        _guard()
        try:
            _, rules, _ = _mods()
            parsed = rules_parser.parse(request)
            if parsed is None or parsed.intent != "auto_answer":
                raise ToolError(
                    "I could not read that as an auto-answer rule. Say for example: answer all calls for the next 2 hours."
                )
            rule = await asyncio.to_thread(
                lambda: rules.add_rule(**parsed.rule.as_kwargs(created_by="voice"))
            )
        except Exception as exc:
            raise _err(exc) from exc
        _audit("rule_set_voice", {"rule_id": rule.id, "request": request[:200]})
        return {"say": f"Done. {parsed.summary}.", "rule_id": str(rule.id)}

    @function_tool()
    async def cancel_auto_answer(self, context: RunContext) -> dict[str, str]:
        """Stop all auto-answer rules now ('stop auto answer', 'cancel auto answer')."""
        _guard()
        try:
            _, rules, _ = _mods()
            n = await asyncio.to_thread(rules.cancel_rule, None)
        except Exception as exc:
            raise _err(exc) from exc
        _audit("rule_cancel_voice", {"cancelled": n})
        return {
            "say": f"Cancelled {n} auto-answer rule{'s' if n != 1 else ''}."
            if n
            else "No auto-answer rules were active."
        }

    @function_tool()
    async def list_call_rules(self, context: RunContext) -> dict[str, str]:
        """List the auto-answer rules that are active now."""
        _guard()
        try:
            _, rules, _ = _mods()
            rs = await asyncio.to_thread(rules.active_rules, time.time())
        except Exception as exc:
            raise _err(exc) from exc
        return {
            "say": "; ".join(_describe_rule(r) for r in rs)
            if rs
            else "No auto-answer rules are active."
        }

    # ------------------------------------------------------------ contacts

    @function_tool()
    async def add_contact(
        self,
        context: RunContext,
        name: str,
        number: str,
        relationship: str = "",
        trust: str = "unknown",
        notes: str = "",
    ) -> dict[str, str]:
        """Save a contact, or update the number if that exact name exists.

        Use for "Mum's number is 9123 4567". Read the number back.

        Args:
            name: Contact name.
            number: Phone number (Singapore numbers need no country code).
            relationship: e.g. mother, dentist.
            trust: family, work, unknown or blocked.
            notes: Standing notes about the person.
        """
        _guard()
        return await _upsert(name, number, relationship, trust, notes)

    @function_tool()
    async def update_contact(
        self,
        context: RunContext,
        name: str,
        number: str = "",
        relationship: str = "",
        trust: str = "",
        notes: str = "",
        new_name: str = "",
    ) -> dict[str, str]:
        """Change a saved contact's fields; only given fields change.

        Use for "mark Dr Tan as work" (trust=work).

        Args:
            name: Existing contact name.
            number: New number.
            relationship: New relationship.
            trust: family, work, unknown or blocked.
            notes: Replacement notes.
            new_name: Rename.
        """
        _guard()
        return await _update(name, number, relationship, trust, notes, new_name)

    @function_tool()
    async def find_contact(self, context: RunContext, name: str) -> dict[str, str]:
        """Look up saved contacts by name.

        Args:
            name: Full or partial name.
        """
        _guard()
        try:
            contacts, _, _ = _mods()
            found = await asyncio.to_thread(contacts.find, name)
        except Exception as exc:
            raise _err(exc) from exc
        if not found:
            return {"say": f"No contact matches {name}."}
        return {
            "say": "; ".join(
                f"{c.name}: {c.number}, {c.relationship or 'no relationship'}, {c.trust}"
                for c in found[:8]
            )
        }

    @function_tool()
    async def delete_contact(
        self, context: RunContext, name: str, confirmed: bool = False
    ) -> dict[str, str]:
        """Delete a saved contact. Ask Sir to confirm first, then pass confirmed=true.

        Args:
            name: Exact contact name.
            confirmed: True only after Sir said yes to this specific deletion.
        """
        _guard()
        try:
            contacts, _, _ = _mods()
            c = await asyncio.to_thread(_resolve, contacts, name)
            if not confirmed:
                return {"say": f"Delete {c.name} ({c.number})? Say yes to confirm."}
            await asyncio.to_thread(contacts.delete_contact, c.id)
        except Exception as exc:
            raise _err(exc) from exc
        _audit("contact_delete_voice", {"id": c.id, "name": c.name})
        return {"say": f"Deleted {c.name}."}

    # ------------------------------------------------------------ live call

    @function_tool()
    async def call_control(self, context: RunContext, command: str) -> dict[str, str]:
        """Control the live phone call.

        Args:
            command: takeover (Sir speaks), hold, resume, hangup, accept,
                decline, take_message, or stop_all (cancel rules and hang up).
        """
        _guard()
        cmd = " ".join(str(command or "").lower().split()).replace(" ", "_")
        cmd = _ALIASES.get(cmd, cmd)
        if cmd not in CONTROL_COMMANDS:
            raise ToolError("Choose one of: " + ", ".join(CONTROL_COMMANDS) + ".")
        try:
            from calls import events
        except ImportError as exc:
            raise ToolError("Call control is unavailable.") from exc
        res = await events.handle_command(cmd)
        if not res.get("ok"):
            raise ToolError(str(res.get("detail") or "That command did not work."))
        _audit("voice_command", {"name": cmd})
        return {"say": str(res.get("detail") or "Done."), "command": cmd}


# ---------------------------------------------------------------- helpers


def _store_create(c, goal, due, rid):
    return scheduler.Store().create(
        c.name, c.number, goal, due, contact_id=c.id, request_id=rid
    )


async def _upsert(name, number, relationship, trust, notes):
    try:
        contacts, _, _ = _mods()
        trust = (trust or "unknown").lower()
        if trust not in TRUSTS:
            raise ToolError("Trust must be family, work, unknown or blocked.")
        existing = [
            c
            for c in await asyncio.to_thread(contacts.find, name)
            if c.name.lower() == " ".join(name.split()).lower()
        ]
        if existing:
            kw = {"number": number}
            if relationship:
                kw["relationship"] = relationship
            if notes:
                kw["notes"] = notes
            if trust != "unknown":
                kw["trust"] = trust
            c = await asyncio.to_thread(
                lambda: contacts.update_contact(existing[0].id, **kw)
            )
            verb = "Updated"
        else:
            c = await asyncio.to_thread(
                lambda: contacts.add_contact(
                    name, number, relationship=relationship, notes=notes, trust=trust
                )
            )
            verb = "Saved"
    except Exception as exc:
        raise _err(exc) from exc
    return {"say": f"{verb} {c.name}: {c.number}.", "contact_id": str(c.id)}


async def _update(name, number, relationship, trust, notes, new_name):
    kw = {
        k: v
        for k, v in (
            ("number", number),
            ("relationship", relationship),
            ("trust", (trust or "").lower()),
            ("notes", notes),
            ("name", new_name),
        )
        if v
    }
    if not kw:
        raise ToolError(
            "Nothing to change. Give a number, trust, relationship or notes."
        )
    if "trust" in kw and kw["trust"] not in TRUSTS:
        raise ToolError("Trust must be family, work, unknown or blocked.")
    try:
        contacts, _, _ = _mods()
        c = await asyncio.to_thread(_resolve, contacts, name)
        c = await asyncio.to_thread(lambda: contacts.update_contact(c.id, **kw))
    except Exception as exc:
        raise _err(exc) from exc
    return {"say": f"Updated {c.name}: {c.number}, {c.trust}.", "contact_id": str(c.id)}


async def handle_utterance(text):
    """Deterministic intent shortcut for contact phrasing. None if not matched.

    "Mum's number is 9123 4567" -> upsert; "mark Dr Tan as work" -> trust update.
    """
    _guard()
    p = parse_contact_phrase(text)
    if p is None:
        return None
    kind, name, val = p
    if kind == "number":
        return await _upsert(name, val, "", "unknown", "")
    return await _update(name, "", "", val, "", "")


__all__ = [
    "CONTROL_COMMANDS",
    "CallTools",
    "dial_sync",
    "handle_utterance",
    "parse_contact_phrase",
    "set_dialer",
]
