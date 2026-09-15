"""Device tools: wifi, bluetooth, audio outputs, keyboard RGB, monitors,
USB, phone (KDE Connect), smart home, games.

Ported from proven laptop Jarvis logic (~/jarvis/src/new_jarvis/):
- skill_network: nmcli radio on/off, active connection, nearby list
- skill_bluetooth: bluetoothctl power/devices/scan/pair/connect/...
- skill_audio: pactl sink list + default switch (+ move inputs)
- skill_keyboard: OpenRazer via qdbus (static/spectrum/wave/breath/...)
- skill_monitors + learned monitor-place: kscreen-doctor live outputs,
  M1 (main) / M2 (laptop) / M3 (last external) mapping
- usb.py: lsusb list/tree/ports/detail/serial (read-only)
- phone: kdeconnect-cli list/ping/share (share confined to home/tmp)
- home_control.py: Home Assistant REST (refuses when unconfigured)
- launcher.py: sober/roblox via flatpak org.vinegarhq.Sober

Every tool refuses unless JARVIS_LOCAL=1.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import shutil
import urllib.request
from pathlib import Path

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local, run_cmd
from system.core import _resolve_user_path

KB_COLORS: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "purple": (128, 0, 128),
    "orange": (255, 165, 0),
    "pink": (255, 192, 203),
}

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def parse_kb_color(text: str) -> tuple[int, int, int] | None:
    """Color name or #rrggbb from free text. None = not found."""
    t = (text or "").lower()
    m = re.search(r"#([0-9a-f]{6})", t)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    for name, rgb in KB_COLORS.items():
        if name in t:
            return rgb
    return None


def match_bluetooth_device(
    query: str, devices: list[tuple[str, str]]
) -> tuple[str, str] | None:
    """Match a device by MAC or fuzzy name. Returns (mac, name) or None."""
    q = (query or "").strip()
    if not q:
        return None
    if re.fullmatch(r"[0-9A-Fa-f:]{17}", q):
        for mac, nm in devices:
            if mac.lower() == q.lower():
                return mac, nm
        return (q.upper(), q)
    ql = q.lower()
    for mac, nm in devices:
        if ql in nm.lower() or nm.lower() in ql:
            return mac, nm
    names = {nm.lower(): (mac, nm) for mac, nm in devices}
    best = difflib.get_close_matches(ql, list(names), n=1, cutoff=0.55)
    if best:
        return names[best[0]]
    return None


def parse_monitor_outputs(kscreen_out: str) -> list[str]:
    """Live output names (DP-1, HDMI-A-2, eDP-1...) from kscreen-doctor -o."""
    names: list[str] = []
    for ln in _ANSI.sub("", kscreen_out or "").splitlines():
        m = re.search(r"Output:\s*\d*\s*(\S+)", ln)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def logical_monitors(
    live: list[str], priority: list[str] | None = None
) -> dict[str, object]:
    """M1 = main (first priority live), M2 = laptop panel, M3 = last external.

    Port of the learned monitor-place skill. Missing targets clamp to M1.
    """
    prio = priority or ["DP-1", "HDMI-A-2", "eDP-1"]
    live = live or list(prio)
    main = next((n for n in prio if n in live), live[0])

    def _laptop(n: str) -> bool:
        u = n.upper()
        return u.startswith("EDP") or u.startswith("LVDS")

    laptop = next((n for n in live if _laptop(n)), None)
    if laptop is None and "eDP-1" in prio:
        laptop = "eDP-1"
    externals = [n for n in prio if n in live and n != laptop]
    for n in live:
        if n not in externals and n != laptop:
            externals.append(n)
    m1 = main
    m2 = laptop or m1
    m3 = externals[-1] if externals else (laptop if laptop in live else m1)
    return {"M1": m1, "M2": m2, "M3": m3, "live": live, "prio": prio}


