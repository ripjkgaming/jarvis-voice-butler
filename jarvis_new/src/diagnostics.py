"""Jarvis diagnostics: a redacted support bundle (stdlib only).

Collects what a human needs to debug a sick install without ever shipping
a secret: bridge ``/status`` + ``/config`` (presence flags by design), the
last 200 lines of each sidecar log, ``wizard check`` output, and version
strings. Everything is scrubbed before it hits the tarball — exact secret
values from the environment, ``key=value`` pairs, and PEM blocks all become
``***REDACTED***``. Stdout carries exactly one line (the tarball path) so
the shell can parse it; all chatter goes to stderr::

    .venv/bin/python src/diagnostics.py [--out DIR]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SIDECAR_LOGS = ("livekit", "agent", "wake", "bridge")
TAIL_LINES = 200
WIZARD_TIMEOUT_S = 120.0
REDACTED = "***REDACTED***"

# `key = value` / `key: value` / `"key": "value"` with a secret-ish key and
# a value long enough to be real (booleans, counts, short names survive).
_SECRET_KV_RE = re.compile(
    r"""(?i)(["']?(?:api[_-]?key|api[_-]?secret|secret|token|password)["']?\s*[:=]\s*)(["']?)([^\s"'{},;\]\\]{8,})"""
)
# PEM private material: markers stay (useful), the base64 goes.
_PEM_RE = re.compile(
    r"(-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----).*?(-----END [A-Z0-9 ]*PRIVATE KEY-----)",
    re.DOTALL,
)
# Env names whose VALUES are treated as secrets for exact-match redaction.
_SECRET_NAME_RE = re.compile(r"(?i)(secret|api_?key|_token|token_|password|private)")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def jarvis_home() -> Path:
    home = _env("JARVIS_HOME").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def bridge_port() -> int:
    try:
        return int(_env("JARVIS_BRIDGE_PORT", "4317"))
    except ValueError:
        return 4317


def bridge_get(path: str, timeout: float = 5.0) -> dict | None:
    """GET one bridge route (Bearer when set). None when unreachable."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{bridge_port()}{path}")
        if token := _env("JARVIS_BRIDGE_TOKEN").strip():
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
        return body if isinstance(body, dict) else None
    except Exception:
        return None


def tail_lines(path: Path, n: int = TAIL_LINES) -> list[str]:
    """Last `n` lines of a file; [] when missing. Mirrors bridge._tail_log."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            step, buf = 65536, b""
            while len(buf.splitlines()) <= n and size > 0:
                step = min(step, size)
                size -= step
                fh.seek(size)
                buf = fh.read(step) + buf
                if size == 0:
                    break
        return buf.decode(errors="replace").splitlines()[-n:]
    except (OSError, ValueError):
        return []


def collect_secret_values() -> list[str]:
    """Secret values from the environment (exact-match redaction list).

    Only names that look secret-y and values long enough to match safely
    (>= 8 chars — short values false-positive on ordinary words).
    """
    vals = []
    for name, value in os.environ.items():
        if len(value) < 8:
            continue
        if _SECRET_NAME_RE.search(name):
            vals.append(value)
    vals.sort(key=len, reverse=True)
    return vals


def scrub(text: str, secrets: list[str] | None = None) -> str:
    """Redact secrets from bundle-bound text. Never raises."""
    try:
        out = text
        for value in secrets if secrets is not None else collect_secret_values():
            if value:
                out = out.replace(value, REDACTED)
        out = _SECRET_KV_RE.sub(rf"\1\2{REDACTED}", out)
        out = _PEM_RE.sub(rf"\1{REDACTED}\2", out)
        return out
    except Exception:
        return REDACTED


def run_wizard_check(timeout: float = WIZARD_TIMEOUT_S) -> dict:
    """Run `python -m wizard check` (cwd=repo, PYTHONPATH=src). Fail-soft."""
    try:
        env = dict(os.environ)
        src = str(REPO_ROOT / "src")
        env["PYTHONPATH"] = (
            src + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src
        )
        proc = subprocess.run(
            [sys.executable, "-m", "wizard", "check"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (proc.stdout + proc.stderr).splitlines()[-100:]
        return {"rc": proc.returncode, "output": "\n".join(output)}
    except Exception as exc:
        return {"rc": -1, "output": f"wizard check unavailable: {exc}"}


def _cargo_shell_version() -> str | None:
    try:
        import tomllib
    except ImportError:
        return None
    try:
        with (REPO_ROOT / "shell" / "src-tauri" / "Cargo.toml").open("rb") as fh:
            return str(tomllib.load(fh).get("package", {}).get("version"))
    except (OSError, ValueError):
        return None


def _prog_version(argv: list[str], timeout: float = 10.0) -> str | None:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        line = (proc.stdout + proc.stderr).strip().splitlines()
        return line[0][:120] if line else None
    except Exception:
        return None


def collect_versions() -> dict:
    """shell/venvs/livekit-server/frontend versions. All fail-soft."""
    import hashlib

    livekit_bin = _env("JARVIS_LIVEKIT_BIN").strip() or str(
        jarvis_home() / "bin" / "livekit-server"
    )
    ui_index = REPO_ROOT / "shell" / "ui" / "index.html"
    try:
        ui_id = hashlib.sha256(ui_index.read_bytes()).hexdigest()[:16]
    except OSError:
        ui_id = None
    try:
        frontend_pkg = json.loads((REPO_ROOT / "frontend" / "package.json").read_text())
        frontend_version = str(frontend_pkg.get("version"))
    except (OSError, ValueError):
        frontend_version = None
    return {
        "shell": _cargo_shell_version(),
        "agent_python": _prog_version(
            [str(REPO_ROOT / ".venv" / "bin" / "python"), "--version"]
        ),
        "wake_python": _prog_version(
            [str(REPO_ROOT / ".venv-wake" / "bin" / "python"), "--version"]
        ),
        "livekit_server": _prog_version([livekit_bin, "--version"]),
        "frontend": frontend_version,
        "frontend_ui": ui_id,
    }


def collect() -> dict:
    """Gather the bundle payload (unscrubbed; scrubbed at write time)."""
    home = jarvis_home()
    return {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bridge": {
            "status": bridge_get("/status"),
            "config": bridge_get("/config"),
        },
        "logs": {
            name: tail_lines(home / "logs" / f"{name}.log") for name in SIDECAR_LOGS
        },
        "wizard_check": run_wizard_check(),
        "versions": collect_versions(),
    }


def write_bundle(out_dir: Path | str | None = None) -> Path:
    """Collect, scrub, and write the tarball. Returns its path."""
    dest = Path(out_dir) if out_dir else jarvis_home()
    dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    prefix = f"jarvis-diagnostics-{stamp}"
    bundle = dest / f"{prefix}.tar.gz"
    data = collect()
    secrets = collect_secret_values()

    def _add(tf: tarfile.TarFile, arcname: str, text: str) -> None:
        blob = scrub(text, secrets).encode()
        info = tarfile.TarInfo(f"{prefix}/{arcname}")
        info.size = len(blob)
        info.mtime = int(time.time())
        tf.addfile(info, io.BytesIO(blob))

    with tarfile.open(bundle, "w:gz") as tf:
        _add(tf, "diagnostics.json", json.dumps(data, indent=2, default=str))
        for name, lines in data["logs"].items():
            _add(tf, f"logs/{name}.log", "\n".join(lines) + "\n")
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a redacted diagnostics bundle.")
    parser.add_argument("--out", default="", help="output dir (default $JARVIS_HOME)")
    args = parser.parse_args(argv)
    try:
        bundle = write_bundle(args.out or None)
    except Exception as exc:
        print(f"diagnostics failed: {exc}", file=sys.stderr)
        return 1
    print(bundle)  # stdout: exactly the tarball path (shell parses this)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
