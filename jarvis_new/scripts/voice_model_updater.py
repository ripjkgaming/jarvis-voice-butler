#!/usr/bin/env python3
"""Run a check, or install the background timer with --install-timer."""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def unit_quote(value):
    return (
        '"'
        + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        + '"'
    )


def install():
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required")
    folder = Path.home() / ".config/systemd/user"
    folder.mkdir(parents=True, exist_ok=True)
    command = " ".join(
        unit_quote(arg)
        for arg in (
            uv,
            "run",
            "--no-sync",
            "python",
            ROOT / "scripts/voice_model_updater.py",
        )
    )
    (folder / "jarvis-voice-upgrade.service").write_text(
        "[Unit]\nDescription=Check for unlimited Gemini Live voice upgrades\n"
        "After=network-online.target\n\n[Service]\nType=oneshot\n"
        f"WorkingDirectory={str(ROOT).replace('%', '%%')}\nExecStart={command}\n"
        "TimeoutStartSec=10min\nUMask=0077\nNoNewPrivileges=true\n"
        "Nice=10\n"
    )
    (folder / "jarvis-voice-upgrade.timer").write_text(
        "[Unit]\nDescription=Check Jarvis voice models every two days\n\n"
        "[Timer]\nOnStartupSec=5min\nOnUnitActiveSec=2d\nAccuracySec=1min\n"
        "Unit=jarvis-voice-upgrade.service\n\n[Install]\nWantedBy=timers.target\n"
    )
    for command_args in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "jarvis-voice-upgrade.timer"],
        ["systemctl", "--user", "start", "jarvis-voice-upgrade.service"],
    ):
        subprocess.run(command_args, check=True)
    print("Installed and started jarvis-voice-upgrade.timer (every 48 hours).")


if __name__ == "__main__":
    if sys.argv[1:] == ["--install-timer"]:
        install()
    else:
        from voice_upgrade import main

        raise SystemExit(main())