def resolve_monitor_spec(
    spec: str, mapping: dict[str, str], live: list[str]
) -> dict[str, object]:
    """Free text -> output name. Falls back to M1 when offline/missing."""
    text = (spec or "").lower()
    if re.search(r"\blaptop\b|\bbuilt-in\b|\binternal\b", text):
        want, req = mapping.get("M2", ""), "laptop"
    elif re.search(r"\bmain\b|\bprimary\b", text):
        want, req = mapping.get("M1", ""), "main"
    else:
        m = re.search(r"\bm\s*([123])\b", text) or re.search(
            r"monitor\s*(?:number\s*)?([123])\b", text
        )
        if m:
            want, req = mapping.get(f"M{m.group(1)}", ""), f"monitor {m.group(1)}"
        else:
            # Bare output name?
            up = (spec or "").strip().upper()
            hit = next((n for n in live if n.upper() == up), None)
            if hit:
                return {
                    "name": hit,
                    "requested": spec,
                    "mapping": mapping,
                    "live": live,
                    "fallback": False,
                }
            want, req = mapping.get("M1", ""), "default"
    fallback = want not in live
    if fallback:
        want = mapping.get("M1", want)
    return {
        "name": want,
        "requested": req,
        "mapping": mapping,
        "live": live,
        "fallback": fallback,
    }


async def _kb_device() -> str | None:
    rc, out, _ = await run_cmd(
        "qdbus", "org.razer", "/org/razer", "razer.devices.getDevices", timeout=5.0
    )
    if rc != 0:
        return None
    devs = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    if not devs:
        return None
    first = devs[0]
    if first.startswith("/org/razer/device/"):
        return first
    return f"/org/razer/device/{first}"


async def _kb_call(dev: str, method: str, *vals: object) -> tuple[bool, str]:
    rc, out, err = await run_cmd(
        "qdbus", "org.razer", dev, method, *[str(v) for v in vals], timeout=8.0
    )
    if rc == 0:
        return True, (out or "").strip()
    return False, (err or out or "qdbus failed")[:150]


def _ha_config() -> tuple[str, str] | None:
    base = os.environ.get("HOME_ASSISTANT_URL", "").rstrip("/")
    token = os.environ.get("HOME_ASSISTANT_TOKEN", "")
    if not base or not token:
        return None
    return base, token


