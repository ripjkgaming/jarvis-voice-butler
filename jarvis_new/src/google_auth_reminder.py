"""Renew Jarvis's personal Google OAuth test grant before its seven-day expiry.

The helper checks the personal token file's modification time, which is
updated only when scripts/google_auth.py saves a newly approved grant. Five
days after that save, it notifies the owner and starts that existing consent
flow. If it is not approved, it retries once per day. No token or scope is
changed without Google's normal browser consent.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

CHECK_INTERVAL_S = 15 * 60
REMIND_AFTER_S = 5 * 24 * 60 * 60
RETRY_AFTER_S = 24 * 60 * 60

_check_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("JARVIS_GOOGLE_AUTH_REMINDER", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _home() -> Path:
    value = os.environ.get("JARVIS_HOME", "").strip()
    return Path(value) if value else Path.home() / ".jarvis"


def _load_state(path: Path) -> dict:
    if path.is_symlink():
        raise OSError("OAuth reminder state is a symlink")
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError("OAuth reminder state is invalid")
    return value


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("OAuth reminder state is a symlink")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _log(detail: str) -> None:
    with contextlib.suppress(Exception):
        from system import log_action

        log_action("google-auth-reminder", detail)


def _notify() -> None:
    import notify

    notify.send(
        "Jarvis Google access is due to renew. I am opening Google in your browser. "
        "Approve the saved account; if Google shows an unverified-app warning, "
        "use Advanced and continue, then approve the requested access.",
        title="Google reconnect",
        kind="google-auth-renewal",
        source="google-auth-reminder",
        urgency="info",
        fingerprint="google-auth-renewal",
    )


def check_once(*, now: float | None = None) -> bool:
    """Start one due reconnect. Returns True only when the command is launched."""
    if not enabled() or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    now = time.time() if now is None else float(now)
    with _check_lock:
        try:
            from google_api import token_path

            token = token_path("personal")
            if token.is_symlink() or not token.is_file():
                return False
            token_mtime_ns = token.stat().st_mtime_ns
            authorized_at = token_mtime_ns / 1_000_000_000
            if now - authorized_at < REMIND_AFTER_S:
                return False

            state_path = _home() / "google_auth_reminder.json"
            state = _load_state(state_path)
            last_token_mtime = state.get("attempted_token_mtime_ns")
            last_attempt = state.get("last_attempt_at", 0)
            if (
                last_token_mtime == token_mtime_ns
                and isinstance(last_attempt, (int, float))
                and now - last_attempt < RETRY_AFTER_S
            ):
                return False

            root = Path(__file__).resolve().parent.parent
            auth_script = root / "scripts" / "google_auth.py"
            if not auth_script.is_file():
                _log("auth script missing")
                return False

            try:
                from phone_session import private

                if private():
                    return False
            except Exception:
                # Unknown privacy state is not a reason to open a consent page.
                return False

            # Persist the retry window before launching, preventing duplicate
            # consent windows if the bridge restarts during an OAuth attempt.
            _save_state(
                state_path,
                {
                    "attempted_token_mtime_ns": token_mtime_ns,
                    "last_attempt_at": now,
                },
            )
            try:
                _notify()
            except PermissionError:
                # Do not open an OAuth page during a private phone session.
                _save_state(state_path, state)
                return False
            except Exception as exc:
                _log(f"notification unavailable: {type(exc).__name__}")

            try:
                subprocess.Popen(
                    [sys.executable, str(auth_script), "--account", "personal"],
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=True,
                )
            except Exception:
                _save_state(state_path, state)
                raise
            _log("personal reconnect opened")
            return True
        except Exception as exc:
            _log(f"check failed: {type(exc).__name__}")
            return False


def start_thread(interval: float = CHECK_INTERVAL_S):
    """Run the due check in the bridge, without blocking voice or startup."""
    stop = threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            check_once()
            if stop.wait(interval):
                break

    thread = threading.Thread(target=_loop, name="google-auth-reminder", daemon=True)
    thread.start()
    return thread, stop
