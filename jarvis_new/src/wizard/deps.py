"""System dependency catalog + checks (dnf + flatpak).

Each tool records the binaries the code actually invokes (audited from
src/), the dnf package that provides them on Nobara/Fedora, and an
optional flatpak app id. The wizard surfaces every tool with a
wizard-visible check (present / installable / blocked).

Pure apart from shutil.which + flatpak listing.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

# udev rule for /dev/uinput (wtype-style virtual input). Installed with
# explicit consent only; the group is "input" so users in it can write
# the node without being root.
UINPUT_RULE = (
    'KERNEL=="uinput", SUBSYSTEM=="misc", '
    'MODE="0660", GROUP="input", '
    'OPTIONS+="static_node=uinput"'
)
UINPUT_RULE_NAME = "99-jarvis-uinput.rules"
UINPUT_RULE_DIR = "/etc/udev/rules.d"


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    binaries: tuple[str, ...]
    dnf_packages: tuple[str, ...] = ()
    flatpak_app: str | None = None
    flatpak_origin: str = "flathub"
    required: bool = True  # False = optional nicety (skipped on install fail)
    description: str = ""

    def installed(self, which=shutil.which) -> bool:
        if any(which(b) for b in self.binaries):
            return True
        if self.flatpak_app:
            return _flatpak_installed(self.flatpak_app)
        return False

    def install_command(self) -> list[str]:
        if self.flatpak_app:
            return [
                "flatpak",
                "install",
                "-y",
                "--noninteractive",
                self.flatpak_origin,
                self.flatpak_app,
            ]
        if self.dnf_packages:
            return ["sudo", "dnf", "install", "-y", *self.dnf_packages]
        return []


# Audited from src/ (run_cmd + shutil.which call sites). Every binary
# here appears in a tool; keep it in sync with the code.
TOOLS: tuple[Tool, ...] = (
    Tool(
        name="spectacle",
        label="Spectacle (screenshots)",
        binaries=("spectacle",),
        dnf_packages=("spectacle",),
        description="KDE screenshot tool used for take_screenshot.",
    ),
    Tool(
        name="imagemagick",
        label="ImageMagick (image fallback + OCR prep)",
        binaries=("convert", "import"),
        dnf_packages=("ImageMagick",),
        description="convert/import used as the non-KDE screenshot fallback.",
    ),
    Tool(
        name="tesseract",
        label="Tesseract (OCR)",
        binaries=("tesseract",),
        dnf_packages=("tesseract",),
        description="Extracts text from screenshots (read_text/image).",
    ),
    Tool(
        name="pipewire",
        label="PipeWire/Pulse utils (volume)",
        binaries=("pactl",),
        dnf_packages=("pipewire-pulse", "pulseaudio-utils"),
        description="Volume get/set/mute via pactl.",
    ),
    Tool(
        name="playerctl",
        label="playerctl (media keys)",
        binaries=("playerctl",),
        dnf_packages=("playerctl",),
        description="Play/pause/skip/volume for media players.",
    ),
    Tool(
        name="wmctrl",
        label="wmctrl (window control)",
        binaries=("wmctrl",),
        dnf_packages=("wmctrl",),
        description="List/focus/close windows (focus_app, close_window).",
    ),
    Tool(
        name="wl-clipboard",
        label="wl-clipboard (clipboard)",
        binaries=("wl-copy", "wl-paste"),
        dnf_packages=("wl-clipboard",),
        description="Clipboard read/write on Wayland.",
    ),
    Tool(
        name="wtype",
        label="wtype (keyboard typing)",
        binaries=("wtype",),
        dnf_packages=("wtype",),
        description="Types text / sends keys (type_text, press_key).",
    ),
    Tool(
        name="fd-find",
        label="fd (file search)",
        binaries=("fdfind", "fd"),
        dnf_packages=("fd-find",),
        description="Fast fuzzy file search under the home dir.",
    ),
    Tool(
        name="brave",
        label="Brave browser",
        binaries=("brave-browser",),
        flatpak_app="com.brave.Browser",
        description="Automation browser (open_url, browsing tools).",
    ),
    Tool(
        name="whatsie",
        label="WhatSie (WhatsApp desktop)",
        binaries=(),
        flatpak_app="com.ktechpit.whatsie",
        required=False,
        description="WhatsApp via CDP on :9223 (open whatsie, whatsapp tools).",
    ),
    Tool(
        name="sober",
        label="Sober (Roblox)",
        binaries=("sober",),
        flatpak_app="org.vinegarhq.Sober",
        required=False,
        description="Play Roblox. Optional nicety.",
    ),
    Tool(
        name="libnotify",
        label="notify-send (notifications)",
        binaries=("notify-send",),
        dnf_packages=("libnotify",),
        description="Desktop notifications (briefings, alerts).",
    ),
    Tool(
        name="nmap",
        label="nmap (network scan)",
        binaries=("nmap",),
        dnf_packages=("nmap",),
        required=False,
        description="Port/service discovery (pentest track).",
    ),
    Tool(
        name="nikto",
        label="nikto (web server scan)",
        binaries=("nikto",),
        dnf_packages=("nikto",),
        required=False,
        description="Web server vulnerability sweep (pentest track).",
    ),
    Tool(
        name="gobuster",
        label="gobuster (path enumeration)",
        binaries=("gobuster",),
        dnf_packages=("gobuster",),
        required=False,
        description="Directory/file brute force (pentest track).",
    ),
    Tool(
        name="upower",
        label="upower (battery)",
        binaries=("upower",),
        dnf_packages=("upower",),
        description="Battery percentage + state.",
    ),
    Tool(
        name="kdeconnect",
        label="KDE Connect (phone bridge)",
        binaries=("kdeconnect-cli",),
        dnf_packages=("kde-connect",),
        required=False,
        description="Share files / ping the owner's phone.",
    ),
    Tool(
        name="qdbus",
        label="qdbus (D-Bus, brightness)",
        binaries=("qdbus",),
        dnf_packages=("qt", "qt5-qtbase"),
        description="Brightness via PowerDevil and Razer D-Bus. On Fedora 44"
        " qdbus is shipped by qt (Qt4); qt5-qtbase covers other releases.",
    ),
)

# Tools that need the uinput udev rule (virtual input node).
UINPUT_TOOLS = ("wtype",)


def _flatpak_installed(app_id: str) -> bool:
    """True when the flatpak app is installed (user or system). I/O."""
    import subprocess

    try:
        proc = subprocess.run(
            ["flatpak", "info", app_id],
            capture_output=True,
            timeout=10,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def tool_by_name(name: str) -> Tool | None:
    for t in TOOLS:
        if t.name == name:
            return t
    return None


def missing_tools() -> list[Tool]:
    """Tools whose binaries are absent. Pure."""
    return [t for t in TOOLS if not t.installed()]


def required_missing() -> list[Tool]:
    return [t for t in missing_tools() if t.required]


def installable(tool: Tool) -> bool:
    """True when we know how to install it (dnf pkg or flatpak app)."""
    return bool(tool.dnf_packages or tool.flatpak_app)


def check_summary(which=shutil.which) -> dict[str, dict]:
    """Per-tool status for a wizard-visible checklist. Pure."""
    out: dict[str, dict] = {}
    for t in TOOLS:
        out[t.name] = {
            "installed": t.installed(which),
            "required": t.required,
            "installable": installable(t),
            "flatpak": t.flatpak_app,
            "dnf": list(t.dnf_packages),
            "label": t.label,
            "description": t.description,
        }
    return out