class DeviceTools:
    """Hardware + device tools. Register via .tools on the SystemAgent."""

    @property
    def tools(self) -> list:
        return [
            self.wifi_control,
            self.bluetooth_control,
            self.audio_output,
            self.keyboard_light,
            self.monitor_setup,
            self.open_on_monitor,
            self.usb_devices,
            self.phone_devices,
            self.home_control,
            self.play_game,
        ]

    # --- wifi ---

    @function_tool()
    async def wifi_control(
        self, context: RunContext, action: str = "status"
    ) -> dict[str, str]:
        """Wifi on/off/status/nearby list via NetworkManager.

        Args:
            action: One of status, on, off, list.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.lower()
        if action == "on":
            await run_cmd("nmcli", "radio", "wifi", "on", timeout=10.0)
            log_action("wifi", "on")
            return {"say": "Wifi on."}
        if action == "off":
            await run_cmd("nmcli", "radio", "wifi", "off", timeout=10.0)
            log_action("wifi", "off")
            return {"say": "Wifi off."}
        if action == "list":
            _rc, out, _ = await run_cmd(
                "nmcli",
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY",
                "dev",
                "wifi",
                "list",
                "--rescan",
                "no",
                timeout=15.0,
            )
            rows = []
            for ln in (out or "").splitlines():
                if ln.strip():
                    parts = ln.split(":")
                    rows.append(
                        (parts[0] or "hidden", parts[1] if len(parts) > 1 else "?")
                    )
            if not rows:
                return {"say": "No networks seen."}
            say = "Nearby wifi: " + "; ".join(f"{s} ({g}%)" for s, g in rows[:8])
            log_action("wifi", "list")
            return {"say": say[:400]}
        _rc, out, _ = await run_cmd(
            "nmcli", "-t", "-f", "NAME", "con", "show", "--active", timeout=5.0
        )
        active = [ln for ln in (out or "").splitlines() if ln.strip()][:3]
        log_action("wifi", "status")
        return {
            "say": ("Connected: " + ", ".join(active))
            if active
            else "No active connection."
        }

    # --- bluetooth ---

    @function_tool()
    async def bluetooth_control(
        self, context: RunContext, action: str = "status", device: str = ""
    ) -> dict[str, str]:
        """Bluetooth power, paired devices, scan, pair/connect/disconnect.

        Args:
            action: One of status, on, off, devices, scan, pair, connect,
                disconnect, trust, remove.
            device: Name fragment or MAC for pair/connect/disconnect.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.lower()

        async def _ctl(*c: str, timeout: float = 15.0) -> str:
            _rc, out, _ = await run_cmd("bluetoothctl", *c, timeout=timeout)
            return out or ""

        def _devices(out: str) -> list[tuple[str, str]]:
            found = []
            for ln in out.splitlines():
                m = re.match(r"Device\s+([0-9A-F:]{17})\s+(.+)", ln.strip())
                if m:
                    found.append((m.group(1), m.group(2).strip()))
            return found

        if action == "on":
            await _ctl("power", "on")
            log_action("bluetooth", "on")
            return {"say": "Bluetooth on."}
        if action == "off":
            await _ctl("power", "off")
            log_action("bluetooth", "off")
            return {"say": "Bluetooth off."}
        if action == "devices":
            devs = _devices(await _ctl("devices"))
            if not devs:
                return {"say": "No paired Bluetooth devices."}
            return {"say": ("Paired: " + "; ".join(nm for _, nm in devs))[:400]}
        if action == "scan":
            await _ctl("scan", "on", timeout=5.0)
            await asyncio.sleep(6)
            await _ctl("scan", "off", timeout=5.0)
            devs = _devices(await _ctl("devices"))
            names = "; ".join(nm for _, nm in devs[:8])
            log_action("bluetooth", "scan")
            return {"say": f"Scan done. Known: {names or 'none'}."}
        if action in ("pair", "connect", "disconnect", "trust", "remove"):
            devs = _devices(await _ctl("devices"))
            hit = match_bluetooth_device(device, devs)
            if not hit:
                raise ToolError(
                    f"No Bluetooth device matches '{device[:40]}'. "
                    "Say 'bluetooth scan' first."
                )
            mac, nm = hit
            out = await _ctl(action, mac, timeout=25.0)
            low = out.lower()
            ok = (
                "successful" in low
                or "already" in low
                or f"{action}ed" in low
                or "trusted" in low
            )
            if action == "connect" and not ok:
                ok = "Connected: yes" in await _ctl("info", mac, timeout=10.0)
            past = {
                "pair": "Paired",
                "connect": "Connected",
                "disconnect": "Disconnected",
                "trust": "Trusted",
                "remove": "Removed",
            }[action]
            log_action("bluetooth", f"{action} {nm}")
            if ok:
                return {"say": f"{past} {nm}."}
            raise ToolError(f"{action} failed: {out.strip()[-200:]}")
        out = await _ctl("show")
        log_action("bluetooth", "status")
        return {"say": f"Bluetooth {'on' if 'Powered: yes' in out else 'off'}."}

    # --- audio outputs ---

    @function_tool()
    async def audio_output(
        self, context: RunContext, action: str = "status", device: str = ""
    ) -> dict[str, str]:
        """List/switch speaker + headphone outputs (PipeWire/pactl).

        Args:
            action: One of status, list, switch.
            device: Name fragment for switch (e.g. "headphones").
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.lower()

        async def _sinks() -> list[tuple[str, str]]:
            _rc, out, _ = await run_cmd("pactl", "list", "sinks", timeout=10.0)
            sinks: list[tuple[str, str]] = []
            name, desc = "", ""
            for ln in (out or "").splitlines():
                s = ln.strip()
                if s.startswith("Name:"):
                    name = s.split("Name:", 1)[1].strip()
                elif s.startswith("Description:"):
                    desc = s.split("Description:", 1)[1].strip()
                    if name:
                        sinks.append((name, desc))
                        name, desc = "", ""
            return sinks

        sinks = await _sinks()
        if action == "list":
            if not sinks:
                return {"say": "No audio outputs found."}
            return {
                "say": ("Outputs: " + "; ".join(d for _, d in sinks))[:400],
                "count": str(len(sinks)),
            }
        if action == "switch":
            want = (device or "").strip().lower()
            if not want:
                raise ToolError("Switch to which output? Name part of it.")
            hit = next(
                (n for n, d in sinks if want in d.lower() or want in n.lower()),
                None,
            )
            if not hit:
                known = "; ".join(d for _, d in sinks)[:200]
                raise ToolError(f"No output matches '{device[:40]}'. I see: {known}.")
            await run_cmd("pactl", "set-default-sink", hit, timeout=5.0)
            # Move live streams so sound follows immediately.
            _rc, out, _ = await run_cmd(
                "pactl", "list", "short", "sink-inputs", timeout=5.0
            )
            for ln in (out or "").splitlines():
                sid = ln.split()[0] if ln.split() else ""
                if sid.isdigit():
                    await run_cmd("pactl", "move-sink-input", sid, hit, timeout=5.0)
            log_action("audio", f"switch {hit}")
            return {"say": f"Sound now on {hit[:80]}."}
        _rc, out, _ = await run_cmd("pactl", "get-default-sink", timeout=5.0)
        log_action("audio", "status")
        return {"say": f"Current output: {(out or 'unknown')[:100]}."}

    # --- keyboard RGB ---

    @function_tool()
    async def keyboard_light(
        self, context: RunContext, action: str = "status", text: str = ""
    ) -> dict[str, str]:
        """Keyboard backlight via OpenRazer (static/spectrum/wave/off...).

        Args:
            action: One of status, static, spectrum, wave, breath,
                reactive, starlight, brightness, off. Free text also works
                ("polychromatic", "static red", "brightness 70").
            text: Color ("red", "#00ff00") or extra detail.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        raw = f"{action} {text}".strip()
        low = raw.lower()
        dev = await _kb_device()
        if not dev:
            raise ToolError("No Razer keyboard seen (is openrazer running?).")
        if low in ("status", "") or re.search(
            r"\bwhat\b.{0,15}\b(effect|light|color)\b", low
        ):
            _, name = await _kb_call(dev, "razer.device.misc.getDeviceName")
            _, eff = await _kb_call(dev, "razer.device.lighting.chroma.getEffect")
            _, br = await _kb_call(
                dev, "razer.device.lighting.brightness.getBrightness"
            )
            return {
                "say": f"{name or 'Keyboard'}: effect {eff or '?'}, "
                f"brightness {br or '?'}%."
            }
        if re.search(r"\b(off|out|disable|no lights?)\b", low):
            ok, err = await _kb_call(dev, "razer.device.lighting.chroma.setNone")
            if ok:
                log_action("keyboard", "off")
                return {"say": "Keyboard lights off."}
            raise ToolError(err)
        if re.search(
            r"\b(polychromatic|rainbow|spectrum|multicolor|multi-colou?r|cycle|all colors?|all colours?)\b",
            low,
        ):
            ok, err = await _kb_call(dev, "razer.device.lighting.chroma.setSpectrum")
            if ok:
                log_action("keyboard", "spectrum")
                return {"say": "Keyboard polychromatic, full spectrum cycle."}
            raise ToolError(err)
        if re.search(r"\bwave\b", low):
            direction = 2 if re.search(r"\bleft\b", low) else 1
            ok, err = await _kb_call(
                dev, "razer.device.lighting.chroma.setWave", direction
            )
            if ok:
                log_action("keyboard", f"wave {direction}")
                return {"say": f"Wave flowing {'left' if direction == 2 else 'right'}."}
            raise ToolError(err)
        if re.search(r"\bbreath", low):
            c = parse_kb_color(low)
            if c:
                ok, err = await _kb_call(
                    dev, "razer.device.lighting.chroma.setBreathSingle", *c
                )
                say = "Breathing in that color."
            else:
                ok, err = await _kb_call(
                    dev, "razer.device.lighting.chroma.setBreathRandom"
                )
                say = "Breathing random colors."
            if ok:
                log_action("keyboard", "breath")
                return {"say": say}
            raise ToolError(err)
        if re.search(r"\breactive\b", low):
            c = parse_kb_color(low) or (255, 255, 255)
            m = re.search(r"\bspeed\s*([1-4])\b", low)
            ok, err = await _kb_call(
                dev,
                "razer.device.lighting.chroma.setReactive",
                *c,
                int(m.group(1)) if m else 2,
            )
            if ok:
                log_action("keyboard", "reactive")
                return {"say": "Reactive keys armed."}
            raise ToolError(err)
        if re.search(r"\bstarlight\b|\bstars?\b|\btwinkle\b", low):
            c = parse_kb_color(low)
            if c:
                ok, err = await _kb_call(
                    dev, "razer.device.lighting.chroma.setStarlightSingle", *c, 2
                )
            else:
                ok, err = await _kb_call(
                    dev, "razer.device.lighting.chroma.setStarlightRandom", 2
                )
            if ok:
                log_action("keyboard", "starlight")
                return {"say": "Starlight on."}
            raise ToolError(err)
        m = re.search(
            r"\bbrightness\b.{0,10}\b(\d{1,3})\b|\b(\d{1,3})\s*%?\s*\bbrightness\b",
            low,
        )
        if m or "brightness" in low:
            if m and (m.group(1) or m.group(2)):
                lvl = max(0, min(100, int(m.group(1) or m.group(2))))
            else:
                raise ToolError("Brightness what? Say 0 to 100.")
            ok, err = await _kb_call(
                dev, "razer.device.lighting.brightness.setBrightness", lvl
            )
            if ok:
                log_action("keyboard", f"brightness {lvl}")
                return {"say": f"Keyboard brightness {lvl}%."}
            raise ToolError(err)
        c = parse_kb_color(low)
        if c or re.search(r"\b(static|solid|color|colour|set|make|turn|change)\b", low):
            c = c or (255, 255, 255)
            ok, err = await _kb_call(dev, "razer.device.lighting.chroma.setStatic", *c)
            if ok:
                log_action("keyboard", f"static {c}")
                return {"say": "Keyboard set solid."}
            raise ToolError(err)
        raise ToolError(
            "Keyboard what? Try polychromatic, wave, static red, "
            "breathing blue, reactive, starlight, brightness 70, off."
        )

    # --- monitors ---

    @function_tool()
    async def monitor_setup(
        self, context: RunContext, action: str = "status", text: str = ""
    ) -> dict[str, str]:
        """Monitor layout: which screens are live, M1/M2/M3 mapping.

        Args:
            action: One of status, resolve.
            text: Monitor spec for resolve ("laptop", "main", "monitor 3").
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        _rc, out, _ = await run_cmd("kscreen-doctor", "-o", timeout=10.0)
        live = parse_monitor_outputs(out or "")
        mapping = logical_monitors(live)
        if action.lower() == "resolve":
            r = resolve_monitor_spec(
                text,
                {k: str(v) for k, v in mapping.items() if k in ("M1", "M2", "M3")},
                live,
            )
            fb = " (fell back to M1)" if r["fallback"] else ""
            return {
                "say": f"'{r['requested']}' is {r['name']}{fb}.",
                "name": str(r["name"]),
            }
        log_action("monitors", f"status {len(live)}")
        return {
            "say": (
                f"Live screens: {', '.join(live) if live else 'none seen'}. "
                f"M1={mapping['M1']} main, M2={mapping['M2']} laptop, "
                f"M3={mapping['M3']} last external."
            )
        }

    @function_tool()
    async def open_on_monitor(
        self, context: RunContext, app: str, monitor: str = "main"
    ) -> dict[str, str]:
        """Open an app and move its window toward a monitor area.

        Best-effort on KDE/Wayland: launches the app, resolves the monitor
        spec (M1/M2/M3, laptop, main), and reports where to look. Exact
        pixel placement is a Wayland compositor decision.

        Args:
            app: App name (brave, code, konsole, dolphin, sober...).
            monitor: "main", "laptop", "monitor 3", "M1"...
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        _rc, out, _ = await run_cmd("kscreen-doctor", "-o", timeout=10.0)
        live = parse_monitor_outputs(out or "")
        mapping = logical_monitors(live)
        r = resolve_monitor_spec(
            monitor,
            {k: str(v) for k, v in mapping.items() if k in ("M1", "M2", "M3")},
            live,
        )
        known = {
            "brave": "brave-browser",
            "chrome": "google-chrome",
            "code": "code",
            "vscode": "code",
            "konsole": "konsole",
            "terminal": "konsole",
            "dolphin": "dolphin",
            "files": "dolphin",
            "sober": "sober",
            "roblox": "sober",
            "spotify": "spotify",
            "discord": "discord",
        }
        target = known.get(app.strip().lower(), app.strip())
        if target == "sober" and shutil.which("sober") is None:
            target = "flatpak run org.vinegarhq.Sober"
        try:
            argv = target.split() if " " in target else [target]
            await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            raise ToolError(f"I could not find the app {app}.") from None
        log_action("launch-monitor", f"{target} -> {r['name']}")
        fb = " (only that screen is live, clamped to main)" if r["fallback"] else ""
        return {"say": f"Opening {app} toward {r['name']}{fb}."}

    # --- usb ---

    @function_tool()
    async def usb_devices(
        self, context: RunContext, action: str = "list", text: str = ""
    ) -> dict[str, str]:
        """USB: what is plugged in, topology, serial ports (read-only).

        Args:
            action: One of list, tree, ports, detail, serial.
            text: VID:PID, name fragment, or bus/dev for detail.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        action = action.lower()
        if action == "tree":
            _rc, out, _ = await run_cmd("lsusb", "-t", timeout=10.0)
            return {"say": (out or "No USB topology.")[:1500]}
        if action == "ports":
            _rc, out, _ = await run_cmd("lsusb", "-t", timeout=10.0)
            used = len(re.findall(r"Dev\s+\d+", out or ""))
            return {
                "say": f"{used} USB devices in the tree. Say detail with a name for more."
            }
        if action == "serial":
            devs = sorted(str(p) for p in Path("/dev").glob("ttyUSB*"))
            devs += sorted(str(p) for p in Path("/dev").glob("ttyACM*"))
            by_id = (
                sorted(str(p) for p in Path("/dev/serial/by-id").glob("*"))
                if Path("/dev/serial/by-id").exists()
                else []
            )
            bits = devs + by_id
            log_action("usb", "serial")
            return {
                "say": ("Serial ports: " + "; ".join(bits)[:400])
                if bits
                else "No USB serial ports."
            }
        _rc, out, _ = await run_cmd("lsusb", timeout=10.0)
        rows = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
        if action == "detail":
            q = (text or "").strip().lower()
            if not q:
                raise ToolError("Detail on which device? Name or VID:PID.")
            hits = [r for r in rows if q in r.lower()]
            if not hits:
                raise ToolError(f"No USB device matches '{text[:40]}'.")
            return {"say": hits[0][:400]}
        log_action("usb", f"list {len(rows)}")
        if not rows:
            return {"say": "No USB devices seen."}
        short = []
        for r in rows[:10]:
            m = re.match(r"Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+(\S+)\s*(.*)", r)
            short.append(m.group(4).strip() or m.group(3) if m else r[:60])
        return {"say": f"{len(rows)} USB devices: {'; '.join(short)[:400]}."}

    # --- phone ---

    @function_tool()
    async def phone_devices(
        self, context: RunContext, action: str = "status", text: str = ""
    ) -> dict[str, str]:
        """Phone link via KDE Connect: paired devices, ping, file share.

        Args:
            action: One of status, list, ping, share.
            text: Device name fragment (ping) or file path (share).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        if shutil.which("kdeconnect-cli") is None:
            raise ToolError("KDE Connect is not installed.")
        action = action.lower()
        _rc, out, _ = await run_cmd("kdeconnect-cli", "-l", timeout=10.0)
        if action in ("status", "list"):
            log_action("phone", "list")
            lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
            if not lines:
                return {"say": "No paired phones seen."}
            return {"say": ("Phones: " + "; ".join(lines))[:400]}
        # Find device id by fuzzy name.
        _rc2, avail, _ = await run_cmd(
            "kdeconnect-cli", "-a", "--id-name-only", timeout=10.0
        )
        pairs: list[tuple[str, str]] = []
        for ln in (avail or "").splitlines():
            parts = ln.strip().split(" ", 1)
            if len(parts) == 2:
                pairs.append((parts[0].strip(), parts[1].strip()))
        q = (text or "").strip().lower()
        dev_id = pairs[0][0] if pairs and not q else None
        if q:
            for did, nm in pairs:
                if q in nm.lower() or q in did.lower():
                    dev_id = did
                    break
        if action == "ping":
            if not dev_id:
                raise ToolError("No reachable phone to ping.")
            await run_cmd("kdeconnect-cli", "-d", dev_id, "--ping", timeout=10.0)
            log_action("phone", f"ping {dev_id}")
            return {"say": "Ping sent to your phone."}
        if action == "share":
            rp = _resolve_user_path(text)
            if rp is None or not rp.is_file():
                raise ToolError("Share which file? Path inside home or /tmp.")
            if not dev_id:
                raise ToolError("No reachable phone for sharing.")
            await run_cmd(
                "kdeconnect-cli", "-d", dev_id, "--share", str(rp), timeout=30.0
            )
            log_action("phone", f"share {rp}")
            return {"say": f"Shared {rp.name} with your phone."}
        raise ToolError(f"Unknown phone action {action}.")

    # --- smart home ---

    @function_tool()
    async def home_control(
        self,
        context: RunContext,
        action: str = "status",
        entity: str = "",
        data: str = "",
    ) -> dict[str, str]:
        """Smart home via Home Assistant (needs env config).

        Args:
            action: One of status, turn_on, turn_off, toggle.
            entity: e.g. "light.bedroom". Empty = list lights/switches.
            data: Optional JSON like '{"brightness_pct": 50}'.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        cfg = _ha_config()
        if cfg is None:
            raise ToolError(
                "Home control is not connected. Set HOME_ASSISTANT_URL and "
                "HOME_ASSISTANT_TOKEN on the host."
            )
        base, token = cfg
        if entity and not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", entity):
            raise ToolError("Use a valid entity_id like light.bedroom.")
        headers = {"Authorization": f"Bearer {token}"}

        def _get(path: str):
            req = urllib.request.Request(base + path, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())

        def _post(path: str, payload: dict):
            req = urllib.request.Request(
                base + path,
                data=json.dumps(payload).encode(),
                headers={**headers, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()

        try:
            if action == "status":
                states = _get("/api/states" + (f"/{entity}" if entity else ""))
                rows = states if isinstance(states, list) else [states]
                keep = [
                    s
                    for s in rows
                    if s.get("entity_id", "").split(".")[0]
                    in ("light", "switch", "fan", "climate", "cover", "lock")
                ][:20]
                if not keep:
                    return {"say": "No smart devices reported."}
                bits = "; ".join(
                    f"{s.get('attributes', {}).get('friendly_name', s['entity_id'])}: {s.get('state')}"
                    for s in keep
                )
                log_action("home", "status")
                return {"say": bits[:500]}
            domain = entity.split(".")[0]
            allowed_actions = {
                "light": {"turn_on", "turn_off", "toggle"},
                "switch": {"turn_on", "turn_off", "toggle"},
                "fan": {"turn_on", "turn_off", "toggle"},
                "cover": {"open_cover", "close_cover", "stop_cover"},
                "lock": {"lock", "unlock"},
                "climate": {"turn_on", "turn_off"},
                "scene": {"turn_on"},
            }
            if action not in allowed_actions.get(domain, set()):
                raise ToolError("That action does not fit that device.")
            payload: dict = {"entity_id": entity}
            if data.strip():
                extra = json.loads(data)
                if not isinstance(extra, dict):
                    raise ToolError("Data must be a JSON object.")
                for k in extra:
                    if k not in (
                        "brightness_pct",
                        "rgb_color",
                        "temperature",
                        "hvac_mode",
                        "percentage",
                    ):
                        raise ToolError(f"Unsupported parameter {k}.")
                payload.update(extra)
            _post(f"/api/services/{domain}/{action}", payload)
            log_action("home", f"{action} {entity}")
            return {"say": f"Home Assistant accepted {action} for {entity}."}
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Home Assistant did not respond ({exc}).") from exc

    # --- games ---

    @function_tool()
    async def play_game(
        self, context: RunContext, game: str = "roblox"
    ) -> dict[str, str]:
        """Launch a game (Roblox via Sober, Steam titles by name).

        Args:
            game: "roblox"/"sober" or a Steam app name.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        g = game.strip().lower()
        if g in ("roblox", "sober"):
            cmds = []
            if shutil.which("sober"):
                cmds = ["sober"]
            else:
                cmds = ["flatpak", "run", "org.vinegarhq.Sober"]
            try:
                await asyncio.create_subprocess_exec(
                    *cmds,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
            except FileNotFoundError:
                raise ToolError("Roblox (Sober) is not installed.") from None
            log_action("game", "roblox")
            return {"say": "Opening Roblox. Have fun, sir."}
        # Steam: fuzzy match installed app names is overkill; open steam.
        if shutil.which("steam"):
            try:
                await asyncio.create_subprocess_exec(
                    "steam",
                    f"steam://run/{g}" if g.isdigit() else "steam://open/main",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
                log_action("game", g[:40])
                return {"say": f"Opening {game[:40]} on Steam."}
            except FileNotFoundError:
                pass
        raise ToolError(f"I do not know the game '{game[:40]}'. Try roblox.")
