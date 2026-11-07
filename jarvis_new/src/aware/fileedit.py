"""Always Aware journaled file executor (PLAN.md §5).

Reach: anywhere the uid can write under ``$HOME`` or ``/mnt/data``, minus a
hard secrets denylist. Every path is checked twice, lexically (after
``normpath``) and after resolving its parent's symlinks, so a link cannot
smuggle a write into ``~/.ssh`` or outside the allowed roots. The final
component is never followed: writing to, moving or deleting a symlink is
refused. Every op goes through the run's :class:`undo_journal.Journal`
(backup first, diff logged, deletes become moves into the journal trash),
and writes are atomic (tmp + rename inside the parent's directory fd).

Ops schema (``plan.targets["ops"]`` or derived from ``paths`` + ``dest``)::

    {"op": "mkdir",   "path": abs}
    {"op": "move",    "src": abs, "dst": abs}
    {"op": "write",   "path": abs, "content": str}
    {"op": "replace", "path": abs, "old": str, "new": str}
    {"op": "delete",  "path": abs}
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aware import state
from aware import undo_journal as uj

MAX_OPS = 500

# relative to $HOME
DENY_HOME_DIRS = (
    ".ssh",
    ".gnupg",
    ".local/share/keyrings",
    ".local/share/kwalletd",
    ".password-store",
    ".pki",
    ".aws",
    ".kube",
    ".docker",
    ".config/gh",
    ".config/hub",
    ".config/rclone",
    ".config/op",
    ".config/BraveSoftware",
    ".config/google-chrome",
    ".config/google-chrome-beta",
    ".config/chromium",
    ".config/microsoft-edge",
    ".config/vivaldi",
    ".config/opera",
    ".mozilla",
    ".librewolf",
    ".waterfox",
    ".var/app/com.brave.Browser",
    ".var/app/org.mozilla.firefox",
    ".var/app/com.google.Chrome",
    ".var/app/org.chromium.Chromium",
    ".var/app/com.microsoft.Edge",
    ".var/app/io.gitlab.librewolf-community",
    ".claude/.credentials.json",
    ".codex/auth.json",
)
# relative to $JARVIS_HOME
DENY_JARVIS = (
    "consent",
    "voice_secret",
    "keys.env",
    "bridge_token",
    "aware/journal",
    "aware/audit.jsonl",
    "aware/transactions.jsonl",
)
DENY_JARVIS_PREFIX = ("brave-profile", "brave-research-profile")
DENY_COMPONENTS = frozenset({".git", ".ssh", ".gnupg"})
SECRET_NAME = re.compile(
    r"(^\.env($|\.)|^\.netrc$|^\.pgpass$|^\.git-credentials$|^\.npmrc$|^\.pypirc$"
    r"|^id_(rsa|dsa|ecdsa|ed25519)"
    r"|\.(pem|key|p12|pfx|kdbx|keystore|jks|gpg)$"
    r"|(^|[._-])(token|tokens|secret|secrets|credentials?|passwords?)([._-]|$))",
    re.I,
)


class Denied(Exception):  # noqa: N818  (public API name)
    """The path is off limits (secret, outside roots, symlink, not writable)."""


class OpError(Exception):
    """An op could not be done as planned (nothing was changed by it)."""


class Stopped(Exception):  # noqa: N818
    """The watchdog or Sir stopped the run between ops."""


@dataclass(frozen=True)
class Policy:
    home: Path
    jarvis_home: Path
    roots: tuple[Path, ...] = field(default=())

    @classmethod
    def default(cls) -> Policy:
        home = Path.home()
        return cls(
            home=home, jarvis_home=state.jarvis_home(), roots=(home, Path("/mnt/data"))
        )


def _within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _deny_reason(path: Path, policy: Policy) -> str | None:
    roots = [Path(os.path.normpath(r)) for r in policy.roots]
    inside = [r for r in roots if _within(path, r)]
    if not inside:
        return "outside the allowed area"
    root = max(inside, key=lambda r: len(r.parts))
    for part in path.relative_to(root).parts:  # names below the root only
        if part in DENY_COMPONENTS:
            return f"protected directory {part}"
        if SECRET_NAME.search(part):
            return f"secret-looking name {part}"
    home = Path(os.path.normpath(policy.home))
    for rel in DENY_HOME_DIRS:
        if _within(path, home / rel):
            return f"secrets area ~/{rel}"
    jh = Path(os.path.normpath(policy.jarvis_home))
    for rel in DENY_JARVIS:
        if _within(path, jh / rel):
            return f"Jarvis secret {rel}"
    if _within(path, jh):
        first = path.relative_to(jh).parts[:1]
        if first and first[0].startswith(DENY_JARVIS_PREFIX):
            return "browser profile"
    return None


def _real(path: Path) -> Path:
    """Resolve every component except the last; missing tail kept lexically."""
    parent, tail = path.parent, [path.name]
    while not os.path.lexists(parent) and parent != parent.parent:
        tail.insert(0, parent.name)
        parent = parent.parent
    return Path(os.path.realpath(parent)).joinpath(*tail)


def _writable(real: Path) -> bool:
    probe = real if os.path.lexists(real) else real.parent
    while not os.path.lexists(probe) and probe != probe.parent:
        probe = probe.parent
    if not os.access(probe, os.W_OK):
        return False
    return not (os.path.lexists(real) and not os.access(real.parent, os.W_OK))


def check_path(path, policy: Policy | None = None, *, must_write: bool = True) -> Path:
    """The real, safe path for an op, or raise Denied. Never follows the last link."""
    policy = policy or Policy.default()
    raw = os.path.expanduser(str(path))
    if not os.path.isabs(raw):
        raise Denied(f"not an absolute path: {path}")
    lexical = Path(os.path.normpath(raw))
    reason = _deny_reason(lexical, policy)
    if reason:
        raise Denied(f"{lexical}: {reason}")
    real = _real(lexical)
    reason = _deny_reason(real, policy)
    if reason:
        raise Denied(f"{lexical}: {reason} (via a link)")
    if os.path.islink(real):
        raise Denied(f"{lexical}: is a symlink")
    if must_write and not _writable(real):
        raise Denied(f"{lexical}: not writable")
    return real


def _read_nofollow(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


class Editor:
    """Every change goes through the journal. One Editor per run."""

    def __init__(self, journal: uj.Journal, policy: Policy | None = None):
        self.journal = journal
        self.policy = policy or Policy.default()

    def _ensure_parent(self, real: Path) -> None:
        missing = []
        p = real.parent
        while not os.path.lexists(p):
            missing.insert(0, p)
            p = p.parent
        for d in missing:
            check_path(d, self.policy)
            os.mkdir(d, 0o755)
            self.journal.record_mkdir(d)

    def write_bytes(self, path, data: bytes) -> Path:
        real = check_path(path, self.policy)
        if os.path.lexists(real) and not stat.S_ISREG(os.lstat(real).st_mode):
            raise OpError(f"{real} is not a regular file")
        self._ensure_parent(real)
        self.journal.backup(real)
        before = _read_nofollow(real) if os.path.lexists(real) else None
        uj.atomic_write(real, data)
        self.journal.record_write(real, before=before, after=data)
        return real

    def write_text(self, path, text: str) -> Path:
        return self.write_bytes(path, text.encode())

    def replace(self, path, old: str, new: str, count: int = 1) -> Path:
        real = check_path(path, self.policy)
        try:
            text = _read_nofollow(real).decode()
        except (OSError, UnicodeDecodeError) as exc:
            raise OpError(f"cannot read {real}: {exc}") from exc
        if not old or old not in text:
            raise OpError(f"text to replace not found in {real}")
        return self.write_bytes(
            real, text.replace(old, new, count if count > 0 else -1).encode()
        )

    def move(self, src, dst) -> Path:
        rs = check_path(src, self.policy)
        rd = check_path(dst, self.policy)
        if not os.path.lexists(rs):
            raise OpError(f"{rs} does not exist")
        if os.path.lexists(rd):
            raise OpError(f"{rd} already exists")
        self._ensure_parent(rd)
        self.journal.move(rs, rd)
        return rd

    def delete(self, path) -> Path:
        real = check_path(path, self.policy)
        if not os.path.lexists(real):
            raise OpError(f"{real} does not exist")
        return self.journal.trash(real)

    def mkdir(self, path) -> Path:
        real = check_path(path, self.policy)
        if os.path.isdir(real):
            return real
        if os.path.lexists(real):
            raise OpError(f"{real} exists and is not a directory")
        self._ensure_parent(real)
        os.mkdir(real, 0o755)
        self.journal.record_mkdir(real)
        return real


def derive_ops(targets: dict) -> list[dict]:
    """The op list for a file plan: explicit ops, else move paths into dest."""
    targets = targets or {}
    ops = targets.get("ops")
    if isinstance(ops, list):
        return [dict(o) for o in ops if isinstance(o, dict)]
    paths, dest = targets.get("paths") or [], targets.get("dest")
    if not paths or not dest:
        return []
    out = [{"op": "mkdir", "path": str(dest)}]
    for p in paths:
        out.append({"op": "move", "src": str(p), "dst": str(Path(dest) / Path(p).name)})
    return out


def apply_ops(
    editor: Editor,
    ops: list[dict],
    *,
    on_event: Callable[[str, str], None] | None = None,
    should_stop: Callable[[], str | None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Run ops in order. Raises Denied/OpError/Stopped on the first problem."""
    if len(ops) > MAX_OPS:
        raise OpError(f"{len(ops)} ops is over the {MAX_OPS} limit")
    done = 0
    for op in ops:
        if should_stop:
            why = should_stop()
            if why:
                raise Stopped(why)
        kind = op.get("op")
        try:
            if kind == "mkdir":
                target = editor.mkdir(op["path"])
            elif kind == "move":
                target = editor.move(op["src"], op["dst"])
            elif kind == "write":
                target = editor.write_text(op["path"], str(op.get("content", "")))
            elif kind == "replace":
                target = editor.replace(
                    op["path"], str(op.get("old", "")), str(op.get("new", ""))
                )
            elif kind == "delete":
                target = Path(op["path"])
                editor.delete(target)
            else:
                raise OpError(f"unknown op {kind!r}")
        except KeyError as exc:
            raise OpError(f"op {kind} is missing {exc}") from exc
        except uj.TooLarge as exc:
            raise OpError(f"{exc.filename} is too large to back up") from exc
        except FileExistsError as exc:
            raise OpError(f"{exc.filename} already exists") from exc
        done += 1
        if on_event:
            on_event("action", f"{kind} {target}")
        if on_progress:
            on_progress(done, len(ops))
    return done


