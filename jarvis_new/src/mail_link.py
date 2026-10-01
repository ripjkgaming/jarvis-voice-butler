"""Email command sidecar: Sir emails Jarvis, Jarvis acts and replies.

Sir creates a dedicated mailbox for Jarvis (a Gmail account + App Password;
see scripts/mail_link_setup.py). Sir emails that address from his own
personal address; this loop reads the mail over IMAP, carries out the
commands on this laptop (through the same local bridge HTTP API the
phone app uses), and replies by email with the results.

Layout mirrors src/telegram_bot.py: fail-soft, injectable seams
(connect_imap/send_mail/bridge/clock) so tests never touch the network,
and start_thread() -> (Thread, Event) for the background loop.

This is a REMOTE CONTROL channel for a real computer, so every mail must
pass all acceptance checks before anything runs: owner allowlist, own
address + auto-reply skip, sender authentication (DMARC/DKIM alignment
against the receiving provider's Authentication-Results header),
freshness, Message-ID replay cache, optional PIN, and a rolling-hour
rate limit. Anything that fails is marked seen with NO reply.
"""

from __future__ import annotations

import base64
import contextlib
import email.message
import email.policy
import email.utils
import hashlib
import hmac
import imaplib
import json
import os
import re
import secrets
import smtplib
import ssl
import tempfile
import threading
import time
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path

try:
    import agent_tasks
except ImportError:  # pragma: no cover - missing in tiny checkouts
    agent_tasks = None  # type: ignore[assignment]

#: Poll cadence for new mail.
POLL_S = 30.0
#: Recheck for config this often when none is present.
IDLE_S = 60.0
#: Backoff ceiling for transient poll errors.
BACKOFF_CAP_S = 300.0
#: Authentication failures wait longer (wrong/revoked app password).
AUTH_BACKOFF_S = 600.0
#: Mail must be newer than this (and not too far in the future).
FRESH_PAST_S = 2 * 3600.0
FRESH_FUTURE_S = 10 * 60.0
#: Max commands read from one mail; each trimmed to this many chars.
MAX_COMMANDS = 5
CMD_CHARS = 500
#: At most this many accepted commands per rolling hour.
RATE_LIMIT = 30
RATE_WINDOW_S = 3600.0
#: The "rate limit reached" notice goes out at most this often.
RATE_NOTICE_GAP_S = 3600.0
#: Sensitive commands await a CONFIRM code for this long.
CONFIRM_TTL_S = 10 * 60.0
#: First command "confirm <code>" redeems a pending sensitive command.
CONFIRM_RE = re.compile(r"^\s*confirm\s+(\S+)\s*$", re.IGNORECASE)
#: Commands matching this need a confirmation round trip first.
SENSITIVE_RE = re.compile(r"\b(unlock|remote|type)\b", re.IGNORECASE)
#: A line that is just the PIN: "PIN abc123" or "pin: abc123".
PIN_RE = re.compile(r"^\s*pin(?:\s+|:)\s*(\S+)\s*$", re.IGNORECASE)
#: Leading "Re:/Fwd:/Fw:" prefixes, stripped for the subject fallback.
SUBJECT_PREFIX_RE = re.compile(r"(?i)^(?:(?:re|fw|fwd)\s*:\s*)+")
#: Screenshot attachments bigger than this are skipped with a note.
SCREENSHOT_MAX_BYTES = 15 * 1024 * 1024
#: Replay cache: the last this many Message-IDs are remembered.
MAX_MSGIDS = 500
#: agent_tasks statuses that end a task (read from src/agent_tasks.py).
TERMINAL_TASK_STATUS = frozenset({"done", "failed", "cancelled"})
#: agent_tasks status where the task waits for Sir's voice approval.
TASK_WAITING_STATUS = "waiting"

HELP_TEXT = (
    "Mail Link at your service, Sir. Email me commands, one per line:\n"
    "play some music\n"
    "volume to 30\n"
    "lock the computer\n"
    "open youtube\n"
    "screenshot\n"
    "status\n"
    "task: research X and summarise\n"
    "what's on my calendar today"
)


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def state_path(home: Path | None = None) -> Path:
    """Replay/rate/confirm/task state file. Pure."""
    return (home or jarvis_home()) / "mail_link.json"


def keys_env_path(home: Path | None = None) -> Path:
    """Secrets file holding the mail keys. Pure."""
    return (home or jarvis_home()) / "keys.env"


def parse_keys_env(text: str) -> dict[str, str]:
    """KEY=VALUE lines (quotes/comments tolerated) -> dict. Pure."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        val = val.strip().strip("\"'")
        out[key] = val
    return out


def _env_or_keys(name: str, keys: dict[str, str], default: str = "") -> str:
    """Env wins, then keys.env, then the default. Never logs values."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return keys.get(name, default).strip()


