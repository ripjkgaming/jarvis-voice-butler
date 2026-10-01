#!/usr/bin/env python3
"""One-time Mail Link setup for Jarvis.

Sir creates a dedicated Gmail account for Jarvis, then runs:

    uv run python scripts/mail_link_setup.py

The script checks IMAP + SMTP logins and saves the mailbox address, app
password, owner address(es) and optional PIN into $JARVIS_HOME/keys.env
(0600, other lines kept). Afterwards src/mail_link.py reads that mail,
runs Sir's commands, and replies with the results.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import imaplib
import os
import re
import smtplib
import ssl
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mail_link import keys_env_path

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
TIMEOUT = 20.0

MAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def jarvis_home() -> Path:
    """$JARVIS_HOME or ~/.jarvis. Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def valid_mail_shape(addr: str) -> bool:
    """Rough email shape check. Pure."""
    return bool(MAIL_RE.match((addr or "").strip()))


def parse_owner_list(raw: str) -> list[str]:
    """Comma-separated addresses -> lower-cased list, empties dropped. Pure."""
    return [p.strip().lower() for p in (raw or "").split(",") if p.strip()]


def upsert_env_line(text: str, key: str, value: str) -> str:
    """Set KEY=value in keys.env content, keeping other lines. Pure."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or any(
        c in value for c in "\r\n\0"
    ):
        raise ValueError("Invalid environment key or multiline value")
    lines = []
    entry = f"{key}={value}"
    hit = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(line)
            continue
        name, _, _ = stripped.partition("=")
        if name.strip() == key:
            if not hit:
                lines.append(entry)
            hit = True
        else:
            lines.append(line)
    if not hit:
        lines.append(entry)
    return "\n".join(lines) + "\n"


def save_keys(values: dict[str, str], home: Path) -> Path:
    """Upsert mail keys into keys.env (0600). Returns the path."""
    path = keys_env_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = path.read_text()
    except FileNotFoundError:
        current = ""
    for key, value in values.items():
        current = upsert_env_line(current, key, value)
    # mkstemp creates mode 0600 before any secret bytes reach disk.
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(current)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(name)
    return path


def test_imap(
    address: str,
    password: str,
    host: str = IMAP_HOST,
    port: int = IMAP_PORT,
    timeout: float = TIMEOUT,
) -> dict:
    """IMAP_SSL login + INBOX select. {ok, error?} — never the password."""
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": f"no answer ({type(exc).__name__})"}
    try:
        conn.login(address, password)
        typ, _ = conn.select("INBOX")
        if typ != "OK":
            return {"ok": False, "error": "INBOX would not open"}
        return {"ok": True}
    except imaplib.IMAP4.error:
        return {"ok": False, "error": "login or mailbox access refused"}
    except Exception as exc:
        return {"ok": False, "error": f"no answer ({type(exc).__name__})"}
    finally:
        with contextlib.suppress(Exception):
            conn.logout()


def test_smtp(
    address: str,
    password: str,
    host: str = SMTP_HOST,
    port: int = SMTP_PORT,
    timeout: float = TIMEOUT,
) -> dict:
    """SMTP_SSL login. {ok, error?} — never the password."""
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as smtp:
            smtp.login(address, password)
        return {"ok": True}
    except smtplib.SMTPException:
        return {"ok": False, "error": "SMTP login refused"}
    except Exception as exc:
        return {"ok": False, "error": f"no answer ({type(exc).__name__})"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--address", default="", help="Jarvis's mailbox address")
    ap.add_argument("--owners", default="", help="comma-separated owner addresses")
    args = ap.parse_args(argv)

    print("First, in a browser (use a Google account just for Jarvis):")
    print("  1. Create the account (the address below).")
    print("  2. Turn on 2-Step Verification for it.")
    print("  3. Create an App Password at")
    print("     https://myaccount.google.com/apppasswords")
    print("     (IMAP is on by default; the 16-letter code goes below).")
    print("Sir's own address stays personal — Jarvis only obeys mail from it.")
    try:
        address = (args.address or "").strip() or input("Jarvis's address: ").strip()
        if not valid_mail_shape(address):
            print("That doesn't look like an email address.")
            return 1
        password = "".join(
            getpass.getpass("App password for that mailbox (hidden): ").split()
        )
        if not password:
            print("No app password given.")
            return 1
        owners_raw = (args.owners or "").strip() or input(
            "Owner address(es), comma-separated: "
        ).strip()
        owners = parse_owner_list(owners_raw)
        if not owners or not all(valid_mail_shape(o) for o in owners):
            print("Give at least one valid owner address.")
            return 1
        if address.lower() in owners:
            print("Use your personal address as owner, separate from Jarvis's mailbox.")
            return 1
        pin = getpass.getpass("Optional PIN (hidden, empty for none): ").strip()
        if pin and (any(c.isspace() for c in pin) or any(c in pin for c in "\"'\0")):
            print("Use a PIN without whitespace or quotes.")
            return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 1

    print("Checking the IMAP login...")
    imap_result = test_imap(address, password)
    if not imap_result.get("ok"):
        print(f"IMAP refused it: {imap_result.get('error', '?')}")
        return 1
    print("Checking the SMTP login...")
    smtp_result = test_smtp(address, password)
    if not smtp_result.get("ok"):
        print(f"SMTP refused it: {smtp_result.get('error', '?')}")
        return 1

    home = jarvis_home()
    values = {
        "JARVIS_MAIL_LINK": "1",
        "JARVIS_MAIL_ADDRESS": address.lower(),
        "JARVIS_MAIL_PASSWORD": password,
        "JARVIS_MAIL_OWNERS": ",".join(owners),
        "JARVIS_MAIL_PIN": pin,
    }
    save_keys(values, home)
    print(f"Saved to {keys_env_path(home)}.")
    print(f"Jarvis will obey: {', '.join(owners)}.")
    print("PIN: " + ("set (every mail needs a PIN line)." if pin else "none."))
    print("Mail Link enabled. Start/restart the Jarvis bridge to host the listener.")
    print("Send a mail from an owner address with subject 'status' to try it.")
    if pin:
        print("Include PIN <your PIN> on a separate body line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
