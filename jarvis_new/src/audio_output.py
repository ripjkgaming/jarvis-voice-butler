"""Route master-volume commands through Jarvis's echo-cancel sink.

The filter has its own gain, independent of Plasma's hardware speaker gain.
Keep the filter at unity on writes and control its underlying speaker instead.
Other default outputs (including headphones) retain normal default routing.
"""

from __future__ import annotations

import re
import shlex
import subprocess


def _master(run, options):
    try:
        proc = run(["pactl", "get-default-sink"], **options)
        if proc.returncode or proc.stdout.strip() != "jarvis_aec_speaker":
            return None
        proc = run(["pactl", "list", "short", "modules"], **options)
        if proc.returncode:
            return None
        for line in proc.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) < 3 or fields[1] != "module-echo-cancel":
                continue
            args = dict(
                token.split("=", 1) for token in shlex.split(fields[2]) if "=" in token
            )
            if args.get("sink_name") == "jarvis_aec_speaker":
                target = args.get("sink_master")
                if target and target != "jarvis_aec_speaker":
                    return target
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def run(argv, *, runner=None, **options):
    """subprocess.run adapter; injected runner allows routing tests without I/O."""
    execute = runner or subprocess.run
    default = "@DEFAULT_AUDIO_SINK@" if argv[0] == "wpctl" else "@DEFAULT_SINK@"
    if argv[0] not in ("wpctl", "pactl") or default not in argv:
        return execute(argv, **options)
    target = _master(execute, options)
    if not target:
        return execute(argv, **options)
    action = argv[1]
    if argv[0] == "wpctl":
        if action == "get-volume":
            volume = execute(["pactl", "get-sink-volume", target], **options)
            mute = execute(["pactl", "get-sink-mute", target], **options)
            match = re.search(r"(\d+)\s*%", volume.stdout or "")
            if volume.returncode or mute.returncode or not match:
                return subprocess.CompletedProcess(
                    argv, 1, "", "speaker volume unavailable"
                )
            suffix = " [MUTED]" if "yes" in mute.stdout.lower() else ""
            return subprocess.CompletedProcess(
                argv, 0, f"Volume: {int(match[1]) / 100:.2f}{suffix}\n", ""
            )
        if action == "set-volume":
            command = [
                "pactl",
                "set-sink-volume",
                target,
                f"{float(argv[-1]) * 100:.0f}%",
            ]
        elif action == "set-mute":
            command = ["pactl", "set-sink-mute", target, argv[-1]]
        else:
            return execute(argv, **options)
    else:
        command = [target if arg == default else arg for arg in argv]
    result = execute(command, **options)
    if result.returncode == 0 and action in (
        "set-volume",
        "set-mute",
        "set-sink-volume",
        "set-sink-mute",
    ):
        for cmd in (
            ["pactl", "set-sink-volume", "jarvis_aec_speaker", "100%"],
            ["pactl", "set-sink-mute", "jarvis_aec_speaker", "0"],
        ):
            result = execute(cmd, **options)
            if result.returncode:
                break
    return result
