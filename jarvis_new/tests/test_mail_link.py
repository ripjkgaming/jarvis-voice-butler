"""Tests for the Mail Link sidecar + setup (all fakes, no network)."""

import base64
import hashlib
import imaplib
import importlib.util
import json
import re
import stat
import sys
from datetime import datetime, timezone
from email import utils as eut
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, "src")

import mail_link as ml
from mail_link import MailLink

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

NOW = 1_700_000_000.0
JARVIS = "jarvis@example.com"
SIR = "sir@example.com"
AUTH_PASS = (
    "mx.google.com; dkim=pass header.i=@example.com header.s=x; "
    "spf=pass smtp.mailfrom=example.com; "
    "dmarc=pass header.from=example.com"
)


def load_setup():
    spec = importlib.util.spec_from_file_location(
        "mail_link_setup", SCRIPTS / "mail_link_setup.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def date_hdr(offset_s: float = -60.0) -> str:
    moment = datetime.fromtimestamp(NOW + offset_s, tz=timezone.utc)
    return eut.format_datetime(moment)


def make_raw(
    *,
    frm: str = SIR,
    subject: str = "status",
    body: str = "status",
    offset_s: float = -60.0,
    msgid: str | None = "<m1@example.com>",
    auth: str | None = AUTH_PASS,
    extra_auth: tuple[str, ...] = (),
    auto: str | None = None,
    precedence: str | None = None,
    listid: bool = False,
    html_only: bool = False,
    no_date: bool = False,
    bad_date: bool = False,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = JARVIS
    msg["Subject"] = subject
    if not no_date:
        msg["Date"] = "not a date" if bad_date else date_hdr(offset_s)
    if msgid is not None:
        msg["Message-ID"] = msgid
    if auth is not None:
        msg["Authentication-Results"] = auth
    for extra in extra_auth:
        msg["Authentication-Results"] = extra
    if auto is not None:
        msg["Auto-Submitted"] = auto
    if precedence is not None:
        msg["Precedence"] = precedence
    if listid:
        msg["List-ID"] = "Jarvis <jarvis.example.com>"
    if html_only:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    return msg.as_bytes()


class FakeIMAP:
    """Minimal IMAP4_SSL double: login/select/uid/logout only."""

    def __init__(
        self,
        messages: dict[str, bytes] | None = None,
        login_error: Exception | None = None,
        always_unseen: bool = False,
    ) -> None:
        self.messages = dict(messages or {})
        self.login_error = login_error
        self.always_unseen = always_unseen
        self.seen: list[str] = []
        self.selected: str | None = None
        self.logged_out = False
        self.logins = 0

    def login(self, user: str, password: str):
        self.logins += 1
        if self.login_error is not None:
            raise self.login_error
        return ("OK", [b"ok"])

    def select(self, mbox: str):
        self.selected = mbox
        return ("OK", [b"1"])

    def uid(self, cmd: str, *args):
        if cmd == "SEARCH":
            uids = [
                u for u in self.messages if self.always_unseen or u not in self.seen
            ]
            return ("OK", [" ".join(uids).encode()])
        if cmd == "FETCH":
            raw = self.messages[args[0]]
            return ("OK", [(f"{args[0]} (BODY[] {{{len(raw)}}}".encode(), raw), b")"])
        if cmd == "STORE":
            if args[0] not in self.seen:
                self.seen.append(args[0])
            return ("OK", [b""])
        raise AssertionError(f"unexpected imap {cmd}")

    def logout(self):
        self.logged_out = True
        return ("OK", [b""])


class FakeBridge:
    """Callable bridge double recording (method, path, body)."""

    def __init__(self, handler=None) -> None:
        self.handler = handler or self._default
        self.calls: list[tuple[str, str, dict | None]] = []

    @staticmethod
    def _default(method: str, path: str, body: dict | None):
        text = (body or {}).get("text", "")
        if path == "/route":
            return {"ok": True, "reply": f"routed:{text}"}
        if path == "/chat":
            return {"ok": True, "reply": f"chatted:{text}"}
        if path == "/sys":
            return {"ok": True}
        if path == "/tool":
            return {"ok": False, "error": "nope"}
        raise AssertionError(f"unexpected bridge {path}")

    def __call__(self, method: str, path: str, body: dict | None = None):
        self.calls.append((method, path, body))
        return self.handler(method, path, body)

    def routes(self) -> list[dict | None]:
        return [b for m, p, b in self.calls if p == "/route"]

    def chats(self) -> list[dict | None]:
        return [b for m, p, b in self.calls if p == "/chat"]


BASE_CFG = {
    "enabled": True,
    "address": JARVIS,
    "password": "pw",
    "owners": [SIR],
    "pin": "",
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "authserv": "mx.google.com",
}


def make_link(
    tmp_path: Path,
    *,
    sent: list | None = None,
    bridge=None,
    imap: FakeIMAP | None = None,
    clock=None,
    **cfg_over,
):
    cfg = dict(BASE_CFG)
    cfg.update(cfg_over)
    sent_list: list = sent if sent is not None else []
    fake_bridge = bridge if bridge is not None else FakeBridge()
    fake_imap = imap if imap is not None else FakeIMAP({})
    link = MailLink(
        config=cfg,
        home=tmp_path,
        connect_imap=lambda h, p: fake_imap,
        send_mail=sent_list.append,
        bridge=fake_bridge,
        clock=clock or (lambda: NOW),
    )
    return link, sent_list, fake_bridge, fake_imap


def reply_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain",))
    return part.get_content() if part is not None else ""


def actions_log() -> str:
    import system

    try:
        return system.LOG_PATH.read_text()
    except OSError:
        return ""


class FakeTasks:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def start(self, goal: str, background: bool = True) -> dict:
        task = {
            "id": f"t{len(self.store) + 1}",
            "title": goal[:60],
            "goal": goal,
            "status": "running",
        }
        self.store[task["id"]] = task
        return task

    def get(self, task_id: str) -> dict | None:
        return self.store.get(task_id)

    def report(self, task: dict) -> str:
        return f"# {task.get('title')}\nGoal: {task.get('goal')}"


# --- auth-results parser -------------------------------------------------------


def test_auth_dmarc_pass() -> None:
    assert ml.auth_results_ok([AUTH_PASS], "mx.google.com", "example.com") is True


def test_auth_dkim_aligned_and_parent() -> None:
    header = "mx.google.com; dkim=pass header.d=example.com header.s=x"
    assert ml.auth_results_ok([header], "mx.google.com", "example.com") is True
    # Parent domain of the From domain aligns too.
    assert ml.auth_results_ok([header], "mx.google.com", "sub.example.com") is True
    header_i = "mx.google.com; dkim=pass header.i=@example.com"
    assert ml.auth_results_ok([header_i], "mx.google.com", "example.com") is True


def test_auth_spoofed_second_header_ignored() -> None:
    forged = "mx.google.com; dmarc=pass header.from=example.com"
    real = "mx.google.com; dmarc=fail header.from=example.com"
    # The FIRST matching header (the receiver's) rules; the forged lower
    # one must not rescue the mail.
    assert ml.auth_results_ok([real, forged], "mx.google.com", "example.com") is False
    assert ml.auth_results_ok([forged], "mx.google.com", "example.com") is True


def test_auth_other_authserv_headers_ignored() -> None:
    other = "forwarder.example; dmarc=pass header.from=example.com"
    assert ml.auth_results_ok([other], "mx.google.com", "example.com") is False
    assert (
        ml.auth_results_ok(
            [other, "mx.google.com; dmarc=pass header.from=example.com"],
            "mx.google.com",
            "example.com",
        )
        is True
    )


def test_auth_unrelated_dkim_domain() -> None:
    header = "mx.google.com; dkim=pass header.d=evil.com header.s=x"
    assert ml.auth_results_ok([header], "mx.google.com", "example.com") is False
    # A sibling subdomain does not align either.
    sibling = "mx.google.com; dkim=pass header.d=sub.example.com"
    assert ml.auth_results_ok([sibling], "mx.google.com", "example.com") is False


def test_auth_missing_or_failing() -> None:
    assert ml.auth_results_ok([], "mx.google.com", "example.com") is False
    assert (
        ml.auth_results_ok(
            ["mx.google.com; dmarc=fail header.from=example.com; spf=fail"],
            "mx.google.com",
            "example.com",
        )
        is False
    )
    # dmarc=fail but an aligned dkim=pass still accepts (OR rule).
    both = (
        "mx.google.com; dmarc=fail header.from=example.com; "
        "dkim=pass header.d=example.com"
    )
    assert ml.auth_results_ok([both], "mx.google.com", "example.com") is True


def test_auth_case_and_whitespace() -> None:
    header = "  MX.Google.COM  ;\n  DMARC = PASS  header.from=example.com"
    assert ml.auth_results_ok([header], "mx.google.com", "example.com") is True
    dkim = "Mx.Google.Com; Dkim=Pass Header.D=EXAMPLE.COM"
    assert ml.auth_results_ok([dkim], "mx.google.com", "example.com") is True
    assert ml.auth_results_ok([""], "mx.google.com", "example.com") is False
    assert ml.auth_results_ok([AUTH_PASS], "", "example.com") is False
    assert ml.auth_results_ok([AUTH_PASS], "mx.google.com", "") is False


# --- sender checks -------------------------------------------------------------


def test_owner_allowlist_case_insensitive(tmp_path: Path) -> None:
    link, sent, _, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(frm="Sir@Example.COM", msgid="<o1@e>")}
    assert link.poll_once() == "ok"
    assert len(sent) == 1


def test_unknown_sender_rejected_silently(tmp_path: Path) -> None:
    link, sent, bridge, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(frm="intruder@evil.com", msgid="<bad@e>")}
    assert link.poll_once() == "ok"
    assert sent == [] and bridge.calls == []
    assert fake.seen == ["1"]  # marked seen even though rejected
    assert "rejected not-owner" in actions_log()


