"""livekit-server vendoring + local config + env rewire (Phase 0).

Downloads the per-arch livekit-server binary to ``~/.jarvis/bin``
($JARVIS_LIVEKIT_BIN honors the shell's contract), writes a 0600
``~/.jarvis/livekit.yaml`` with a fresh static keypair (TURN off, room
empty timeout 60s), and rewires ``.env.local`` + ``frontend/.env.local``
so the whole stack talks to ``ws://127.0.0.1:7880`` instead of LiveKit
Cloud. Pure builders are unit-tested; download runs only on demand.

Never writes real keys anywhere but the user's own 0600 files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path

LIVEKIT_VERSION = "1.13.7"
ROOM_EMPTY_TIMEOUT_S = 60
DEFAULT_KEY_LEN = 32  # hex chars -> 128-bit secret each side


def arch_golang() -> str:
    """x86_64 -> amd64, aarch64 -> arm64 (Go release naming). Pure."""
    machine = os.uname().machine
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine


def download_url(version: str = LIVEKIT_VERSION, arch: str | None = None) -> str:
    """GitHub release asset URL for the current arch. Pure."""
    a = arch or arch_golang()
    return (
        f"https://github.com/livekit/livekit/releases/download/"
        f"v{version}/livekit_{version}_linux_{a}.tar.gz"
    )


def resolve_livekit_bin(jarvis_home: Path | str | None = None) -> Path:
    """$JARVIS_LIVEKIT_BIN > ~/.jarvis/bin/livekit-server. Pure."""
    override = os.environ.get("JARVIS_LIVEKIT_BIN", "").strip()
    if override:
        return Path(override)
    if jarvis_home is not None:
        return Path(jarvis_home) / "bin" / "livekit-server"
    home = os.environ.get("JARVIS_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / "bin" / "livekit-server"


def generate_keypair(rand=os.urandom) -> tuple[str, str]:
    """(api_key, api_secret) hex strings. api_key is short, secret is long."""
    api_key = "devkey"
    secret = rand(DEFAULT_KEY_LEN).hex()
    return api_key, secret


def build_livekit_yaml(api_key: str, api_secret: str) -> str:
    """Render livekit.yaml: loopback-only, static key, TURN off, 60s empty.

    Pure. Returns the exact file contents.
    """
    return (
        "# Jarvis local LiveKit server (auto-generated; never commit)\n"
        "port: 7880\n"
        "bind_addresses:\n"
        "  - 127.0.0.1\n"
        "keys:\n"
        f"  {api_key}: {api_secret}\n"
        "rtc:\n"
        "  use_external_ip: false\n"
        "  tcp_port: 7881\n"
        "room:\n"
        f"  empty_timeout: {ROOM_EMPTY_TIMEOUT_S}\n"
        "turn:\n"
        "  enabled: false\n"
    )


_ENV_PAT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def read_env(path: Path) -> dict[str, str]:
    """Parse a .env file (last value wins, quotes stripped). Pure."""
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            m = _ENV_PAT.match(line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if val[:1] == val[-1:] and val[:1] in ("'", '"') and len(val) >= 2:
                val = val[1:-1]
            out[key] = val
    except OSError:
        pass
    return out


def write_env(path: Path, updates: dict[str, str], template: str = "") -> None:
    """Merge updates into a .env file preserving other keys/comments.

    Pure-ish (file I/O). ``template`` is the file body to start from when
    the file does not exist yet.
    """
    existing = (
        path.read_text().splitlines()
        if path.exists()
        else (template.splitlines() if template else [])
    )
    keys_seen: set[str] = set()
    out: list[str] = []
    for line in existing:
        m = _ENV_PAT.match(line.strip())
        if m and m.group(1) in updates:
            key = m.group(1)
            if key in keys_seen:
                continue  # dedupe; last writer wins
            keys_seen.add(key)
            out.append(f"{key}={updates[key]}")
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in keys_seen:
            out.append(f"{key}={val}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")


def livekit_env_updates(url: str, api_key: str, api_secret: str) -> dict[str, str]:
    """The env keys the worker + frontend need. Pure."""
    return {
        "LIVEKIT_URL": url,
        "LIVEKIT_API_KEY": api_key,
        "LIVEKIT_API_SECRET": api_secret,
    }


def download_verify(url: str, dest: Path, *, expect_sha256: str | None = None) -> None:
    """Download ``url`` to ``dest`` with optional sha256 check. I/O."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    if expect_sha256:
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if digest.lower() != expect_sha256.lower():
            tmp.unlink(missing_ok=True)
            raise ValueError(f"sha256 mismatch for {url}: got {digest}")
    tmp.replace(dest)


def install_livekit_from_tarball(tarball: Path, dest: Path) -> None:
    """Extract livekit-server from the release tarball into ``dest``. I/O."""
    import tarfile

    with tarfile.open(tarball, "r:gz") as tf:
        names = tf.getnames()
        member = next((n for n in names if n.endswith("livekit-server")), None)
        if member is None:
            raise ValueError(f"livekit-server not in tarball: {names}")
        f = tf.extractfile(member)
        if f is None:
            raise ValueError(f"cannot read member {member}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(f.read())
        tmp.chmod(0o755)
        tmp.replace(dest)


def latest_release_tag(version: str = LIVEKIT_VERSION) -> str:
    """Validate the pinned version against GitHub. Returns the tag. I/O."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/livekit/livekit/releases/tags/v{version}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "jarvis-wizard",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode())
    return str(body.get("tag_name", ""))


def ensure_archive(dest: Path, version: str = LIVEKIT_VERSION) -> None:
    """Download + extract livekit-server to ``dest`` (idempotent). I/O."""
    if dest.is_file() and os.access(dest, os.X_OK):
        return
    tarball = dest.with_suffix(".tar.gz")
    if not tarball.is_file():
        download_verify(download_url(version), tarball)
    try:
        install_livekit_from_tarball(tarball, dest)
    finally:
        tarball.unlink(missing_ok=True)
