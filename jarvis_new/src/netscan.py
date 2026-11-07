"""Protocol 12 scan layer: a real nmap sweep of the local subnet.

Everything here runs in the bridge process (never the HUD render thread).
The web view polls :func:`snapshot` through ``GET /protocol/state``.

Flow of one run (:func:`start`):

1. Preflight: ``command -v nmap`` plus the privilege mode (see below).
2. Active interface + CIDR from ``ip -j route`` / ``ip -j addr``, skipping
   virtual links (docker0, virbr0, tun/tap, VPN...).
3. Phase A, host discovery (``-sn``). Hosts found become the device list, so
   the count is exact from here on.
4. Phase B, ``-sV -O`` against exactly those hosts, streamed as XML
   (``-oX -``) with ``--stats-every`` ``<taskprogress>`` driving ``progress``.
5. The host machine is added from interface data (nmap reports no MAC for
   the machine it runs on), merged by IP if nmap listed it.

Privilege modes, decided once per run and never prompting mid-protocol unless
the pkexec fallback is all that is left:

* ``raw``: the nmap binary carries cap_net_raw (``protocols/setup.sh``) or we
  are root; runs ``--privileged``.
* ``pkexec``: polkit prompts once for the run.
* ``connect``: ``-sT``, no OS detection; the HUD is told.

Only fields nmap (or local interface data for the host) actually returned are
kept; nothing is invented.
"""

from __future__ import annotations

import atexit
import ipaddress
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Iterator

INSTALL_HINT = "nmap is not installed. Run: sudo dnf install nmap"
SETUP_HINT = "Raw-socket scanning is not enabled. Run protocols/setup.sh once."

#: Interfaces that are not the real LAN: containers, VMs, tunnels, VPNs.
_VIRTUAL = re.compile(
    r"^(lo|docker\d*|br-.*|virbr.*|veth.*|vnet.*|vmnet.*|vboxnet.*|tun\d*|tap\d*|"
    r"wg\d*|tailscale.*|zt.*|ppp\d*|ipsec.*|cni.*|flannel.*|podman.*|lxc.*|lxd.*|"
    r"dummy.*|bond.*|nordlynx|proton.*|mullvad.*|utun.*)$"
)

HOST_TIMEOUT = os.environ.get("JARVIS_NSCAN_HOST_TIMEOUT", "90s")
DISCOVERY_WEIGHT = 0.2
_STAGE_ORDER = ("scan", "service", "os")

# --------------------------------------------------------------- preflight


def nmap_path() -> str | None:
    return shutil.which("nmap")