def test_multiple_from_rejected(tmp_path: Path) -> None:
    link, sent, _, fake = make_link(tmp_path)
    fake.messages = {
        "1": make_raw(frm="sir@example.com, pal@example.com", msgid="<m@e>")
    }
    assert link.poll_once() == "ok"
    assert sent == []


def test_own_address_skipped(tmp_path: Path) -> None:
    link, sent, _, fake = make_link(tmp_path, owners=[SIR, JARVIS])
    fake.messages = {"1": make_raw(frm=JARVIS, msgid="<self@e>")}
    assert link.poll_once() == "ok"
    assert sent == []
    assert "rejected self" in actions_log()


def test_auto_submitted_list_and_precedence_skipped(tmp_path: Path) -> None:
    for kwargs, msgid in [
        ({"auto": "auto-replied"}, "<a1@e>"),
        ({"auto": "auto-generated"}, "<a2@e>"),
        ({"precedence": "bulk"}, "<a3@e>"),
        ({"precedence": "list"}, "<a4@e>"),
        ({"precedence": "junk"}, "<a5@e>"),
        ({"listid": True}, "<a6@e>"),
    ]:
        link, sent, _, fake = make_link(tmp_path)
        fake.messages = {"1": make_raw(msgid=msgid, **kwargs)}
        assert link.poll_once() == "ok"
        assert sent == [], kwargs
    assert actions_log().count("rejected auto") == 6


def test_auto_submitted_no_is_allowed(tmp_path: Path) -> None:
    link, sent, _, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(msgid="<ok@e>", auto="no")}
    assert link.poll_once() == "ok"
    assert len(sent) == 1


# --- freshness + replay ----------------------------------------------------------


def test_freshness_window(tmp_path: Path) -> None:
    good = [(-7199.0, True), (-60.0, True), (599.0, True)]
    bad = [(-7201.0, False), (601.0, False)]
    for i, (offset, accepted) in enumerate(good + bad):
        link, sent, _, fake = make_link(tmp_path)
        fake.messages = {
            "1": make_raw(msgid=f"<f{i}@e>", offset_s=offset, body="status")
        }
        link.poll_once()
        assert (len(sent) == 1) == accepted, offset
    assert actions_log().count("rejected stale") == len(bad)


