"""Observation-only Linux build monitor. No launch, signal, or notification path."""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import threading
import time
import unicodedata
from pathlib import Path, PurePosixPath

import jobs

OWNER = "build-monitor"
MAX_READ = 16384
MAX_TEXT = 8000
REQUEST_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
# CSI, OSC (BEL or ST), and other escape sequences, then control characters.
ESCAPES = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]"
)


class MonitorError(jobs.JobError):
    pass


@dataclasses.dataclass(frozen=True)
class Identity:
    boot_id: str
    uid: int
    pid: int
    start_ticks: int
    state: str

    def key(self):
        return (self.boot_id, self.uid, self.pid, self.start_ticks)


def parse_stat(text: str) -> tuple[int, str, int]:
    end = text.rfind(")")
    begin = text.find("(")
    if begin <= 0 or end <= begin:
        raise ValueError("Invalid process stat")
    pid = int(text[:begin].strip())
    fields = text[end + 1 :].split()
    if len(fields) < 20 or len(fields[0]) != 1:
        raise ValueError("Invalid process stat")
    return pid, fields[0], int(fields[19])


def read_identity(pid: int) -> Identity | None:
    """Only named PID metadata; never enumerate proc, cmdline, environ or children."""
    with Path("/proc/sys/kernel/random/boot_id").open() as stream:
        boot = stream.read(81).strip()
    if len(boot) > 80:
        raise OSError("Boot identity exceeded bounds")
    if not boot:
        raise OSError("Boot identity unavailable")
    proc = Path("/proc")
    directory = proc / str(pid)
    try:
        info = directory.stat()
        with (directory / "stat").open() as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise OSError("Process stat exceeded bounds")
        number, state, ticks = parse_stat(raw)
        if number != pid:
            raise OSError("Process identity changed")
        return Identity(boot, info.st_uid, number, ticks, state)
    except FileNotFoundError:
        # A missing proc filesystem is unavailable observation, not process exit.
        if not proc.is_dir():
            raise OSError("Proc filesystem unavailable") from None
        return None


def _safe_text(text, limit):
    value = ESCAPES.sub("", str(text))
    return "".join(
        c
        for c in value
        if c in "\n\t"
        or (
            ord(c) >= 32
            and ord(c) != 127
            and not 0x80 <= ord(c) <= 0x9F
            and unicodedata.category(c) != "Cf"
        )
    )[:limit]


