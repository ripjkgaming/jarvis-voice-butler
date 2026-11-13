"""PipeWire/pulse routing for WhatsApp calls (null-sinks + virtual mic).

    caller -> WhatSie out -> [jarvis_call_in] -> .monitor -> Jarvis ears
    Jarvis TTS -> [jarvis_call_out] -> .monitor -> [jarvis_mic] -> WhatSie mic

Never changes default devices. Only streams whose application.name or
application.process.binary match ``matchers`` are moved. All pactl access goes
through an injectable ``runner`` so tests never touch real audio.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.calls.audio_route")

# (returncode, stdout)
Runner = Callable[[Sequence[str]], "tuple[int, str]"]

DEFAULT_MATCHERS = ("whatsie", "com.ktechpit.whatsie")


def subprocess_runner(argv: Sequence[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return p.returncode, p.stdout


@dataclass
class AudioRoute:
    sink_in: str = "jarvis_call_in"
    sink_out: str = "jarvis_call_out"
    mic: str = "jarvis_mic"
    matchers: tuple[str, ...] = DEFAULT_MATCHERS
    runner: Runner = field(default=subprocess_runner, repr=False)

    @classmethod
    def unique(cls, tag: str, **kw) -> AudioRoute:
        """Isolated names for tests, e.g. jarvis_call_test_<tag>_in."""
        base = f"jarvis_call_test_{tag}"
        return cls(
            sink_in=f"{base}_in", sink_out=f"{base}_out", mic=f"{base}_mic", **kw
        )

    # -- low level -------------------------------------------------------
    def _pactl(self, *args: str) -> tuple[int, str]:
        return self.runner(["pactl", *args])

    def _names(self, kind: str) -> set[str]:
        rc, out = self._pactl("list", "short", kind)
        if rc != 0:
            return set()
        return {ln.split("\t")[1] for ln in out.splitlines() if "\t" in ln}

    def _module_ids(self, needle: str) -> list[str]:
        """Module ids whose argument string contains ``needle`` exactly."""
        rc, out = self._pactl("list", "short", "modules")
        if rc != 0:
            return []
        ids = []
        for ln in out.splitlines():
            parts = ln.split("\t")
            if len(parts) >= 3 and needle in parts[2].split():
                ids.append(parts[0])
        return ids

    # -- lifecycle -------------------------------------------------------
    def setup(self) -> bool:
        """Create sinks + virtual mic. Idempotent. True when all exist."""
        sinks = self._names("sinks")
        for name in (self.sink_in, self.sink_out):
            if name in sinks:
                continue
            rc, out = self._pactl(
                "load-module",
                "module-null-sink",
                f"sink_name={name}",
                f"sink_properties=device.description={name}",
            )
            if rc != 0:
                log.error("load null-sink %s failed: %s", name, out.strip())
                return False
        if self.mic not in self._names("sources"):
            rc, out = self._pactl(
                "load-module",
                "module-remap-source",
                f"master={self.sink_out}.monitor",
                f"source_name={self.mic}",
                f"source_properties=device.description={self.mic}",
            )
            if rc != 0:
                log.error("load remap-source %s failed: %s", self.mic, out.strip())
                return False
        return True

    def teardown(self) -> bool:
        """Unload our modules (mic first). Idempotent; never touches others."""
        ok = True
        targets = [
            f"source_name={self.mic}",
            f"sink_name={self.sink_out}",
            f"sink_name={self.sink_in}",
        ]
        for needle in targets:
            for mid in self._module_ids(needle):
                rc, out = self._pactl("unload-module", mid)
                if rc != 0:
                    log.error("unload module %s failed: %s", mid, out.strip())
                    ok = False
        return ok

    # -- stream routing --------------------------------------------------
    def _is_ours(self, props: dict) -> bool:
        hay = " ".join(
            str(props.get(k, ""))
            for k in ("application.name", "application.process.binary")
        ).lower()
        return any(m.lower() in hay for m in self.matchers)

    def _streams(self, kind: str) -> list[dict]:
        rc, out = self._pactl("--format=json", "list", kind)
        if rc != 0:
            return []
        try:
            data = json.loads(out)
        except ValueError:
            return []
        return [d for d in data if isinstance(d, dict)]

    def route_streams(self) -> dict[str, list[int]]:
        """Move matching WhatSie streams onto our devices. Idempotent."""
        moved: dict[str, list[int]] = {"sink_inputs": [], "source_outputs": []}
        sink_idx = self._index("sinks", self.sink_in)
        src_idx = self._index("sources", self.mic)
        for s in self._streams("sink-inputs"):
            if not self._is_ours(s.get("properties", {})):
                continue
            if sink_idx is not None and s.get("sink") == sink_idx:
                continue
            rc, _ = self._pactl("move-sink-input", str(s["index"]), self.sink_in)
            if rc == 0:
                moved["sink_inputs"].append(s["index"])
        for s in self._streams("source-outputs"):
            if not self._is_ours(s.get("properties", {})):
                continue
            if src_idx is not None and s.get("source") == src_idx:
                continue
            rc, _ = self._pactl("move-source-output", str(s["index"]), self.mic)
            if rc == 0:
                moved["source_outputs"].append(s["index"])
        return moved

    def _index(self, kind: str, name: str) -> int | None:
        rc, out = self._pactl("list", "short", kind)
        if rc != 0:
            return None
        for ln in out.splitlines():
            p = ln.split("\t")
            if len(p) > 1 and p[1] == name:
                try:
                    return int(p[0])
                except ValueError:
                    return None
        return None

    # -- health ----------------------------------------------------------
    def health(self) -> dict:
        sinks, sources = self._names("sinks"), self._names("sources")
        missing = [n for n in (self.sink_in, self.sink_out) if n not in sinks]
        if self.mic not in sources:
            missing.append(self.mic)
        if f"{self.sink_in}.monitor" not in sources:
            missing.append(f"{self.sink_in}.monitor")
        wa_si = sum(
            1
            for s in self._streams("sink-inputs")
            if self._is_ours(s.get("properties", {}))
        )
        wa_so = sum(
            1
            for s in self._streams("source-outputs")
            if self._is_ours(s.get("properties", {}))
        )
        return {
            "ok": not missing,
            "missing": missing,
            "whatsie_sink_inputs": wa_si,
            "whatsie_source_outputs": wa_so,
        }
