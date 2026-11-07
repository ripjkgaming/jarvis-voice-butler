"""Tiny registry of long-running jobs (tests, builds, terminal commands).

Each job is ~/.jarvis/jobs/<id>.json: {id, title, status, started,
finished, summary, failures}. Tools that start work call start(); when it
ends they call finish(). The proactive jobs source announces finished
jobs ("the tests finished, 2 failures"). Never raises.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from pathlib import Path

from goodnight_fence import admission


def jobs_dir() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(h) if h else Path.home() / ".jarvis") / "jobs"


def _path(job_id: str) -> Path:
    return jobs_dir() / f"{re.sub(r'[^A-Za-z0-9_-]', '_', job_id)[:80]}.json"


def _write(job: dict) -> None:
    with contextlib.suppress(OSError):
        jobs_dir().mkdir(parents=True, exist_ok=True)
        tmp = _path(job["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(job))
        os.replace(tmp, _path(job["id"]))


def _activity_id(job_id: str) -> str:
    """HUD activity-feed id for a job (path-safe slug, <= 64 chars)."""
    return "job-" + re.sub(r"[^a-z0-9]", "", job_id[-32:].lower())


def _mirror_start(job_id: str, title: str, kind: str) -> None:
    """Show a running job on the reactor progress ring (indeterminate: a
    test/build run has no known length). Fail-soft, never blocks the job."""
    with contextlib.suppress(Exception):
        import activity

        activity.start(
            "build" if kind in ("tests", "build") else "task",
            title,
            source="jobs",
            item_id=_activity_id(job_id),
        )


def _mirror_finish(job_id: str, ok: bool, summary: str) -> None:
    with contextlib.suppress(Exception):
        import activity

        activity.finish(_activity_id(job_id), ok=ok, detail=summary[:200] or None)


@admission
def start(title: str, kind: str = "job", now: float | None = None) -> str:
    now = time.time() if now is None else now
    # Concurrent builds can start within the same millisecond. Keep the kind
    # readable but reserve space for an independent identity in the filename.
    prefix = re.sub(r"[^A-Za-z0-9_-]", "_", kind)[:24]
    job_id = f"{prefix}-{int(now * 1000)}-{secrets.token_hex(16)}"
    _write(
        {
            "id": job_id,
            "kind": kind,
            "title": title[:120],
            "status": "running",
            "started": now,
        }
    )
    if get(job_id) is None:
        raise RuntimeError("Job registration failed; no work admitted.")
    _mirror_start(job_id, title, kind)
    return job_id


def finish(
    job_id: str,
    ok: bool,
    summary: str = "",
    failures: int = 0,
    now: float | None = None,
) -> None:
    job = get(job_id) or {"id": job_id, "title": job_id, "started": now or time.time()}
    job.update(
        status="done" if ok else "failed",
        finished=time.time() if now is None else now,
        summary=" ".join(str(summary).split())[:400],
        failures=int(failures),
    )
    _write(job)
    _mirror_finish(job_id, ok, job["summary"])


def get(job_id: str) -> dict | None:
    try:
        data = json.loads(_path(job_id).read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def recent(limit: int = 30) -> list[dict]:
    out = []
    with contextlib.suppress(OSError):
        for p in sorted(
            jobs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:limit]:
            with contextlib.suppress(OSError, ValueError):
                data = json.loads(p.read_text())
                if isinstance(data, dict):
                    out.append(data)
    return out


# Additive canonical lifecycle registry; legacy JSON feed functions are unchanged.
ID_RE = re.compile(r"job-[0-9a-f]{32}\Z")
OWNER_RE = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")
SCOPES = {"monitor_observation", "adapter_work"}
OUTCOMES = {
    "succeeded",
    "failed",
    "cancelled",
    "ended_unknown",
    "monitoring_stopped",
    "blocked",
}
MAX_PAYLOAD = 16384


class JobError(Exception):
    def __init__(self, message: str, code: int = 503):
        super().__init__(message)
        self.code = code


def public(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "payload"}


def _payload(value: dict) -> str:
    if not isinstance(value, dict):
        raise JobError("Invalid job metadata.", 400)
    try:
        raw = json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise JobError("Invalid job metadata.", 400) from exc
    if len(raw.encode()) > MAX_PAYLOAD:
        raise JobError("Job metadata exceeded bounds.", 400)
    return raw


def _decode(raw):
    if not isinstance(raw, str) or len(raw.encode()) > MAX_PAYLOAD + 2048:
        raise JobError("Job record exceeded bounds.")
    try:
        row = json.loads(raw)
        required = {
            "id",
            "owner",
            "native_id",
            "scope",
            "label",
            "lifecycle",
            "outcome",
            "created_at",
            "updated_at",
            "cancellation_requested_at",
            "settled_at",
            "revision",
            "payload",
        }
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("Invalid fields")
        if (
            not isinstance(row["id"], str)
            or not ID_RE.fullmatch(row["id"])
            or not isinstance(row["owner"], str)
            or not OWNER_RE.fullmatch(row["owner"])
            or not isinstance(row["native_id"], str)
            or not 1 <= len(row["native_id"]) <= 128
            or row["scope"] not in SCOPES
            or row["lifecycle"]
            not in {"pending", "running", "cancellation_requested", "settled"}
            or type(row["revision"]) is not int
            or row["revision"] < 0
            or not isinstance(row["label"], str)
            or len(row["label"]) > 80
        ):
            raise ValueError("Invalid record")
        if (row["lifecycle"] == "settled") != (row["outcome"] in OUTCOMES):
            raise ValueError("Invalid settlement")
        _payload(row["payload"])
        return row
    except (ValueError, TypeError) as exc:
        raise JobError("Job record is corrupt.") from exc


def _private_directory(path: Path, *, create=False) -> int:
    """Walk without following symlinks; only create the private final directory."""
    path = Path(os.path.abspath(path))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            try:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
            except FileNotFoundError:
                if not create or index != len(parts) - 1:
                    raise JobError("Job store parent must already exist.") from None
                os.mkdir(part, mode=0o700, dir_fd=fd)
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise JobError("Job store must be a private owner directory.")
        return fd
    except BaseException as exc:
        os.close(fd)
        if isinstance(exc, OSError):
            raise JobError("Unsafe or unavailable job store.") from exc
        raise


class Registry:
    def __init__(self, root: Path, *, clock=time.time):
        self.root = Path(os.path.abspath(root))
        self.clock = clock
        directory = _private_directory(self.root, create=True)
        self.db_identity = None
        info = os.fstat(directory)
        self.root_identity = (info.st_dev, info.st_ino)
        os.close(directory)
        with self.db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise JobError("Unsupported job store schema.")
            if (
                version == 0
                and db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ):
                raise JobError("Unrecognized existing job store schema.")
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, native_id TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(owner,native_id))"
            )
            db.execute("PRAGMA user_version=1")

    @contextlib.contextmanager
    def db(self):
        directory = _private_directory(self.root)
        connection = None
        try:
            root_info = os.fstat(directory)
            if (root_info.st_dev, root_info.st_ino) != self.root_identity:
                raise JobError("Job store identity changed.")
            for suffix in ("-journal", "-wal", "-shm"):
                try:
                    sidecar = os.stat(
                        "jobs.sqlite3" + suffix, dir_fd=directory, follow_symlinks=False
                    )
                except FileNotFoundError:
                    continue
                if (
                    not stat.S_ISREG(sidecar.st_mode)
                    or sidecar.st_nlink != 1
                    or sidecar.st_uid != os.getuid()
                    or sidecar.st_mode & 0o077
                ):
                    raise JobError("Unsafe job database sidecar.")
            fd = os.open(
                "jobs.sqlite3",
                os.O_RDWR
                | os.O_NOFOLLOW
                | (os.O_CREAT if self.db_identity is None else 0),
                0o600,
                dir_fd=directory,
            )
            info = os.fstat(fd)
            os.close(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise JobError("Unsafe job database.")
            identity = (info.st_dev, info.st_ino)
            if self.db_identity is not None and identity != self.db_identity:
                raise JobError("Job database identity changed.")
            self.db_identity = identity
            # SQLite's standard journal uses this private pinned directory too.
            connection = sqlite3.connect(
                f"/proc/self/fd/{directory}/jobs.sqlite3", timeout=0.25
            )
            connection.execute("PRAGMA busy_timeout=250")
            actual = os.stat("jobs.sqlite3", dir_fd=directory, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino):
                raise JobError("Job database identity changed.")
            with connection:
                yield connection
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise JobError("Job store unavailable; no work was launched.") from exc
        finally:
            if connection:
                connection.close()
            os.close(directory)

    @staticmethod
    def _row(db, job_id):
        if not isinstance(job_id, str) or not ID_RE.fullmatch(job_id):
            raise JobError("Invalid job ID.", 400)
        found = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _decode(found[0]) if found else None

    def get(self, job_id, owner=None):
        with self.db() as db:
            row = self._row(db, job_id)
            if row and owner is not None and row["owner"] != owner:
                raise JobError("Wrong job owner.", 403)
            return row

    def by_native(self, owner, native_id):
        with self.db() as db:
            found = db.execute(
                "SELECT payload FROM jobs WHERE owner=? AND native_id=?",
                (owner, native_id),
            ).fetchone()
            return _decode(found[0]) if found else None

    def list(self, owner, limit=20):
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 20
        ):
            raise JobError("Invalid job list limit.", 400)
        with self.db() as db:
            return [
                _decode(row[0])
                for row in db.execute(
                    "SELECT payload FROM jobs WHERE owner=? ORDER BY rowid DESC LIMIT ?",
                    (owner, limit),
                )
            ]

    @admission
    def register(
        self,
        owner,
        native_id,
        scope,
        label,
        payload,
        *,
        lifecycle="running",
        single_active=False,
    ):
        if (
            not isinstance(owner, str)
            or not OWNER_RE.fullmatch(owner)
            or not isinstance(native_id, str)
            or not 1 <= len(native_id) <= 128
            or scope not in SCOPES
            or lifecycle not in {"pending", "running"}
        ):
            raise JobError("Invalid job registration.", 400)
        _payload(payload)
        label = " ".join(str(label).split())[:80]
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute(
                "SELECT payload FROM jobs WHERE owner=? AND native_id=?",
                (owner, native_id),
            ).fetchone()
            if found:
                existing = _decode(found[0])
                if (
                    existing["scope"] != scope
                    or existing["label"] != label
                    or existing["payload"] != payload
                ):
                    raise JobError("Native job ID already registered.", 409)
                return existing
            if single_active:
                for (raw,) in db.execute(
                    "SELECT payload FROM jobs WHERE owner=?", (owner,)
                ):
                    if _decode(raw)["lifecycle"] != "settled":
                        raise JobError("An active job is already registered.", 409)
            now = self.clock()
            row = {
                "id": "job-" + secrets.token_hex(16),
                "owner": owner,
                "native_id": native_id,
                "scope": scope,
                "label": label,
                "lifecycle": lifecycle,
                "outcome": None,
                "created_at": now,
                "updated_at": now,
                "cancellation_requested_at": None,
                "settled_at": None,
                "revision": 0,
                "payload": payload,
            }
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?)",
                (row["id"], owner, native_id, json.dumps(row)),
            )
            return row

    def _write(self, db, row):
        db.execute(
            "UPDATE jobs SET payload=? WHERE id=?",
            (json.dumps(row, allow_nan=False), row["id"]),
        )

    def request_cancel(self, job_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, job_id)
            if row is None:
                raise JobError("Unknown job.", 404)
            if row["lifecycle"] not in {"settled", "cancellation_requested"}:
                row.update(
                    lifecycle="cancellation_requested",
                    cancellation_requested_at=self.clock(),
                    updated_at=self.clock(),
                    revision=row["revision"] + 1,
                )
                self._write(db, row)
            return {
                "job_id": row["id"],
                "owner": row["owner"],
                "scope": row["scope"],
                "request_recorded": row["cancellation_requested_at"] is not None,
                "already_settled": row["lifecycle"] == "settled",
                "lifecycle": row["lifecycle"],
                "outcome": row["outcome"],
                "work_stopped": False,
            }

    def _change(self, job_id, owner, payload, expected_revision, outcome=None):
        _payload(payload)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, job_id)
            if row is None:
                raise JobError("Unknown job.", 404)
            if row["owner"] != owner:
                raise JobError("Wrong job owner.", 403)
            if row["revision"] != expected_revision or row["lifecycle"] == "settled":
                raise JobError("Stale or settled job.", 409)
            if outcome is not None:
                if outcome not in OUTCOMES or (
                    row["scope"] == "monitor_observation"
                    and outcome
                    not in {"ended_unknown", "monitoring_stopped", "blocked"}
                ):
                    raise JobError("Unsupported settlement for this scope.", 400)
                row.update(
                    lifecycle="settled", outcome=outcome, settled_at=self.clock()
                )
            row.update(
                payload=payload, updated_at=self.clock(), revision=row["revision"] + 1
            )
            self._write(db, row)
            # Prune only settled rows; active records never fall out of a list cap.
            settled = [
                (key, _decode(raw))
                for key, raw in db.execute(
                    "SELECT id,payload FROM jobs WHERE owner=? ORDER BY rowid DESC",
                    (owner,),
                )
                if _decode(raw)["lifecycle"] == "settled"
            ]
            for key, _ in settled[20:]:
                if owner == "build-monitor":
                    continue  # detached observation may still own a live PID
                db.execute("DELETE FROM jobs WHERE id=?", (key,))
            return row

    def update(self, job_id, owner, payload, expected_revision):
        return self._change(job_id, owner, payload, expected_revision)

    def settle(self, job_id, owner, outcome, payload, expected_revision):
        return self._change(job_id, owner, payload, expected_revision, outcome)