def _open_directory(path):
    value = PurePosixPath(path)
    if not value.is_absolute() or any(p in {"..", "."} for p in value.parts):
        raise MonitorError("Invalid log workspace.", 400)
    if value.parts[1:2] in [("proc",), ("sys",), ("dev",)]:
        raise MonitorError("Invalid log workspace.", 400)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in value.parts[1:]:
            if part.startswith("."):
                raise MonitorError("Private log workspace refused.", 400)
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        info = os.fstat(fd)
        if info.st_uid != os.getuid():
            raise MonitorError("Log workspace is not owner-owned.", 403)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _log_parts(relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or len(path.parts) > 16:
        raise MonitorError("Invalid log path.", 400)
    # Exact approved file only; conservative exclusion is intentional.
    if any(
        p in {"..", "."}
        or p.startswith(".")
        or re.search(r"(?i)(secret|credential|password|token|auth|private)", p)
        for p in path.parts
    ):
        raise MonitorError("Private or unsafe log path refused.", 400)
    return path.parts


@contextlib.contextmanager
def _open_log(root, relative):
    parts = _log_parts(relative)
    root_fd = _open_directory(root)
    directory = os.dup(root_fd)
    file_fd = None
    try:
        for part in parts[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = child
        file_fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        info = os.fstat(file_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise MonitorError(
                "Approved log must be a regular owner file with one link.", 400
            )
        yield root_fd, file_fd
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory)
        os.close(root_fd)


def _inode(info):
    return [info.st_dev, info.st_ino]


def _binding(root, relative):
    with _open_log(root, relative) as (root_fd, fd):
        return {
            "root": root,
            "relative": relative,
            "root_identity": _inode(os.fstat(root_fd)),
            "file_identity": _inode(os.fstat(fd)),
        }


class Monitor:
    def __init__(self, registry, *, proc_reader=read_identity, clock=time.time):
        self.registry = registry
        self.proc_reader = proc_reader
        self.clock = clock

    @staticmethod
    def _data(row):
        try:
            data = row["payload"]
            identity = Identity(**data["identity"])
            if (
                not isinstance(identity.boot_id, str)
                or not 1 <= len(identity.boot_id) <= 80
                or type(identity.uid) is not int
                or identity.uid != os.getuid()
                or type(identity.pid) is not int
                or not 1 < identity.pid <= 2147483647
                or type(identity.start_ticks) is not int
                or identity.start_ticks < 0
                or not isinstance(identity.state, str)
                or len(identity.state) != 1
                or data["status"]
                not in {
                    "running",
                    "observation_unknown",
                    "ended_unknown",
                    "monitoring_stopped",
                }
                or type(data["last_observed_at"]) not in {int, float}
                or not math.isfinite(data["last_observed_at"])
                or not isinstance(data["input_fingerprint"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", data["input_fingerprint"])
            ):
                raise ValueError("Invalid monitor metadata")
            binding = data.get("log")
            if binding is not None:
                if (
                    not isinstance(binding, dict)
                    or set(binding)
                    != {"root", "relative", "root_identity", "file_identity"}
                    or not isinstance(binding["root"], str)
                    or len(binding["root"]) > 1024
                    or not isinstance(binding["relative"], str)
                    or len(binding["relative"]) > 1024
                ):
                    raise ValueError("Invalid log binding")
                _log_parts(binding["relative"])
                for field in ("root_identity", "file_identity"):
                    if (
                        not isinstance(binding[field], list)
                        or len(binding[field]) != 2
                        or any(
                            type(value) is not int or value < 0
                            for value in binding[field]
                        )
                    ):
                        raise ValueError("Invalid log identity")
            return data
        except (TypeError, ValueError, KeyError) as exc:
            raise MonitorError("Monitor record is corrupt.") from exc

    def _observe(self, pid):
        try:
            found = self.proc_reader(pid)
            if found is not None and (
                not isinstance(found, Identity) or found.pid != pid
            ):
                raise ValueError("Invalid observer response")
            return found
        except (OSError, ValueError) as exc:
            raise MonitorError("Process observation unavailable.") from exc

    def register(
        self, request_id, pid, label, workspace_root=None, log_relative_path=None
    ):
        if (
            not isinstance(request_id, str)
            or not REQUEST_RE.fullmatch(request_id)
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or not 1 < pid <= 2147483647
            or not isinstance(label, str)
            or not 1 <= len(label.strip()) <= 80
        ):
            raise MonitorError("Invalid build registration.", 400)
        if bool(workspace_root) != bool(log_relative_path):
            raise MonitorError(
                "Supply both exact workspace and relative log path.", 400
            )
        if workspace_root is not None and (
            not isinstance(workspace_root, str)
            or len(workspace_root) > 1024
            or not isinstance(log_relative_path, str)
            or len(log_relative_path) > 1024
        ):
            raise MonitorError("Invalid log binding.", 400)
        label = " ".join(_safe_text(label, 80).split())
        fingerprint = hashlib.sha256(
            json.dumps([pid, label, workspace_root, log_relative_path]).encode()
        ).hexdigest()
        prior = self.registry.by_native(OWNER, request_id)
        if prior:
            if prior["payload"].get("input_fingerprint") != fingerprint:
                raise MonitorError("Registration request ID conflict.", 409)
            return self._public(prior)
        first = self._observe(pid)
        if first is None or first.uid != os.getuid() or first.state in {"Z", "X", "x"}:
            raise MonitorError("Owner process is absent, exited or inaccessible.", 403)
        log = None
        try:
            if workspace_root:
                log = _binding(workspace_root, log_relative_path)
        except OSError as exc:
            raise MonitorError("Approved log unavailable or unsafe.", 400) from exc
        second = self._observe(pid)
        if (
            second is None
            or second.key() != first.key()
            or second.state in {"Z", "X", "x"}
        ):
            raise MonitorError("Process changed during registration.", 409)
        data = {
            "identity": dataclasses.asdict(first),
            "status": "running",
            "reason": None,
            "input_fingerprint": fingerprint,
            "last_observed_at": self.clock(),
            "log": log,
        }
        try:
            row = self.registry.register(
                OWNER,
                request_id,
                "monitor_observation",
                label,
                data,
                single_active=True,
            )
        except jobs.JobError as exc:
            # Concurrent replay has newly sampled timestamps but the same input.
            prior = self.registry.by_native(OWNER, request_id)
            if prior and prior["payload"].get("input_fingerprint") == fingerprint:
                return self._public(prior)
            raise MonitorError(str(exc), exc.code) from exc
        self.poll_once()
        return self.get(row["id"])

    def _public(self, row):
        result = jobs.public(row)
        data = self._data(row)
        result.update(
            status=data["status"],
            reason=data.get("reason"),
            last_observed_at=data.get("last_observed_at"),
            stale=row["lifecycle"] != "settled"
            and self.clock() - data.get("last_observed_at", 0) > 6,
            log_registered=data.get("log") is not None,
        )
        return result

    def get(self, job_id):
        row = self.registry.get(job_id, OWNER)
        if row is None:
            raise MonitorError("Unknown build registration.", 404)
        return self._public(row)

    def latest(self):
        rows = self.registry.list(OWNER)
        return self._public(rows[0]) if rows else None

    def poll_once(self):
        for row in self.registry.list(OWNER):
            if row["lifecycle"] == "settled":
                continue
            data = dict(self._data(row))
            outcome = None
            if row["lifecycle"] == "cancellation_requested":
                data.update(status="monitoring_stopped", reason="owner_detached")
                outcome = "monitoring_stopped"
            else:
                saved = Identity(**data["identity"])
                try:
                    observed = self._observe(saved.pid)
                    reason = None
                    if observed is None:
                        reason = "process_absent"
                    elif observed.key() != saved.key():
                        reason = "identity_changed"
                    elif observed.state in {"Z", "X", "x"}:
                        reason = "process_exited"
                    data.update(
                        status="ended_unknown" if reason else "running",
                        reason=reason,
                        last_observed_at=self.clock(),
                    )
                    outcome = "ended_unknown" if reason else None
                except MonitorError:
                    data.update(
                        status="observation_unknown", reason="observer_unavailable"
                    )
            try:
                if outcome:
                    self.registry.settle(
                        row["id"], OWNER, outcome, data, row["revision"]
                    )
                else:
                    self.registry.update(row["id"], OWNER, data, row["revision"])
            except jobs.JobError as exc:
                if exc.code != 409:
                    raise
                # A request or detach won the race; never overwrite it.

    def stop_monitoring(self, job_id):
        row = self.registry.get(job_id, OWNER)
        if row is None:
            raise MonitorError("Unknown build registration.", 404)
        if row["lifecycle"] != "settled":
            self.registry.request_cancel(job_id)
            self.poll_once()
        result = self.get(job_id)
        result["work_stopped"] = False
        return result

    def tail(self, job_id, max_lines=80):
        if (
            isinstance(max_lines, bool)
            or not isinstance(max_lines, int)
            or not 1 <= max_lines <= 80
        ):
            raise MonitorError("Tail lines must be 1 to 80.", 400)
        row = self.registry.get(job_id, OWNER)
        if row is None:
            raise MonitorError("Unknown build registration.", 404)
        binding = self._data(row).get("log")
        unavailable = {
            "available": False,
            "text": "",
            "reason": "approved_log_unavailable",
        }
        if not binding:
            return unavailable
        try:
            with _open_log(binding["root"], binding["relative"]) as (root_fd, fd):
                before = os.fstat(fd)
                if (
                    _inode(os.fstat(root_fd)) != binding["root_identity"]
                    or _inode(before) != binding["file_identity"]
                ):
                    return unavailable
                offset = max(0, before.st_size - MAX_READ)
                raw = os.pread(fd, min(MAX_READ, before.st_size), offset)
                after = os.fstat(fd)
                if (
                    _inode(after) != _inode(before)
                    or after.st_size < before.st_size
                    or len(raw) != before.st_size - offset
                ):
                    return unavailable
                # Verify the named path again, not just the still-open old inode.
                if _binding(binding["root"], binding["relative"]) != binding:
                    return unavailable
                decoded = _safe_text(raw.decode("utf-8", errors="replace"), MAX_READ)
                lines = decoded.splitlines()
                chosen = "\n".join(lines[-max_lines:])
                return {
                    "available": True,
                    "text": chosen[-MAX_TEXT:],
                    "bytes_read": len(raw),
                    "truncated": offset > 0
                    or len(lines) > max_lines
                    or len(chosen) > MAX_TEXT,
                    "partial_first_line": offset > 0,
                    "reason": None,
                }
        except (OSError, MonitorError):
            return unavailable


_service = None
_service_lock = threading.Lock()


def enabled():
    """Background observation opt-in, separate from owner-requested tools."""
    return (
        os.environ.get("JARVIS_BUILD_MONITOR", "0").lower()
        in {"1", "true", "on", "yes"}
        and os.environ.get("JARVIS_LOCAL") == "1"
    )


def tools_enabled():
    return (
        os.environ.get("JARVIS_BUILD_TOOLS", "1").lower() in {"1", "true", "on", "yes"}
        and os.environ.get("JARVIS_LOCAL") == "1"
    )


def service():
    global _service
    if not (tools_enabled() or enabled()):
        raise MonitorError("Build tools are disabled.")
    home = Path(os.environ.get("JARVIS_HOME", str(Path.home() / ".jarvis")))
    with _service_lock:
        root = home / "job_registry"
        if _service is None or _service.registry.root != Path(os.path.abspath(root)):
            _service = Monitor(jobs.Registry(root))
        return _service


def start_thread(interval=2.0):
    """One elected observer; stopping it never controls the registered build."""
    if not enabled():
        raise MonitorError("Background build monitoring is disabled.")
    mon = service()
    fd = os.open(
        mon.registry.root / "build-monitor.owner.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        os.close(fd)
        raise MonitorError("Unsafe monitor owner lock.")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    stop = threading.Event()

    def loop():
        try:
            while enabled() and not stop.is_set():
                with contextlib.suppress(jobs.JobError):
                    mon.poll_once()
                stop.wait(interval)
        finally:
            os.close(fd)

    thread = threading.Thread(target=loop, name="build-monitor", daemon=True)
    thread.start()
    return thread, stop


def owner_allowed(peer):
    """Conservative desktop-only gate; no detached private-phone work."""
    try:
        import ipaddress

        import phone_session
        import watch_mode

        return (
            ipaddress.ip_address(peer).is_loopback
            and not phone_session.private()
            and not phone_session.guest_required()
            and not watch_mode.guest_required()
            and not phone_session.has_private_calls()
        )
    except Exception:
        return False


def _endpoint(method, path, query, body, *, authorized, peer):
    """Strict bridge contract. Internal callers must provide actual auth/peer."""
    try:
        if not authorized or not owner_allowed(peer):
            raise MonitorError("Owner desktop authorization required.", 403)
        if not tools_enabled():
            raise MonitorError("Build tools are disabled.")
        if not isinstance(query, dict):
            raise MonitorError("Invalid monitor query.", 400)
        mon = service()
        if method == "POST" and path == "/build-monitor/register":
            required = {"request_id", "pid", "label"}
            allowed = required | {"workspace_root", "log_relative_path"}
            if (
                query
                or not isinstance(body, dict)
                or set(body) - allowed
                or not required <= set(body)
            ):
                raise MonitorError("Invalid build registration body.", 400)
            prior = (
                mon.registry.by_native(OWNER, body["request_id"])
                if isinstance(body["request_id"], str)
                else None
            )
            row = mon.register(**body)
            return (200 if prior else 201), {"ok": True, "job": row}
        if method == "GET" and path == "/build-monitor/latest":
            if query:
                raise MonitorError("Invalid monitor query.", 400)
            return 200, {"ok": True, "job": mon.latest()}
        matched = re.fullmatch(r"/build-monitor/(job-[0-9a-f]{32})(/tail|/stop)?", path)
        if matched:
            job_id, action = matched.groups()
            if method == "GET" and action is None:
                if query:
                    raise MonitorError("Invalid monitor query.", 400)
                return 200, {"ok": True, "job": mon.get(job_id)}
            if method == "GET" and action == "/tail":
                if set(query) - {"lines"}:
                    raise MonitorError("Invalid tail query.", 400)
                values = query.get("lines", ["80"])
                if (
                    not isinstance(values, list)
                    or len(values) != 1
                    or not re.fullmatch(r"[0-9]{1,2}", values[0])
                ):
                    raise MonitorError("Invalid tail line count.", 400)
                return 200, dict(ok=True, **mon.tail(job_id, int(values[0])))
            if method == "POST" and action == "/stop":
                if query or body != {}:
                    raise MonitorError("Stop monitoring takes an empty body.", 400)
                return 200, {
                    "ok": True,
                    "job": mon.stop_monitoring(job_id),
                    "say": "Monitoring stopped; the build was left untouched.",
                }
        raise MonitorError("Unknown monitor route.", 404)
    except jobs.JobError as exc:
        return exc.code, {"ok": False, "error": str(exc)}


def endpoint(method, path, query, body, *, authorized, peer):
    code, result = _endpoint(
        method, path, query, body, authorized=authorized, peer=peer
    )
    # A private call, guest transition or revocation during a read cannot disclose it.
    if code < 400 and not owner_allowed(peer):
        return 403, {"ok": False, "error": "Owner desktop authorization changed."}
    return code, result