def test_missing_or_invalid_date_rejected(tmp_path: Path) -> None:
    for kwargs, msgid in [
        ({"no_date": True}, "<d1@e>"),
        ({"bad_date": True}, "<d2@e>"),
    ]:
        link, sent, _, fake = make_link(tmp_path)
        fake.messages = {"1": make_raw(msgid=msgid, **kwargs)}
        link.poll_once()
        assert sent == []
    assert actions_log().count("rejected stale") == 2


def test_message_id_replay_and_missing(tmp_path: Path) -> None:
    link, sent, _, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(msgid="<once@e>")}
    link.poll_once()
    assert len(sent) == 1
    # The server offers the same mail again: silent, no second reply.
    fake2 = FakeIMAP({"9": make_raw(msgid="<once@e>")}, always_unseen=True)
    link._connect_imap = lambda h, p: fake2
    link.poll_once()
    assert len(sent) == 1
    assert "rejected replay" in actions_log()
    # A mail with no Message-ID at all is rejected too.
    link2, sent2, _, fake3 = make_link(tmp_path)
    fake3.messages = {"1": make_raw(msgid=None)}
    link2.poll_once()
    assert sent2 == []


def test_msgids_persisted(tmp_path: Path) -> None:
    link, _, _, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(msgid="<p@e>")}
    link.poll_once()
    saved = json.loads((tmp_path / "mail_link.json").read_text())
    assert "<p@e>" in saved["msgids"]


# --- PIN -------------------------------------------------------------------------


def test_pin_required_wrong_and_ok(tmp_path: Path) -> None:
    missing, sent, _, fake = make_link(tmp_path, pin="s3cret")
    fake.messages = {"1": make_raw(msgid="<n1@e>", body="status")}
    missing.poll_once()
    assert sent == []
    wrong, sent2, _, fake2 = make_link(tmp_path, pin="s3cret")
    fake2.messages = {"1": make_raw(msgid="<n2@e>", body="PIN nope\nstatus")}
    wrong.poll_once()
    assert sent2 == []
    assert actions_log().count("rejected pin") == 2


def test_pin_body_ok_stripped_and_not_echoed(tmp_path: Path) -> None:
    link, sent, bridge, fake = make_link(tmp_path, pin="s3cret")
    fake.messages = {
        "1": make_raw(msgid="<p1@e>", subject="cmds", body="PIN s3cret\nvolume to 30")
    }
    link.poll_once()
    assert len(sent) == 1
    assert bridge.routes() == [{"text": "volume to 30"}]
    assert "s3cret" not in reply_text(sent[0])
    assert "s3cret" not in actions_log()


def test_pin_colon_form_and_subject(tmp_path: Path) -> None:
    link, sent, _, fake = make_link(tmp_path, pin="s3cret")
    fake.messages = {
        "1": make_raw(msgid="<p2@e>", subject="cmds", body="pin: s3cret\nstatus")
    }
    assert link.poll_once() == "ok"
    assert len(sent) == 1
    link2, sent2, _, fake2 = make_link(tmp_path, pin="s3cret")
    fake2.messages = {
        "1": make_raw(msgid="<p3@e>", subject="PIN s3cret", body="status")
    }
    assert link2.poll_once() == "ok"
    assert len(sent2) == 1
    assert "s3cret" not in reply_text(sent2[0])


# --- body reading --------------------------------------------------------------


def test_quote_stripping_and_command_limits(tmp_path: Path) -> None:
    link, sent, bridge, fake = make_link(tmp_path)
    body = (
        "volume to 30\n"
        "> quoted old line\n"
        "  > indented quote\n"
        "On Tue, Bob <b@x> wrote:\n"
        "old stuff\n"
    )
    fake.messages = {"1": make_raw(msgid="<q@e>", subject="ignored", body=body)}
    link.poll_once()
    assert bridge.routes() == [{"text": "volume to 30"}]
    assert "▸ volume to 30" in reply_text(sent[0])
    assert "quoted" not in reply_text(sent[0])


def test_quote_variants(tmp_path: Path) -> None:
    body = "play some music\n-- \nSig Name\n-----Original Message-----\nold"
    link, _sent, bridge, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(msgid="<q2@e>", subject="s", body=body)}
    link.poll_once()
    assert bridge.routes() == [{"text": "play some music"}]


def test_max_five_commands_and_trim(tmp_path: Path) -> None:
    link, sent, bridge, fake = make_link(tmp_path)
    lines = [f"cmd{i} " + "x" * 600 for i in range(7)]
    fake.messages = {"1": make_raw(msgid="<m5@e>", subject="s", body="\n".join(lines))}
    link.poll_once()
    assert len(bridge.routes()) == 5
    assert all(len(b["text"]) == 500 for b in bridge.routes())
    assert reply_text(sent[0]).count("▸ ") == 5


def test_subject_fallback_and_prefixes(tmp_path: Path) -> None:
    link, _sent, bridge, fake = make_link(tmp_path)
    fake.messages = {
        "1": make_raw(msgid="<s@e>", subject="Re: Fwd: volume to 30", body="   \n")
    }
    link.poll_once()
    assert bridge.routes() == [{"text": "volume to 30"}]
    # Subject is ignored whenever the body has commands.
    link2, sent2, bridge2, fake2 = make_link(tmp_path)
    fake2.messages = {
        "1": make_raw(msgid="<s2@e>", subject="DELETE EVERYTHING", body="status")
    }
    link2.poll_once()
    assert bridge2.routes() == []  # "status" is handled locally
    assert "DELETE" not in reply_text(sent2[0])


def test_html_only_body(tmp_path: Path) -> None:
    link, _sent, bridge, fake = make_link(tmp_path)
    fake.messages = {
        "1": make_raw(
            msgid="<h@e>",
            subject="s",
            body="<p>volume to <b>30</b> &amp; friends</p>",
            html_only=True,
        )
    }
    link.poll_once()
    assert bridge.routes() == [{"text": "volume to 30 & friends"}]


# --- rate limit --------------------------------------------------------------------


