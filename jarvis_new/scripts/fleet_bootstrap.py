#!/usr/bin/env python3
"""Make this computer linkable by your other Jarvis devices over Tailscale.

One idempotent command per device. Stdlib only; Windows, Linux and macOS.

    python scripts/fleet_bootstrap.py [--dry-run] [--uninstall]
                                      [--skip-firewall | --firewall-only]

Steps: Tailscale running -> local loopback token -> fleet listener starts at
login (Scheduled Task / systemd --user / LaunchAgent) -> Windows firewall rule
for TCP 4318 from the tailnet -> listener answers /fleet/hello on the tailnet
IP. The main bridge (127.0.0.1:4317) is not bound to any tailnet address.

Every OS command is an argv list (never shell=True). The builders below are
pure functions so tests can assert them without touching the system.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

DEFAULT_FLEET_PORT = 4318
TASK_NAME = "JarvisFleet"
OLD_TASK_NAME = "JarvisFleetBridge"  # pre-zero-install bridge task, removed here
FIREWALL_RULE = "Jarvis Fleet"
SYSTEMD_UNIT = "jarvis-fleet.service"
DESKTOP_FILE = "jarvis-fleet.desktop"
LAUNCHD_LABEL = "com.jarvis.fleet"
OLD_BIND_ENV = "JARVIS_BRIDGE_BIND"  # old model: bridge bound to the tailnet IP
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
REPO_ROOT = Path(__file__).resolve().parent.parent
LISTENER_SCRIPT = REPO_ROOT / "src" / "fleet_server.py"
MIN_PYTHON = (3, 11)


# ---------------------------------------------------------------- pure helpers


def current_os() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def fleet_port(env: dict | None = None) -> int:
    """JARVIS_FLEET_PORT, default 4318. Invalid values fall back to the default."""
    raw = str((os.environ if env is None else env).get("JARVIS_FLEET_PORT", "")).strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_FLEET_PORT
    return port if 1 <= port <= 65535 else DEFAULT_FLEET_PORT


def jarvis_home(env: dict | None = None, home: Path | None = None) -> Path:
    env = os.environ if env is None else env
    raw = str(env.get("JARVIS_HOME", "")).strip()
    return Path(raw) if raw else (home or Path.home()) / ".jarvis"


def windows_user(env: dict | None = None) -> str:
    """DOMAIN\\user for icacls, or just user when no domain is set."""
    env = os.environ if env is None else env
    name = str(env.get("USERNAME", "")).strip()
    domain = str(env.get("USERDOMAIN", "")).strip()
    return f"{domain}\\{name}" if domain and name else name


def device_name(host: str) -> str:
    """Lowercase slug, the name peers use (`approve <name>`, `ask <name> ...`)."""
    return re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-") or "jarvis"


def tailscale_candidates(
    os_name: str, which: Callable[[str], str | None] = shutil.which
) -> list[str]:
    found: list[str] = []
    on_path = which("tailscale")
    if on_path:
        found.append(on_path)
    if os_name == "windows":
        found.append(r"C:\Program Files\Tailscale\tailscale.exe")
    elif os_name == "darwin":
        found.append("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    return found


def find_tailscale(
    os_name: str,
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[str], bool] = os.path.isfile,
) -> str | None:
    for candidate in tailscale_candidates(os_name, which):
        if exists(candidate):
            return candidate
    return None


def _is_tailnet_v4(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in TAILNET_V4
    except ValueError:
        return False


def parse_tailscale_status(data: dict) -> dict:
    """Summarise `tailscale status --json`: backend state, tailnet IPv4, login, host."""
    self_node = data.get("Self") or {}
    users = data.get("User") or {}
    ips = list(data.get("TailscaleIPs") or self_node.get("TailscaleIPs") or [])
    ipv4 = next((ip for ip in ips if _is_tailnet_v4(str(ip))), "")
    login = ""
    uid = self_node.get("UserID")
    if uid is not None and isinstance(users, dict):
        login = str((users.get(str(uid)) or {}).get("LoginName") or "").strip().lower()
    dns_first = str(self_node.get("DNSName") or "").split(".")[0]
    host = str(self_node.get("HostName") or dns_first or "")
    backend = str(data.get("BackendState") or "")
    return {
        "backend": backend,
        "running": backend == "Running",
        "ipv4": ipv4,
        "account": login,
        "host": host,
    }


def tailscale_fix(backend: str) -> str:
    if backend == "NeedsLogin":
        return "Sign in: run `tailscale up` and open the link it prints."
    if backend in ("", "Stopped"):
        return "Start Tailscale: run `tailscale up`."
    return "Run `tailscale up` and wait for BackendState Running."


def interpreter_for(
    repo: Path,
    os_name: str,
    sys_exe: str,
    exists: Callable[[str], bool] = os.path.isfile,
) -> str:
    """Repo .venv python if present, else this interpreter; pythonw.exe on Windows (no console)."""
    venv = repo / (
        ".venv/Scripts/python.exe" if os_name == "windows" else ".venv/bin/python"
    )
    exe = str(venv) if exists(str(venv)) else sys_exe
    if os_name == "windows":
        windowless = str(Path(exe).with_name("pythonw.exe"))
        if exists(windowless):
            return windowless
    return exe


def schtasks_create_argv(launcher: str, script: str) -> list[str]:
    tr = f'"{launcher}" "{script}"'
    return [
        "schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
        "/SC", "ONLOGON", "/RL", "LIMITED", "/F",
    ]  # fmt: skip


def schtasks_query_argv(name: str = TASK_NAME) -> list[str]:
    return ["schtasks", "/Query", "/TN", name]


def schtasks_run_argv(name: str = TASK_NAME) -> list[str]:
    return ["schtasks", "/Run", "/TN", name]


def schtasks_end_argv(name: str = TASK_NAME) -> list[str]:
    return ["schtasks", "/End", "/TN", name]


def schtasks_delete_argv(name: str = TASK_NAME) -> list[str]:
    return ["schtasks", "/Delete", "/TN", name, "/F"]


def firewall_add_argv(port: int) -> list[str]:
    return [
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name={FIREWALL_RULE}", "dir=in", "action=allow", "protocol=TCP",
        f"localport={port}", "remoteip=100.64.0.0/10",
    ]  # fmt: skip


def firewall_delete_argv() -> list[str]:
    return [
        "netsh",
        "advfirewall",
        "firewall",
        "delete",
        "rule",
        f"name={FIREWALL_RULE}",
    ]


def icacls_argv(path: Path | str, user: str) -> list[str]:
    return ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R,W)"]


def reg_query_env_argv(name: str) -> list[str]:
    return ["reg", "query", r"HKCU\Environment", "/v", name]


def reg_delete_env_argv(name: str) -> list[str]:
    return ["reg", "delete", r"HKCU\Environment", "/v", name, "/f"]


def systemctl_argv(*args: str) -> list[str]:
    return ["systemctl", "--user", *args]


def launchctl_bootout_argv(uid: int) -> list[str]:
    return ["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"]


def launchctl_bootstrap_argv(uid: int, plist_path: Path | str) -> list[str]:
    return ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)]


def _sd_quote(value: str) -> str:
    """systemd word: double quotes, backslash/quote escaped, % doubled (specifiers)."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def systemd_unit_text(
    python: str, script: str, workdir: str, env: dict | None = None
) -> str:
    lines = [
        "[Unit]",
        "Description=Jarvis fleet listener (tailnet only)",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={workdir.replace('%', '%%')}",
    ]
    for key, value in sorted((env or {}).items()):
        lines.append(f"Environment={_sd_quote(f'{key}={value}')}")
    lines += [
        f"ExecStart={_sd_quote(python)} {_sd_quote(script)}",
        "Restart=on-failure",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def systemd_user_dir(env: dict | None = None, home: Path | None = None) -> Path:
    env = os.environ if env is None else env
    base = str(env.get("XDG_CONFIG_HOME", "")).strip()
    return (
        (Path(base) if base else (home or Path.home()) / ".config") / "systemd" / "user"
    )


def _desktop_quote(value: str) -> str:
    """Desktop Entry Exec word: quoted; reserved chars backslash-escaped; % doubled."""
    escaped = value.replace("\\", "\\\\")
    for ch in ('"', "`", "$"):
        escaped = escaped.replace(ch, "\\" + ch)
    return '"' + escaped.replace("%", "%%") + '"'


def desktop_entry_text(python: str, script: str) -> str:
    exec_line = f"{_desktop_quote(python)} {_desktop_quote(script)}"
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=Jarvis Fleet",
            f"Exec={exec_line}",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            "",
        ]
    )