def load_config(home: Path | None = None) -> dict:
    """Mail Link config: env overrides $JARVIS_HOME/keys.env. Never logs."""
    try:
        keys = parse_keys_env(keys_env_path(home).read_text())
    except OSError:
        keys = {}
    try:
        imap_port = int(_env_or_keys("JARVIS_MAIL_IMAP_PORT", keys, "993"))
    except ValueError:
        imap_port = 993
    try:
        smtp_port = int(_env_or_keys("JARVIS_MAIL_SMTP_PORT", keys, "465"))
    except ValueError:
        smtp_port = 465
    owners = [
        part.strip().lower()
        for part in _env_or_keys("JARVIS_MAIL_OWNERS", keys).split(",")
    ]
    return {
        "enabled": _env_or_keys("JARVIS_MAIL_LINK", keys, "0").lower()
        in ("1", "true", "on", "yes"),
        "address": _env_or_keys("JARVIS_MAIL_ADDRESS", keys).lower(),
        "password": _env_or_keys("JARVIS_MAIL_PASSWORD", keys),
        "owners": [o for o in owners if o],
        "pin": _env_or_keys("JARVIS_MAIL_PIN", keys),
        "imap_host": _env_or_keys("JARVIS_MAIL_IMAP_HOST", keys, "imap.gmail.com"),
        "imap_port": imap_port,
        "smtp_host": _env_or_keys("JARVIS_MAIL_SMTP_HOST", keys, "smtp.gmail.com"),
        "smtp_port": smtp_port,
        "authserv": _env_or_keys("JARVIS_MAIL_AUTHSERV", keys, "mx.google.com").lower(),
    }


def configured(cfg: dict | None) -> bool:
    """Explicit opt-in, credentials and owners, or the link stays idle."""
    if not isinstance(cfg, dict):
        return False
    return bool(
        cfg.get("enabled") is True
        and cfg.get("address")
        and cfg.get("password")
        and cfg.get("owners")
    )


def backoff_s(failures: int, cap: float = BACKOFF_CAP_S) -> float:
    """1, 2, 4 ... capped. Pure."""
    try:
        n = max(1, int(failures))
    except (TypeError, ValueError):
        n = 1
    return min(cap, float(2 ** min(n - 1, 10)))


def _default_state() -> dict:
    return {
        "msgids": [],
        "accepted_ts": [],
        "confirms": [],
        "tasks": [],
        "rate_notice_at": None,
    }


def load_state(home: Path | None = None) -> dict:
    """Read state; missing is new, unreadable/corrupt state fails closed."""
    state = _default_state()
    try:
        raw = json.loads(state_path(home).read_text())
    except FileNotFoundError:
        return state
    except (OSError, ValueError):
        state["unavailable"] = True
        return state
    if (
        not isinstance(raw, dict)
        or raw.get("unavailable")
        or not isinstance(raw.get("msgids"), list)
        or not isinstance(raw.get("accepted_ts"), list)
    ):
        state["unavailable"] = True
        return state
    if isinstance(raw.get("msgids"), list):
        state["msgids"] = [str(m) for m in raw["msgids"] if m][-MAX_MSGIDS:]
    if isinstance(raw.get("accepted_ts"), list):
        kept = []
        for ts in raw["accepted_ts"]:
            try:
                kept.append(float(ts))
            except (TypeError, ValueError):
                continue
        state["accepted_ts"] = kept[-RATE_LIMIT * 4 :]
    if isinstance(raw.get("confirms"), list):
        state["confirms"] = [c for c in raw["confirms"] if isinstance(c, dict)]
    if isinstance(raw.get("tasks"), list):
        state["tasks"] = [t for t in raw["tasks"] if isinstance(t, dict)]
    try:
        state["rate_notice_at"] = (
            float(raw["rate_notice_at"])
            if raw.get("rate_notice_at") is not None
            else None
        )
    except (TypeError, ValueError):
        state["rate_notice_at"] = None
    return state


def save_state(state: dict, home: Path | None = None) -> bool:
    """Atomic persist (tmp + replace, mode 600). Never raises."""
    if state.get("unavailable"):
        return False
    tmp = None
    try:
        path = state_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False
        ) as stream:
            tmp = Path(stream.name)
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        with contextlib.suppress(OSError):
            path.chmod(0o600)
        return True
    except (OSError, ValueError):
        return False
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)


def bridge_base() -> str:
    """Bridge root from env (bind + port, loopback default). Pure (env)."""
    bind = os.environ.get("JARVIS_BRIDGE_BIND", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.environ.get("JARVIS_BRIDGE_PORT", "4317"))
    except ValueError:
        port = 4317
    return f"http://{bind}:{port}"


def bridge_token(home: Path | None = None) -> str:
    """Bearer token: env first, then $JARVIS_HOME/bridge_token. Never logs."""
    token = os.environ.get("JARVIS_BRIDGE_TOKEN", "").strip()
    if token:
        return token
    try:
        base = home or jarvis_home()
        return (base / "bridge_token").read_text().strip().split()[0]
    except (OSError, IndexError):
        return ""


def _urlopen_bytes(req, timeout: float) -> bytes:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        # The bridge uses HTTP 404 for a deterministic routing miss. Keep
        # that JSON result so /chat can handle it, but never retry an action
        # after an auth failure, server failure, or ambiguous network error.
        with exc:
            if exc.code == 404 and req.full_url.endswith("/route"):
                raw = exc.read()
                if json.loads(raw).get("error") == "no-route":
                    return raw
        raise


def bridge_call(
    method: str, path: str, body: dict | None = None, opener=None
) -> dict | None:
    """One JSON call to the local bridge. None on any failure."""
    import urllib.request

    opener = opener or _urlopen_bytes
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{bridge_base()}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    token = bridge_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        raw = opener(req, 15.0)
        parsed = json.loads(raw.decode())
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