def test_rate_limit_notice_once_then_silent(tmp_path: Path) -> None:
    now = [NOW]
    seed = load_state_for(tmp_path)
    seed["accepted_ts"] = [NOW - 10.0] * 30
    (tmp_path / "mail_link.json").write_text(json.dumps(seed))
    link, sent, _, fake = make_link(tmp_path, clock=lambda: now[0])
    fake.messages = {"1": make_raw(msgid="<r1@e>", body="status")}
    link.poll_once()
    assert len(sent) == 1
    assert "Rate limit reached" in reply_text(sent[0])
    # A second mail inside the hour: silent.
    fake.messages = {"2": make_raw(msgid="<r2@e>", body="status")}
    link.poll_once()
    assert len(sent) == 1
    # After the window passes, mail flows again.
    now[0] += 7200.0
    fake.messages = {"3": make_raw(msgid="<r3@e>", body="status", offset_s=7140.0)}
    link.poll_once()
    assert len(sent) == 2
    assert "Rate limit" not in reply_text(sent[1])


def load_state_for(home: Path) -> dict:
    return {
        "msgids": [],
        "accepted_ts": [],
        "confirms": [],
        "tasks": [],
        "rate_notice_at": None,
    }


# --- sensitive commands + confirm ----------------------------------------------------


def confirm_flow(tmp_path: Path, owners=(SIR, "lady@example.com")):
    link, sent, bridge, fake = make_link(tmp_path, owners=list(owners))
    fake.messages = {"1": make_raw(msgid="<c1@e>", body="unlock the laptop")}
    link.poll_once()
    assert len(sent) == 1
    assert bridge.routes() == [] and bridge.chats() == []
    match = re.search(r"CONFIRM (\d{6})", reply_text(sent[0]))
    assert match, reply_text(sent[0])
    return link, sent, bridge, fake, match.group(1)


def test_sensitive_issues_confirm_and_hides_code(tmp_path: Path) -> None:
    _link, sent, _, _, _ = confirm_flow(tmp_path)
    saved = json.loads((tmp_path / "mail_link.json").read_text())
    assert len(saved["confirms"]) == 1
    entry = saved["confirms"][0]
    assert entry["command"] == "unlock the laptop"
    assert entry["requester"] == SIR
    # Only the hash is stored — the plaintext code is nowhere on disk.
    raw = (tmp_path / "mail_link.json").read_text()
    code = re.search(r"CONFIRM (\d{6})", reply_text(sent[0])).group(1)
    assert code not in raw
    assert entry["hash"] == hashlib.sha256(code.encode()).hexdigest()
    # And the code is not in the action log either.
    assert code not in actions_log()


def test_confirm_success_runs_stored_command(tmp_path: Path) -> None:
    link, sent, bridge, fake, code = confirm_flow(tmp_path)
    fake.messages = {"2": make_raw(msgid="<c2@e>", body=f"confirm {code}")}
    link.poll_once()
    assert bridge.routes() == [{"text": "unlock the laptop"}]
    assert "routed:unlock the laptop" in reply_text(sent[1])
    # Single use: the same code is dead now.
    fake.messages = {"3": make_raw(msgid="<c3@e>", body=f"confirm {code}")}
    before = len(bridge.routes())
    link.poll_once()
    assert len(bridge.routes()) == before
    assert "doesn't match" in reply_text(sent[2])


def test_confirm_wrong_code_other_requester_expired(tmp_path: Path) -> None:
    link, sent, bridge, fake, code = confirm_flow(tmp_path)
    # Wrong code.
    fake.messages = {"2": make_raw(msgid="<w@e>", body="confirm 000000")}
    link.poll_once()
    assert bridge.routes() == [] and "doesn't match" in reply_text(sent[1])
    # The right code from a different owner.
    fake.messages = {
        "3": make_raw(msgid="<o@e>", frm="lady@example.com", body=f"confirm {code}")
    }
    link.poll_once()
    assert bridge.routes() == [] and "doesn't match" in reply_text(sent[2])
    # The right code from the right owner still works afterwards.
    fake.messages = {"4": make_raw(msgid="<r@e>", body=f"confirm {code}")}
    link.poll_once()
    assert bridge.routes() == [{"text": "unlock the laptop"}]


def test_confirm_expired(tmp_path: Path) -> None:
    now = [NOW]
    link, sent, bridge, fake = make_link(tmp_path, owners=[SIR], clock=lambda: now[0])
    fake.messages = {"1": make_raw(msgid="<e1@e>", body="type hello")}
    link.poll_once()
    code = re.search(r"CONFIRM (\d{6})", reply_text(sent[0])).group(1)
    now[0] += 11 * 60.0
    fake.messages = {"2": make_raw(msgid="<e2@e>", body=f"confirm {code}")}
    link.poll_once()
    assert bridge.routes() == []
    assert "doesn't match" in reply_text(sent[1])


# --- route / chat / bridge -----------------------------------------------------------


def test_route_hit_skips_chat(tmp_path: Path) -> None:
    link, sent, bridge, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(msgid="<v@e>", body="volume to 30")}
    link.poll_once()
    assert bridge.routes() == [{"text": "volume to 30"}]
    assert bridge.chats() == []
    assert "routed:volume to 30" in reply_text(sent[0])


def test_chat_fallback_and_warning(tmp_path: Path) -> None:
    def handler(method: str, path: str, body):
        if path == "/route":
            return {"ok": False, "error": "no-route"}
        if path == "/chat":
            return {"ok": True, "reply": "Chatted."}
        return {"ok": True}

    link, sent, _bridge, fake = make_link(tmp_path, bridge=FakeBridge(handler))
    fake.messages = {"1": make_raw(msgid="<c@e>", body="tell me a story")}
    link.poll_once()
    assert "Chatted." in reply_text(sent[0])

    def warning_handler(method: str, path: str, body):
        if path == "/route":
            return {"ok": False, "error": "no-route"}
        if path == "/chat":
            return {"ok": True, "reply": "", "warning": "Quota low."}
        return {"ok": True}

    link2, sent2, _, fake2 = make_link(tmp_path, bridge=FakeBridge(warning_handler))
    fake2.messages = {"1": make_raw(msgid="<w2@e>", body="tell me a story")}
    link2.poll_once()
    assert "Quota low." in reply_text(sent2[0])