def launchd_plist(python: str, script: str, workdir: str, log_path: str) -> bytes:
    return plistlib.dumps(
        {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": [python, script],
            "WorkingDirectory": workdir,
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": log_path,
            "StandardErrorPath": log_path,
        },
        fmt=plistlib.FMT_XML,
    )


def hello_url(ip: str, port: int) -> str:
    return f"http://{ip}:{port}/fleet/hello"


def parse_hello(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("ok") is True else None


def new_token() -> str:
    """32 random bytes as 64 hex chars (the loopback bridge token format)."""
    return secrets.token_hex(32)


# ------------------------------------------------------------------ installer


class Bootstrap:
    """Runs the steps and prints an OK/WARN/FAIL checklist.

    Dry run: read-only queries still run (tailscale status, task/service probes);
    every change is printed as PLAN and skipped; no files are written.
    """

    def __init__(
        self,
        *,
        dry_run: bool,
        os_name: str,
        home: Path,
        repo: Path = REPO_ROOT,
        port: int = DEFAULT_FLEET_PORT,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        is_admin: Callable[[], bool] | None = None,
        out: Callable[[str], None] = print,
        env: dict | None = None,
    ) -> None:
        self.dry_run = dry_run
        self.os = os_name
        self.home = home
        self.repo = repo
        self.port = port
        self.run = run
        self.out = out
        self.env = dict(os.environ if env is None else env)
        self.is_admin = is_admin or self._default_admin
        self.failures = 0
        self.firewall_ok = False
        self.tailnet_ip = ""
        self.account = ""
        self.name = ""

    # -- output
    def _line(self, tag: str, msg: str) -> None:
        self.out(f"[{tag:<4}] {msg}")

    def ok(self, msg: str) -> None:
        self._line("OK", msg)

    def done(self, msg: str) -> None:
        """OK for a finished change; PLAN instead while dry run."""
        self.plan(msg) if self.dry_run else self.ok(msg)

    def warn(self, msg: str) -> None:
        self._line("WARN", msg)

    def fail(self, msg: str) -> None:
        self.failures += 1
        self._line("FAIL", msg)

    def plan(self, msg: str) -> None:
        self._line("PLAN", msg)

    # -- command and file helpers (the only places that change the system)
    def _exec(
        self, argv: list[str], *, mutate: bool = True
    ) -> tuple[int | None, str, str]:
        """Run argv without a shell. Returns (rc, stdout, stderr); rc None = not run."""
        if mutate and self.dry_run:
            self.plan(f"run: {subprocess.list2cmdline(argv)}")
            return None, "", ""
        try:
            proc = self.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, "", str(exc)
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def _write(self, path: Path, data: str | bytes) -> bool:
        if self.dry_run:
            self.plan(f"write {path}")
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
        return True

    def _remove(self, path: Path) -> None:
        if self.dry_run:
            self.plan(f"remove {path}")
        else:
            path.unlink()

    def _default_admin(self) -> bool:
        if self.os != "windows":
            return os.geteuid() == 0 if hasattr(os, "geteuid") else False
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            return False

    # -- steps
    def check_tailscale(self) -> dict | None:
        binary = find_tailscale(self.os)
        if not binary:
            self.fail(
                "Tailscale CLI not found. Install from https://tailscale.com/download, then `tailscale up`."
            )
            return None
        rc, out, err = self._exec([binary, "status", "--json"], mutate=False)
        if rc != 0:
            self.fail(
                f"`tailscale status` failed: {(err or out).strip().splitlines()[:1]}"
            )
            return None
        try:
            status = parse_tailscale_status(json.loads(out))
        except ValueError:
            self.fail("`tailscale status --json` returned no JSON.")
            return None
        if not status["running"]:
            self.fail(
                f"Tailscale BackendState={status['backend'] or 'unknown'}. {tailscale_fix(status['backend'])}"
            )
            return None
        if not status["ipv4"]:
            self.fail("Tailscale has no 100.x IPv4 address. Run: tailscale up")
            return None
        self.tailnet_ip = status["ipv4"]
        self.account = status["account"]
        self.name = device_name(status["host"])
        self.ok(
            f"Tailscale running: {self.tailnet_ip}, device {self.name}, account {self.account or 'unknown'}"
        )
        return status

    def ensure_token(self) -> None:
        token = self.home / "bridge_token"
        if token.is_file() and token.read_text(encoding="utf-8").strip():
            self.ok(f"local loopback token kept: {token}")
        else:
            if self.dry_run:
                self.plan(f"create {token} (32 random bytes, hex)")
            else:
                self.home.mkdir(parents=True, exist_ok=True)
                token.write_text(new_token(), encoding="utf-8")
                self.ok(f"created local loopback token: {token}")
        if self.dry_run:
            self.plan(f"restrict {token} to the current user")
        elif self.os == "windows":
            rc, _, err = self._exec(icacls_argv(token, windows_user(self.env)))
            if rc == 0:
                self.ok("token restricted to current user (icacls)")
            else:
                self.warn(f"icacls failed on {token}: {err.strip()}")
        else:
            try:
                os.chmod(token, 0o600)
                self.ok("token mode 0600")
            except OSError as exc:
                self.warn(f"chmod failed on {token}: {exc}")

    def legacy_cleanup(self) -> None:
        """Remove the old token-pairing setup: bridge tailnet bind and its task."""
        if self.os == "windows":
            rc, _, _ = self._exec(schtasks_query_argv(OLD_TASK_NAME), mutate=False)
            if rc == 0:
                self._exec(schtasks_end_argv(OLD_TASK_NAME))
                rc, _, err = self._exec(schtasks_delete_argv(OLD_TASK_NAME))
                if rc == 0 or self.dry_run:
                    self.done(f"removed old task {OLD_TASK_NAME}")
                else:
                    self.warn(
                        f"could not remove old task {OLD_TASK_NAME}: {err.strip()}"
                    )
            rc, _, _ = self._exec(reg_query_env_argv(OLD_BIND_ENV), mutate=False)
            if rc == 0:
                self._exec(reg_delete_env_argv(OLD_BIND_ENV))
                self.warn(
                    f"cleared old {OLD_BIND_ENV}; restart Jarvis so the bridge drops its tailnet bind"
                )
        elif OLD_BIND_ENV in self.env:
            self.warn(
                f"{OLD_BIND_ENV} is set in this environment. Unset it: the bridge must stay on loopback."
            )

    def firewall(self) -> None:
        if self.os != "windows":
            return
        add = subprocess.list2cmdline(firewall_add_argv(self.port))
        if not self.is_admin():
            self.warn(
                f"firewall rule not added (needs Administrator). Run once in an elevated PowerShell: {add}"
            )
            return
        self._exec(firewall_delete_argv())
        rc, _, err = self._exec(firewall_add_argv(self.port))
        if rc == 0 or self.dry_run:
            self.firewall_ok = True
            self.done(
                f"firewall rule '{FIREWALL_RULE}': TCP {self.port} from 100.64.0.0/10"
            )
        else:
            self.warn(f"firewall rule failed: {err.strip() or 'unknown error'}")

    def _systemd_available(self) -> bool:
        if not shutil.which("systemctl"):
            return False
        rc, _, _ = self._exec(systemctl_argv("show-environment"), mutate=False)
        return rc == 0

    def register_listener(self) -> None:
        python = interpreter_for(self.repo, self.os, sys.executable)
        script = str(LISTENER_SCRIPT)
        workdir = str(self.repo)
        if self.os == "windows":
            self._exec(schtasks_create_argv(python, script))
            self.done(
                f"scheduled task '{TASK_NAME}' registered (at logon, hidden, {python})"
            )
            self._exec(schtasks_end_argv())
            rc, _, err = self._exec(schtasks_run_argv())
            if rc != 0 and not self.dry_run:
                self.warn(
                    f"could not start task now (starts at next logon): {err.strip()}"
                )
        elif self.os == "darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
            log = str(self.home / "logs" / "fleet.log")
            self._write(plist, launchd_plist(python, script, workdir, log))
            uid = os.getuid() if hasattr(os, "getuid") else 0
            self._exec(launchctl_bootout_argv(uid))
            rc, _, err = self._exec(launchctl_bootstrap_argv(uid, plist))
            if rc != 0 and not self.dry_run:
                self.fail(f"launchctl bootstrap failed: {err.strip()}")
            else:
                self.done(f"LaunchAgent {LAUNCHD_LABEL} loaded: {plist}")
        elif self._systemd_available():
            unit_dir = systemd_user_dir(self.env)
            unit = unit_dir / SYSTEMD_UNIT
            text = systemd_unit_text(
                python, script, workdir, {"JARVIS_HOME": str(self.home)}
            )
            if not (unit.is_file() and unit.read_text(encoding="utf-8") == text):
                self._write(unit, text)
            self._exec(systemctl_argv("daemon-reload"))
            self._exec(systemctl_argv("enable", SYSTEMD_UNIT))
            rc, _, err = self._exec(systemctl_argv("restart", SYSTEMD_UNIT))
            if rc != 0 and not self.dry_run:
                self.fail(
                    f"systemctl --user restart {SYSTEMD_UNIT} failed: {err.strip()}"
                )
            else:
                self.done(f"systemd user unit {SYSTEMD_UNIT} enabled and started")
        else:
            entry = (
                Path(self.env.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
                / "autostart"
                / DESKTOP_FILE
            )
            self._write(entry, desktop_entry_text(python, script))
            if self.dry_run:
                self.plan(f"start {python} {script} (detached)")
            else:
                self.warn(
                    "systemd --user unavailable: using autostart entry; listener started detached"
                )
                subprocess.Popen(  # argv list, no shell
                    [python, script],
                    cwd=workdir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )

    def verify_listener(self) -> None:
        url = hello_url(self.tailnet_ip, self.port)
        if self.dry_run:
            self.plan(f"verify GET {url}")
            return
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        hello = None
        for _ in range(15):
            try:
                with opener.open(url, timeout=4) as resp:
                    hello = parse_hello(resp.read(65536).decode("utf-8", "replace"))
            except (OSError, ValueError):
                hello = None
            if hello:
                break
            time.sleep(1)
        if hello:
            trusted = "yes" if hello.get("trusted") else "no"
            self.ok(
                f"listener answers {url} (name {hello.get('name')}, trusts you: {trusted})"
            )
        elif self.os == "windows" and not self.firewall_ok:
            self.warn(
                f"listener did not answer {url}; the firewall rule is missing, which blocks it"
            )
        else:
            self.fail(
                f"listener did not answer {url}. Check logs in {self.home / 'logs'}"
            )

    def next_steps(self) -> None:
        self.out("")
        self.out(f"Device   : {self.name}")
        self.out(f"Account  : {self.account or 'unknown'}")
        self.out(f"Listener : http://{self.tailnet_ip}:{self.port} (tailnet only)")
        self.out("")
        self.out("Next:")
        self.out(
            '  - Same Tailscale account: devices link automatically. Say "list my devices".'
        )
        self.out(
            "  - Another Tailscale account: share this device to it in the Tailscale admin"
        )
        self.out(
            "    console (Machines > Share). The other account's devices then show as pending."
        )
        self.out('    On the device that should accept, say "approve <account>" or run')
        self.out("    python src/fleet.py approve <account>.")

    # -- entry points
    def install(self, *, skip_firewall: bool = False) -> int:
        status = self.check_tailscale()
        if status is None:
            return 1
        self.ensure_token()
        self.legacy_cleanup()
        if not LISTENER_SCRIPT.is_file():
            self.fail(f"listener script missing: {LISTENER_SCRIPT}")
            return 1
        if skip_firewall:
            self.out("[SKIP] firewall: handled by the elevated step")
        else:
            self.firewall()
        self.register_listener()
        self.verify_listener()
        if self.failures == 0:
            self.next_steps()
        self.out("")
        if self.dry_run:
            self.out("Dry run: nothing was changed.")
        elif self.failures:
            self.out("Finished with FAIL lines above; fix them and re-run.")
        else:
            self.out("Done.")
        return 1 if self.failures else 0

    def firewall_only(self) -> int:
        if self.os != "windows":
            self.warn("the firewall step is Windows only")
            return 0
        self.firewall()
        return 1 if self.failures else 0

    def uninstall(self) -> int:
        if self.os == "windows":
            for name in (TASK_NAME, OLD_TASK_NAME):
                rc, _, _ = self._exec(schtasks_query_argv(name), mutate=False)
                if rc == 0:
                    self._exec(schtasks_end_argv(name))
                    self._exec(schtasks_delete_argv(name))
                    self.done(f"removed scheduled task {name}")
                else:
                    self.ok(f"scheduled task {name} not registered")
            if self.is_admin() or self.dry_run:
                self._exec(firewall_delete_argv())
                self.done(f"firewall rule '{FIREWALL_RULE}' removed")
            else:
                self.warn(
                    f"firewall rule kept (needs Administrator): {subprocess.list2cmdline(firewall_delete_argv())}"
                )
        elif self.os == "darwin":
            plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
            uid = os.getuid() if hasattr(os, "getuid") else 0
            self._exec(launchctl_bootout_argv(uid))
            if plist.is_file():
                self._remove(plist)
            self.done(f"LaunchAgent {LAUNCHD_LABEL} removed")
        else:
            if shutil.which("systemctl"):
                self._exec(systemctl_argv("disable", "--now", SYSTEMD_UNIT))
            unit = systemd_user_dir(self.env) / SYSTEMD_UNIT
            if unit.is_file():
                self._remove(unit)
                self._exec(systemctl_argv("daemon-reload"))
            entry = (
                Path(self.env.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
                / "autostart"
                / DESKTOP_FILE
            )
            if entry.is_file():
                self._remove(entry)
            self.done("systemd unit and autostart entry removed")
        self.ok(
            f"local token kept: {self.home / 'bridge_token'} (delete it by hand if you want)"
        )
        self.out("")
        self.out(
            "Dry run: nothing was changed."
            if self.dry_run
            else "Jarvis Fleet listener removed from this computer."
        )
        self.out(
            "Peers that accepted this device keep their trust entry; remove it on them."
        )
        return 1 if self.failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Make this computer linkable by your other Jarvis devices."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan; change nothing"
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the listener service and firewall rule",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--skip-firewall",
        action="store_true",
        help="leave the firewall step to an elevated run",
    )
    group.add_argument(
        "--firewall-only",
        action="store_true",
        help="run only the Windows firewall step",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.version_info < MIN_PYTHON:
        print(f"[FAIL] Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required")
        return 1
    os_name = current_os()
    home = jarvis_home()
    boot = Bootstrap(
        dry_run=args.dry_run,
        os_name=os_name,
        home=home,
        port=fleet_port(),
    )
    print(f"Jarvis fleet bootstrap ({os_name}, home {home})")
    if args.uninstall:
        return boot.uninstall()
    if args.firewall_only:
        return boot.firewall_only()
    return boot.install(skip_firewall=args.skip_firewall)


if __name__ == "__main__":
    sys.exit(main())
