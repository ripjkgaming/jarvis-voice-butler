"""Background producers for the activity feed (HUD Execution tab).

Watches four non-Jarvis sources and mirrors them into activity items:

- partial downloads in ~/Downloads (``JARVIS_DOWNLOADS_DIR`` override),
- PackageKit transactions (GNOME Software / Discover / pkcon),
- system updater processes (dnf, flatpak, nobara-sync, ...),
- Jarvis research/coding projects that are currently running.

One :class:`ActivityWatcher` pass polls every source; :func:`start_thread`
runs it in a daemon thread. Each source is a method with injectable inputs
(paths, runners, project lists) so tests never touch the real machine.
Everything is fail-soft: a failing source is skipped, never fatal.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import activity

#: Partial-download suffixes (Firefox .part, Chrome .crdownload, ...).
PARTIAL_SUFFIXES = (".part", ".crdownload", ".download", ".partial", ".!qB")

#: A partial file this old on first sight was abandoned: ignore it.
ABANDONED_S = 600.0

#: PackageKit D-Bus polling knobs.
PK_POLL_GAP_S = 2.0
PK_RETRY_S = 60.0
PK_BUS = "org.freedesktop.PackageKit"
PK_ROOT = "/org/freedesktop/PackageKit"
PK_TX_IFACE = "org.freedesktop.PackageKit.Transaction"

#: Only mirror projects started within the last day.
PROJECT_WINDOW_S = 24 * 3600.0

#: EMA weight for download speed (bytes/sec).
SPEED_ALPHA = 0.3


def human_bytes(num: float) -> str:
    """1024-based size like ``48.2 MB``. Pure."""
    try:
        size = float(num)
    except (TypeError, ValueError):
        return "0 B"
    if size < 0:
        size = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def strip_partial(name: str) -> str | None:
    """Final filename for a partial download, or None if not partial. Pure."""
    for suffix in PARTIAL_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None


def download_id(path: str) -> str:
    """Stable feed id for a partial file. Pure."""
    return "download-" + hashlib.sha1(path.encode()).hexdigest()[:10]


def shorten_cmd(argv: list[str], limit: int = 80) -> str:
    """Command line squeezed to one short line. Pure."""
    return " ".join(argv)[:limit]


def model_short(model: str) -> str:
    """``opencode/muse-spark-...`` → ``muse-spark-...``. Pure."""
    return (model or "").split("/")[-1].split(":")[0].strip() or "unknown"


# --- PackageKit roles -------------------------------------------------------
# Numeric order verified on this machine via the PackageKitGlib typelib
# (gi.repository.PackageKitGlib.RoleEnum); names double as the string form.

ROLE_NAMES = {
    0: "unknown", 1: "cancel", 2: "depends-on", 3: "get-details",
    4: "get-files", 5: "get-packages", 6: "get-repo-list", 7: "required-by",
    8: "get-update-detail", 9: "get-updates", 10: "install-files",
    11: "install-packages", 12: "install-signature", 13: "refresh-cache",
    14: "remove-packages", 15: "repo-enable", 16: "repo-set-data",
    17: "resolve", 18: "search-details", 19: "search-file",
    20: "search-group", 21: "search-name", 22: "update-packages",
    23: "what-provides", 24: "accept-eula", 25: "download-packages",
    26: "get-distro-upgrades", 27: "get-categories",
    28: "get-old-transactions", 29: "repair-system",
    30: "get-details-local", 31: "get-files-local", 32: "repo-remove",
    33: "upgrade-system",
}  # fmt: skip

ROLE_TITLES = {
    "update-packages": "Updating packages",
    "install-packages": "Installing packages",
    "refresh-cache": "Refreshing package cache",
    "download-packages": "Downloading packages",
    "upgrade-system": "Upgrading system",
    "remove-packages": "Removing packages",
    "install-files": "Installing files",
    "get-updates": "Checking for updates",
}


def role_title(role: object) -> str:
    """Display title for a PackageKit role (int enum or string). Pure."""
    name = ROLE_NAMES.get(role, "") if isinstance(role, int) else str(role or "")
    if name in ROLE_TITLES:
        return ROLE_TITLES[name]
    if name and name != "unknown":
        # Fallback: humanize ("install-signature" → "Install signature").
        return name.replace("-", " ").replace("_", " ").capitalize()
    return "Package task"


def package_short(package_id: object) -> str:
    """``firefox;1.2;x86_64;fedora`` → ``firefox``. Pure."""
    return str(package_id or "").split(";")[0].strip()


def pk_item_id(object_path: str) -> str:
    """Stable feed id for a PackageKit transaction path. Pure."""
    return "update-pk-" + hashlib.sha1(object_path.encode()).hexdigest()[:8]


# --- updater processes ------------------------------------------------------

#: argv[0] basenames → verbs that count as an update + title template.
#: nobara-sync takes no verb (any invocation is a system update).
PROC_RULES: tuple[tuple[frozenset, frozenset, str], ...] = (
    (
        frozenset({"dnf", "dnf5"}),
        frozenset({"install", "upgrade", "update", "distro-sync"}),
        "{base} {verb}",
    ),
    (frozenset({"flatpak"}), frozenset({"update", "install"}), "Flatpak {verb}"),
    (frozenset({"nobara-sync"}), frozenset(), "Nobara system update"),
    (frozenset({"rpm-ostree"}), frozenset({"upgrade"}), "rpm-ostree {verb}"),
    (frozenset({"fwupdmgr"}), frozenset({"update", "upgrade"}), "Firmware update"),
    (frozenset({"pkcon"}), frozenset({"update", "install"}), "PackageKit {verb}"),
    (frozenset({"snap"}), frozenset({"refresh"}), "Snap refresh"),
)


def match_updater(argv: list[str]) -> str | None:
    """Feed title if this command line is a system update, else None. Pure."""
    if not argv:
        return None
    base = Path(argv[0]).name
    args = argv[1:]
    for names, verbs, template in PROC_RULES:
        if base not in names:
            continue
        if not verbs:
            return template
        for arg in args:
            verb = arg.lstrip("-")
            if verb in verbs:
                base_label = base.upper() if base in ("dnf", "dnf5") else base
                return template.format(base=base_label, verb=verb)
    return None


def read_cmdline(path: Path) -> list[str] | None:
    """NUL-split /proc/<pid>/cmdline; None when unreadable. Never raises."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    parts = [p.decode(errors="replace") for p in raw.split(b"\x00") if p]
    return parts or None