def test_bridge_offline(tmp_path: Path) -> None:
    offline = FakeBridge(lambda m, p, b: None)
    link, sent, _, fake = make_link(tmp_path, bridge=offline)
    fake.messages = {"1": make_raw(msgid="<o@e>", body="volume to 30")}
    link.poll_once()
    assert "bridge is offline" in reply_text(sent[0])
    assert "cmd 'volume to 30' ok=False" in actions_log()


def test_help_and_status(tmp_path: Path) -> None:
    link, sent, bridge, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(msgid="<help@e>", body="help")}
    link.poll_once()
    text = reply_text(sent[0])
    assert "play some music" in text and "screenshot" in text
    assert bridge.calls == []


def test_status_formatting(tmp_path: Path) -> None:
    def handler(method: str, path: str, body):
        assert path == "/sys"
        return {
            "ok": True,
            "load_1_5_15": ["0.10", "0.20", "0.30"],
            "cpu_count": 8,
            "mem_bytes": {"MemTotal": 8 * 1024**3, "MemAvailable": 2 * 1024**3},
            "home_free_bytes": 50 * 1024**3,
            "cpu_temp_c": 55.5,
            "laptop_power": {
                "battery": 80,
                "status": "Discharging",
                "ac": False,
                "watts": 12.3,
            },
            "mode": "normal",
            "call_live": True,
        }

    link, sent, _, fake = make_link(tmp_path, bridge=FakeBridge(handler))
    fake.messages = {"1": make_raw(msgid="<st@e>", body="status")}
    link.poll_once()
    text = reply_text(sent[0])
    assert "0.10" in text and "8 cores" in text
    assert "55.5" in text and "80%" in text and "AC off" in text
    assert "normal" in text and "live" in text

    sparse = FakeBridge(lambda m, p, b: {"ok": True})
    link2, sent2, _, fake2 = make_link(tmp_path, bridge=sparse)
    fake2.messages = {"1": make_raw(msgid="<st2@e>", body="status")}
    link2.poll_once()
    assert "—" in reply_text(sent2[0])
    assert "None" not in reply_text(sent2[0])


def test_status_bridge_down(tmp_path: Path) -> None:
    link, sent, _, fake = make_link(tmp_path, bridge=FakeBridge(lambda m, p, b: None))
    fake.messages = {"1": make_raw(msgid="<st3@e>", body="status")}
    link.poll_once()
    assert "didn't answer" in reply_text(sent[0])


# --- screenshots -----------------------------------------------------------------------


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def shot_handler(png: bytes):
    def handler(method: str, path: str, body):
        if path == "/tool":
            assert body["tool"] == "screenshot"
            return {"ok": True, "image_b64": base64.b64encode(png).decode()}
        return {"ok": True}

    return handler


def test_screenshot_attached(tmp_path: Path) -> None:
    link, sent, _, fake = make_link(tmp_path, bridge=FakeBridge(shot_handler(PNG)))
    fake.messages = {"1": make_raw(msgid="<shot@e>", body="screenshot")}
    link.poll_once()
    files = list(sent[0].iter_attachments())
    assert len(files) == 1
    assert files[0].get_content_type() == "image/png"
    assert files[0].get_content() == PNG


def test_screenshot_named_output_and_tool_error(tmp_path: Path) -> None:
    seen_args: list = []

    def handler(method: str, path: str, body):
        if path == "/tool":
            seen_args.append(body)
            return {"ok": True, "path": "/nonexistent/shot.png"}
        return {"ok": True}

    link, sent, _, fake = make_link(tmp_path, bridge=FakeBridge(handler))
    fake.messages = {"1": make_raw(msgid="<shot2@e>", body="screenshot HDMI-A-1")}
    link.poll_once()
    assert seen_args == [{"tool": "screenshot", "args": {"output": "HDMI-A-1"}}]
    assert list(sent[0].iter_attachments()) == []
    assert "didn't come back" in reply_text(sent[0])

    failing = FakeBridge(lambda m, p, b: {"ok": False, "error": "no camera"})
    link2, sent2, _, fake2 = make_link(tmp_path, bridge=failing)
    fake2.messages = {"1": make_raw(msgid="<shot3@e>", body="screenshot")}
    link2.poll_once()
    assert "no camera" in reply_text(sent2[0])


def test_screenshot_too_large_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ml, "SCREENSHOT_MAX_BYTES", 10)
    link, sent, _, fake = make_link(tmp_path, bridge=FakeBridge(shot_handler(PNG)))
    fake.messages = {"1": make_raw(msgid="<shot4@e>", body="screenshot")}
    link.poll_once()
    assert list(sent[0].iter_attachments()) == []
    assert "too large" in reply_text(sent[0])


# --- background tasks --------------------------------------------------------------------


def test_task_start_and_terminal_followup_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = FakeTasks()
    monkeypatch.setattr(ml, "agent_tasks", tasks)
    link, sent, _, fake = make_link(tmp_path)
    fake.messages = {
        "1": make_raw(
            msgid="<t1@e>",
            subject="please research",
            body="task: research fusion and summarise",
        )
    }
    link.poll_once()
    assert len(sent) == 1
    assert "Task started: research fusion and summarise" in reply_text(sent[0])
    saved = json.loads((tmp_path / "mail_link.json").read_text())
    assert saved["tasks"][0]["id"] == "t1"
    assert saved["tasks"][0]["msgid"] == "<t1@e>"
    # Still running: no follow-up.
    link.poll_once()
    assert len(sent) == 1
    # Done: one follow-up in the same thread, then silence.
    tasks.store["t1"]["status"] = "done"
    link.poll_once()
    assert len(sent) == 2
    assert "Goal: research fusion" in reply_text(sent[1])
    assert sent[1]["In-Reply-To"] == "<t1@e>"
    link.poll_once()
    assert len(sent) == 2