class MailAuthError(Exception):
    """IMAP login rejected the credentials (wrong/revoked app password)."""


def _is_auth_error(exc: Exception) -> bool:
    """Login-shaped IMAP failures get the long backoff. Pure-ish."""
    if isinstance(exc, MailAuthError):
        return True
    if isinstance(exc, imaplib.IMAP4.error):
        text = str(exc).lower()
        return any(
            marker in text
            for marker in ("auth", "login", "credential", "password", "invalid")
        )
    return False


def auth_results_ok(headers: list[str], authserv_id: str, from_domain: str) -> bool:
    """Did the receiving server authenticate this sender? Pure.

    Only Authentication-Results headers whose authserv-id (the token
    before the first ";") equals `authserv_id` are considered, and only
    the FIRST such header in header order — the one the receiving
    server prepended; lower ones can be forged by the sender.

    Accepts on `dmarc=pass`, or on `dkim=pass` whose `header.d=` (or
    `header.i=@domain`) is the From domain or a parent of it.
    """
    want = (authserv_id or "").strip().lower()
    domain = (from_domain or "").strip().lower().rstrip(".")
    if not want or not domain:
        return False
    target: str | None = None
    for raw in headers or []:
        text = raw or ""
        head, sep, _ = text.partition(";")
        if not sep:
            continue
        if head.strip().lower() == want:
            target = text
            break
    if target is None:
        return False
    # Comments and quoted reason text are untrusted prose, not method results.
    # Remove them before splitting clauses (a reason can contain semicolons).
    lowered = _auth_tokens(target).lower()
    clauses = lowered.split(";")[1:]
    for clause in clauses:
        if re.match(r"\s*dmarc\s*=\s*pass\b", clause):
            match = re.search(r"\bheader\.from\s*=\s*([a-z0-9_.\-]+)", clause)
            if match and match.group(1).rstrip(".") == domain:
                return True
    for clause in clauses:
        if not re.match(r"\s*dkim\s*=\s*pass\b", clause):
            continue
        for match in re.finditer(r"header\.d\s*=\s*([a-z0-9_.\-]+)", clause):
            if _domain_aligned(match.group(1), domain):
                return True
        for match in re.finditer(r"header\.i\s*=\s*([a-z0-9_@.\-]+)", clause):
            candidate = match.group(1).split("@")[-1]
            if _domain_aligned(candidate, domain):
                return True
    return False


def _auth_tokens(text: str) -> str:
    """Strip nested comments / quoted strings; reject unclosed syntax."""
    out = []
    depth = 0
    quoted = escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if (depth or quoted) and char == "\\":
            escaped = True
        elif depth:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
        elif quoted:
            if char == '"':
                quoted = False
        elif char == "(":
            depth = 1
            out.append(" ")
        elif char == '"':
            quoted = True
            out.append(" ")
        else:
            out.append(char)
    return "" if depth or quoted else "".join(out)


def _domain_aligned(candidate: str, from_domain: str) -> bool:
    """DKIM domain equals the From domain or a parent of it. Pure."""
    cand = (candidate or "").strip().lower().rstrip(".")
    if not cand or not from_domain:
        return False
    return cand == from_domain or from_domain.endswith("." + cand)


def from_domain_of(addr: str) -> str:
    """Domain part of an address, lower-cased. Pure."""
    return (addr or "").partition("@")[2].strip().lower().rstrip(".")


def parse_pin_line(line: str) -> str | None:
    """The PIN value when this line is just the PIN, else None. Pure."""
    match = PIN_RE.match(line or "")
    return match.group(1) if match else None


def strip_pin_lines(text: str) -> str:
    """Drop PIN-only lines so commands never carry the secret. Pure."""
    return "\n".join(
        line for line in (text or "").splitlines() if parse_pin_line(line) is None
    )


def html_to_text(raw: str) -> str:
    """Keep rich-mail command lines, excluding quoted history and hidden text."""

    class BodyParser(HTMLParser):
        breaks = frozenset({"br", "p", "div", "li", "tr", "section", "h1", "h2", "h3"})
        ignored = frozenset({"script", "style", "blockquote"})

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self.skipping: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag in self.ignored:
                self.skipping.append(tag)
            elif not self.skipping and tag in self.breaks:
                self.parts.append("\n")

        def handle_endtag(self, tag):
            if self.skipping:
                if tag == self.skipping[-1]:
                    self.skipping.pop()
            elif tag in self.breaks:
                self.parts.append("\n")

        def handle_data(self, data):
            if not self.skipping:
                self.parts.append(data)

    parser = BodyParser()
    parser.feed(raw or "")
    return "\n".join(
        " ".join(line.split()) for line in "".join(parser.parts).splitlines()
    )


def extract_body_text(msg: email.message.EmailMessage) -> str:
    """The text/plain part (HTML fallback, tags stripped). Pure-ish."""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except (AttributeError, ValueError):
        return ""
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (ValueError, LookupError):
        return ""
    if not isinstance(content, str):
        return ""
    if part.get_content_type() == "text/html":
        return html_to_text(content)
    return content


_ON_WROTE_RE = re.compile(r"^On .+ wrote:\s*$")
_ORIGINAL_MSG_RE = re.compile(r"^-{2,}\s*Original Message\s*-{2,}")