def _default_runner(argv: list[str], timeout: float = 2.0):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _busctl_values(text: str) -> list:
    """Values from `busctl --json=short` output (one object per line for
    multi-property queries, a single object otherwise). Pure-ish."""
    out: list = []
    # Whole-document parse first, then line-by-line for concatenated objects.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "data" in obj:
            return [obj["data"]]
    except ValueError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "data" in obj:
            out.append(obj["data"])
    return out


def _busctl_paths(value: object) -> list[str]:
    """Object paths from a GetTransactionList reply. Pure."""
    paths: list[str] = []

    def collect(node: object) -> None:
        if isinstance(node, str):
            if node.startswith("/"):
                paths.append(node)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(value)
    return paths


class ActivityWatcher:
    """Polls background sources into the activity feed.

    Inject paths / runners for tests; defaults watch the real machine.
    Per-source memory lives on the instance (speeds, known transactions).
    """

    def __init__(
        self,
        *,
        downloads_dir: str | Path | None = None,
        proc_root: str | Path = "/proc",
        runner=None,
    ) -> None:
        self._downloads_override = Path(downloads_dir) if downloads_dir else None
        self._proc_root = Path(proc_root)
        self._run = runner or _default_runner
        self._dl: dict[str, dict] = {}
        self._pk: dict[str, str] = {}
        self._pk_last = 0.0
        self._pk_until = 0.0
        self._procs: dict[str, str] = {}
        self._projects: set[str] = set()

    # -- driver ----------------------------------------------------------

    def poll(self, now: float | None = None) -> None:
        """One pass over all sources. Never raises."""
        now = time.time() if now is None else now
        for source in (
            self.poll_downloads,
            self.poll_packagekit,
            self.poll_processes,
            self.poll_projects,
        ):
            try:
                source(now)
            except Exception:
                continue

    def run(self, stop: threading.Event, interval: float = 1.0) -> None:
        """Loop poll() until stop is set. Never raises."""
        while not stop.is_set():
            with contextlib.suppress(Exception):
                self.poll()
            stop.wait(interval)

    # -- helpers ----------------------------------------------------------

    def _downloads_dir(self) -> Path:
        if self._downloads_override is not None:
            return self._downloads_override
        override = os.environ.get("JARVIS_DOWNLOADS_DIR", "").strip()
        return Path(override) if override else Path.home() / "Downloads"

    def _upsert(self, item_id: str, kind: str, title: str, **fields) -> None:
        # Store is the truth across restarts: update live items, start new.
        current = activity.get(item_id)
        if current and current.get("status") == "running":
            activity.update(item_id, title=title, kind=kind, **fields)
        else:
            activity.start(kind, title, item_id=item_id, **fields)

    # -- downloads ---------------------------------------------------------

    def poll_downloads(self, now: float | None = None) -> None:
        """Mirror partial files in ~/Downloads. Never raises."""
        try:
            now = time.time() if now is None else now
            d = self._downloads_dir()
            try:
                entries = list(os.scandir(d))
            except OSError:
                entries = []
            seen: set[str] = set()
            for entry in entries:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    final = strip_partial(entry.name)
                    if final is None:
                        continue
                    st = entry.stat(follow_symlinks=False)
                    iid = download_id(os.path.abspath(entry.path))
                    seen.add(iid)
                    prev = self._dl.get(iid)
                    if prev is None:
                        if now - st.st_mtime > ABANDONED_S:
                            continue  # stale leftover, not an active download
                        self._dl[iid] = {
                            "path": os.path.abspath(entry.path),
                            "final": str(Path(d) / final),
                            "size": st.st_size,
                            "ts": now,
                            "speed": 0.0,
                        }
                        self._upsert(
                            iid,
                            "download",
                            final[:120],
                            detail=human_bytes(st.st_size),
                            progress=None,
                            source="downloads",
                            meta={"bytes_done": st.st_size, "speed_bps": 0.0},
                        )
                    else:
                        prev["path"] = os.path.abspath(entry.path)
                        prev["final"] = str(Path(d) / final)
                        dt = now - prev["ts"]
                        speed = prev["speed"]
                        if dt > 0:
                            if st.st_size < prev["size"]:
                                speed = 0.0  # restarted from scratch
                            else:
                                inst = (st.st_size - prev["size"]) / dt
                                speed = SPEED_ALPHA * inst + (1 - SPEED_ALPHA) * speed
                            prev.update(size=st.st_size, ts=now, speed=speed)
                        detail = human_bytes(st.st_size)
                        if speed > 0:
                            detail += f" · {human_bytes(speed)}/s"
                        self._upsert(
                            iid,
                            "download",
                            final[:120],
                            detail=detail,
                            progress=None,
                            source="downloads",
                            meta={"bytes_done": st.st_size, "speed_bps": speed},
                        )
                except OSError:
                    continue
            for iid, state in list(self._dl.items()):
                if iid in seen:
                    continue
                # Partial file gone: finished download or cancelled one.
                try:
                    final_path = Path(state["final"])
                    if final_path.is_file():
                        try:
                            size = final_path.stat().st_size
                        except OSError:
                            size = 0
                        activity.finish(
                            iid, ok=True, detail=f"{human_bytes(size)} saved"
                        )
                    else:
                        activity.finish(iid, ok=False, status="cancelled")
                except Exception:
                    pass
                self._dl.pop(iid, None)
        except Exception:
            pass

    # -- PackageKit --------------------------------------------------------

    def poll_packagekit(self, now: float | None = None) -> None:
        """Mirror PackageKit transactions (≤1 poll per 2s). Never raises."""
        try:
            now = time.time() if now is None else now
            if now < self._pk_until:
                return  # busctl broken; retry window hasn't elapsed
            if now - self._pk_last < PK_POLL_GAP_S:
                return
            self._pk_last = now
            try:
                reply = self._run(
                    [
                        "busctl",
                        "--system",
                        "--json=short",
                        "call",
                        PK_BUS,
                        PK_ROOT,
                        PK_BUS,
                        "GetTransactionList",
                    ],
                    timeout=2.0,
                )
                if getattr(reply, "returncode", 1) != 0:
                    raise RuntimeError("busctl GetTransactionList failed")
                values = _busctl_values(getattr(reply, "stdout", "") or "")
                paths = _busctl_paths(values[0] if values else [])
            except Exception:
                self._pk_until = now + PK_RETRY_S
                return
            live: set[str] = set()
            for path in paths:
                try:
                    props = self._run(
                        [
                            "busctl",
                            "--system",
                            "--json=short",
                            "get-property",
                            PK_BUS,
                            path,
                            PK_TX_IFACE,
                            "Role",
                            "Status",
                            "Percentage",
                            "LastPackage",
                        ],
                        timeout=2.0,
                    )
                    if getattr(props, "returncode", 1) != 0:
                        continue
                    vals = _busctl_values(getattr(props, "stdout", "") or "")
                    if len(vals) < 4:
                        continue
                    role, status, pct, last = vals[0], vals[1], vals[2], vals[3]
                    iid = pk_item_id(path)
                    live.add(path)
                    try:
                        progress: float | None = None
                        if int(pct) != 101:  # 101 = unknown percentage
                            progress = min(100.0, max(0.0, float(int(pct))))
                    except (TypeError, ValueError):
                        progress = None
                    self._pk[path] = iid
                    self._upsert(
                        iid,
                        "update",
                        role_title(role),
                        detail=package_short(last),
                        progress=progress,
                        source="packagekit",
                        meta={"object_path": path, "role": role, "status": status},
                    )
                except Exception:
                    continue
            for path, iid in list(self._pk.items()):
                if path not in live:
                    with contextlib.suppress(Exception):
                        activity.finish(iid, ok=True)
                    self._pk.pop(path, None)
        except Exception:
            pass

    # -- updater processes ---------------------------------------------------

    def poll_processes(self, now: float | None = None) -> None:
        """Mirror updater processes from /proc cmdlines. Never raises."""
        try:
            if now is None:
                now = time.time()
            try:
                names = os.listdir(self._proc_root)
            except OSError:
                return
            alive: set[str] = set()
            for name in names:
                if not name.isdigit():
                    continue
                argv = read_cmdline(self._proc_root / name / "cmdline")
                if argv is None:
                    continue  # unreadable pid: skip, don't touch tracked state
                title = match_updater(argv)
                if title is None:
                    continue
                iid = f"update-proc-{name}"
                alive.add(name)
                self._procs[name] = iid
                self._upsert(
                    iid,
                    "update",
                    title[:120],
                    detail=shorten_cmd(argv),
                    progress=None,
                    source="processes",
                    meta={"pid": int(name)},
                )
            for pid, iid in list(self._procs.items()):
                if pid in alive:
                    continue
                if (self._proc_root / pid).exists():
                    continue  # still there but unreadable: leave it alone
                with contextlib.suppress(Exception):
                    activity.finish(iid, ok=True)
                self._procs.pop(pid, None)
        except Exception:
            pass

    # -- Jarvis projects -------------------------------------------------------

    def poll_projects(
        self, now: float | None = None, projects: list[dict] | None = None
    ) -> None:
        """Mirror running research/coding projects. Never raises."""
        try:
            now = time.time() if now is None else now
            if projects is None:
                try:
                    import projects as _projects

                    projects = _projects.list_projects()
                except Exception:
                    return
            by_id = {m.get("id"): m for m in (projects or []) if m.get("id")}
            for pid, meta in by_id.items():
                try:
                    if meta.get("status") != "running":
                        continue
                    try:
                        age = now - float(meta.get("created_at", 0.0))
                    except (TypeError, ValueError):
                        continue
                    if age > PROJECT_WINDOW_S:
                        continue
                    kind = "research" if meta.get("kind") == "research" else "coding"
                    iid = f"project-{pid}"
                    try:
                        raw = meta.get("progress")
                        progress: float | None = None
                        if isinstance(raw, bool):
                            progress = None
                        elif raw is not None:
                            progress = min(100.0, max(0.0, float(raw)))
                    except (TypeError, ValueError):
                        progress = None
                    stage = str(meta.get("stage") or "").strip()
                    engine = "opencode" if kind == "coding" else "claude"
                    detail = (
                        stage
                        or f"{engine} · {model_short(str(meta.get('model') or ''))}"
                    )
                    self._projects.add(iid)
                    self._upsert(
                        iid,
                        kind,
                        str(meta.get("title") or "Untitled")[:120],
                        detail=detail[:200],
                        progress=progress,
                        source="projects",
                        meta={"project_id": pid, "model": meta.get("model")},
                    )
                except Exception:
                    continue
            for iid in list(self._projects):
                pid = iid[len("project-") :]
                meta = by_id.get(pid)
                if meta is not None and meta.get("status") == "running":
                    continue
                try:
                    if meta is None:
                        activity.finish(iid, ok=False)
                    else:
                        status = meta.get("status")
                        activity.finish(
                            iid,
                            ok=(status == "done"),
                            status="cancelled" if status == "cancelled" else None,
                        )
                except Exception:
                    pass
                self._projects.discard(iid)
        except Exception:
            pass


def start_thread(interval: float = 1.0) -> tuple[threading.Thread, threading.Event]:
    """Run a watcher in a daemon thread. Returns (thread, stop event)."""
    watcher = ActivityWatcher()
    stop = threading.Event()

    def _loop() -> None:
        watcher.run(stop, interval)

    thread = threading.Thread(target=_loop, name="activity-watch", daemon=True)
    thread.start()
    return thread, stop
