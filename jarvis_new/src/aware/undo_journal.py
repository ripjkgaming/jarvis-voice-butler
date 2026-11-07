"""Always Aware run journal + undo (PLAN.md §5).

One directory per takeover run, ``$JARVIS_HOME/aware/journal/<run_id>/``:

* ``meta.json``      {run_id, kind, title, created, status, undone, ...}
* ``files/<n>``      the original bytes of a path, copied before the FIRST
                     write to it (reflink when the filesystem can, else a copy)
* ``trash/<n>``      deletes become moves in here
* ``manifest.jsonl`` one op per line, folded last-wins by ``seq``:
                     write {path, existed, sha256_before, sha256_after, mode, backup}
                     move  {from, to, sha256}
                     delete{path, trash, sha256}
                     mkdir {path}
* ``diff.patch``     unified text diff of every write as it lands (a record,
                     not an approval step; binaries noted by hash)
* ``events.jsonl``   progress / blocked / action lines ("what did you try")

Undo walks the ops newest first and restores a path only when its current
state still matches what the run left (sha256_after, or absent). A file Sir
changed since is left alone and named. Nothing here follows a symlink: the
low-level helpers open with ``O_NOFOLLOW`` and replace atomically inside the
parent directory's fd.
"""

from __future__ import annotations

import contextlib
import difflib
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from aware import state

MAX_BACKUP_BYTES = 200 * 1024 * 1024
RETAIN_S = 14 * 86400
RETAIN_BYTES = 2 * 1024**3
DIFF_TEXT_LIMIT = 2 * 1024 * 1024
FICLONE = 0x40049409
ABSENT = "absent"
LINK = "symlink"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class TooLarge(OSError):  # noqa: N818
    """A file over the backup cap: the run must not write it."""


# --------------------------------------------------------------------------
# low-level, symlink-safe IO


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _open_nofollow(path: Path, flags: int = os.O_RDONLY) -> int:
    return os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC)


def path_state(path: Path) -> str:
    """sha256 of a regular file, ABSENT, LINK, or "dir"/"other". Never follows links."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return ABSENT
    if stat.S_ISLNK(st.st_mode):
        return LINK
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if not stat.S_ISREG(st.st_mode):
        return "other"
    h = hashlib.sha256()
    fd = _open_nofollow(path)
    with os.fdopen(fd, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: Path) -> str | None:
    """sha256 of a regular file; None when absent, a link or not a file."""
    try:
        s = path_state(Path(path))
    except OSError:
        return None
    return s if len(s) == 64 else None


def _parent_fd(path: Path) -> int:
    parent = path.parent
    real = os.path.realpath(parent)
    if os.path.normpath(str(parent)) != real:
        raise OSError(errno.ELOOP, "parent path goes through a symlink", str(parent))
    return os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def _replace_in_dir(dfd: int, name: str, writer, mode: int | None) -> None:
    try:
        st = os.lstat(name, dir_fd=dfd)
    except FileNotFoundError:
        st = None
    if st is not None and not stat.S_ISREG(st.st_mode):
        raise OSError(errno.ELOOP, "refusing to replace a non-regular file", name)
    if mode is None:
        mode = stat.S_IMODE(st.st_mode) if st is not None else 0o644
    tmp = f".{name}.aware-{os.getpid()}-{time.monotonic_ns()}.tmp"
    fd = os.open(
        tmp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=dfd,
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            writer(fh)
            fh.flush()
            os.fchmod(fh.fileno(), mode)
            os.fsync(fh.fileno())
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dfd)
        raise
    with contextlib.suppress(OSError):
        os.fsync(dfd)


def atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Write bytes via tmp + rename in the same directory. Refuses links."""
    path = Path(path)
    dfd = _parent_fd(path)
    try:
        _replace_in_dir(dfd, path.name, lambda fh: fh.write(data), mode)
    finally:
        os.close(dfd)


def atomic_copy(src: Path, dst: Path, mode: int | None = None) -> None:
    """Atomically replace dst with the bytes of src (journal-internal file)."""
    sfd = _open_nofollow(Path(src))
    try:
        dfd = _parent_fd(Path(dst))
        try:

            def writer(fh):
                with os.fdopen(os.dup(sfd), "rb") as sf:
                    shutil.copyfileobj(sf, fh, 1 << 20)

            _replace_in_dir(dfd, Path(dst).name, writer, mode)
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