def test_task_waiting_needs_voice_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = FakeTasks()
    monkeypatch.setattr(ml, "agent_tasks", tasks)
    link, sent, _, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(msgid="<t2@e>", body="task: book a flight")}
    link.poll_once()
    tasks.store["t1"]["status"] = "waiting"
    link.poll_once()
    assert len(sent) == 2
    assert "approval" in reply_text(sent[1]).lower()
    assert "voice" in reply_text(sent[1]).lower()
    link.poll_once()
    assert len(sent) == 2


# --- reply shape ---------------------------------------------------------------------------


def test_reply_threading_headers(tmp_path: Path) -> None:
    link, sent, _, fake = make_link(tmp_path)
    raw_msg = EmailMessage()
    raw_msg["From"] = SIR
    raw_msg["To"] = JARVIS
    raw_msg["Subject"] = "volume question"
    raw_msg["Date"] = date_hdr()
    raw_msg["Message-ID"] = "<th@e>"
    raw_msg["References"] = "<older@e>"
    raw_msg["Authentication-Results"] = AUTH_PASS
    raw_msg.set_content("status")
    fake.messages = {"1": raw_msg.as_bytes()}
    link.poll_once()
    reply = sent[0]
    assert reply["Subject"] == "Re: volume question"
    assert reply["In-Reply-To"] == "<th@e>"
    assert "<older@e>" in reply["References"] and "<th@e>" in reply["References"]
    assert reply["Auto-Submitted"] == "auto-replied"
    assert reply["To"] == SIR and reply["From"] == JARVIS
    assert reply_text(reply).endswith("— J.A.R.V.I.S.\n")


def test_reply_subject_no_double_re(tmp_path: Path) -> None:
    link, sent, _, fake = make_link(tmp_path)
    fake.messages = {
        "1": make_raw(msgid="<re@e>", subject="Re: status update", body="")
    }
    link.poll_once()
    assert sent[0]["Subject"] == "Re: status update"


# --- end to end ------------------------------------------------------------------------------


def test_end_to_end_mixed_poll(tmp_path: Path) -> None:
    link, sent, _bridge, fake = make_link(tmp_path)
    fake.messages = {
        "1": make_raw(frm="spoof@evil.com", msgid="<spoof@e>", body="unlock all"),
        "2": make_raw(msgid="<good@e>", subject="g", body="help"),
    }
    assert link.poll_once() == "ok"
    # The spoof got nothing; the good mail got exactly one reply.
    assert len(sent) == 1
    assert sent[0]["To"] == SIR
    # Both are marked seen; the connection is logged out.
    assert fake.seen == ["1", "2"]
    assert fake.logged_out is True
    assert fake.selected == "INBOX"


def test_backoff_and_auth_classification() -> None:
    assert ml.backoff_s(1) == 1.0
    assert ml.backoff_s(3) == 4.0
    assert ml.backoff_s(99) == ml.BACKOFF_CAP_S
    auth_err = imaplib.IMAP4.error("[AUTHENTICATIONFAILED] bad credentials")
    assert ml._is_auth_error(auth_err) is True
    assert ml._is_auth_error(ml.MailAuthError("login refused")) is True
    assert ml._is_auth_error(OSError("boom")) is False
    assert ml._is_auth_error(imaplib.IMAP4.error("search refused")) is False


def test_auth_failure_raises_mail_auth_error(tmp_path: Path) -> None:
    bad = FakeIMAP({}, login_error=imaplib.IMAP4.error("[AUTHENTICATIONFAILED] nope"))
    link, _, _, _ = make_link(tmp_path, imap=bad)
    with pytest.raises(ml.MailAuthError):
        link.poll_once()


def test_idle_when_not_configured(tmp_path: Path) -> None:
    def boom(host: str, port: int):
        raise AssertionError("no network when idle")

    link = MailLink(
        config=dict(BASE_CFG, owners=[]),
        home=tmp_path,
        connect_imap=boom,
        send_mail=lambda m: None,
        bridge=FakeBridge(),
        clock=lambda: NOW,
    )
    assert link.poll_once() == "idle"


def test_load_config_env_and_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS_MAIL_LINK", "1")
    for key in (
        "JARVIS_MAIL_ADDRESS",
        "JARVIS_MAIL_PASSWORD",
        "JARVIS_MAIL_OWNERS",
        "JARVIS_MAIL_PIN",
    ):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "keys.env").write_text(
        "JARVIS_MAIL_ADDRESS=jarvis@example.com\n"
        "JARVIS_MAIL_PASSWORD=pw\n"
        "JARVIS_MAIL_OWNERS=Sir@Example.com\n"
    )
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    cfg = ml.load_config(tmp_path)
    assert cfg["address"] == "jarvis@example.com"
    assert cfg["owners"] == ["sir@example.com"]
    assert ml.configured(cfg) is True
    monkeypatch.setenv("JARVIS_MAIL_OWNERS", "a@x.com, B@X.com")
    assert ml.load_config(tmp_path)["owners"] == ["a@x.com", "b@x.com"]
    assert ml.configured({}) is False


def test_start_thread_idle_stops_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as _time

    monkeypatch.setattr(ml, "IDLE_S", 0.01)
    link = MailLink(
        config=dict(BASE_CFG, owners=[]),
        home=tmp_path,
        connect_imap=lambda h, p: (_ for _ in ()).throw(AssertionError("net")),
        send_mail=lambda m: None,
        bridge=FakeBridge(),
        clock=lambda: NOW,
    )
    thread, stop = ml.start_thread(link)
    try:
        _time.sleep(0.05)
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


# --- setup script ------------------------------------------------------------------------------


def test_setup_upsert_and_shapes() -> None:
    setup = load_setup()
    out = setup.upsert_env_line(
        "A=1\nJARVIS_MAIL_ADDRESS=old\n# c\n", "JARVIS_MAIL_ADDRESS", "new@x.com"
    )
    assert "A=1" in out and "# c" in out and "JARVIS_MAIL_ADDRESS=new@x.com" in out
    assert out.count("JARVIS_MAIL_ADDRESS") == 1
    assert setup.valid_mail_shape("jarvis@example.com") is True
    assert setup.valid_mail_shape("nope") is False
    assert setup.valid_mail_shape("a@b") is False
    assert setup.parse_owner_list("A@x.com, b@y.com ") == ["a@x.com", "b@y.com"]


