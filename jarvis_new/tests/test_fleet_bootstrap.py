"""Fleet bootstrap: pure builders, Tailscale state parsing, dry-run safety (no system changes)."""

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fleet_bootstrap as fb  # noqa: E402

FAKE_EMAIL = "partner@example.com"
TS_BIN = "/usr/bin/tailscale"

STATUS_RUNNING = {
    "BackendState": "Running",
    "TailscaleIPs": ["100.77.6.93", "fd7a:115c:a1e0::c701:6c4"],
    "Self": {
        "UserID": 7,
        "HostName": "Living Room PC",
        "DNSName": "living-room-pc.tail.ts.net.",
    },
    "User": {"7": {"LoginName": "Owner@Example.com"}, "8": {"LoginName": FAKE_EMAIL}},
}


# ----------------------------------------------------------------- parsing


def test_parse_status_running_extracts_ip_account_host():
    st = fb.parse_tailscale_status(STATUS_RUNNING)
    assert st == {
        "backend": "Running",
        "running": True,
        "ipv4": "100.77.6.93",
        "account": "owner@example.com",
        "host": "Living Room PC",
    }


def test_parse_status_stopped_and_no_tailnet_ip():
    st = fb.parse_tailscale_status(
        {"BackendState": "NeedsLogin", "TailscaleIPs": ["fd7a:115c:a1e0::1"]}
    )
    assert st["running"] is False
    assert st["ipv4"] == ""
    assert st["account"] == ""


def test_parse_status_ignores_non_tailnet_ipv4():
    st = fb.parse_tailscale_status(
        {"BackendState": "Running", "TailscaleIPs": ["192.168.1.5", "100.64.0.9"]}
    )
    assert st["ipv4"] == "100.64.0.9"


def test_tailscale_fix_messages():
    assert "tailscale up" in fb.tailscale_fix("Stopped")
    assert "Sign in" in fb.tailscale_fix("NeedsLogin")
    assert "BackendState Running" in fb.tailscale_fix("Starting")


def test_find_tailscale_candidates_per_os():
    assert fb.tailscale_candidates("windows", which=lambda _: None) == [
        r"C:\Program Files\Tailscale\tailscale.exe"
    ]
    assert fb.tailscale_candidates("darwin", which=lambda _: "/opt/ts") == [
        "/opt/ts",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    ]
    assert (
        fb.find_tailscale("linux", which=lambda _: TS_BIN, exists=lambda p: p == TS_BIN)
        == TS_BIN
    )
    assert (
        fb.find_tailscale("linux", which=lambda _: None, exists=lambda _: False) is None
    )


# ----------------------------------------------------------- argv builders


def test_schtasks_create_argv_is_list_with_quoted_tr():
    argv = fb.schtasks_create_argv(
        r"C:\Py\pythonw.exe", r"C:\Jarvis\src\fleet_server.py"
    )
    assert argv == [
        "schtasks", "/Create", "/TN", "JarvisFleet",
        "/TR", r'"C:\Py\pythonw.exe" "C:\Jarvis\src\fleet_server.py"',
        "/SC", "ONLOGON", "/RL", "LIMITED", "/F",
    ]  # fmt: skip
    assert all(isinstance(a, str) for a in argv)


def test_schtasks_other_verbs():
    assert fb.schtasks_run_argv() == ["schtasks", "/Run", "/TN", "JarvisFleet"]
    assert fb.schtasks_end_argv("X") == ["schtasks", "/End", "/TN", "X"]
    assert fb.schtasks_delete_argv() == [
        "schtasks",
        "/Delete",
        "/TN",
        "JarvisFleet",
        "/F",
    ]
    assert fb.schtasks_query_argv(fb.OLD_TASK_NAME)[-1] == "JarvisFleetBridge"


