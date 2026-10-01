"""System-wide activity feed: non-Jarvis events for the HUD Execution tab.

Downloads, package updates, coding/research projects and other background
work post small items here; the HUD polls them. One JSON file per item
(``$JARVIS_HOME/activity/<id>.json``, written atomically) so many producer
processes can write concurrently without a shared lock file.

Everything here is fail-soft: every public function swallows its errors
and returns a safe default, so a broken feed can never break a producer.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import time
from pathlib import Path

#: Allowed item kinds.
KINDS = ("download", "update", "coding", "research", "build", "task")

#: Allowed item statuses.
STATUSES = ("running", "done", "failed", "cancelled")

#: Item ids are path-safe slugs (also guards against directory traversal).
_ID_OK = re.compile(r"^[a-z0-9-]{1,64}$")

#: Fields update() will accept; anything else in **fields is ignored.
KNOWN_FIELDS = frozenset(
    {
        "kind",
        "title",
        "detail",
        "progress",
        "status",
        "source",
        "meta",
        "started",
        "updated",
        "finished",
    }
)


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def activity_dir() -> Path:
    """Feed directory (created on demand). Never raises."""
    try:
        path = jarvis_home() / "activity"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return jarvis_home() / "activity"


def valid_id(item_id: str) -> bool:
    """Is this a safe feed id? Pure."""
    return bool(_ID_OK.match(item_id or ""))


def _path(item_id: str) -> Path | None:
    if not valid_id(item_id):
        return None
    return activity_dir() / f"{item_id}.json"


def _clean_progress(value: object) -> float | str | None:
    """Clamped 0..100 float; None stays None (indeterminate).

    Returns the string "invalid" for garbage (bools, NaN, non-numbers)
    so callers can leave the stored value untouched. Pure.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "invalid"
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "invalid"
    if num != num:  # NaN: treat as unknown rather than storing garbage
        return "invalid"
    return min(100.0, max(0.0, num))


def _clean_text(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _read_item(item_id: str) -> dict | None:
    path = _path(item_id)
    if path is None:
        return None
    try:
        item = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return item if isinstance(item, dict) else None


def _write_item(item: dict) -> bool:
    # Atomic so concurrent producers never leave half-written JSON.
    try:
        path = activity_dir() / f"{item['id']}.json"
        tmp = activity_dir() / f"{item['id']}.tmp"
        tmp.write_text(json.dumps(item))
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, KeyError):
        return False


def start(
    kind: str,
    title: str,
    *,
    detail: str = "",
    progress: float | None = None,
    source: str = "",
    item_id: str | None = None,
    meta: dict | None = None,
) -> str:
    """Create (or refresh) a running item. Returns its id, "" on failure.

    Never raises. An unknown kind is coerced to "task". If item_id names a
    still-running item, that item is updated in place instead of replaced.
    """
    try:
        now = time.time()
        kind = kind if kind in KINDS else "task"
        if item_id and valid_id(item_id):
            current = _read_item(item_id)
            if current and current.get("status") == "running":
                fields: dict = {"updated": now}
                if title:
                    fields["title"] = _clean_text(title, 120)
                if detail:
                    fields["detail"] = _clean_text(detail, 200)
                if progress is not None:
                    cleaned = _clean_progress(progress)
                    if cleaned != "invalid":
                        fields["progress"] = cleaned
                if source:
                    fields["source"] = _clean_text(source, 80)
                if isinstance(meta, dict):
                    fields["meta"] = meta
                current.update(fields)
                return item_id if _write_item(current) else ""
        # A finished item's id is reused for its next run (a repeated
        # Jarvis tool, a re-appearing download): callers finish() by that
        # id, so minting a random one here stranded the run as "running".
        new_id = (
            item_id
            if item_id and valid_id(item_id)
            else f"{kind}-{secrets.token_hex(4)}"
        )
        cleaned_progress = _clean_progress(progress)
        item = {
            "id": new_id,
            "kind": kind,
            "title": _clean_text(title, 120) or "Untitled",
            "detail": _clean_text(detail, 200),
            "progress": None if cleaned_progress == "invalid" else cleaned_progress,
            "status": "running",
            "started": now,
            "updated": now,
            "finished": None,
            "source": _clean_text(source, 80),
            "meta": dict(meta) if isinstance(meta, dict) else {},
        }
        return new_id if _write_item(item) else ""
    except Exception:
        return ""