def test_setup_save_keys_preserves_and_locks(tmp_path: Path) -> None:
    setup = load_setup()
    (tmp_path / "keys.env").write_text("OTHER=1\n")
    setup.save_keys({"JARVIS_MAIL_ADDRESS": "j@x.com"}, tmp_path)
    text = (tmp_path / "keys.env").read_text()
    assert "OTHER=1" in text and "JARVIS_MAIL_ADDRESS=j@x.com" in text
    assert stat.S_IMODE((tmp_path / "keys.env").stat().st_mode) == 0o600


def test_setup_imap_and_smtp_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    setup = load_setup()

    class GoodIMAP:
        def __init__(self, *a, **k) -> None:
            pass

        def login(self, u: str, p: str) -> None:
            assert u == "j@x.com"

        def select(self, mbox: str):
            return ("OK", [b"1"])

        def logout(self) -> None:
            pass

    class BadIMAP(GoodIMAP):
        def login(self, u: str, p: str) -> None:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] bad")

    class GoodSMTP:
        def __init__(self, *a, **k) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a) -> bool:
            return False

        def login(self, u: str, p: str) -> None:
            pass

    monkeypatch.setattr(setup.imaplib, "IMAP4_SSL", GoodIMAP)
    assert setup.test_imap("j@x.com", "pw") == {"ok": True}
    monkeypatch.setattr(setup.imaplib, "IMAP4_SSL", BadIMAP)
    assert setup.test_imap("j@x.com", "pw")["ok"] is False
    monkeypatch.setattr(setup.smtplib, "SMTP_SSL", GoodSMTP)
    assert setup.test_smtp("j@x.com", "pw") == {"ok": True}


@pytest.mark.parametrize(
    "header",
    [
        "mx.google.com; dmarc=pass header.from=evil.com",
        'mx.google.com; dmarc=fail reason="dmarc=pass" header.from=example.com',
        "mx.google.com; dmarc=fail (dmarc=pass) header.from=example.com",
        'mx.google.com; dmarc=fail reason="x; dkim=pass header.d=example.com"',
    ],
)
def test_auth_rejects_mismatched_or_embedded_pass(header):
    assert not ml.auth_results_ok([header], "mx.google.com", "example.com")


def test_replay_state_must_be_saved_before_acting(tmp_path, monkeypatch):
    link, sent, bridge, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(body="volume to 30")}
    monkeypatch.setattr(ml, "save_state", lambda *a: False)
    link.poll_once()
    assert bridge.calls == []
    assert sent == []


def test_failed_smtp_still_counts_executed_commands(tmp_path):
    link, _, bridge, fake = make_link(tmp_path)

    def fail(msg):
        raise OSError("offline")

    link._send_mail_fn = fail
    fake.messages = {"1": make_raw(body="volume to 30")}
    link.poll_once()
    assert len(bridge.routes()) == 1
    saved = json.loads((tmp_path / "mail_link.json").read_text())
    assert saved["accepted_ts"] == [NOW]
    link2, _, bridge2, fake2 = make_link(tmp_path)
    fake2.messages = fake.messages
    link2.poll_once()
    assert bridge2.calls == []


def test_successful_loop_waits_between_polls(tmp_path):
    link, _, _, fake = make_link(tmp_path)

    class Stop:
        def __init__(self):
            self.stopped = False
            self.delays = []

        def is_set(self):
            return self.stopped or fake.logins > 2

        def wait(self, seconds):
            self.delays.append(seconds)
            self.stopped = True

    stop = Stop()
    link.run(stop)
    assert fake.logins == 1
    assert stop.delays == [ml.POLL_S]


def test_credentials_alone_do_not_enable_remote_commands(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_MAIL_LINK", raising=False)
    (tmp_path / "keys.env").write_text(
        "JARVIS_MAIL_ADDRESS=jarvis@example.com\n"
        "JARVIS_MAIL_PASSWORD=pw\nJARVIS_MAIL_OWNERS=sir@example.com\n"
    )
    assert not ml.configured(ml.load_config(tmp_path))
    monkeypatch.setenv("JARVIS_MAIL_LINK", "1")
    assert ml.configured(ml.load_config(tmp_path))
    monkeypatch.setenv("JARVIS_MAIL_LINK", "0")
    assert not ml.configured(ml.load_config(tmp_path))


def test_subject_pin_is_never_executed_or_echoed(tmp_path):
    link, sent, bridge, fake = make_link(tmp_path, pin="secret")
    fake.messages = {"1": make_raw(subject="PIN secret", body="status")}
    link.poll_once()
    assert bridge.routes() == []
    assert "secret" not in sent[0]["Subject"]
    fake.messages = {"2": make_raw(msgid="<pinonly@e>", subject="PIN secret", body="")}
    link.poll_once()
    assert bridge.routes() == []
    assert len(sent) == 1


def test_removed_owner_gets_no_task_followups(tmp_path, monkeypatch):
    tasks = FakeTasks()
    monkeypatch.setattr(ml, "agent_tasks", tasks)
    link, sent, _, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(body="task: research test")}
    link.poll_once()
    tasks.store["t1"]["status"] = "done"
    link.check_task_followups(dict(BASE_CFG, owners=["new@example.com"]))
    assert len(sent) == 1


def test_corrupt_state_fails_closed(tmp_path):
    (tmp_path / "mail_link.json").write_text("{broken")
    link, sent, bridge, fake = make_link(tmp_path)
    fake.messages = {"1": make_raw(body="volume to 30")}
    link.poll_once()
    assert bridge.calls == [] and sent == []
    assert (tmp_path / "mail_link.json").read_text() == "{broken"


@pytest.mark.parametrize(
    "route_result",
    [
        {"ok": True, "reply": ""},
        {"ok": False, "error": "action failed"},
        None,
    ],
)
def test_route_outcome_is_not_reexecuted_in_chat(tmp_path, route_result):
    link, _, bridge, fake = make_link(
        tmp_path, bridge=FakeBridge(lambda m, p, b: route_result)
    )
    fake.messages = {"1": make_raw(body="volume to 30")}
    link.poll_once()
    assert len(bridge.routes()) == 1
    assert bridge.chats() == []