def test_firewall_argv_allows_tailnet_only_on_fleet_port():
    argv = fb.firewall_add_argv(4318)
    assert argv[:5] == ["netsh", "advfirewall", "firewall", "add", "rule"]
    assert "localport=4318" in argv
    assert "remoteip=100.64.0.0/10" in argv
    assert "dir=in" in argv and "protocol=TCP" in argv
    assert "name=Jarvis Fleet" in argv
    assert "localport=4317" not in argv
    assert fb.firewall_delete_argv() == [
        "netsh", "advfirewall", "firewall", "delete", "rule", "name=Jarvis Fleet",
    ]  # fmt: skip


def test_icacls_and_reg_argv():
    assert fb.icacls_argv(Path(r"C:\h\bridge_token"), r"PC\me") == [
        "icacls", r"C:\h\bridge_token", "/inheritance:r", "/grant:r", r"PC\me:(R,W)",
    ]  # fmt: skip
    assert fb.reg_delete_env_argv("JARVIS_BRIDGE_BIND")[:3] == [
        "reg",
        "delete",
        r"HKCU\Environment",
    ]


def test_windows_user_domain_and_plain():
    assert fb.windows_user({"USERDOMAIN": "PC", "USERNAME": "me"}) == r"PC\me"
    assert fb.windows_user({"USERNAME": "me"}) == "me"


def test_systemd_unit_text():
    text = fb.systemd_unit_text(
        "/repo/.venv/bin/python",
        "/repo/src/fleet_server.py",
        "/repo",
        {"JARVIS_HOME": "/h"},
    )
    assert 'ExecStart="/repo/.venv/bin/python" "/repo/src/fleet_server.py"' in text
    assert "WorkingDirectory=/repo" in text
    assert 'Environment="JARVIS_HOME=/h"' in text
    assert "Restart=on-failure" in text
    assert "[Install]\nWantedBy=default.target" in text
    # bridge must never be bound by the listener unit
    assert "4317" not in text


def test_systemd_unit_escapes_spaces_quotes_and_percent():
    text = fb.systemd_unit_text(
        '/opt/my "py"/python', "/opt/100%/fleet_server.py", "/opt/100%"
    )
    assert 'ExecStart="/opt/my \\"py\\"/python" "/opt/100%%/fleet_server.py"' in text
    assert "WorkingDirectory=/opt/100%%" in text


def test_systemd_user_dir_respects_xdg(tmp_path):
    assert fb.systemd_user_dir(
        {"XDG_CONFIG_HOME": str(tmp_path)}, home=Path("/nope")
    ) == (tmp_path / "systemd" / "user")
    assert (
        fb.systemd_user_dir({}, home=tmp_path)
        == tmp_path / ".config" / "systemd" / "user"
    )


def test_launchd_plist_round_trip():
    raw = fb.launchd_plist("/py", "/repo/src/fleet_server.py", "/repo", "/log")
    data = plistlib.loads(raw)
    assert data["Label"] == "com.jarvis.fleet"
    assert data["ProgramArguments"] == ["/py", "/repo/src/fleet_server.py"]
    assert data["RunAtLoad"] is True and data["KeepAlive"] is True
    assert data["WorkingDirectory"] == "/repo"
    assert b"4317" not in raw


def test_launchctl_argv():
    assert fb.launchctl_bootout_argv(501) == [
        "launchctl",
        "bootout",
        "gui/501/com.jarvis.fleet",
    ]
    assert fb.launchctl_bootstrap_argv(501, "/p.plist") == [
        "launchctl",
        "bootstrap",
        "gui/501",
        "/p.plist",
    ]


def test_desktop_entry_exec_quoting():
    text = fb.desktop_entry_text("/py thon", "/r/fleet_server.py")
    assert 'Exec="/py thon" "/r/fleet_server.py"' in text
    assert "Terminal=false" in text