def _clone_file(src: Path, dst: Path) -> None:
    """cp --reflink=auto --preserve=mode,timestamps, without following src links."""
    sfd = _open_nofollow(src)
    try:
        st = os.fstat(sfd)
        dfd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.ioctl(dfd, FICLONE, sfd)
            except OSError:
                os.lseek(sfd, 0, os.SEEK_SET)
                with (
                    os.fdopen(os.dup(sfd), "rb") as sf,
                    os.fdopen(os.dup(dfd), "wb") as df,
                ):
                    shutil.copyfileobj(sf, df, 1 << 20)
            os.utime(dfd, ns=(st.st_atime_ns, st.st_mtime_ns))
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)


def _move_path(src: Path, dst: Path) -> None:
    """Rename, falling back to copy + unlink across filesystems (files only)."""
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "destination exists", str(dst))
    try:
        os.rename(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    st = os.lstat(src)
    if not stat.S_ISREG(st.st_mode):
        raise OSError(
            errno.EXDEV, "cannot move a directory across filesystems", str(src)
        )
    _clone_file(src, dst)
    os.chmod(dst, stat.S_IMODE(st.st_mode))
    os.unlink(src)


# --------------------------------------------------------------------------
# journal


def journal_root() -> Path:
    return state.sub("journal")


def _diff_text(path: str, before: bytes | None, after: bytes | None) -> str:
    b, a = before or b"", after or b""
    if len(b) > DIFF_TEXT_LIMIT or len(a) > DIFF_TEXT_LIMIT or b"\0" in b or b"\0" in a:
        return (
            f"Binary or large file {path}: sha256 "
            f"{sha256_bytes(b) if before is not None else ABSENT} -> "
            f"{sha256_bytes(a) if after is not None else ABSENT}\n"
        )
    try:
        bt, at = b.decode(), a.decode()
    except UnicodeDecodeError:
        return f"Binary file {path}: sha256 {sha256_bytes(b)} -> {sha256_bytes(a)}\n"
    lines = difflib.unified_diff(
        bt.splitlines(keepends=True),
        at.splitlines(keepends=True),
        fromfile="a" + path if before is not None else "/dev/null",
        tofile="b" + path if after is not None else "/dev/null",
    )
    out = "".join(ln if ln.endswith("\n") else ln + "\n" for ln in lines)
    return out


@dataclass
class UndoReport:
    run_id: str
    restored: list[str] = field(default_factory=list)
    left_alone: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.left_alone


class Journal:
    def __init__(self, run_id: str, root: Path | None = None):
        if not _RUN_ID.match(run_id or ""):
            raise ValueError(f"bad run id {run_id!r}")
        self.run_id = run_id
        self.dir = (root or journal_root()) / run_id
        self._pending: dict[str, dict] = {}

    # -- lifecycle

    @classmethod
    def create(
        cls,
        run_id: str,
        *,
        kind: str,
        title: str,
        now: float | None = None,
        meta: dict | None = None,
    ) -> Journal:
        j = cls(run_id)
        j.dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        os.chmod(j.dir, 0o700)
        data = dict(meta or {})
        data.update(
            run_id=run_id,
            kind=kind,
            title=title[:200],
            created=time.time() if now is None else now,
            status="open",
            undone=False,
        )
        j._write_meta(data)
        return j

    @classmethod
    def open(cls, run_id: str) -> Journal:
        j = cls(run_id)
        if not (j.dir / "meta.json").is_file():
            raise FileNotFoundError(run_id)
        return j

    def _write_meta(self, data: dict) -> None:
        state.write_private(self.dir / "meta.json", json.dumps(data, sort_keys=True))

    def meta(self) -> dict:
        try:
            data = json.loads((self.dir / "meta.json").read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def set_status(self, status_: str, **extra) -> None:
        data = self.meta()
        data.update(extra)
        data["status"] = status_
        data["updated"] = time.time()
        self._write_meta(data)

    def update_meta(self, **extra) -> None:
        data = self.meta()
        data.update(extra)
        self._write_meta(data)

    # -- records

    def _sub(self, name: str) -> Path:
        p = self.dir / name
        p.mkdir(mode=0o700, exist_ok=True)
        return p

    def _next_slot(self, name: str) -> Path:
        d = self._sub(name)
        n = len(list(d.iterdir()))
        while (d / str(n)).exists():
            n += 1
        return d / str(n)

    def _raw_ops(self) -> list[dict]:
        out = []
        try:
            text = (self.dir / "manifest.jsonl").read_text()
        except OSError:
            return out
        for line in text.splitlines():
            with contextlib.suppress(ValueError):
                row = json.loads(line)
                if isinstance(row, dict) and "seq" in row:
                    out.append(row)
        return out

    def ops(self) -> list[dict]:
        """Folded ops (last line per seq wins), in the order they happened."""
        folded: dict[int, dict] = {}
        for row in self._raw_ops():
            folded[int(row["seq"])] = row
        return [folded[k] for k in sorted(folded)]

    def _append(self, row: dict) -> dict:
        if "seq" not in row:
            row["seq"] = len({int(r["seq"]) for r in self._raw_ops()})
        row.setdefault("ts", time.time())
        state.append_private(
            self.dir / "manifest.jsonl", json.dumps(row, sort_keys=True)
        )
        return row

    def _last_op_for(self, path: str) -> dict | None:
        for op in reversed(self.ops()):
            if path in (op.get("path"), op.get("from"), op.get("to")):
                return op
        return None

    def _diff(self, path: str, before: bytes | None, after: bytes | None) -> None:
        text = _diff_text(path, before, after)
        if text:
            with contextlib.suppress(OSError):
                fd = os.open(
                    self.dir / "diff.patch",
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                    0o600,
                )
                with os.fdopen(fd, "a") as fh:
                    fh.write(text)

    def backup(self, path: Path, *, max_bytes: int = MAX_BACKUP_BYTES) -> None:
        """Copy the original before the first write to path in this run."""
        key = str(path)
        last = self._last_op_for(key)
        if key in self._pending or (
            last and last["op"] == "write" and last["path"] == key
        ):
            return
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            self._pending[key] = {"existed": False, "sha256_before": None, "mode": None}
            return
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, "not a regular file", key)
        if st.st_size > max_bytes:
            raise TooLarge(errno.EFBIG, "file too large to back up", key)
        slot = self._next_slot("files")
        _clone_file(Path(path), slot)
        self._pending[key] = {
            "existed": True,
            "sha256_before": sha256_file(slot),
            "mode": stat.S_IMODE(st.st_mode),
            "backup": str(slot.relative_to(self.dir)),
        }

    def record_write(self, path: Path, *, before: bytes | None, after: bytes) -> dict:
        key = str(path)
        last = self._last_op_for(key)
        if last and last["op"] == "write" and last["path"] == key:
            row = dict(last)
            row["sha256_after"] = sha256_file(Path(path))
            row["ts"] = time.time()
        else:
            info = self._pending.pop(key, None)
            if info is None:
                raise RuntimeError(f"write to {key} without a backup first")
            row = {
                "op": "write",
                "path": key,
                **info,
                "sha256_after": sha256_file(Path(path)),
            }
        self._diff(key, before, after)
        return self._append(row)

    def record_external(
        self,
        path: Path,
        *,
        before: bytes | None,
        after_sha: str | None,
        mode: int | None,
    ) -> dict:
        """A change made by an outside executor (claude) reconstructed after the fact."""
        key = str(path)
        row = {"op": "write", "path": key, "existed": before is not None, "mode": mode}
        if before is not None:
            slot = self._next_slot("files")
            fd = os.open(slot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(before)
            row["backup"] = str(slot.relative_to(self.dir))
            row["sha256_before"] = sha256_bytes(before)
        else:
            row["sha256_before"] = None
        row["sha256_after"] = after_sha
        return self._append(row)

    def note_diff(self, text: str) -> None:
        with contextlib.suppress(OSError):
            fd = os.open(
                self.dir / "diff.patch", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            with os.fdopen(fd, "a") as fh:
                fh.write(text if text.endswith("\n") else text + "\n")

    def move(self, src: Path, dst: Path) -> dict:
        src, dst = Path(src), Path(dst)
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, "destination exists", str(dst))
        st = os.lstat(src)
        if stat.S_ISLNK(st.st_mode):
            raise OSError(errno.ELOOP, "refusing to move a symlink", str(src))
        sha = sha256_file(src) if stat.S_ISREG(st.st_mode) else None
        _move_path(src, dst)
        return self._append(
            {"op": "move", "from": str(src), "to": str(dst), "sha256": sha}
        )

    def trash(self, path: Path) -> Path:
        path = Path(path)
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            raise OSError(errno.ELOOP, "refusing to delete a symlink", str(path))
        sha = sha256_file(path) if stat.S_ISREG(st.st_mode) else None
        slot = self._next_slot("trash")
        _move_path(path, slot)
        self._append(
            {
                "op": "delete",
                "path": str(path),
                "trash": str(slot.relative_to(self.dir)),
                "sha256": sha,
            }
        )
        return slot

    def record_mkdir(self, path: Path) -> dict:
        return self._append({"op": "mkdir", "path": str(path)})

    def event(self, kind: str, text: str, **extra) -> None:
        row = {"ts": time.time(), "kind": kind, "text": str(text)[:500], **extra}
        state.append_private(self.dir / "events.jsonl", json.dumps(row, sort_keys=True))

    def events(self) -> list[dict]:
        out = []
        with contextlib.suppress(OSError):
            for line in (self.dir / "events.jsonl").read_text().splitlines():
                with contextlib.suppress(ValueError):
                    out.append(json.loads(line))
        return out

    def diff_text(self) -> str:
        try:
            return (self.dir / "diff.patch").read_text()
        except OSError:
            return ""

    def size_bytes(self) -> int:
        total = 0
        for root, _dirs, files in os.walk(self.dir):
            for name in files:
                with contextlib.suppress(OSError):
                    total += os.lstat(os.path.join(root, name)).st_size
        return total


# --------------------------------------------------------------------------
# undo


def _undo_op(j: Journal, op: dict, rep: UndoReport) -> None:
    kind = op.get("op")
    if kind == "write":
        path = Path(op["path"])
        want = op.get("sha256_after") or ABSENT
        now = path_state(path)
        if now != want:
            rep.left_alone.append(str(path))
            return
        if op.get("existed"):
            src = j.dir / op["backup"]
            if not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            atomic_copy(src, path, op.get("mode"))
        elif now != ABSENT:
            os.unlink(path)
        rep.restored.append(str(path))
    elif kind == "move":
        src, dst = Path(op["from"]), Path(op["to"])
        now = path_state(dst)
        ok = now == op["sha256"] if op.get("sha256") else now == "dir"
        if not ok or os.path.lexists(src):
            rep.left_alone.append(str(dst))
            return
        src.parent.mkdir(parents=True, exist_ok=True)
        _move_path(dst, src)
        rep.restored.append(str(src))
    elif kind == "delete":
        path = Path(op["path"])
        if os.path.lexists(path):
            rep.left_alone.append(str(path))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        _move_path(j.dir / op["trash"], path)
        rep.restored.append(str(path))
    elif kind == "mkdir":
        with contextlib.suppress(OSError):
            os.rmdir(op["path"])


def undo(j: Journal) -> UndoReport:
    """Restore everything the run changed that nobody touched since.

    Used directly by the takeover's own revert (run still open) and by
    undo_last for "undo that" (finished runs only). Never raises."""
    rep = UndoReport(j.run_id)
    if j.meta().get("undone"):
        return rep
    for op in reversed(j.ops()):
        try:
            _undo_op(j, op, rep)
        except OSError as exc:
            rep.errors.append(
                f"{op.get('path') or op.get('to')}: {exc.strerror or exc}"
            )
    j.update_meta(
        undone=True,
        undone_at=time.time(),
        undo={
            "restored": rep.restored,
            "left_alone": rep.left_alone,
            "errors": rep.errors,
        },
    )
    j.event("undo", f"restored {len(rep.restored)}, left alone {len(rep.left_alone)}")
    return rep


def runs() -> list[Journal]:
    """All journals, newest first."""
    rows = []
    with contextlib.suppress(OSError):
        for d in journal_root().iterdir():
            if not d.is_dir() or not _RUN_ID.match(d.name):
                continue
            j = Journal(d.name)
            meta = j.meta()
            if meta:
                rows.append((float(meta.get("created") or 0), j))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [j for _, j in rows]


def undo_last(n: int = 1) -> list[UndoReport]:
    """'undo that' / 'undo the last three': newest finished, not-yet-undone runs."""
    out = []
    for j in runs():
        if len(out) >= max(1, int(n)):
            break
        meta = j.meta()
        if meta.get("undone") or meta.get("status") == "open" or not j.ops():
            continue
        out.append(undo(j))
    return out


def last() -> Journal | None:
    for j in runs():
        if j.ops() or j.events():
            return j
    return None


def _drop_refs(meta: dict) -> None:
    repo = meta.get("repo")
    for ref in meta.get("refs") or []:
        if repo and str(ref).startswith("refs/jarvis/aware/"):
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["git", "-C", repo, "update-ref", "-d", ref],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )


def prune(
    *,
    now: float | None = None,
    max_age_s: float = RETAIN_S,
    max_bytes: int = RETAIN_BYTES,
) -> list[str]:
    """Drop journals older than 14 d, then oldest first until under 2 GB."""
    now = time.time() if now is None else now
    pruned = []
    keep = []
    for j in runs():
        meta = j.meta()
        if meta.get("status") == "open":
            keep.append((j, j.size_bytes(), True))
            continue
        if now - float(meta.get("created") or 0) > max_age_s:
            _drop_refs(meta)
            shutil.rmtree(j.dir, ignore_errors=True)
            pruned.append(j.run_id)
        else:
            keep.append((j, j.size_bytes(), False))
    total = sum(size for _, size, _ in keep)
    for j, size, is_open in reversed(keep):  # oldest first
        if total <= max_bytes:
            break
        if is_open:
            continue
        _drop_refs(j.meta())
        shutil.rmtree(j.dir, ignore_errors=True)
        pruned.append(j.run_id)
        total -= size
    return pruned