def test_setup_replaces_duplicate_keys_and_rejects_newlines():
    setup = load_setup()
    out = setup.upsert_env_line("K=old\nK=older\n", "K", "new")
    assert out.count("K=") == 1
    with pytest.raises(ValueError):
        setup.upsert_env_line("", "K", "value\nBAD=1")


def test_bridge_http_no_route_reaches_chat_fallback(tmp_path, monkeypatch):
    import io
    import urllib.error
    import urllib.request

    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if req.full_url.endswith("/route"):
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "Not found",
                {},
                io.BytesIO(b'{"ok":false,"error":"no-route"}'),
            )
        return io.BytesIO(b'{"ok":true,"reply":"Chat answered."}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ml, "bridge_token", lambda: "test-token")
    link, sent, _, fake = make_link(tmp_path, bridge=ml.bridge_call)
    fake.messages = {"1": make_raw(body="tell me a story")}
    link.poll_once()
    assert "Chat answered" in reply_text(sent[0])
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_bridge_http_failures_do_not_retry_commands(tmp_path, monkeypatch, status):
    import io
    import urllib.error
    import urllib.request

    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(
            req.full_url,
            status,
            "Failure",
            {},
            io.BytesIO(b'{"ok":false,"error":"no-route"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    link, _, _, fake = make_link(tmp_path, bridge=ml.bridge_call)
    fake.messages = {"1": make_raw(body="volume to 30")}
    link.poll_once()
    assert len(calls) == 1


def test_setup_main_enables_only_after_both_logins(tmp_path, monkeypatch, capsys):
    setup = load_setup()
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    secrets = iter(["mail password", "mypin"])
    monkeypatch.setattr(setup.getpass, "getpass", lambda _: next(secrets))
    checks = []

    def check(address, password):
        checks.append(address)
        assert password == "mailpassword"
        return {"ok": True}

    monkeypatch.setattr(setup, "test_imap", check)
    monkeypatch.setattr(setup, "test_smtp", check)
    assert setup.main(["--address", JARVIS, "--owners", SIR]) == 0
    assert checks == [JARVIS, JARVIS]
    cfg = ml.parse_keys_env((tmp_path / "keys.env").read_text())
    assert cfg["JARVIS_MAIL_LINK"] == "1"
    assert cfg["JARVIS_MAIL_PIN"] == "mypin"
    assert "mailpassword" not in capsys.readouterr().out


def test_setup_failed_login_leaves_existing_keys_untouched(tmp_path, monkeypatch):
    setup = load_setup()
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    path = tmp_path / "keys.env"
    path.write_text("OTHER=keep\n")
    secrets = iter(["password", ""])
    monkeypatch.setattr(setup.getpass, "getpass", lambda _: next(secrets))
    monkeypatch.setattr(setup, "test_imap", lambda *args: {"ok": False})
    monkeypatch.setattr(setup, "test_smtp", lambda *args: pytest.fail("SMTP called"))
    assert setup.main(["--address", JARVIS, "--owners", SIR]) == 1
    assert path.read_text() == "OTHER=keep\n"


def test_setup_unreadable_keys_are_not_replaced(tmp_path, monkeypatch):
    setup = load_setup()
    path = tmp_path / "keys.env"
    path.write_text("OTHER=keep\n")

    def denied(*args, **kwargs):
        raise PermissionError("unreadable")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", denied)
        with pytest.raises(PermissionError):
            setup.save_keys({"JARVIS_MAIL_LINK": "1"}, tmp_path)
    assert path.read_text() == "OTHER=keep\n"


def test_html_commands_preserve_lines_and_ignore_quoted_commands(tmp_path):
    raw = make_raw(
        html_only=True,
        body="<div>volume to 30</div><div>open youtube</div><blockquote><div>open calculator</div></blockquote>",
    )
    link, _, bridge, _ = make_link(tmp_path, imap=FakeIMAP({"1": raw}))
    link.poll_once()
    assert bridge.routes() == [{"text": "volume to 30"}, {"text": "open youtube"}]


def test_temporary_fetch_failure_keeps_mail_unread_for_retry(tmp_path):
    class FlakyIMAP(FakeIMAP):
        failed = False

        def uid(self, command, *args):
            if command == "FETCH" and not self.failed:
                self.failed = True
                return "NO", [b"Temporary failure"]
            return super().uid(command, *args)

    fake = FlakyIMAP({"1": make_raw(body="open youtube")})
    link, sent, bridge, _ = make_link(tmp_path, imap=fake)
    link.poll_once()
    assert fake.seen == []
    link.poll_once()
    assert bridge.routes() == [{"text": "open youtube"}]
    assert len(sent) == 1


def test_routed_action_failure_is_not_logged_as_success(tmp_path):
    bridge = FakeBridge(
        lambda *args: {"ok": True, "action": {"ok": False}, "reply": "Launch failed."}
    )
    link, *_ = make_link(tmp_path, bridge=bridge)
    assert link._route_chat(BASE_CFG, "open youtube") == ("Launch failed.", False)


def test_chat_error_reply_is_not_logged_as_success(tmp_path):
    bridge = FakeBridge(
        lambda method, path, body: (
            {"error": "no-route"}
            if path == "/route"
            else {"ok": False, "reply": "Unavailable."}
        )
    )
    link, *_ = make_link(tmp_path, bridge=bridge)
    assert link._route_chat(BASE_CFG, "tell me a story") == ("Unavailable.", False)


def test_confirmation_storage_failure_does_not_offer_unusable_code(
    tmp_path, monkeypatch
):
    link, *_ = make_link(tmp_path)
    monkeypatch.setattr(ml, "save_state", lambda *args: False)
    text = link._offer_confirm(SIR, "unlock", NOW)
    assert "unavailable" in text.lower()
    assert "CONFIRM" not in text
    assert link.state["confirms"] == []