def test_interpreter_prefers_repo_venv_and_pythonw(tmp_path):
    venv_py = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_pyw = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
    existing = {str(venv_py), str(venv_pyw)}
    exists = lambda p: p in existing  # noqa: E731
    assert fb.interpreter_for(tmp_path, "windows", r"C:\Py\python.exe", exists) == str(
        venv_pyw
    )
    assert (
        fb.interpreter_for(tmp_path, "windows", r"C:\Py\python.exe", lambda _: False)
        == r"C:\Py\python.exe"
    )
    posix_venv = tmp_path / ".venv" / "bin" / "python"
    assert fb.interpreter_for(
        tmp_path, "linux", "/usr/bin/python3", lambda p: p == str(posix_venv)
    ) == str(posix_venv)


def test_fleet_port_and_home(tmp_path):
    assert fb.fleet_port({}) == 4318
    assert fb.fleet_port({"JARVIS_FLEET_PORT": "5000"}) == 5000
    assert fb.fleet_port({"JARVIS_FLEET_PORT": "junk"}) == 4318
    assert fb.fleet_port({"JARVIS_FLEET_PORT": "70000"}) == 4318
    assert fb.jarvis_home({"JARVIS_HOME": str(tmp_path)}) == tmp_path
    assert fb.jarvis_home({}, home=tmp_path) == tmp_path / ".jarvis"


def test_device_name_slug():
    assert fb.device_name("Living Room PC!") == "living-room-pc"


def test_hello_parse():
    assert fb.parse_hello('{"ok": true, "name": "pc"}') == {"ok": True, "name": "pc"}
    assert fb.parse_hello('{"ok": false}') is None
    assert fb.parse_hello("not json") is None


# ------------------------------------------------- dry run: no changes at all


class Recorder:
    """Fake subprocess.run: answers the read-only tailscale query, records everything."""

    def __init__(self, status=STATUS_RUNNING, rc_for=None):
        self.calls: list[list[str]] = []
        self.status = status
        self.rc_for = rc_for or {}

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[1:] == ["status", "--json"]:
            import json

            return subprocess.CompletedProcess(argv, 0, json.dumps(self.status), "")
        rc = self.rc_for.get(argv[0], 1)
        return subprocess.CompletedProcess(argv, rc, "", "")


MUTATING = {"/Create", "/Delete", "/Run", "/End", "add", "delete", "enable", "disable", "restart",
            "daemon-reload", "bootstrap", "bootout", "/inheritance:r"}  # fmt: skip


def _ran_mutating(calls):
    return [c for c in calls if any(tok in MUTATING for tok in c[1:])]


