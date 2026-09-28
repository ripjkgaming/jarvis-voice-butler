#!/usr/bin/env python3
"""One-time Google Docs/Sheets/Drive connect: browser OAuth -> $JARVIS_HOME/google_token.json.

Usage:
    .venv/bin/python scripts/google_auth.py [--port PORT]
    .venv/bin/python scripts/google_auth.py --help

Opens Google's consent page in your browser (or prints the URL), listens on
loopback for the approval, exchanges the code for a refresh token, and saves
exactly what src/google_api.py reads: client_id, client_secret,
refresh_token, token_uri, scopes. The client id/secret are reused from the
existing Gmail token (same Google Cloud project) when present, else read
from GOOGLE_OAUTH_CLIENT_ID/SECRET in $JARVIS_HOME/keys.env.

Safe to run with --help any time; anything else needs Sir at the browser
to click consent (60s window).
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from google_api import SCOPES, jarvis_home, remember_account_email
from google_api import token_path as google_token_path

SCOPE = " ".join(SCOPES)


def _find_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _client_creds() -> tuple[str, str]:
    direct = (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
        os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
    )
    if all(direct):
        return direct
    try:
        saved = json.loads(
            (Path.home() / "jarvis" / "data" / "gmail_token.json").read_text()
        )
        if (
            isinstance(saved, dict)
            and saved.get("client_id")
            and saved.get("client_secret")
        ):
            return str(saved["client_id"]), str(saved["client_secret"])
    except (OSError, ValueError):
        pass
    keys: dict[str, str] = {}
    try:
        for line in (jarvis_home() / "keys.env").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                keys[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return keys.get("GOOGLE_OAUTH_CLIENT_ID", ""), keys.get(
        "GOOGLE_OAUTH_CLIENT_SECRET", ""
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=0, help="loopback port (0 = free port)")
    ap.add_argument(
        "--account",
        default="personal",
        choices=["personal", "school"],
        help="which Google account to connect (school = your secondary-school account)",
    )
    args = ap.parse_args()

    client_id, client_secret = _client_creds()
    if not client_id or not client_secret:
        raise SystemExit(
            "No OAuth client found, Sir: connect Gmail first, or put "
            "GOOGLE_OAUTH_CLIENT_ID/SECRET in $JARVIS_HOME/keys.env."
        )
    port = args.port or _find_free_port()

    code_box: dict[str, list[str]] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.urlparse(self.path).query
            code_box.update(urllib.parse.parse_qs(query))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Connected.</h1><p>Return to the terminal, Sir.</p>")
            done.set()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    redirect = f"http://127.0.0.1:{port}/"
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            # select_account: always show the picker so Sir can choose his
            # school account when connecting --account school.
            "prompt": "select_account consent",
        }
    )
    print("Opening the consent page in your browser...")
    print(auth_url)
    with contextlib.suppress(Exception):
        webbrowser.open(auth_url)
    print("Waiting for approval (180s)...")
    if not done.wait(timeout=180):
        raise SystemExit("Timed out. Re-run and approve faster, Sir.")
    server.shutdown()
    code = code_box.get("code", [""])[0]
    if not code:
        raise SystemExit(f"Google refused: {code_box}")

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode(
            {
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect,
                "grant_type": "authorization_code",
            }
        ).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        tokens = json.loads(resp.read().decode())
    if not tokens.get("refresh_token"):
        raise SystemExit("No refresh token returned. Remove prior consent and retry.")
    token_path = google_token_path(args.account)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(
        json.dumps(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": tokens["refresh_token"],
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": SCOPES,
            }
        )
    )
    token_path.chmod(0o600)
    print(f"Saved. Google ({args.account}) is connected: {token_path}")
    email = remember_account_email(args.account)
    if email:
        print(f"Account: {email}")


if __name__ == "__main__":
    main()