def strip_quotes(text: str) -> str:
    """Drop quoted replies, forwards headers and signatures. Pure."""
    kept: list[str] = []
    for line in (text or "").splitlines():
        if line.lstrip().startswith(">"):
            continue
        if _ON_WROTE_RE.match(line) or _ORIGINAL_MSG_RE.match(line):
            break
        if line == "-- ":
            break
        kept.append(line)
    return "\n".join(kept)


def clean_subject(subject: str) -> str:
    """Strip leading Re:/Fwd:/Fw: prefixes for the subject fallback. Pure."""
    return SUBJECT_PREFIX_RE.sub("", subject or "").strip()


def extract_commands(body_text: str, subject: str) -> list[str]:
    """Non-empty cleaned lines (max 5, each <= 500 chars), else subject.

    The Subject is only used when the body has no commands at all, with
    its Re:/Fwd: prefixes removed. Pure.
    """
    lines = [
        line.strip()[:CMD_CHARS]
        for line in strip_quotes(body_text or "").splitlines()
        if line.strip()
    ]
    commands = [line for line in lines if line][:MAX_COMMANDS]
    if commands:
        return commands
    fallback = clean_subject(subject or "")
    if fallback:
        return [fallback[:CMD_CHARS]]
    return []


def _gb(num) -> str | None:
    try:
        return f"{float(num) / 1024**3:.1f}"
    except (TypeError, ValueError):
        return None


def format_sys(data: dict | None) -> str:
    """Bridge GET /sys -> a few plain-text lines. Pure; missing -> "—"."""
    if not isinstance(data, dict):
        return "My instruments are quiet, Sir — the bridge didn't answer."
    lines: list[str] = []
    load = data.get("load_1_5_15")
    if isinstance(load, (list, tuple)) and load:
        cpu = "load " + " ".join(str(x) for x in list(load)[:3])
        if isinstance(data.get("cpu_count"), int):
            cpu += f" on {data['cpu_count']} cores"
        lines.append(f"CPU: {cpu}")
    else:
        lines.append("CPU: —")
    mem = data.get("mem_bytes") if isinstance(data.get("mem_bytes"), dict) else {}
    total = _gb(mem.get("MemTotal")) if mem else None
    avail = _gb(mem.get("MemAvailable")) if mem else None
    if total is not None and avail is not None:
        try:
            pct = round(
                100
                * (float(mem["MemTotal"]) - float(mem["MemAvailable"]))
                / float(mem["MemTotal"])
            )
            mem_txt = f"{float(total) - float(avail):.1f}/{total} GB ({pct}%)"
        except (TypeError, ValueError, ZeroDivisionError, KeyError):
            mem_txt = None
        lines.append(f"Memory: {mem_txt}" if mem_txt else "Memory: —")
    else:
        lines.append("Memory: —")
    free = _gb(data.get("home_free_bytes"))
    lines.append(f"Disk free: {free} GB" if free else "Disk free: —")
    temp = data.get("cpu_temp_c")
    lines.append(
        f"CPU temp: {temp}°C" if isinstance(temp, (int, float)) else "CPU temp: —"
    )
    power = data.get("laptop_power")
    if isinstance(power, dict):
        bat = power.get("battery")
        status = power.get("status") or "—"
        ac = power.get("ac")
        ac_txt = "on" if ac is True else ("off" if ac is False else "—")
        watts = power.get("watts")
        watts_txt = f", {watts} W" if isinstance(watts, (int, float)) else ""
        bat_txt = f"{bat}%" if isinstance(bat, int) else "—"
        lines.append(f"Battery: {bat_txt} ({status}, AC {ac_txt}{watts_txt})")
    else:
        lines.append("Battery: —")
    lines.append(f"Mode: {data.get('mode') or '—'}")
    if "call_live" in data:
        lines.append("Call: live" if data.get("call_live") is True else "Call: none")
    else:
        lines.append("Call: —")
    return "\n".join(lines)


def _redact(text: str) -> str:
    """Hide CONFIRM codes (and any PIN-shaped line) from log lines. Pure."""
    text = re.sub(r"(?i)\bconfirm\s+\S+", "confirm ***", text or "")
    return text


def _default_connect_imap(host: str, port: int):
    return imaplib.IMAP4_SSL(host, port, timeout=20.0)


def _smtp_send(cfg: dict, msg: EmailMessage) -> None:
    """Deliver one message over SMTP_SSL. Raises on failure."""
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        cfg["smtp_host"], cfg["smtp_port"], context=context, timeout=20.0
    ) as smtp:
        smtp.login(cfg["address"], cfg["password"])
        smtp.send_message(msg)


