"""Always Aware shared types (owned by P2, imported by everyone).

Contract: docs/always-aware/COORD.md. The first fields of each dataclass are
exactly the contract; everything after them has a default so callers that
only know the contract still construct them. All types round-trip through
``to_dict`` / ``from_dict`` (plain JSON) so the sidecar, the voice agent and
the takeover runner can pass them through files.

Kinds:
  * ``Opportunity.kind`` is the coarse executor family: code | file | web | other.
  * ``Opportunity.task_kind`` is the fine vision kind (``vision.KINDS``):
    code_fix, test_fix, web_form, web_research, web_lookup, file_organise,
    file_convert, doc_draft, download.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from enum import Enum


class OppKind(str, Enum):
    CODE = "code"
    FILE = "file"
    WEB = "web"
    OTHER = "other"


# fine vision kind -> coarse executor family
TASK_FAMILY: dict[str, str] = {
    "code_fix": "code",
    "test_fix": "code",
    "web_form": "web",
    "web_research": "other",  # agent_tasks research (doc deliverable), not live Brave
    "web_lookup": "web",
    "file_organise": "file",
    "file_convert": "file",
    "doc_draft": "other",
    "download": "file",
    "worksheet_help": "assist",  # spoken hint only, never a takeover
}


class Stage(str, Enum):
    """Gate stage names used in ``GateResult.stage_failed`` and log lines."""

    SELF_CHECK = "self_check"  # stage 1: model self-feasibility
    CHECKLIST = "checklist"  # stage 2: deterministic per-kind checklist
    VERIFIER = "verifier"  # stage 3: adversarial second model
    DRY_RUN = "dry_run"  # stage 4: read-only first step


class TakeoverStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    STUCK = "stuck"
    CANCELLED = "cancelled"


class OfferAnswer(str, Enum):
    YES = "yes"
    NO = "no"
    SILENT = "silent"
    NEVER = "never"


class OfferStatus(str, Enum):
    ACCEPTED = "accepted"
    DECLINED = "declined"
    DEFERRED = "deferred"
    SUPPRESSED = "suppressed"


class ActionKind(str, Enum):
    """Consequential actions (same vocabulary as transactions.KINDS)."""

    SEND = "send"
    SUBMIT = "submit"
    PAYMENT = "payment"


_VOLATILE = frozenset(
    {"tick_id", "context", "ts", "idle_s", "self_feasibility", "est_minutes"}
)


def _filter(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in names}


@dataclass
class Consequential:
    """A send/submit/payment the run is allowed to do, fixed before the offer.

    The takeover may not exceed or change any of it (PLAN.md §4.4).
    ``amount`` is a decimal string in major units ("94.99")."""

    kind: str  # ActionKind value
    site: str = ""  # host
    recipient: str = ""  # send: who
    content: str = ""  # send/submit: exact text or gist
    merchant: str = ""  # payment
    amount: str = ""  # payment: exact total, major units
    currency: str = ""  # payment: ISO code, e.g. "GBP"
    items: list[str] = field(default_factory=list)
    payment_method: str = ""  # saved method label, e.g. "saved Visa ending 4242"

    @classmethod
    def from_dict(cls, data: dict) -> Consequential:
        return cls(**_filter(cls, data))


@dataclass
class Opportunity:
    # --- contract ---
    id: str
    kind: str  # OppKind value
    summary: str
    evidence: dict
    est_minutes: float
    # --- extras (all optional) ---
    task_kind: str = ""  # vision kind, e.g. "test_fix"
    title: str = ""
    subject: str = ""  # project / site / document from the tick
    outcome: str = ""
    success_check: str = ""
    self_feasibility: float = 0.0
    needs: dict = field(default_factory=dict)  # tools/data/credentials/user_decisions
    blockers: list[str] = field(default_factory=list)
    actions: list[Consequential] = field(default_factory=list)
    tick_id: str = ""

    def fingerprint(self) -> str:
        """kind + subject + evidence hash (PLAN.md §3.2 dedupe key)."""
        e = self.evidence or {}
        text = e.get("text") if isinstance(e.get("text"), str) else e.get("evidence")
        if isinstance(text, str) and text.strip():
            ev = " ".join(text.lower().split())
        else:  # structured only: hash it minus per-tick volatile keys
            stable = {k: v for k, v in e.items() if k not in _VOLATILE}
            ev = json.dumps(stable, sort_keys=True, default=str)
        kind = self.task_kind or str(e.get("vision_kind") or "") or self.kind
        subject = (self.subject or str(e.get("subject") or "")).strip().lower()
        raw = f"{kind}|{subject}|{ev}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Opportunity:
        data = dict(data or {})
        data["actions"] = [
            a if isinstance(a, Consequential) else Consequential.from_dict(a)
            for a in data.get("actions") or []
        ]
        return cls(**_filter(cls, data))

    @classmethod
    def from_vision(
        cls, raw: dict, *, subject: str = "", tick_id: str = "", idx: int = 0
    ) -> Opportunity:
        """Build from one ``vision.parse()`` opportunity dict.

        Vision's ``evidence`` is a string; it lands in ``evidence["text"]``.
        Extra structured evidence (repo, tab_id, paths, actions, ...) may be
        merged in later by the sidecar before calling ``gate.evaluate``."""
        task_kind = str(raw.get("kind") or "")
        ev = raw.get("evidence")
        evidence = dict(ev) if isinstance(ev, dict) else {"text": str(ev or "")}
        actions = [
            Consequential.from_dict(a)
            for a in (raw.get("actions") or evidence.get("actions") or [])
            if isinstance(a, dict)
        ]
        base = f"{tick_id}|{idx}|{task_kind}|{raw.get('title', '')}"
        return cls(
            id=hashlib.sha256(base.encode()).hexdigest()[:12],
            kind=TASK_FAMILY.get(task_kind, "other"),
            summary=str(raw.get("title") or raw.get("outcome") or "")[:200],
            evidence=evidence,
            est_minutes=float(raw.get("est_minutes") or 0),
            task_kind=task_kind,
            title=str(raw.get("title") or ""),
            subject=subject,
            outcome=str(raw.get("outcome") or ""),
            success_check=str(raw.get("success_check") or ""),
            self_feasibility=float(raw.get("self_feasibility") or 0),
            needs=dict(raw.get("needs") or {}),
            blockers=list(raw.get("blockers") or []),
            actions=actions,
            tick_id=tick_id,
        )


@dataclass
class PlanStep:
    step: str
    tool: str


@dataclass
class TakeoverPlan:
    """Produced by gate.evaluate only when all four stages pass."""

    id: str
    opp_id: str
    kind: str  # OppKind value: dispatch key for takeover.run
    task_kind: str  # vision kind
    title: str
    steps: list[PlanStep]
    success_criteria: list[str]  # machine-verifiable, in plain words
    time_budget_s: float  # wall-time budget (watchdog uses 1.5x est, floor 5 min)
    est_minutes: float = 0.0
    # resolved targets from stage 2, by kind:
    #   code: {"repo": "/abs", "test_command": [...], "files": [...]}
    #   web:  {"tab_id": "...", "url": "...", "host": "...", "success_predicate": {...}}
    #   file: {"paths": [...], "dest": "/abs"}
    #   other:{"deliverable_dir": "/abs", "min_words": N}
    targets: dict = field(default_factory=dict)
    actions: list[Consequential] = field(default_factory=list)  # exact sends/pays
    offer_text: str = ""  # what Sir said yes to (filled by offers)
    max_steps: int = 0  # bound from the checklist (browser actions, files, ...)
    dry_run: dict = field(
        default_factory=dict
    )  # stage 4 evidence (diff stat, plan.json path)
    created_ts: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TakeoverPlan:
        data = dict(data or {})
        data["steps"] = [
            s if isinstance(s, PlanStep) else PlanStep(**_filter(PlanStep, s))
            for s in data.get("steps") or []
        ]
        data["actions"] = [
            a if isinstance(a, Consequential) else Consequential.from_dict(a)
            for a in data.get("actions") or []
        ]
        return cls(**_filter(cls, data))


@dataclass
class GateResult:
    # --- contract ---
    passed: bool
    stage_failed: str | None  # Stage value
    reasons: list[str]
    plan: TakeoverPlan | None = None  # only when passed
    # --- extras ---
    opp_id: str = ""
    inventory: dict = field(default_factory=dict)  # stage 2 tools/targets

    def log_tag(self) -> str:
        """``pass`` or ``fail:<stage>:<reason>`` for the tick line."""
        if self.passed:
            return "pass"
        reason = self.reasons[0] if self.reasons else "unknown"
        return f"fail:{self.stage_failed}:{reason}"[:160]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TakeoverResult:
    run_id: str
    plan_id: str
    status: str  # TakeoverStatus value; only "verified" may be reported as success
    evidence: str = ""  # verifier evidence, e.g. "14 of 14 tests pass"
    spoken: str = ""  # one honest line for Sir
    reverted: bool = False
    left_alone: list[str] = field(default_factory=list)  # files Sir edited meanwhile
    reason: str = ""  # stuck/failed reason
    journal: str = ""  # path to the run journal
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TakeoverResult:
        return cls(**_filter(cls, data))


@dataclass
class OfferOutcome:
    status: str  # OfferStatus value
    reason: str = ""  # for suppressed/deferred
    offer_id: str = ""
    text: str = ""  # the spoken (or shadow-logged) sentence
    spoken: bool = False