def update(item_id: str, **fields) -> bool:
    """Patch known fields of an item (progress is clamped). Never raises."""
    try:
        current = _read_item(item_id)
        if current is None:
            return False
        now = time.time()
        changed = False
        explicit_updated = "updated" in fields
        explicit_finished = "finished" in fields
        for key, value in fields.items():
            if key not in KNOWN_FIELDS:
                continue
            if key in ("title",):
                current[key] = _clean_text(value, 120)
                changed = True
            elif key in ("detail",):
                current[key] = _clean_text(value, 200)
                changed = True
            elif key == "source":
                current[key] = _clean_text(value, 80)
                changed = True
            elif key == "kind":
                if value in KINDS:
                    current[key] = value
                    changed = True
            elif key == "status":
                if value in STATUSES:
                    current[key] = value
                    changed = True
            elif key == "progress":
                cleaned = _clean_progress(value)
                if cleaned != "invalid":
                    current[key] = cleaned
                    changed = True
            elif key == "meta":
                if isinstance(value, dict):
                    current[key] = dict(value)
                    changed = True
            elif key in ("started", "updated", "finished"):
                if value is None and key == "finished":
                    current[key] = None
                    changed = True
                else:
                    try:
                        current[key] = float(value)  # type: ignore[arg-type]
                        changed = True
                    except (TypeError, ValueError):
                        continue
        if (
            not explicit_finished
            and current.get("status") != "running"
            and current.get("finished") is None
        ):
            current["finished"] = now
            changed = True
        if not explicit_updated:
            current["updated"] = now
        return _write_item(current) if changed else True
    except Exception:
        return False


def finish(
    item_id: str,
    ok: bool = True,
    detail: str | None = None,
    status: str | None = None,
) -> bool:
    """Close a running item as done/failed (or the given status). Never raises."""
    try:
        current = _read_item(item_id)
        if current is None:
            return False
        now = time.time()
        final = status if status in STATUSES else ("done" if ok else "failed")
        current["status"] = final
        if ok and current.get("progress") is not None:
            current["progress"] = 100.0
        if detail is not None:
            current["detail"] = _clean_text(detail, 200)
        current["finished"] = now
        current["updated"] = now
        return _write_item(current)
    except Exception:
        return False


def get(item_id: str) -> dict | None:
    """One item by id, or None. Never raises."""
    try:
        return _read_item(item_id)
    except Exception:
        return None


def list_items(
    now: float | None = None,
    keep_done_s: float = 600.0,
    stale_running_s: float = 1800.0,
) -> list[dict]:
    """Fresh feed: live running items + recently finished ones. Never raises.

    Running items whose updated timestamp is older than stale_running_s are
    treated as abandoned orphans and hidden; finished items older than
    keep_done_s have expired. Running comes first (newest started first),
    then finished (newest finished first).
    """
    try:
        now = time.time() if now is None else now
        try:
            files = list(activity_dir().glob("*.json"))
        except OSError:
            return []
        if len(files) > 200:
            # Occasional housekeeping so the dir can't grow unbounded.
            with contextlib.suppress(Exception):
                prune(now=now)
        running: list[dict] = []
        finished: list[dict] = []
        for path in files:
            try:
                item = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(item, dict) or not valid_id(item.get("id", "")):
                continue
            status = item.get("status")
            if status == "running":
                try:
                    age = max(0.0, now - float(item.get("updated", 0.0)))
                except (TypeError, ValueError):
                    continue
                if age <= stale_running_s:
                    running.append(item)
            elif status in ("done", "failed", "cancelled"):
                try:
                    fin = item.get("finished")
                    age = (
                        max(0.0, now - float(fin)) if fin is not None else float("inf")
                    )
                except (TypeError, ValueError):
                    continue
                if age <= keep_done_s:
                    finished.append(item)
        running.sort(key=lambda i: i.get("started", 0.0), reverse=True)
        finished.sort(
            key=lambda i: i.get("finished") or 0.0,
            reverse=True,
        )
        return running + finished
    except Exception:
        return []