class MailLink:
    """IMAP command sidecar for Sir's mailbox.

    Inject connect_imap/send_mail/bridge/clock in tests; defaults talk
    to the real servers, SMTP and the local bridge HTTP API.
    """

    def __init__(
        self,
        config: dict | None = None,
        home: Path | None = None,
        connect_imap=None,
        send_mail=None,
        bridge=None,
        clock=None,
    ) -> None:
        self.home = home or jarvis_home()
        self._explicit_config = config
        self._connect_imap = connect_imap or _default_connect_imap
        self._send_mail_fn = send_mail
        self._bridge = bridge or bridge_call
        self._clock = clock or time.time
        self.state = load_state(self.home)

    @classmethod
    def from_env(cls, **kwargs) -> MailLink:
        home = kwargs.pop("home", None) or jarvis_home()
        return cls(config=None, home=home, **kwargs)

    def _config(self) -> dict:
        if self._explicit_config is not None:
            return self._explicit_config
        return load_config(self.home)

    def _log(self, detail: str) -> None:
        try:
            from system import log_action

            log_action("mail-link", detail[:300])
        except Exception:
            with contextlib.suppress(Exception):
                pass

    # -- state helpers --------------------------------------------------------

    def _prune(self, now: float) -> None:
        """Drop expired confirms and out-of-window rate stamps. Pure-ish."""
        self.state["confirms"] = [
            c
            for c in self.state.get("confirms", [])
            if isinstance(c, dict)
            and isinstance(c.get("expires"), (int, float))
            and float(c["expires"]) > now
        ]
        self.state["accepted_ts"] = [
            ts
            for ts in self.state.get("accepted_ts", [])
            if isinstance(ts, (int, float)) and now - float(ts) < RATE_WINDOW_S
        ]
        self.state["msgids"] = list(self.state.get("msgids", []))[-MAX_MSGIDS:]

    def _remember_msgid(self, msgid: str) -> bool:
        msgids = self.state.setdefault("msgids", [])
        if msgid not in msgids:
            msgids.append(msgid)
        del msgids[:-MAX_MSGIDS:]
        return save_state(self.state, self.home)

    # -- reply building -------------------------------------------------------

    @staticmethod
    def _reply_subject(original: str) -> str:
        subject = (original or "").strip() or "(no subject)"
        if re.match(r"(?i)^re\s*:", subject):
            return subject
        return f"Re: {subject}"

    def _build_reply(
        self,
        to_addr: str,
        cfg: dict,
        original_subject: str,
        original_msgid: str,
        original_refs: str,
        body_text: str,
        attachments: list[tuple[bytes, str]] | None = None,
    ) -> EmailMessage:
        msg = EmailMessage(policy=email.policy.default)
        msg["From"] = cfg["address"]
        msg["To"] = to_addr
        msg["Subject"] = self._reply_subject(original_subject)
        if original_msgid:
            msg["In-Reply-To"] = original_msgid
            refs = ((original_refs or "").strip() + " " + original_msgid).strip()
            if refs:
                msg["References"] = refs
        msg["Auto-Submitted"] = "auto-replied"
        msg.set_content(body_text or "")
        for raw, filename in attachments or []:
            msg.add_attachment(raw, maintype="image", subtype="png", filename=filename)
        return msg

    @staticmethod
    def _pairs_body(pairs: list[tuple[str, str]]) -> str:
        parts = []
        for command, reply in pairs:
            indented = "\n".join(
                f"  {line}" for line in (reply or "").splitlines() or [""]
            )
            parts.append(f"▸ {command}\n{indented}\n")
        return "".join(parts) + "\n— J.A.R.V.I.S."

    def _deliver(self, cfg: dict, msg: EmailMessage) -> None:
        if self._send_mail_fn is not None:
            self._send_mail_fn(msg)
        else:
            _smtp_send(cfg, msg)

    # -- acceptance -----------------------------------------------------------

    def _reject(self, reason: str, from_addr: str) -> str:
        self._log(f"rejected {reason} from={from_addr or '?'}")
        return f"rejected:{reason}"

    def process_message(self, cfg: dict, msg: email.message.EmailMessage) -> str:
        """Check, execute and reply to one message. Never raises."""
        try:
            return self._process(cfg, msg)
        except Exception as exc:
            self._log(f"message error {type(exc).__name__}")
            return "error"

    def _process(self, cfg: dict, msg: email.message.EmailMessage) -> str:
        if not configured(cfg):
            return "idle"
        if self.state.get("unavailable"):
            return self._reject("state-unavailable", "?")
        addrs = email.utils.getaddresses(list(msg.get_all("From", [])))
        if len(addrs) != 1 or not (addrs[0][1] or "").strip():
            return self._reject("not-owner", "?")
        from_addr = addrs[0][1].strip().lower()
        if from_addr not in [o.lower() for o in cfg.get("owners", [])]:
            return self._reject("not-owner", from_addr)
        if from_addr == (cfg.get("address") or "").lower():
            return self._reject("self", from_addr)
        auto = msg.get_all("Auto-Submitted", [])
        if any((v or "").strip().lower() != "no" for v in auto if (v or "").strip()):
            return self._reject("auto", from_addr)
        if (msg.get("Precedence", "") or "").strip().lower() in (
            "bulk",
            "list",
            "junk",
        ):
            return self._reject("auto", from_addr)
        if msg.get("List-Id") is not None:
            return self._reject("auto", from_addr)
        auth_headers = list(msg.get_all("Authentication-Results", []))
        if not auth_results_ok(
            auth_headers, cfg.get("authserv", ""), from_domain_of(from_addr)
        ):
            return self._reject("unauthenticated", from_addr)
        now = self._clock()
        if not self._fresh(msg.get("Date", ""), now):
            return self._reject("stale", from_addr)
        msgid = (msg.get("Message-ID", "") or "").strip()
        if not msgid or msgid in self.state.get("msgids", []):
            return self._reject("replay", from_addr)

        body_text = extract_body_text(msg)
        subject = str(msg.get("Subject", "") or "")
        pin_expected = cfg.get("pin") or ""
        if pin_expected:
            found = parse_pin_line(subject)
            if found is None:
                for line in (body_text or "").splitlines():
                    found = parse_pin_line(line)
                    if found is not None:
                        break
            if found is None or not hmac.compare_digest(
                found.encode("utf-8"), pin_expected.encode("utf-8")
            ):
                return self._reject("pin", from_addr)
            body_text = strip_pin_lines(body_text or "")
        # Never echo a subject PIN or send it to the command dispatcher.
        if parse_pin_line(subject) is not None:
            subject = ""

        commands = extract_commands(body_text, subject)
        if not commands:
            self._remember_msgid(msgid)
            return "empty"
        if not self._remember_msgid(msgid):
            return self._reject("state-unavailable", from_addr)

        self._prune(now)
        recent = self.state.get("accepted_ts", [])
        if len(recent) + len(commands) > RATE_LIMIT:
            last_notice = self.state.get("rate_notice_at")
            self._log(f"rejected rate-limited from={from_addr}")
            if last_notice is None or now - float(last_notice) >= RATE_NOTICE_GAP_S:
                notice = self._build_reply(
                    from_addr,
                    cfg,
                    subject,
                    msgid,
                    str(msg.get("References", "") or ""),
                    "Rate limit reached, Sir. Try again later.",
                )
                try:
                    self._deliver(cfg, notice)
                except Exception:
                    self._log("notice send failed")
                    return "limited"
                self.state["rate_notice_at"] = now
                save_state(self.state, self.home)
            return "limited"

        # Reserve rate budget durably before effects, even when SMTP fails.
        self.state["accepted_ts"] = list(recent) + [now] * len(commands)
        if not save_state(self.state, self.home):
            return self._reject("state-unavailable", from_addr)
        pairs: list[tuple[str, str]] = []
        attachments: list[tuple[bytes, str]] = []
        first = commands[0]
        confirm_match = CONFIRM_RE.match(first)
        rest = commands
        if confirm_match:
            pairs.append(
                self._redeem_confirm(cfg, from_addr, confirm_match.group(1), now)
            )
            rest = commands[1:]
        for command in rest:
            reply, files, ok = self._execute_command(
                cfg, from_addr, command, now, msgid, subject
            )
            pairs.append((command, reply))
            attachments.extend(files)
            self._log(f"cmd {_redact(command[:80])!r} ok={ok}")

        body = self._pairs_body(pairs)
        reply_msg = self._build_reply(
            from_addr,
            cfg,
            subject,
            msgid,
            str(msg.get("References", "") or ""),
            body,
            attachments,
        )
        try:
            self._deliver(cfg, reply_msg)
        except Exception:
            self._log("reply send failed")
            return "send-failed"
        save_state(self.state, self.home)
        return "replied"

    @staticmethod
    def _fresh(date_value: str, now: float) -> bool:
        """Date within the last 2h and at most 10 min in the future. Pure."""
        try:
            moment = email.utils.parsedate_to_datetime(date_value or "")
        except (TypeError, ValueError):
            return False
        if moment is None:
            return False
        try:
            stamp = moment.timestamp()
        except (OverflowError, OSError, ValueError):
            return False
        return now - FRESH_PAST_S <= stamp <= now + FRESH_FUTURE_S

    # -- confirm round trip ---------------------------------------------------

    def _redeem_confirm(
        self, cfg: dict, requester: str, code: str, now: float
    ) -> tuple[str, str]:
        """Run a pending sensitive command for a matching CONFIRM code."""
        self._prune(now)
        digest = hashlib.sha256((code or "").encode("utf-8")).hexdigest()
        for entry in list(self.state.get("confirms", [])):
            if not isinstance(entry, dict):
                continue
            if entry.get("requester") != requester:
                continue
            stored = entry.get("hash") or ""
            if stored and hmac.compare_digest(stored, digest):
                self.state["confirms"].remove(entry)
                if not save_state(self.state, self.home):
                    return (
                        "confirm ***",
                        "State storage is unavailable; no command was run.",
                    )
                stored_cmd = str(entry.get("command", ""))
                reply, ok = self._route_chat(cfg, stored_cmd)
                self._log(f"cmd {_redact(stored_cmd[:80])!r} ok={ok} via=confirm")
                return stored_cmd, reply
        save_state(self.state, self.home)
        return (
            f"confirm {code}",
            "That code doesn't match anything I offered, Sir — it may have expired.",
        )

    # -- command execution ----------------------------------------------------

    def _execute_command(
        self,
        cfg: dict,
        requester: str,
        command: str,
        now: float,
        msgid: str = "",
        subject: str = "",
    ) -> tuple[str, list[tuple[bytes, str]], bool]:
        """Run one command -> (reply, attachments, ok). Never raises."""
        try:
            cmd = (command or "").strip()
            lowered = cmd.lower()
            if lowered == "help":
                return HELP_TEXT, [], True
            if lowered == "status":
                data = self._bridge("GET", "/sys", None)
                return format_sys(data), [], True
            shot = re.match(r"(?i)^screenshot(?:\s+(.*?))?\s*$", cmd)
            if shot:
                return self._do_screenshot(cfg, (shot.group(1) or "").strip())
            task_match = re.match(r"(?i)^task\s*:\s*(.+)$", cmd, re.DOTALL)
            if task_match and task_match.group(1).strip():
                return self._do_task(
                    cfg, requester, task_match.group(1).strip(), msgid, subject
                )
            if SENSITIVE_RE.search(cmd):
                return self._offer_confirm(requester, cmd, now), [], True
            reply, ok = self._route_chat(cfg, cmd)
            return reply, [], ok
        except Exception as exc:
            self._log(f"command error {type(exc).__name__}")
            return "Something went wrong with that one, Sir.", [], False

    def _route_chat(self, cfg: dict, cmd: str) -> tuple[str, bool]:
        """POST /route, falling back to /chat. (reply, ok). Never raises."""
        try:
            routed = self._bridge("POST", "/route", {"text": cmd})
        except Exception:
            routed = None
        if not isinstance(routed, dict):
            return "Jarvis's bridge is offline, Sir.", False
        if routed.get("ok") is True:
            action = routed.get("action")
            ok = not isinstance(action, dict) or action.get("ok") is True
            return str(routed.get("reply") or "Command completed, Sir."), ok
        # Only an explicit routing miss permits the LLM fallback. A timeout
        # or failed action may already have changed the computer.
        if routed.get("error") != "no-route":
            return str(routed.get("error") or "Command failed, Sir."), False
        try:
            chatted = self._bridge("POST", "/chat", {"text": cmd})
        except Exception:
            chatted = None
        if not isinstance(chatted, dict):
            return "Jarvis's bridge is offline, Sir.", False
        reply = chatted.get("reply")
        if isinstance(reply, str) and reply.strip():
            return reply, chatted.get("ok") is not False
        warning = chatted.get("warning")
        if isinstance(warning, str) and warning.strip():
            return warning, chatted.get("ok") is not False
        return "Jarvis's bridge is offline, Sir.", False

    def _do_screenshot(
        self, cfg: dict, output: str
    ) -> tuple[str, list[tuple[bytes, str]], bool]:
        """POST /tool screenshot -> (reply, png attachments, ok)."""
        try:
            result = self._bridge(
                "POST", "/tool", {"tool": "screenshot", "args": {"output": output}}
            )
        except Exception:
            result = None
        if not isinstance(result, dict):
            return "Jarvis's bridge is offline, Sir.", [], False
        if result.get("ok") is not True:
            error = str(result.get("error", "") or "")[:150]
            note = (
                f"I couldn't take that, Sir — {error}."
                if error
                else ("The screenshot didn't come back, Sir.")
            )
            return note, [], True
        raw: bytes | None = None
        image_b64 = result.get("image_b64")
        if isinstance(image_b64, str) and image_b64:
            try:
                raw = base64.b64decode(image_b64, validate=True)
            except (ValueError, base64.binascii.Error):
                raw = None
        if raw is None:
            for key in ("path", "file", "filename", "filepath"):
                candidate = result.get(key)
                if isinstance(candidate, str) and candidate:
                    try:
                        if Path(candidate).is_file():
                            raw = Path(candidate).read_bytes()
                            break
                    except OSError:
                        continue
        if raw is None:
            return "The screenshot didn't come back, Sir.", [], True
        if len(raw) > SCREENSHOT_MAX_BYTES:
            mb = len(raw) / 1024 / 1024
            return (
                f"Screenshot taken but too large to email ({mb:.1f} MB), Sir.",
                [],
                True,
            )
        name = f"{output}.png" if output else "screenshot.png"
        return "Screenshot, Sir.", [(raw, name)], True

    def _do_task(
        self, cfg: dict, requester: str, goal: str, msgid: str, subject: str
    ) -> tuple[str, list[tuple[bytes, str]], bool]:
        """Start an in-process background task, remembering it. Never raises."""
        if agent_tasks is None:
            return "Tasks aren't available just now, Sir.", [], False
        try:
            task = agent_tasks.start(goal, background=True)
        except Exception:
            return "I couldn't start that task, Sir.", [], False
        if not isinstance(task, dict) or not task.get("id"):
            return "I couldn't start that task, Sir.", [], False
        task_id = str(task["id"])
        title = str(task.get("title") or goal[:60])
        entries = [t for t in self.state.get("tasks", []) if t.get("id") != task_id]
        entries.append(
            {
                "id": task_id,
                "requester": requester,
                "msgid": msgid,
                "subject": subject,
                "reported": False,
                "waiting_told": False,
            }
        )
        self.state["tasks"] = entries
        save_state(self.state, self.home)
        return f"Task started: {title}", [], True

    def _offer_confirm(self, requester: str, command: str, now: float) -> str:
        """Issue a one-time CONFIRM code; store only its hash. Never raises."""
        code = f"{secrets.randbelow(900000) + 100000:06d}"
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        entry = {
            "hash": digest,
            "command": command,
            "requester": requester,
            "expires": now + CONFIRM_TTL_S,
        }
        self.state.setdefault("confirms", []).append(entry)
        if not save_state(self.state, self.home):
            self.state["confirms"].remove(entry)
            return "State storage is unavailable; no confirmation was issued."
        return f"Reply with: CONFIRM {code} within 10 minutes to run: {command}"

    # -- background tasks follow-up -------------------------------------------

    def check_task_followups(self, cfg: dict) -> str:
        """Email task completions / approval waits. Returns count label."""
        if agent_tasks is None or not configured(cfg) or self.state.get("unavailable"):
            return "no-tasks"
        self._prune(self._clock())
        changed = False
        for entry in list(self.state.get("tasks", [])):
            if not isinstance(entry, dict) or entry.get("reported"):
                continue
            task_id = str(entry.get("id", ""))
            if not task_id:
                continue
            try:
                task = agent_tasks.get(task_id)
            except Exception:
                continue
            if not isinstance(task, dict):
                continue
            status = task.get("status")
            requester = entry.get("requester", "")
            if requester not in cfg.get("owners", []):
                continue
            if status in TERMINAL_TASK_STATUS:
                try:
                    body = agent_tasks.report(task)
                except Exception:
                    body = ""
                if not body:
                    body = f"Task '{task.get('title', task_id)}' is {status}, Sir."
                subject = str(entry.get("subject") or "Jarvis task update")
                if not re.match(r"(?i)^re\s*:", subject):
                    subject = f"Re: {subject}"
                try:
                    self._deliver(
                        cfg,
                        self._build_reply(
                            requester,
                            cfg,
                            subject,
                            str(entry.get("msgid") or ""),
                            "",
                            body,
                        ),
                    )
                except Exception:
                    self._log("task follow-up send failed")
                    continue
                entry["reported"] = True
                changed = True
            elif status == TASK_WAITING_STATUS and not entry.get("waiting_told"):
                subject = str(entry.get("subject") or "Jarvis task update")
                if not re.match(r"(?i)^re\s*:", subject):
                    subject = f"Re: {subject}"
                title = str(task.get("title") or task_id)
                try:
                    self._deliver(
                        cfg,
                        self._build_reply(
                            requester,
                            cfg,
                            subject,
                            str(entry.get("msgid") or ""),
                            "",
                            f"The task '{title}' needs your approval "
                            "by voice before it acts, Sir — I haven't "
                            "approved anything myself.",
                        ),
                    )
                except Exception:
                    self._log("task follow-up send failed")
                    continue
                entry["waiting_told"] = True
                changed = True
        if changed:
            save_state(self.state, self.home)
        return "ok"

    # -- poll loop ------------------------------------------------------------

    def _fetch_raw(self, conn, uid: str) -> bytes | None:
        typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK" or not data:
            return None
        for part in data:
            if isinstance(part, tuple) and len(part) == 2:
                payload = part[1]
                if isinstance(payload, bytes) and payload:
                    return payload
        return None

    def poll_once(self) -> str:
        """One IMAP round: fetch unseen, process, mark seen. 'idle' unconfigured."""
        cfg = self._config()
        if not configured(cfg):
            return "idle"
        try:
            self.check_task_followups(cfg)
        except Exception:
            self._log("task check error")
        conn = self._connect_imap(cfg["imap_host"], cfg["imap_port"])
        try:
            try:
                conn.login(cfg["address"], cfg["password"])
            except imaplib.IMAP4.error as exc:
                raise MailAuthError(str(exc) or "login refused") from None
            typ, _ = conn.select("INBOX")
            if typ != "OK":
                raise imaplib.IMAP4.error("select INBOX refused")
            typ, data = conn.uid("SEARCH", None, "UNSEEN")
            if typ != "OK":
                raise imaplib.IMAP4.error("search refused")
            uids = (data[0].decode(errors="replace") if data else "").split()
            for uid in uids:
                try:
                    raw = self._fetch_raw(conn, uid)
                    if raw is None:
                        continue  # FETCH can fail transiently; retry unread mail.
                    if raw:
                        try:
                            parsed = email.message_from_bytes(
                                raw, policy=email.policy.default
                            )
                        except (ValueError, LookupError):
                            parsed = None
                        if parsed is not None:
                            self.process_message(cfg, parsed)
                    with contextlib.suppress(Exception):
                        conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
                except Exception:
                    self._log("uid error")
        finally:
            with contextlib.suppress(Exception):
                conn.logout()
        save_state(self.state, self.home)
        return "ok"

    def run(self, stop: threading.Event) -> None:
        """Poll until stop is set. Fail-soft: backoff, never raises."""
        failures = 0
        while not stop.is_set():
            cfg = self._config()
            if not configured(cfg):
                stop.wait(IDLE_S)
                continue
            try:
                self.poll_once()
                failures = 0
                stop.wait(POLL_S)
            except Exception as exc:
                failures += 1
                self._log(f"poll error {type(exc).__name__}")
                if _is_auth_error(exc):
                    stop.wait(AUTH_BACKOFF_S)
                else:
                    stop.wait(backoff_s(failures))


def start_thread(
    link: MailLink | None = None,
) -> tuple[threading.Thread, threading.Event]:
    """Run the sidecar in a daemon thread. Returns (thread, stop event)."""
    link = link or MailLink.from_env()
    stop = threading.Event()

    def _loop() -> None:
        with contextlib.suppress(Exception):
            link.run(stop)

    thread = threading.Thread(target=_loop, name="mail-link", daemon=True)
    thread.start()
    return thread, stop
