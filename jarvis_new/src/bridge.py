"""Localhost control plane for embedding Jarvis in a desktop shell.

A desktop app (Electron/Tauri) cannot reach into the LiveKit worker or the
wake-word listener directly, so this module exposes host-side state over a
loopback-only HTTP server using stdlib only (no new dependencies)::

    .venv/bin/python src/bridge.py            # serves 127.0.0.1:4317
    JARVIS_BRIDGE_PORT=4320 .venv/bin/python src/bridge.py

Endpoints (all JSON):
    GET  /health    liveness + uptime + version
    GET  /status    voice pipeline state: livekit configured (presence only,
                   never secret values), pipeline mode, wake threshold,
                   voice model present, WhatsApp CDP reachable, log stats
    GET  /actions   tail of ~/.jarvis/actions.log (?limit=1..200, default 50)
    GET  /sys       cpu load + memory + home-disk (Linux /proc; degraded
                   JSON elsewhere instead of crashing)
    GET  /config    sanitized config surface for a settings screen
    POST /mic       {"muted": bool} -> proxied to the wake_client listener
                   on $JARVIS_HOME/wake.sock (2s timeout); 503 fail-soft
                   {"ok": false} when the listener is absent
    GET  /mic       wake_client mute status passthrough; 200 {"ok": false}
                   when the listener is absent

Deliberately NOT here: hangup, sendText, hotword events. Those live
inside the LiveKit room (token via the shell's mint_token command) and
have no out-of-process API yet. Every endpoint is fail-soft: a missing
file, binary, or socket degrades one field, never the whole response.

Phone API (JarvisLink Android app over Tailscale): the same server, with
`JARVIS_BRIDGE_BIND=0.0.0.0` (or the tailnet IP) + a MANDATORY
`JARVIS_BRIDGE_TOKEN` (refuses to start public without one):

    POST /type {text?, key?}     type text / press key via wtype
    POST /tool {tool, args?}     allowlisted control (volume/media/apps/
                                 screenshot/notify/lock — argv only, the
                                 same binaries the system tools use)
    POST /talk {audio_b64, rate?} 16k PCM -> whisper transcript +
                                 Gemini-direct reply + Piper audio back
                                 {transcript, reply, audio_b64, audio_rate}
    POST /camera/frame {image_b64} ingest phone photo (pruned to last 50)
    GET  /camera/latest          newest ingested photo (image/jpeg)

Security: binds 127.0.0.1 by default. If JARVIS_BRIDGE_TOKEN is set, all
endpoints require `Authorization: Bearer <token>` (constant-time compare).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

VERSION = "0.2.0"
DEFAULT_PORT = 4317
_WAKE_RPC_TIMEOUT = 2.0
_STARTED_AT = time.time()
_PHONE_WHISPER = None  # lazy singleton for /talk (wake venv has it too)
# Stable alias for direct text calls. Pinned "gemini-2.5-flash" rotted
# (retired for new keys -> instant 404); keep in sync with agent.py's
# DIRECT_LLM_MODEL.
GEMINI_TEXT_MODEL = "gemini-flash-latest"
_TYPE_MAX = 500
_TOOL_BODY_MAX = 65536
_TALK_AUDIO_MAX = 2 * 1024 * 1024
_CAM_KEEP = 50

# Phone-safe app launcher subset (argv only, detached like open_app).
# JARVIS_APP_MAP (JSON name->[argv]) extends or overrides it.
_APP_MAP = {
    "brave": ["brave-browser"],
    "files": ["dolphin"],
    "terminal": ["konsole"],
    "calculator": ["kcalc"],
    "whatsie": ["flatpak", "run", "com.ktechpit.whatsie"],
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _actions_log_path() -> Path:
    override = _env("JARVIS_ACTIONS_LOG")
    if override:
        return Path(override)
    return Path.home() / ".jarvis" / "actions.log"


def _wake_socket_path() -> Path:
    """Unix socket of the wake_client mic-control listener. Pure (env)."""
    home = _env("JARVIS_HOME").strip()
    base = Path(home) if home else Path.home() / ".jarvis"
    return base / "wake.sock"


def _wake_rpc(payload: dict, timeout: float = _WAKE_RPC_TIMEOUT) -> dict | None:
    """Send one JSON message to wake.sock, return its reply. Fail-soft.

    Returns None when the listener is absent, too slow, or answers
    garbage — callers degrade to {"ok": false} instead of raising.
    """
    try:
        raw = json.dumps(payload).encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
            cli.settimeout(timeout)
            cli.connect(str(_wake_socket_path()))
            cli.sendall(raw)
            chunks = []
            while True:
                data = cli.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if len(b"".join(chunks)) > 65536:
                    return None
        reply = json.loads(b"".join(chunks).decode())
        return reply if isinstance(reply, dict) else None
    except Exception:
        return None


def _voice_model_present() -> bool:
    model = _env("JARVIS_VOICE_MODEL", "")
    if model:
        return Path(model.split(":")[0]).expanduser().exists()
    return (Path.home() / ".jarvis" / "voices").exists()


def _whatsapp_reachable(timeout: float = 1.0) -> bool | None:
    """Probe the WhatSie CDP endpoint. None = probe not possible."""
    try:
        req = urllib.request.Request("http://127.0.0.1:9223/json/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _sys_stats() -> dict:
    stats: dict = {}
    try:
        with open("/proc/loadavg") as fh:
            stats["load_1_5_15"] = fh.read().split()[:3]
        mem: dict = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].rstrip(":") in (
                    "MemTotal",
                    "MemAvailable",
                ):
                    mem[parts[0].rstrip(":")] = int(parts[1]) * 1024
        stats["mem_bytes"] = mem
        du = os.statvfs(str(Path.home()))
        stats["home_free_bytes"] = du.f_bavail * du.f_frsize
    except OSError:
        stats["unsupported"] = True
    return stats


def _tail_log(path: Path, limit: int) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            step, buf = 4096, b""
            while len(buf.splitlines()) <= limit and size > 0:
                step = min(step, size)
                size -= step
                fh.seek(size)
                buf = fh.read(step) + buf
                if size == 0:
                    break
        lines = buf.decode(errors="replace").splitlines()
    except (OSError, ValueError):
        return []
    return lines[-limit:]


def _log_stats() -> dict:
    path = _actions_log_path()
    try:
        st = path.stat()
        return {"path": str(path), "bytes": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return {"path": str(path), "bytes": 0, "mtime": 0}


def build_status() -> dict:
    return {
        "ok": True,
        "version": VERSION,
        "uptime_s": int(time.time() - _STARTED_AT),
        "pipeline": _env("JARVIS_PIPELINE", "realtime"),
        "agent_name": _env("AGENT_NAME", "my-agent"),
        "local_unlocked": _env("JARVIS_LOCAL") == "1",
        "livekit_configured": bool(
            _env("LIVEKIT_URL")
            and _env("LIVEKIT_API_KEY")
            and _env("LIVEKIT_API_SECRET")
        ),
        "needle_enabled": _env("JARVIS_NEEDLE", "0") != "0",
        "wake_threshold": _env("JARVIS_WAKE_THRESHOLD", "0.5"),
        "voice_model_present": _voice_model_present(),
        "whatsapp_reachable": _whatsapp_reachable(),
        "log": _log_stats(),
    }


def resolve_bind() -> tuple[str, bool]:
    """(host, token_required). Non-loopback binds demand a token.

    Pure: JARVIS_BRIDGE_BIND (default 127.0.0.1). Anything other than
    127.0.0.1/localhost fails closed without JARVIS_BRIDGE_TOKEN.
    """
    host = _env("JARVIS_BRIDGE_BIND", "127.0.0.1").strip() or "127.0.0.1"
    public = host not in ("127.0.0.1", "localhost", "::1")
    return host, public


def _run(argv: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    """Run an argv (no shell). Returns (rc, stdout, stderr), never raises."""
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", f"command not found: {argv[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s: {argv[0]}"
    except OSError as exc:
        return 1, "", str(exc)[:200]
    decode = lambda b: b.decode(errors="replace").strip() if b else ""  # noqa: E731
    return proc.returncode, decode(proc.stdout), decode(proc.stderr)


def _which(name: str) -> str | None:
    return shutil.which(name)


def _read_json_body(handler: BaseHTTPRequestHandler, limit: int) -> dict | None:
    """Read + parse a JSON object body. None = missing/invalid/too big."""
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
    except ValueError:
        return None
    if length <= 0 or length > limit:
        return None
    try:
        body = json.loads(handler.rfile.read(length).decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _app_map() -> dict[str, list[str]]:
    try:
        extra = json.loads(_env("JARVIS_APP_MAP", "") or "{}")
        merged = dict(_APP_MAP)
        for name, argv in extra.items():
            if (
                isinstance(argv, list)
                and argv
                and all(isinstance(a, str) for a in argv)
            ):
                merged[str(name)] = argv
        return merged
    except ValueError:
        return dict(_APP_MAP)


def _parse_volume_pct(out: str) -> int | None:
    """Parse `pactl get-sink-volume` ("... / 100%") to 0-100. Pure."""
    match = re.search(r"(\d{1,3})\s*%", out or "")
    if not match:
        return None
    return max(0, min(100, int(match.group(1))))


def _tool_result(ok: bool, **fields) -> dict:
    return {"ok": ok, **fields}


def run_phone_tool(tool: str, args: dict) -> dict:
    """Execute one allowlisted phone-control tool. No shell, argv only."""
    if not isinstance(args, dict):
        args = {}
    if tool == "volume_get":
        rc, out, err = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], 5.0)
        _, mute_out, _ = _run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], 5.0)
        if rc != 0:
            return _tool_result(False, error=err or "pactl failed")
        return _tool_result(
            True,
            volume=_parse_volume_pct(out),
            muted="yes" in mute_out.lower(),
        )
    if tool in ("volume_up", "volume_down"):
        if _which("pactl") is None:
            return _tool_result(False, error="pactl not installed")
        delta = "+5%" if tool == "volume_up" else "-5%"
        rc, _, err = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", delta], 5.0)
        return _tool_result(rc == 0, error=None if rc == 0 else err)
    if tool in ("volume_mute", "volume_unmute"):
        toggle = "1" if tool == "volume_mute" else "0"
        rc, _, err = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", toggle], 5.0)
        return _tool_result(rc == 0, error=None if rc == 0 else err)
    if tool in ("media_play_pause", "media_next", "media_prev"):
        action = {
            "media_play_pause": "play-pause",
            "media_next": "next",
            "media_prev": "previous",
        }[tool]
        rc, out, err = _run(["playerctl", action], 5.0)
        return _tool_result(rc == 0, state=out or None, error=None if rc == 0 else err)
    if tool == "open_app":
        name = str(args.get("app", "")).strip().lower()
        entry = _app_map().get(name)
        if entry is None:
            return _tool_result(
                False,
                error=f"unknown app {name!r} (allowed: {sorted(_app_map())})",
            )
        if _which(entry[0]) is None and entry[0] != "flatpak":
            return _tool_result(False, error=f"{entry[0]} not installed")
        try:
            subprocess.Popen(  # argv from fixed map only, never user input
                entry,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return _tool_result(False, error=str(exc)[:200])
        return _tool_result(True, app=name)
    if tool == "screenshot":
        shot = _which("spectacle") or _which("import")
        if shot is None:
            return _tool_result(False, error="need spectacle or imagemagick")
        path = Path(f"/tmp/jarvis-phone-shot-{int(time.time())}.png")
        if shot.endswith("spectacle"):
            rc, _, err = _run(["spectacle", "-b", "-n", "-o", str(path)], 15.0)
        else:
            rc, _, err = _run(["import", "-window", "root", str(path)], 15.0)
        if rc != 0 or not path.is_file():
            return _tool_result(False, error=err or "capture failed")
        try:
            image_b64 = base64.b64encode(path.read_bytes()).decode()
        finally:
            path.unlink(missing_ok=True)
        return _tool_result(True, image_b64=image_b64, format="png")
    if tool == "notify":
        title = str(args.get("title", "Jarvis phone"))[:120]
        body = str(args.get("body", ""))[:300]
        rc, _, err = _run(["notify-send", title, body], 5.0)
        return _tool_result(rc == 0, error=None if rc == 0 else err)
    if tool == "lock":
        rc, _, err = _run(["loginctl", "lock-session"], 5.0)
        return _tool_result(rc == 0, error=None if rc == 0 else err)
    return _tool_result(False, error=f"unknown tool {tool!r}")


def _phone_cam_dir() -> Path:
    d = Path.home() / ".jarvis" / "phone_cam"
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_camera_frame(image_b64: str) -> dict:
    """Persist one phone photo, prune to the last _CAM_KEEP. Returns stats."""
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except (ValueError, base64.binascii.Error):
        return _tool_result(False, error="image_b64 is not valid base64")
    if not raw[:4] or len(raw) > 8 * 1024 * 1024:
        return _tool_result(False, error="image empty or >8MB")
    d = _phone_cam_dir()
    name = f"{int(time.time() * 1000)}.jpg"
    (d / name).write_bytes(raw)
    (d / "latest.jpg").write_bytes(raw)
    olds = sorted(
        (p for p in d.iterdir() if p.suffix == ".jpg" and p.name != "latest.jpg"),
        key=lambda p: p.name,
    )
    for stale in olds[: max(0, len(olds) - _CAM_KEEP)]:
        stale.unlink(missing_ok=True)
    return _tool_result(True, name=name, bytes=len(raw))


def handle_talk(body: dict) -> tuple[int, dict]:
    """16k PCM -> transcript + Gemini reply + Piper audio. Lazy heavy deps.

    Returns (http_code, payload). 501 when the venv lacks a stage
    (faster-whisper / google-genai / local voice); the bridge itself stays
    stdlib-runnable without them.
    """
    audio_b64 = body.get("audio_b64", "")
    rate = body.get("rate", 16000)
    if not isinstance(audio_b64, str) or not audio_b64:
        return 400, {"ok": False, "error": "body needs audio_b64"}
    if rate != 16000:
        return 400, {"ok": False, "error": "only rate 16000 supported"}
    try:
        pcm = base64.b64decode(audio_b64, validate=True)
    except (ValueError, base64.binascii.Error):
        return 400, {"ok": False, "error": "audio_b64 is not valid base64"}
    if len(pcm) > _TALK_AUDIO_MAX or len(pcm) < 3200:
        return 400, {"ok": False, "error": "audio must be 0.1s..60s of 16k PCM"}
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except ImportError:
        return 501, {"ok": False, "error": "voice stack missing (faster-whisper)"}
    global _PHONE_WHISPER
    if _PHONE_WHISPER is None:
        _PHONE_WHISPER = WhisperModel(
            os.environ.get("JARVIS_PHONE_WHISPER", "tiny"),
            device="cpu",
            compute_type="int8",
        )
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    try:
        segments, _ = _PHONE_WHISPER.transcribe(audio, language="en", beam_size=1)
        transcript = " ".join(s.text.strip() for s in segments).strip()
    except Exception as exc:
        return 500, {"ok": False, "error": f"transcribe failed: {exc}"[:200]}
    if not transcript:
        return 200, {"ok": True, "transcript": "", "reply": "", "audio_b64": ""}
    try:
        from google import genai
    except ImportError:
        return 501, {"ok": False, "error": "voice stack missing (google-genai)"}
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return 501, {"ok": False, "error": "GOOGLE_API_KEY not configured"}
    try:
        client = genai.Client(api_key=api_key, http_options={"timeout": 30})
        reply = client.models.generate_content(
            model=GEMINI_TEXT_MODEL,
            contents=(
                "You are Jarvis, a terse British butler voice assistant. "
                "Reply in one or two short spoken sentences, no formatting. "
                f"User said: {transcript}"
            ),
        ).text.strip()
    except Exception as exc:
        # Degrade, don't fail: the phone shows what was heard and notes the
        # brain outage (free-tier quota droughts read-timeout this path).
        return 200, {
            "ok": True,
            "transcript": transcript,
            "reply": "",
            "audio_b64": "",
            "warning": f"LLM unavailable: {exc}"[:200],
        }
    try:
        try:
            from src.local_voice import PiperTTS
        except ImportError:
            from local_voice import PiperTTS

        out = asyncio.run(_render_tts(PiperTTS(), reply))
    except Exception as exc:
        return 500, {"ok": False, "error": f"TTS failed: {exc}"[:200]}
    return 200, {
        "ok": True,
        "transcript": transcript,
        "reply": reply,
        "audio_b64": base64.b64encode(out[0]).decode(),
        "audio_rate": out[1],
    }


async def _render_tts(tts, text: str) -> tuple[bytes, int]:
    pcm = await asyncio.to_thread(tts._render_sentence, text)
    rate = getattr(tts, "_sample_rate", 22050) or 22050
    return pcm, rate


class _Handler(BaseHTTPRequestHandler):
    token: str = ""

    def log_message(self, *args: object) -> None:  # keep voice logs clean
        pass

    def _authorized(self) -> bool:
        if not self.token:
            return True
        got = self.headers.get("Authorization", "")
        want = "Bearer " + self.token
        return (
            hashlib.sha256(got.encode()).digest()
            == hashlib.sha256(want.encode()).digest()
        )

    def _send(self, code: int, payload: dict | list) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The Tauri webview (tauri://localhost) and the Next dev server
        # fetch the bridge cross-origin. Loopback-only server + no cookies:
        # a wildcard is safe here (auth rides the Authorization header,
        # which browsers allow under `*` outside credentialed mode).
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        """Preflight sink: allow reads + the /mic mute POST, short-circuit."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_bytes(self, code: int, content_type: str, raw: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/mic":
            body = _read_json_body(self, 1024)
            if not isinstance(body, dict) or not isinstance(body.get("muted"), bool):
                self._send(400, {"ok": False, "error": 'body must be {"muted": bool}'})
                return
            reply = _wake_rpc({"mute": body["muted"]})
            if reply is None:
                self._send(503, {"ok": False, "error": "wake listener unavailable"})
                return
            self._send(200, reply)
            return
        if route == "/type":
            body = _read_json_body(self, 4096)
            if body is None:
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            text, key = body.get("text", ""), body.get("key", "")
            if key:
                if not isinstance(key, str) or len(key) > 40:
                    self._send(400, {"ok": False, "error": "bad key"})
                    return
                rc, _, err = _run(["wtype", "-k", key], 10.0)
            else:
                if not isinstance(text, str) or not text or len(text) > _TYPE_MAX:
                    self._send(
                        400, {"ok": False, "error": f"text 1..{_TYPE_MAX} chars"}
                    )
                    return
                rc, _, err = _run(["wtype", "--", text], 10.0)
            if rc != 0:
                self._send(500, {"ok": False, "error": err or "wtype failed"})
                return
            self._send(200, {"ok": True})
            return
        if route == "/tool":
            body = _read_json_body(self, _TOOL_BODY_MAX)
            if body is None or not isinstance(body.get("tool"), str):
                self._send(400, {"ok": False, "error": 'body needs {"tool": str}'})
                return
            args = body.get("args", {})
            result = run_phone_tool(
                body["tool"], args if isinstance(args, dict) else {}
            )
            self._send(200 if result.get("ok") else 400, result)
            return
        if route == "/talk":
            body = _read_json_body(self, _TALK_AUDIO_MAX + 4096)
            if body is None:
                self._send(400, {"ok": False, "error": "invalid JSON body"})
                return
            code, result = handle_talk(body)
            self._send(code, result)
            return
        if route == "/camera/frame":
            body = _read_json_body(self, 8 * 1024 * 1024 + 1024)
            if body is None or not isinstance(body.get("image_b64"), str):
                self._send(400, {"ok": False, "error": "body needs image_b64"})
                return
            result = store_camera_frame(body["image_b64"])
            self._send(200 if result.get("ok") else 400, result)
            return
        self._send(404, {"ok": False, "error": "unknown route"})

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        route, qs = parsed.path, parse_qs(parsed.query)
        if route == "/health":
            self._send(
                200,
                {
                    "ok": True,
                    "version": VERSION,
                    "uptime_s": int(time.time() - _STARTED_AT),
                },
            )
        elif route == "/status":
            self._send(200, build_status())
        elif route == "/actions":
            try:
                limit = max(1, min(200, int(qs.get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            self._send(
                200, {"ok": True, "actions": _tail_log(_actions_log_path(), limit)}
            )
        elif route == "/sys":
            self._send(200, {"ok": True, **_sys_stats()})
        elif route == "/mic":
            reply = _wake_rpc({"status": True})
            if reply is None:
                self._send(200, {"ok": False, "error": "wake listener unavailable"})
            else:
                self._send(200, reply)
        elif route == "/camera/latest":
            latest = _phone_cam_dir() / "latest.jpg"
            try:
                raw = latest.read_bytes()
            except OSError:
                self._send(404, {"ok": False, "error": "no frames yet"})
                return
            self._send_bytes(200, "image/jpeg", raw)
        elif route == "/config":
            self._send(
                200,
                {
                    "ok": True,
                    "pipeline": _env("JARVIS_PIPELINE", "realtime"),
                    "wake_threshold": _env("JARVIS_WAKE_THRESHOLD", "0.5"),
                    "needle_enabled": _env("JARVIS_NEEDLE", "0") != "0",
                    "local_unlocked": _env("JARVIS_LOCAL") == "1",
                    "livekit_configured": bool(
                        _env("LIVEKIT_URL")
                        and _env("LIVEKIT_API_KEY")
                        and _env("LIVEKIT_API_SECRET")
                    ),
                    "bridge_token_set": bool(self.token),
                },
            )
        else:
            self._send(404, {"ok": False, "error": "unknown route"})


def create_server(
    port: int = DEFAULT_PORT, token: str = "", host: str = "127.0.0.1"
) -> ThreadingHTTPServer:
    """Create (not start) the server. Port 0 = ephemeral (tests)."""

    class BoundHandler(_Handler):
        pass

    BoundHandler.token = token
    server = ThreadingHTTPServer((host, port), BoundHandler)
    server.daemon_threads = True
    return server


def serve_forever(
    port: int = DEFAULT_PORT, token: str = "", host: str = "127.0.0.1"
) -> None:
    server = create_server(port, token, host)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    try:
        port = int(_env("JARVIS_BRIDGE_PORT", str(DEFAULT_PORT)))
    except ValueError:
        port = DEFAULT_PORT
    host, token_required = resolve_bind()
    token = _env("JARVIS_BRIDGE_TOKEN")
    if token_required and not token:
        print(
            f"REFUSING to bind {host} without JARVIS_BRIDGE_TOKEN "
            "(set a token or keep JARVIS_BRIDGE_BIND=127.0.0.1).",
            flush=True,
        )
        raise SystemExit(2)
    serve_forever(port=port, token=token, host=host)


if __name__ == "__main__":
    main()


def _run_in_thread(
    port: int = 0, token: str = ""
) -> tuple[ThreadingHTTPServer, Thread]:
    """Test helper: serve on an ephemeral port in a background thread."""
    server = create_server(port, token)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
