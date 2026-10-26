"""Bounded read-back at the authorized effect boundary, without content receipts."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from pathlib import Path

from action_outcome import ActionOutcome

MAX_TEXT_BYTES = 256 * 1024


def write_verified(
    path: Path,
    text: str,
    *,
    roots: tuple[Path, ...],
    authorize=lambda: None,
    request_id=None,
) -> dict:
    """Confined descriptor-relative UTF-8 write followed by fresh bounded read.

    Refuses symlinks and hardlinks. No content, path, exception or digest is
    returned. Does not promise crash durability, atomicity or cloud sync.
    """
    dispatched = False
    descriptors = []

    def finish(state, check="not_attempted", reason="", evidence=None):
        return ActionOutcome(
            "file.write",
            state,
            "started" if dispatched else "not_started",
            check,
            "filesystem",
            reason,
            evidence or {},
            request_id=request_id,
        ).result()

    try:
        if not isinstance(text, str):
            return finish("blocked", reason="invalid_text")
        if len(text) > MAX_TEXT_BYTES:
            return finish("blocked", reason="text_too_large")
        data = text.encode("utf-8")
        if len(data) > MAX_TEXT_BYTES:
            return finish("blocked", reason="text_too_large")
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            return finish("blocked", reason="invalid_path")
        allowed = [
            Path(root).resolve()
            for root in roots
            if path.is_relative_to(Path(root).resolve())
            and path != Path(root).resolve()
        ]
        if not allowed:
            return finish("blocked", reason="outside_root")
        root = max(allowed, key=lambda p: len(p.parts))
        relative = path.relative_to(root)
        authorize()
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        for part in relative.parts[:-1]:
            authorize()
            try:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                )
            except FileNotFoundError:
                # Directory creation is an effect too: uncertainty prevents replay.
                dispatched = True
                os.mkdir(part, dir_fd=parent)
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                )
            parent = child
            descriptors.append(parent)
        authorize()
        filename = relative.name
        # Open without truncating so unsafe file types/links can be refused first.
        try:
            os.stat(filename, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            dispatched = True  # O_CREAT may create the target before later failure.
        fd = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
        descriptors.append(fd)
        initial = os.fstat(fd)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            return finish("blocked", reason="unsafe_target")
        authorize()
        dispatched = True
        os.ftruncate(fd, 0)
        view = memoryview(data)
        while view:
            authorize()
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short write")
            view = view[count:]
        authorize()
        read_fd = os.open(
            filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        descriptors.append(read_fd)
        observed = os.fstat(read_fd)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or (observed.st_dev, observed.st_ino) != (initial.st_dev, initial.st_ino)
            or observed.st_size != len(data)
        ):
            return finish("failed", "mismatched", "target_changed")
        # Re-walk the approved named path after dispatch: a moved/replaced
        # parent must not turn a descriptor write into proof of another target.
        root_named = os.stat(root, follow_symlinks=False)
        root_opened = os.fstat(descriptors[0])
        if (root_named.st_dev, root_named.st_ino) != (
            root_opened.st_dev,
            root_opened.st_ino,
        ):
            return finish("failed", "mismatched", "root_changed")
        named_parent = os.dup(descriptors[0])
        descriptors.append(named_parent)
        for part in relative.parts[:-1]:
            named_parent = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=named_parent
            )
            descriptors.append(named_parent)
        current_parent = os.fstat(named_parent)
        pinned_parent = os.fstat(parent)
        if (current_parent.st_dev, current_parent.st_ino) != (
            pinned_parent.st_dev,
            pinned_parent.st_ino,
        ):
            return finish("failed", "mismatched", "parent_changed")
        actual = os.pread(read_fd, MAX_TEXT_BYTES + 1, 0)
        after = os.fstat(read_fd)
        named = os.stat(filename, dir_fd=parent, follow_symlinks=False)
        if (
            actual != data
            or (named.st_dev, named.st_ino) != (initial.st_dev, initial.st_ino)
            or after.st_size != len(data)
            or after.st_mtime_ns != observed.st_mtime_ns
        ):
            return finish("failed", "mismatched", "bytes_mismatch")
        return finish(
            "verified", "matched", evidence={"bytes_matched": True, "bytes": len(data)}
        )
    except Exception:
        return finish(
            "dispatched_unverified" if dispatched else "blocked",
            "unavailable" if dispatched else "not_attempted",
            "readback_unavailable" if dispatched else "effect_refused",
        )
    finally:
        for fd in reversed(descriptors):
            with suppress(OSError):
                os.close(fd)