def _has_raw_caps(nmap: str) -> bool:
    if os.geteuid() == 0:
        return True
    getcap = shutil.which("getcap") or "/usr/sbin/getcap"
    try:
        out = subprocess.run(
            [getcap, nmap], capture_output=True, text=True, timeout=3, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "cap_net_raw" in out


def privilege_mode(nmap: str | None = None) -> str:
    """ "raw" | "pkexec" | "connect" ("missing" without nmap)."""
    nmap = nmap or nmap_path()
    if not nmap:
        return "missing"
    forced = os.environ.get("JARVIS_NSCAN_MODE")
    if forced in ("raw", "pkexec", "connect"):
        return forced
    if _has_raw_caps(nmap):
        return "raw"
    if shutil.which("pkexec") and (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return "pkexec"
    return "connect"


def preflight() -> dict[str, Any]:
    """Launch-time check surfaced on the HUD ("detect nmap at launch")."""
    nmap = nmap_path()
    if not nmap:
        return {"nmap": False, "mode": "missing", "message": INSTALL_HINT}
    mode = privilege_mode(nmap)
    message = ""
    if mode == "pkexec":
        message = SETUP_HINT + " Until then a polkit prompt appears once per scan."
    elif mode == "connect":
        message = SETUP_HINT + " Using TCP connect scan without OS detection."
    return {"nmap": True, "mode": mode, "message": message}


# --------------------------------------------------------------- interface


def is_virtual(name: str) -> bool:
    return bool(_VIRTUAL.match(name or ""))


def pick_interface(routes: list[dict], addrs: list[dict]) -> dict | None:
    """Active LAN interface from ``ip -j route`` + ``ip -j addr`` output. Pure.

    Prefers the lowest-metric default route on a non-virtual link, then any
    non-virtual link-scope route. Returns name/ip/prefix/cidr/mac/gateway.
    """
    by_name = {a.get("ifname"): a for a in addrs if isinstance(a, dict)}

    def usable(dev: str) -> bool:
        a = by_name.get(dev)
        if not a or is_virtual(dev):
            return False
        return "UP" in (a.get("flags") or []) or a.get("operstate") in ("UP", "UNKNOWN")

    defaults = sorted(
        (r for r in routes if r.get("dst") == "default" and r.get("dev")),
        key=lambda r: int(r.get("metric", 0) or 0),
    )
    candidates = [(r["dev"], r.get("gateway")) for r in defaults]
    candidates += [
        (r["dev"], None)
        for r in routes
        if r.get("dev")
        and r.get("scope") == "link"
        and r.get("dst") not in (None, "default")
        and "/" in str(r.get("dst"))
    ]
    for dev, gateway in candidates:
        if not usable(dev):
            continue
        a = by_name[dev]
        v4 = [
            i
            for i in a.get("addr_info", [])
            if i.get("family") == "inet" and i.get("local")
        ]
        if not v4:
            continue
        info = v4[0]
        prefix = int(info.get("prefixlen", 24))
        return {
            "name": dev,
            "ip": info["local"],
            "prefix": prefix,
            "mac": (a.get("address") or "").lower() or None,
            "gateway": gateway,
        }
    return None


def subnet_for(ip: str, prefix: int) -> tuple[str, str | None]:
    """CIDR to scan and an optional note. Huge networks narrow to the /24 the
    host sits in so a sweep stays a sweep. Pure."""
    note = None
    if prefix < 24:
        note = f"Network is /{prefix}; scanning the local /24 only."
        prefix = 24
    net = ipaddress.ip_interface(f"{ip}/{prefix}").network
    return str(net), note


def _ip_json(args: list[str]) -> list[dict]:
    out = subprocess.run(
        ["ip", "-j", *args], capture_output=True, text=True, timeout=5, check=False
    ).stdout
    try:
        data = json.loads(out or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def find_interface() -> dict | None:
    iface = pick_interface(_ip_json(["route"]), _ip_json(["addr"]))
    if iface is None:
        return None
    cidr, note = subnet_for(iface["ip"], iface["prefix"])
    iface["cidr"] = cidr
    iface["note"] = note
    return iface


# --------------------------------------------------------------- host entry


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text("utf-8", "replace").strip() or None
    except OSError:
        return None


def _host_type() -> str | None:
    chassis = _read("/sys/class/dmi/id/chassis_type")
    if chassis and chassis.isdigit():
        n = int(chassis)
        if n in (8, 9, 10, 14, 31, 32):
            return "laptop"
        if n in (3, 4, 5, 6, 7, 13, 15, 16, 24):
            return "desktop"
    for bat in Path("/sys/class/power_supply").glob("BAT*"):
        if (bat / "present").exists():
            return "laptop"
    return None


def host_entry(iface: dict) -> dict:
    """The machine we run on, from local interface data (nmap gives it no MAC)."""
    entry: dict[str, Any] = {"ip": iface["ip"], "isHost": True}
    if iface.get("mac"):
        entry["mac"] = iface["mac"].upper()
    name = socket.gethostname()
    if name:
        entry["hostnames"] = [name]
    osr = _read("/etc/os-release") or ""
    pretty = re.search(r'^PRETTY_NAME="?(.*?)"?$', osr, re.M)
    if pretty:
        entry["os"] = f"{pretty.group(1)} (Linux {platform.release()})"
    kind = _host_type()
    if kind:
        entry["type"] = kind
    entry["iface"] = iface["name"]
    return entry


# --------------------------------------------------------------- XML


def _num(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def parse_host(elem: ET.Element) -> dict | None:
    """One nmap ``<host>`` -> device dict, or None for a down host. Pure."""
    status = elem.find("status")
    if status is not None and status.get("state") != "up":
        return None
    dev: dict[str, Any] = {}
    for addr in elem.findall("address"):
        kind = addr.get("addrtype")
        if kind == "ipv4":
            dev["ip"] = addr.get("addr")
        elif kind == "mac":
            dev["mac"] = addr.get("addr")
            dev["vendor"] = addr.get("vendor")
        elif kind == "ipv6" and "ip6" not in dev:
            dev["ip6"] = addr.get("addr")
    if "ip" not in dev:
        return None
    names = []
    for hn in elem.findall("hostnames/hostname"):
        n = hn.get("name")
        if n and n not in names:
            names.append(n)
    dev["hostnames"] = names
    times = elem.find("times")
    srtt = _num(times.get("srtt")) if times is not None else None
    if srtt:
        dev["latencyMs"] = round(srtt / 1000.0, 2)
    up = elem.find("uptime")
    if up is not None:
        secs = _num(up.get("seconds"))
        if secs is not None:
            dev["uptimeSeconds"] = int(secs)
        dev["lastBoot"] = up.get("lastboot")
    dist = elem.find("distance")
    if dist is not None and dist.get("value"):
        dev["hops"] = int(dist.get("value"))
    matches = []
    for m in elem.findall("os/osmatch"):
        classes = []
        for c in m.findall("osclass"):
            classes.append(
                _clean(
                    {
                        "type": c.get("type"),
                        "vendor": c.get("vendor"),
                        "family": c.get("osfamily"),
                        "gen": c.get("osgen"),
                        "accuracy": c.get("accuracy"),
                    }
                )
            )
        matches.append(
            _clean(
                {
                    "name": m.get("name"),
                    "accuracy": _num(m.get("accuracy")),
                    "classes": classes,
                }
            )
        )
    if matches:
        best = matches[0]
        dev["os"] = best.get("name")
        dev["osAccuracy"] = best.get("accuracy")
        if len(matches) > 1:
            dev["osAlternatives"] = [
                _clean({"name": m.get("name"), "accuracy": m.get("accuracy")})
                for m in matches[1:3]
            ]
        types = [c["type"] for c in best.get("classes", []) if c.get("type")]
        if types:
            dev["type"] = types[0]
    ports = []
    for p in elem.findall("ports/port"):
        st = p.find("state")
        if st is None or st.get("state") != "open":
            continue
        svc = p.find("service")
        ports.append(
            _clean(
                {
                    "port": int(p.get("portid", 0)),
                    "proto": p.get("protocol"),
                    "service": svc.get("name") if svc is not None else None,
                    "product": svc.get("product") if svc is not None else None,
                    "version": svc.get("version") if svc is not None else None,
                    "extra": svc.get("extrainfo") if svc is not None else None,
                    "tunnel": svc.get("tunnel") if svc is not None else None,
                }
            )
        )
    dev["ports"] = ports
    return _clean(dev)


def merge_device(base: dict, extra: dict) -> dict:
    """Overlay ``extra`` on ``base`` field by field; empty values never erase."""
    out = dict(base)
    for k, v in extra.items():
        if v not in (None, "", [], {}):
            out[k] = v
    return out


class XmlStream:
    """Incremental reader for nmap's ``-oX -`` stream.

    feed() text, get back ("progress", task, percent) and ("host", dict)
    events as soon as their elements close.
    """

    _PROGRESS = re.compile(r"<taskprogress\b[^>]*?/>")
    _HOST = re.compile(r"<host\b.*?</host>", re.S)

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> Iterator[tuple]:
        self._buf += chunk
        while True:
            host = self._HOST.search(self._buf)
            prog = self._PROGRESS.search(self._buf)
            nxt = min(
                (m for m in (host, prog) if m), key=lambda m: m.start(), default=None
            )
            if nxt is None:
                break
            self._buf = self._buf[nxt.end() :]
            try:
                if nxt is host:
                    dev = parse_host(ET.fromstring(nxt.group(0)))
                    if dev:
                        yield ("host", dev)
                else:
                    el = ET.fromstring(nxt.group(0))
                    pct = _num(el.get("percent"))
                    if pct is not None:
                        yield ("progress", el.get("task", ""), pct)
            except ET.ParseError:
                continue
        if len(self._buf) > 4_000_000:  # never grow without bound
            self._buf = self._buf[-100_000:]


# --------------------------------------------------------------- run state

_lock = threading.RLock()
_state: dict[str, Any] = {
    "run": 0,
    "phase": "idle",  # idle|discovering|scanning|done|error|aborted
    "progress": 0.0,
    "devices": [],
    "host": None,
    "subnet": None,
    "iface": None,
    "mode": None,
    "message": "",
    "notes": [],
    "startedAt": 0.0,
    "finishedAt": 0.0,
}
_proc: subprocess.Popen | None = None
_preflight_cache: dict[str, Any] = {}
ACTIVE = ("discovering", "scanning")


def snapshot() -> dict[str, Any]:
    with _lock:
        snap = {
            **_state,
            "devices": list(_state["devices"]),
            "notes": list(_state["notes"]),
        }
    snap["preflight"] = _preflight_cache or preflight()
    snap["serverTime"] = time.time()
    return snap


def active() -> bool:
    with _lock:
        return _state["phase"] in ACTIVE


def _set(**kw: Any) -> None:
    with _lock:
        _state.update(kw)


def _progress(value: float) -> None:
    with _lock:
        _state["progress"] = max(_state["progress"], min(1.0, value))


def refresh_preflight() -> dict[str, Any]:
    pf = preflight()
    _preflight_cache.clear()
    _preflight_cache.update(pf)
    return pf


def start() -> dict:
    """Begin a scan run. Returns at once: {"ok": True} or {"ok": False, "error"}."""
    pf = refresh_preflight()
    if not pf["nmap"]:
        return {"ok": False, "error": pf["message"]}
    with _lock:
        if _state["phase"] in ACTIVE:
            return {"ok": True, "already": True}
    iface = find_interface()
    if iface is None:
        return {"ok": False, "error": "No active network interface found."}
    notes = [n for n in (iface.get("note"), pf["message"] or None) if n]
    with _lock:
        _state.update(
            run=_state["run"] + 1,
            phase="discovering",
            progress=0.0,
            devices=[],
            host=host_entry(iface),
            subnet=iface["cidr"],
            iface=iface["name"],
            mode=pf["mode"],
            message="",
            notes=notes,
            startedAt=time.time(),
            finishedAt=0.0,
        )
        run = _state["run"]
    threading.Thread(target=_worker, args=(run, iface, pf["mode"]), daemon=True).start()
    return {"ok": True, "run": run}


def abort() -> None:
    """Stand down: kill nmap, and dismiss a finished run so the HUD restores."""
    global _proc
    with _lock:
        proc, _proc = _proc, None
        phase = _state["phase"]
        if phase in ACTIVE or phase == "done":
            _state["phase"] = "aborted"
            _state["finishedAt"] = time.time()
    if proc is not None:
        _kill(proc)


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    for pipe in (proc.stdout, proc.stderr):  # pkexec: nmap dies on SIGPIPE
        try:
            if pipe:
                pipe.close()
        except OSError:
            pass


atexit.register(abort)


def nmap_argv(mode: str, nmap: str, targets: list[str], phase: str) -> list[str]:
    """Command line for one phase. Pure."""
    base = [
        nmap,
        "-T3",
        "--host-timeout",
        HOST_TIMEOUT,
        "--stats-every",
        "1s",
        "-oX",
        "-",
    ]
    if phase == "discovery":
        argv = base + ["-sn"]
        if mode == "connect":
            argv += ["-PS21,22,23,25,80,135,139,443,445,3389,8080"]
    else:
        argv = base + ["-Pn", "-sV"]
        argv += ["-sS", "-O", "--osscan-guess"] if mode != "connect" else ["-sT"]
    if mode in ("raw", "pkexec"):
        argv.insert(1, "--privileged")
    if mode == "pkexec":
        argv.insert(0, "pkexec")
    return argv + targets


def _stream(
    argv: list[str], on_event: Callable[[tuple], None], run: int
) -> tuple[int, str]:
    """Run nmap, feeding XML events to ``on_event``. Returns (rc, stderr)."""
    global _proc
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    with _lock:
        if _state["run"] != run or _state["phase"] not in ACTIVE:
            _kill(proc)
            return -1, ""
        _proc = proc
    err: list[str] = []
    t = threading.Thread(
        target=lambda: err.append(proc.stderr.read() or ""), daemon=True
    )
    t.start()
    parser = XmlStream()
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.readline()
            if not chunk:
                break
            for ev in parser.feed(chunk):
                on_event(ev)
    except (OSError, ValueError):
        pass
    rc = proc.wait()
    t.join(timeout=1)
    with _lock:
        if _proc is proc:
            _proc = None
    return rc, "".join(err)


def _fail(run: int, message: str) -> None:
    with _lock:
        if _state["run"] == run and _state["phase"] in ACTIVE:
            _state.update(phase="error", message=message, finishedAt=time.time())


def _worker(run: int, iface: dict, mode: str) -> None:
    nmap = nmap_path() or "nmap"
    found: dict[str, dict] = {}

    def current() -> bool:
        with _lock:
            return _state["run"] == run and _state["phase"] in ACTIVE

    def publish() -> None:
        with _lock:
            if _state["run"] == run:
                _state["devices"] = sorted(
                    found.values(), key=lambda d: _ip_key(d["ip"])
                )

    # ---- phase A: discovery
    def on_discovery(ev: tuple) -> None:
        if ev[0] == "host":
            found[ev[1]["ip"]] = ev[1]
            publish()
        elif ev[0] == "progress":
            _progress(DISCOVERY_WEIGHT * ev[2] / 100.0)

    try:
        rc, err = _stream(
            nmap_argv(mode, nmap, [iface["cidr"]], "discovery"), on_discovery, run
        )
        if not current():
            return
        if mode == "connect":
            for ip in _neighbours(iface["name"], iface["cidr"]):
                found.setdefault(ip, {"ip": ip, "hostnames": []})
            publish()
        if rc != 0 and not found:
            if mode == "pkexec" and rc in (126, 127):
                _fallback_connect(run, iface, nmap)
                return
            _fail(
                run, (err.strip().splitlines() or ["nmap discovery failed."])[-1][:200]
            )
            return
        # The machine we run on: nmap omits its MAC, so overlay local data.
        me = host_entry(iface)
        found[me["ip"]] = merge_device(found.get(me["ip"], {}), me)
        publish()
        _progress(DISCOVERY_WEIGHT)
        _set(phase="scanning")

        # ---- phase B: ports, services, OS
        stage_idx: dict[str, int] = {}

        def on_scan(ev: tuple) -> None:
            if ev[0] == "host":
                ip = ev[1]["ip"]
                keep = {k: v for k, v in ev[1].items() if k not in ("mac", "vendor")}
                found[ip] = merge_device(found.get(ip, {"ip": ip}), keep)
                publish()
            elif ev[0] == "progress":
                task = _stage_name(ev[1])
                idx = stage_idx.setdefault(task, len(stage_idx))
                stages = 3 if mode != "connect" else 2
                frac = (min(idx, stages - 1) + ev[2] / 100.0) / stages
                _progress(DISCOVERY_WEIGHT + (1 - DISCOVERY_WEIGHT) * frac)

        targets = sorted(found, key=_ip_key)
        rc, err = _stream(nmap_argv(mode, nmap, targets, "scan"), on_scan, run)
        if not current():
            return
        if rc != 0 and not any(d.get("ports") or d.get("os") for d in found.values()):
            _fail(run, (err.strip().splitlines() or ["nmap scan failed."])[-1][:200])
            return
        _progress(1.0)
        with _lock:
            if _state["run"] == run and _state["phase"] in ACTIVE:
                _state.update(phase="done", progress=1.0, finishedAt=time.time())
    except Exception as exc:  # never let the worker die silently
        _fail(run, f"Scan failed: {exc}"[:200])


def _fallback_connect(run: int, iface: dict, nmap: str) -> None:
    """pkexec dismissed: restart the run as an unprivileged connect scan."""
    with _lock:
        if _state["run"] != run:
            return
        _state["mode"] = "connect"
        _state["notes"].append(
            "Authentication declined. Using TCP connect scan without OS detection."
        )
        _state["phase"] = "discovering"
        _state["progress"] = 0.0
    _worker(run, iface, "connect")


def _stage_name(task: str) -> str:
    low = task.lower()
    if "service" in low:
        return "service"
    if "os" in low:
        return "os"
    return "scan"


def _ip_key(ip: str) -> tuple:
    try:
        return (0, int(ipaddress.ip_address(ip)))
    except ValueError:
        return (1, 0)


def _neighbours(dev: str, cidr: str) -> list[str]:
    """Reachable neighbours from the kernel ARP table (connect-mode discovery)."""
    net = ipaddress.ip_network(cidr)
    out = []
    for n in _ip_json(["neigh", "show", "dev", dev]):
        state = n.get("state") or []
        ip = n.get("dst")
        if ip and "FAILED" not in state and "INCOMPLETE" not in state:
            try:
                if ipaddress.ip_address(ip) in net:
                    out.append(ip)
            except ValueError:
                pass
    return out