def _primary(op: dict) -> list[str]:
    if op.get("op") == "move":
        return [str(op.get("src")), str(op.get("dst"))]
    return [str(op.get("path"))]


def verify_ops(
    ops: list[dict], *, before: dict | None = None, expect: dict | None = None
) -> tuple[bool, str]:
    """Check the end state on disk against what the ops meant. Independent of
    any claim the executor made. Returns (ok, evidence)."""
    before = before or {}
    checks: list[tuple[str, bool]] = []
    for i, op in enumerate(ops):
        later = {p for o in ops[i + 1 :] for p in _primary(o)}
        if any(p in later for p in _primary(op)):
            continue  # a later op owns this path's final state
        kind = op.get("op")
        if kind == "mkdir":
            p = Path(op["path"])
            checks.append((f"{p} is a folder", p.is_dir() and not p.is_symlink()))
        elif kind == "move":
            src, dst = Path(op["src"]), Path(op["dst"])
            ok = os.path.lexists(dst) and not os.path.lexists(src)
            want = before.get(str(src))
            if ok and want:
                ok = uj.sha256_file(dst) == want
            checks.append((f"{dst.name} moved to {dst.parent}", ok))
        elif kind == "write":
            p = Path(op["path"])
            want = uj.sha256_bytes(str(op.get("content", "")).encode())
            checks.append((f"{p} written", uj.sha256_file(p) == want))
        elif kind == "replace":
            p = Path(op["path"])
            try:
                ok = str(op.get("new", "")) in _read_nofollow(p).decode()
            except (OSError, UnicodeDecodeError):
                ok = False
            checks.append((f"{p} edited", ok))
        elif kind == "delete":
            p = Path(op["path"])
            checks.append((f"{p} removed", not os.path.lexists(p)))
    for raw, want in (expect or {}).items():
        p = Path(raw)
        if want == "exists":
            ok = os.path.lexists(p)
        elif want == "absent":
            ok = not os.path.lexists(p)
        elif want == "dir":
            ok = p.is_dir()
        else:
            ok = uj.sha256_file(p) == want
        checks.append((f"{p} is {want if len(str(want)) < 20 else 'as expected'}", ok))
    if not checks:
        return False, "nothing to check"
    failed = [name for name, ok in checks if not ok]
    passed = len(checks) - len(failed)
    if failed:
        return False, f"{passed} of {len(checks)} checks pass; failed: " + "; ".join(
            failed[:5]
        )
    return True, f"{passed} of {len(checks)} checks pass"
