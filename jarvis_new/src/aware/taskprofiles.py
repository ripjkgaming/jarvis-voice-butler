"""Per-task-kind profiles for the capability gate (PLAN.md §3.1 Stage 2 table).

Pure data plus pure text checks. ``gate.py`` walks the checklist rows using
these numbers; nothing here touches the machine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

GB = 1024**3
WEB_OPS = ("click", "type", "press", "select", "check", "wait_for", "goto")


@dataclass(frozen=True)
class TaskProfile:
    task_kind: str
    family: str  # code | file | web | other
    tools: tuple[str, ...]  # executor tools the plan may use (inventory)
    max_minutes: float
    max_steps: int  # claude runs / browser actions / files / steps
    step_unit: str
    allowed_actions: frozenset[str] = field(default_factory=frozenset)
    needs_git: bool = False
    needs_tab: bool = False
    needs_paths: bool = False
    max_bytes: int = 0  # download only


PROFILES: dict[str, TaskProfile] = {
    "code_fix": TaskProfile(
        "code_fix",
        "code",
        ("claude_code", "tests", "git_read"),
        20,
        1,
        "claude run",
        needs_git=True,
    ),
    "test_fix": TaskProfile(
        "test_fix",
        "code",
        ("claude_code", "tests", "git_read"),
        20,
        1,
        "claude run",
        needs_git=True,
    ),
    "web_form": TaskProfile(
        "web_form",
        "web",
        WEB_OPS,
        20,
        40,
        "browser action",
        allowed_actions=frozenset({"submit", "send", "payment"}),
        needs_tab=True,
    ),
    "web_lookup": TaskProfile(
        "web_lookup",
        "web",
        WEB_OPS,
        20,
        40,
        "browser action",
        needs_tab=True,
    ),
    "download": TaskProfile(
        "download",
        "file",
        ("http_download", "file_read"),
        20,
        1,
        "download",
        max_bytes=2 * GB,
    ),
    "file_organise": TaskProfile(
        "file_organise",
        "file",
        ("file_journal", "file_read"),
        20,
        500,
        "file",
        needs_paths=True,
    ),
    "file_convert": TaskProfile(
        "file_convert",
        "file",
        ("file_journal", "file_read", "convert"),
        20,
        500,
        "file",
        needs_paths=True,
    ),
    "doc_draft": TaskProfile(
        "doc_draft",
        "other",
        ("agent_tasks", "chatgpt", "file_write"),
        20,
        6,
        "step",
        allowed_actions=frozenset({"send"}),
    ),
    "web_research": TaskProfile(
        "web_research",
        "other",
        ("agent_tasks", "chatgpt", "web_search", "file_write"),
        20,
        6,
        "step",
    ),
}


def profile(task_kind: str) -> TaskProfile | None:
    return PROFILES.get((task_kind or "").strip().lower())


# --- text scans (pure) -------------------------------------------------------
# Any of these anywhere in what the opportunity says means a human step.
HUMAN_STEP = (
    (r"\b(?:log ?in|login|sign[ -]?in|signed out|logged out)\b", "login"),
    (r"\bpass(?:word|phrase|code)\b", "password"),
    (r"\b2fa\b|\bmfa\b|two[- ]factor|multi[- ]factor|two[- ]step", "2fa"),
    (
        r"3[- ]?d[- ]?secure|\b3ds\b|verified by visa|mastercard (?:id )?check|"
        r"securecode|safekey",
        "3ds",
    ),
    (
        r"one[- ]time (?:code|pass(?:code|word)?|pin)|\botp\b|verification code|"
        r"security code (?:sent|by sms|by text)|sms code|text(?:ed)? (?:you )?a code|"
        r"authenticator|authentication code",
        "otp",
    ),
    (
        r"bank(?:ing)? app|approve (?:it |this |the payment )?in (?:your |the )?app|"
        r"confirm in (?:your |the )?(?:bank|app)",
        "bank_app",
    ),
    (r"captcha|not a robot|verify (?:you are|you're) human", "captcha"),
    (
        r"\b(?:ask|check with|confirm with) (?:sir|the user|you)\b|"
        r"\b(?:sir|the user|you) (?:must|needs? to|should|has to|have to) "
        r"(?:choose|pick|decide|select|confirm|approve|enter|provide|type)|"
        r"\b(?:your|sir's|user'?s?) (?:choice|decision|preference|approval)\b|"
        r"which (?:one|option|size|colou?r|plan|item) (?:he|you|sir)",
        "user_decision",
    ),
    (
        r"\bcard (?:number|details)\b|\bcvv\b|\bcvc\b|new card|add (?:a )?card|"
        r"enter (?:the |your )?(?:card|payment details)",
        "card_entry",
    ),
)
# Never allowed for anyone (PLAN.md §4.4 "still never").
FORBIDDEN = (
    (
        r"(?:change|reset|recover|disable|update) (?:the |your )?"
        r"(?:password|2fa|two[- ]factor|security|recovery)",
        "account_security",
    ),
    (r"account recovery|security settings|security questions?", "account_security"),
    (
        r"(?:reveal|export|show|copy) (?:the |your |saved )*(?:passwords?|card numbers?)",
        "secret_export",
    ),
    (r"install (?:an? |the )?(?:browser )?extension", "extension_install"),
    (r"\bbrave://|\bchrome://|chrome-extension://", "browser_internal"),
    (
        r"(?:download|fetch)\b.*\b(?:and|then) (?:run|execute|install)\b|"
        r"\bexecute the (?:downloaded|installer)|\brun the installer",
        "download_execute",
    ),
)
CODE_REMOTE = (
    r"\bgit push\b|\bpush(?:ing)? (?:it |the )?(?:to|up)\b|\bopen (?:a )?pr\b|"
    r"pull request|\bdeploy|\brelease\b|\bpublish\b|\bmerge (?:it|to|into)\b"
)
FILE_DELETE = r"\b(?:delete|remove|erase|wipe|rm -rf|shred|purge)\b"
WEB_CONSEQ = (
    r"\b(?:send|pay|buy|order|submit|post|confirm purchase|subscribe|"
    r"checkout|check out|place (?:the |an )?order|purchase)\b"
)


def scan(patterns, text: str) -> list[str]:
    low = (text or "").lower()
    hits: list[str] = []
    for rx, tag in patterns:
        if re.search(rx, low) and tag not in hits:
            hits.append(tag)
    return hits


def human_steps(text: str) -> list[str]:
    return scan(HUMAN_STEP, text)


def forbidden(text: str) -> list[str]:
    return scan(FORBIDDEN, text)


def mentions_remote(text: str) -> bool:
    return bool(re.search(CODE_REMOTE, (text or "").lower()))


def mentions_delete(text: str) -> bool:
    return bool(re.search(FILE_DELETE, (text or "").lower()))


def mentions_consequential(text: str) -> bool:
    return bool(re.search(WEB_CONSEQ, (text or "").lower()))


# --- success predicates (pure) ----------------------------------------------
_QUOTED = re.compile(r"[\"“']([^\"”']{3,120})[\"”']")
_URL = re.compile(
    r"url (?:contains|includes|ends with|is|becomes|changes to) "
    r"[\"“']?([\w./:#?=&%-]{2,200})",
    re.I,
)
_SELECTOR = re.compile(r"selector [\"“']?([^\"”'\s]{1,200})", re.I)


def compile_predicate(success_check: str, given: dict | None = None) -> dict | None:
    """A DOM predicate {url_contains?, selector?, text?} or None (not machine-checkable).

    ``given`` (from structured evidence) wins when it has any usable key."""
    if isinstance(given, dict):
        pred = {
            k: str(v)
            for k, v in given.items()
            if k in ("url_contains", "selector", "text") and str(v or "").strip()
        }
        if pred:
            return pred
    text = success_check or ""
    pred: dict = {}
    m = _URL.search(text)
    if m:
        pred["url_contains"] = m.group(1)
    m = _SELECTOR.search(text)
    if m:
        pred["selector"] = m.group(1)
    if "url_contains" not in pred:
        m = _QUOTED.search(text)
        if m:
            pred["text"] = m.group(1)
    return pred or None