def prune(now: float | None = None, older_than_s: float = 3600.0) -> int:
    """Delete expired files. Returns the removed count. Never raises."""
    try:
        now = time.time() if now is None else now
        removed = 0
        try:
            files = list(activity_dir().glob("*.json"))
        except OSError:
            return 0
        for path in files:
            try:
                try:
                    item = json.loads(path.read_text())
                except ValueError:
                    # Corrupt JSON: drop it once it is clearly abandoned.
                    if time.time() - path.stat().st_mtime > older_than_s:
                        path.unlink(missing_ok=True)
                        removed += 1
                    continue
                if not isinstance(item, dict):
                    continue
                status = item.get("status")
                if status == "running":
                    try:
                        age = max(0.0, now - float(item.get("updated", 0.0)))
                    except (TypeError, ValueError):
                        age = 0.0
                    if age > older_than_s * 6:
                        path.unlink(missing_ok=True)
                        removed += 1
                else:
                    ref = item.get("finished")
                    if ref is None:
                        ref = item.get("updated", 0.0)
                    try:
                        age = max(0.0, now - float(ref))
                    except (TypeError, ValueError):
                        continue
                    if age > older_than_s:
                        path.unlink(missing_ok=True)
                        removed += 1
            except OSError:
                continue
        return removed
    except Exception:
        return 0


def main(argv: list[str] | None = None) -> int:
    """CLI so any script can post events. Never raises."""
    try:
        parser = argparse.ArgumentParser(prog="activity.py", description=__doc__)
        sub = parser.add_subparsers(dest="cmd", required=True)
        p_start = sub.add_parser("start", help="start an item, prints its id")
        p_start.add_argument("--kind", default="task", choices=list(KINDS))
        p_start.add_argument("--title", required=True)
        p_start.add_argument("--detail", default="")
        p_start.add_argument("--progress", type=float, default=None)
        p_start.add_argument("--id", default=None)
        p_start.add_argument("--source", default="")
        p_up = sub.add_parser("update", help="patch an item")
        p_up.add_argument("id")
        p_up.add_argument("--progress", type=float, default=None)
        p_up.add_argument("--detail", default=None)
        p_up.add_argument("--title", default=None)
        p_fin = sub.add_parser("finish", help="close an item")
        p_fin.add_argument("id")
        p_fin.add_argument("--failed", action="store_true")
        p_fin.add_argument("--detail", default=None)
        sub.add_parser("list", help="print the fresh feed as JSON")
        args = parser.parse_args(argv)
        if args.cmd == "start":
            print(
                start(
                    args.kind,
                    args.title,
                    detail=args.detail,
                    progress=args.progress,
                    source=args.source,
                    item_id=args.id,
                )
            )
        elif args.cmd == "update":
            fields: dict = {}
            if args.progress is not None:
                fields["progress"] = args.progress
            if args.detail is not None:
                fields["detail"] = args.detail
            if args.title is not None:
                fields["title"] = args.title
            if update(args.id, **fields):
                print("ok")
            else:
                parser.exit(1, "not-found\n")
        elif args.cmd == "finish":
            if finish(args.id, ok=not args.failed, detail=args.detail):
                print("ok")
            else:
                parser.exit(1, "not-found\n")
        elif args.cmd == "list":
            print(json.dumps(list_items()))
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
