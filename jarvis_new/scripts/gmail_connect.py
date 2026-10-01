#!/usr/bin/env python3
"""One-time Gmail connect: browser OAuth -> ~/jarvis/data/gmail_token.json.

Usage:
    uv run python scripts/gmail_connect.py --client-id ID --client-secret SECRET

Opens Google's consent page in your browser (or prints the URL), listens on
loopback for the approval, exchanges the code for a refresh token, and saves
exactly what src/system/inbox.py reads: client_id, client_secret,
refresh_token, token_uri. Afterwards Jarvis's gmail tools just work.
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

TOKEN_PATH = Path.home() / "jarvis" / "data" / "gmail_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
SCOPE = " ".join(SCOPES)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    code_box: dict[str, str] = {}
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

    server = http.server.HTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    redirect = f"http://127.0.0.1:{args.port}/"
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": args.client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    print("Opening the consent page in your browser...")
    print(auth_url)
    with contextlib.suppress(Exception):
        webbrowser.open(auth_url)
    print("Waiting for approval (60s)...")
    if not done.wait(timeout=60):
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
                "client_id": args.client_id,
                "client_secret": args.client_secret,
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
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(
        json.dumps(
            {
                "client_id": args.client_id,
                "client_secret": args.client_secret,
                "refresh_token": tokens["refresh_token"],
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": SCOPES,
            }
        )
    )
    TOKEN_PATH.chmod(0o600)
    print(f"Saved. Gmail is connected: {TOKEN_PATH}")


if __name__ == "__main__":
    main()
