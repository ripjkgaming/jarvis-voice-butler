"""Read-only Phase 4 readiness check. Never attaches, opens tabs or enables Aware.

Usage: uv run python scripts/aware_brave_preflight.py [--cdp-port 9222]
Prints JSON; exit 0 means prerequisites for existing-tab takeover are present.
KWin movement, focus preservation and minimised timing still need live checks.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def build_report(*, probe, listeners, screens, main, hud, hud_rect=None):
    from aware.tabsplit import choose_placement

    def loopback(address):
        try:
            host, port = address.rsplit(":", 1)
            return port.isdigit() and ipaddress.ip_address(host.strip("[]")).is_loopback
        except (ValueError, AttributeError):
            return False

    blockers = []
    if not probe.get("cdp"):
        blockers.append("cdp_unavailable")
    if not listeners or not all(loopback(address) for address in listeners):
        blockers.append("cdp_listener_not_verified_loopback")
    if not probe.get("worker"):
        blockers.append("no_tab_worker")
    placement = choose_placement(screens, main, hud, hud_rect)
    manual = [
        "kwin_placement_focus_and_handback_unverified",
        "live_extension_control_unverified",
    ]
    if placement.mode == "minimise":
        manual.append("minimised_timing_unverified")
    return {
        "mutated": False,
        "ready_for_existing_tabs": not blockers,
        "blockers": blockers,
        "cdp": bool(probe.get("cdp")),
        "browser": probe.get("browser", ""),
        "worker": probe.get("worker"),
        "listeners": listeners,
        "monitors": screens,
        "main": main,
        "hud": hud,
        "placement": placement.to_dict(),
        "manual_checks": manual,
    }


def socket_listeners(port):
    try:
        result = subprocess.run(
            ["ss", "-H", "-ltn", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []
    return [
        line.split()[3] for line in result.stdout.splitlines() if len(line.split()) >= 5
    ]


def main(argv=None):
    from aware import brave_live, cdp
    from system import monitor_window_move as mwm

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-port", type=int, default=cdp.port())
    args = parser.parse_args(argv)
    if not 1 <= args.cdp_port <= 65535:
        parser.error("CDP port must be between 1 and 65535")
    errors = []
    try:
        probe = asyncio.run(brave_live.probe(args.cdp_port))
    except Exception as exc:
        probe = {"cdp": False, "worker": None}
        errors.append(f"cdp_probe:{type(exc).__name__}")
    screens, main_screen, hud_screen, hud_rect = [], None, None, None
    try:
        screens = mwm.monitors()
        main_screen = mwm._primary(screens)["name"] if screens else None
        hud_screen = mwm.hud_screen(screens) if screens else None
        info = mwm.kwin_info("Jarvis")
        if info and str(info.get("resourceClass", "")).lower() == "jarvis-shell":
            hud_rect = {
                key: int(info[source])
                for key, source in [
                    ("x", "x"),
                    ("y", "y"),
                    ("w", "width"),
                    ("h", "height"),
                ]
            }
    except Exception as exc:
        errors.append(f"desktop_probe:{type(exc).__name__}")
    report = build_report(
        probe=probe,
        listeners=socket_listeners(args.cdp_port),
        screens=screens,
        main=main_screen,
        hud=hud_screen,
        hud_rect=hud_rect,
    )
    report["probe_errors"] = errors
    print(json.dumps(report, indent=2))
    return 0 if report["ready_for_existing_tabs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