@pytest.fixture
def no_popen(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Popen must not run in dry run")

    monkeypatch.setattr(fb.subprocess, "Popen", boom)


@pytest.mark.parametrize("os_name", ["windows", "linux", "darwin"])
def test_dry_run_changes_nothing(tmp_path, monkeypatch, os_name, no_popen):
    listener = tmp_path / "fleet_server.py"
    listener.write_text("# stand-in listener\n")
    monkeypatch.setattr(fb, "LISTENER_SCRIPT", listener)
    monkeypatch.setattr(fb, "find_tailscale", lambda *a, **k: TS_BIN)
    monkeypatch.setattr(fb.shutil, "which", lambda name: "/usr/bin/systemctl")
    home = tmp_path / "home"
    rec = Recorder()
    lines = []
    boot = fb.Bootstrap(
        dry_run=True,
        os_name=os_name,
        home=home,
        repo=ROOT,
        run=rec,
        is_admin=lambda: True,
        out=lines.append,
        env={"XDG_CONFIG_HOME": str(tmp_path / "cfg")},
    )
    rc = boot.install()
    assert rc == 0
    assert not home.exists(), "dry run must not create JARVIS_HOME or the token"
    assert not (tmp_path / "cfg").exists()
    assert _ran_mutating(rec.calls) == [], "dry run ran a mutating command"
    read_only = {"status", "/Query", "query", "show-environment"}
    assert all(read_only & set(c) for c in rec.calls), rec.calls
    assert any(line.startswith("[PLAN]") for line in lines)
    assert "Dry run: nothing was changed." in lines


def test_dry_run_uninstall_changes_nothing(tmp_path, monkeypatch, no_popen):
    monkeypatch.setattr(fb.shutil, "which", lambda name: "/usr/bin/systemctl")
    rec = Recorder(rc_for={"schtasks": 0, "reg": 0})
    boot = fb.Bootstrap(
        dry_run=True,
        os_name="windows",
        home=tmp_path,
        run=rec,
        is_admin=lambda: True,
        out=lambda _: None,
    )
    assert boot.uninstall() == 0
    assert _ran_mutating(rec.calls) == []


def test_missing_tailscale_fails_before_any_change(tmp_path, monkeypatch, no_popen):
    monkeypatch.setattr(fb, "find_tailscale", lambda *a, **k: None)
    rec = Recorder()
    lines = []
    boot = fb.Bootstrap(
        dry_run=False, os_name="linux", home=tmp_path, run=rec, out=lines.append
    )
    assert boot.install() == 1
    assert rec.calls == []
    assert not (tmp_path / "bridge_token").exists()
    assert any(line.startswith("[FAIL]") and "tailscale.com" in line for line in lines)


def test_stopped_tailscale_exits_nonzero_with_instruction(
    tmp_path, monkeypatch, no_popen
):
    monkeypatch.setattr(fb, "find_tailscale", lambda *a, **k: TS_BIN)
    status = {"BackendState": "NeedsLogin", "TailscaleIPs": []}
    lines = []
    boot = fb.Bootstrap(
        dry_run=False,
        os_name="linux",
        home=tmp_path,
        run=Recorder(status=status),
        out=lines.append,
    )
    assert boot.install() == 1
    assert any("Sign in" in line and line.startswith("[FAIL]") for line in lines)
    assert not (tmp_path / "bridge_token").exists()


def test_token_created_once_with_64_hex_chars(tmp_path, monkeypatch):
    lines = []
    boot = fb.Bootstrap(
        dry_run=False, os_name="linux", home=tmp_path, run=Recorder(), out=lines.append
    )
    boot.ensure_token()
    token = (tmp_path / "bridge_token").read_text()
    assert len(token) == 64 and all(c in "0123456789abcdef" for c in token)
    boot.ensure_token()
    assert (tmp_path / "bridge_token").read_text() == token  # idempotent: kept
    if sys.platform != "win32":
        assert (tmp_path / "bridge_token").stat().st_mode & 0o777 == 0o600


def test_windows_firewall_skipped_when_not_admin_prints_command(tmp_path):
    lines = []
    rec = Recorder()
    boot = fb.Bootstrap(
        dry_run=False,
        os_name="windows",
        home=tmp_path,
        run=rec,
        is_admin=lambda: False,
        out=lines.append,
    )
    boot.firewall()
    assert rec.calls == []
    joined = "\n".join(lines)
    assert "netsh advfirewall firewall add rule" in joined
    assert "localport=4318" in joined and "remoteip=100.64.0.0/10" in joined
    assert boot.firewall_ok is False


def test_firewall_only_is_windows_only(tmp_path):
    lines = []
    boot = fb.Bootstrap(
        dry_run=True, os_name="linux", home=tmp_path, run=Recorder(), out=lines.append
    )
    assert boot.firewall_only() == 0
    assert any("Windows only" in line for line in lines)


def test_main_dry_run_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(fb, "current_os", lambda: "windows")
    monkeypatch.setattr(fb, "find_tailscale", lambda *a, **k: None)
    assert fb.main(["--dry-run"]) == 1  # no Tailscale: fail, nothing written
    assert not (tmp_path / "bridge_token").exists()


def test_skip_and_firewall_only_are_exclusive():
    with pytest.raises(SystemExit):
        fb.build_parser().parse_args(["--skip-firewall", "--firewall-only"])
