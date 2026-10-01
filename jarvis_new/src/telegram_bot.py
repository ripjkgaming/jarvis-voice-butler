"""Telegram chat sidecar: Sir talks to Jarvis from mobile or desktop.

Free Telegram Bot API, long polling (no webhook or public URL needed).
One paired chat only: scripts/telegram_setup.py writes a one-time pairing
code, and the first chat that sends it becomes the only allowed chat.

Layout mirrors src/activity_watch.py: fail-soft, injectable seams
(opener/transcribe/synth/run/clock) so tests never touch the network,
and start_thread() -> (Thread, Event) for the background loop.

Text replies come from the SAME brain as the HUD/phone text chat: the
local bridge HTTP API POST /chat (body {"text", "history"}) — never an
in-process import. Voice notes are transcribed locally (faster-whisper)
and answered with text + a Piper voice note.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

#: Long-poll window for getUpdates (urlopen timeout is a bit longer).
POLL_TIMEOUT = 30
API_TIMEOUT = 40.0
#: Telegram hard-caps messages at 4096 chars; stay under it.
MSG_LIMIT = 4000
#: Conversation turns (user+jarvis pairs) sent as /chat history.
HISTORY_TURNS = 10
#: Pairing codes live 15 minutes.
PAIR_TTL_S = 15 * 60.0
#: Recheck for a token this often when none is configured.
IDLE_S = 60.0
#: Strangers/unpaired chats get at most one reply per gap.
REFUSAL_GAP_S = 300.0
#: Backoff ceiling for poll errors.
BACKOFF_CAP_S = 60.0

HELP_TEXT = (
    "Jarvis on Telegram, Sir.\n"
    "/status — how I'm doing\n"
    "/mute, /unmute — the house microphone\n"
    "/voice on, /voice off — always answer with a voice note\n"
    "Or just talk: text, or hold the mic for a voice note."
)


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def state_path(home: Path | None = None) -> Path:
    """Paired-chat file. Pure."""
    return (home or jarvis_home()) / "telegram.json"


def pair_path(home: Path | None = None) -> Path:
    """One-time pairing code file. Pure."""
    return (home or jarvis_home()) / "telegram_pair.json"


def keys_env_path(home: Path | None = None) -> Path:
    """Secrets file holding TELEGRAM_BOT_TOKEN. Pure."""
    return (home or jarvis_home()) / "keys.env"


def parse_keys_env(text: str) -> dict[str, str]:
    """KEY=VALUE lines (quotes/comments tolerated) -> dict. Pure."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        val = val.strip().strip("\"'")
        out[key] = val
    return out


def load_token(home: Path | None = None) -> str:
    """TELEGRAM_BOT_TOKEN from env, else $JARVIS_HOME/keys.env. Never logs."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    try:
        return (
            parse_keys_env(keys_env_path(home).read_text())
            .get("TELEGRAM_BOT_TOKEN", "")
            .strip()
        )
    except OSError:
        return ""


def _default_state() -> dict:
    return {
        "chat_id": None,
        "user_id": None,
        "paired_at": None,
        "offset": None,
        "voice": False,
        "history": [],
    }


def load_state(home: Path | None = None) -> dict:
    """Paired chat + offset + prefs. Tolerant defaults, never raises."""
    state = _default_state()
    try:
        raw = json.loads(state_path(home).read_text())
    except (OSError, ValueError):
        return state
    if not isinstance(raw, dict):
        return state
    for key in ("chat_id", "user_id", "paired_at", "offset"):
        if isinstance(raw.get(key), (int, float)) or raw.get(key) is None:
            state[key] = raw.get(key)
    if raw.get("voice") is True:
        state["voice"] = True
    hist = raw.get("history")
    if isinstance(hist, list):
        state["history"] = [
            [str(t[0]), str(t[1])] for t in hist if isinstance(t, list) and len(t) == 2
        ][-HISTORY_TURNS * 2 :]
    return state


def save_state(state: dict, home: Path | None = None) -> bool:
    """Atomic persist of pairing/offset/prefs. Never raises."""
    try:
        path = state_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, path)
        return True
    except (OSError, ValueError):
        return False


def load_pair(home: Path | None = None) -> dict | None:
    """One-time pairing record {code, expires_at}, or None. Never raises."""
    try:
        raw = json.loads(pair_path(home).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code", "")).strip()
    try:
        expires_at = float(raw.get("expires_at", 0))
    except (TypeError, ValueError):
        return None
    if not re.fullmatch(r"\d{6}", code):
        return None
    return {"code": code, "expires_at": expires_at}


def pairing_ok(pair: dict | None, code: str, now: float) -> bool:
    """Is this the live pairing code? Pure."""
    if not isinstance(pair, dict):
        return False
    try:
        live = now < float(pair.get("expires_at", 0))
    except (TypeError, ValueError):
        return False
    return live and str(pair.get("code", "")) == (code or "").strip()


def pair_expired(pair: dict | None, now: float) -> bool:
    """A code record exists but its window passed. Pure."""
    if not isinstance(pair, dict):
        return False
    try:
        return now >= float(pair.get("expires_at", 0))
    except (TypeError, ValueError):
        return False


def parse_command(text: str) -> tuple[str, str]:
    """'/voice on' -> ('voice', 'on'); '/start@Bot 123' -> ('start', '123').

    Pure.
    """
    text = (text or "").strip()
    if not text.startswith("/"):
        return "", ""
    head = text[1:].split(None, 1)
    cmd = head[0].split("@", 1)[0].lower()
    arg = head[1].strip() if len(head) > 1 else ""
    return cmd, arg


def extract_pair_code(text: str) -> str | None:
    """6-digit code from '/start 123456' or a bare '123456'. Pure."""
    text = (text or "").strip()
    if re.fullmatch(r"\d{6}", text):
        return text
    cmd, arg = parse_command(text)
    if cmd == "start" and re.fullmatch(r"\d{6}", arg.strip()):
        return arg.strip()
    return None


def split_message(text: str, limit: int = MSG_LIMIT) -> list[str]:
    """Chunks <= limit, preferring newline/space breaks. Pure."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else [""]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = max(rest.rfind("\n", 0, limit), rest.rfind(" ", 0, limit))
        cut = cut if cut > limit // 2 else limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def build_multipart(
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
    boundary: str | None = None,
) -> tuple[bytes, str]:
    """multipart/form-data body + content type, stdlib only. Pure."""
    if boundary is None:
        boundary = "jarvis" + secrets.token_hex(8)
    buf = bytearray()
    for name, value in fields.items():
        buf += f"--{boundary}\r\n".encode()
        buf += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        buf += str(value).encode() + b"\r\n"
    for name, (filename, data, mime) in files.items():
        buf += f"--{boundary}\r\n".encode()
        buf += (
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        ).encode()
        buf += f"Content-Type: {mime}\r\n\r\n".encode()
        buf += bytes(data) + b"\r\n"
    buf += f"--{boundary}--\r\n".encode()
    return bytes(buf), f"multipart/form-data; boundary={boundary}"


def backoff_s(failures: int) -> float:
    """1, 2, 4 ... capped at BACKOFF_CAP_S. Pure."""
    try:
        n = max(1, int(failures))
    except (TypeError, ValueError):
        n = 1
    return min(BACKOFF_CAP_S, float(2 ** min(n - 1, 10)))


def should_send_voice(state: dict, incoming_voice: bool) -> bool:
    """Voice note out? Voice in, or Sir toggled /voice on. Pure."""
    return bool(incoming_voice) or state.get("voice") is True


def push_history(state: dict, role: str, text: str) -> None:
    """Append one turn, keeping the last HISTORY_TURNS pairs. Pure."""
    hist = state.setdefault("history", [])
    if not isinstance(hist, list):
        state["history"] = hist = []
    hist.append([role, (text or "")[:1000]])
    del hist[: -HISTORY_TURNS * 2 :]


def _fmt_age(seconds: float) -> str:
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return "?"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600:02d}"


def format_status(data: dict) -> str:
    """Bridge GET /status -> one short Telegram line. Pure."""
    if not isinstance(data, dict):
        return "Status didn't answer, Sir."
    version = str(data.get("version", "?"))[:16]
    up = _fmt_age(data.get("uptime_s", 0))
    pipe = str(data.get("pipeline", "?"))[:16]
    live = "linked" if data.get("livekit_configured") else "not linked"
    return f"Jarvis {version} · up {up} · {pipe} · LiveKit {live}."


class TelegramError(Exception):
    """The Bot API said no, or the network did."""


def _scrub(text: str, token: str) -> str:
    # URLs carry the token; never let one reach a log line or exception.
    return (text or "").replace(token, "***") if token else text or ""


def _urlopen_bytes(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def tg_call(
    token: str,
    method: str,
    params: dict | None = None,
    opener=None,
    timeout: float = 15.0,
) -> dict:
    """One Bot API call -> the `result`. Raises TelegramError (scrubbed)."""
    opener = opener or _urlopen_bytes
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    req = urllib.request.Request(url, data=data)
    try:
        raw = opener(req, timeout)
        payload = json.loads(raw.decode())
    except TelegramError:
        raise
    except Exception as exc:
        # Scrubbed: URLError text carries the request URL, which holds
        # the token.
        raise TelegramError(_scrub(f"telegram {method} failed: {exc}", token)) from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        desc = ""
        if isinstance(payload, dict):
            desc = str(payload.get("description", ""))[:120]
        raise TelegramError(f"telegram {method} refused: {desc or 'unknown'}")
    return payload.get("result", {})


def tg_send_voice(
    token: str,
    chat_id: int,
    ogg: bytes,
    opener=None,
    timeout: float = 30.0,
) -> dict:
    """sendVoice with stdlib multipart. Raises TelegramError (scrubbed)."""
    opener = opener or _urlopen_bytes
    body, content_type = build_multipart(
        {"chat_id": str(chat_id)}, {"voice": ("reply.ogg", ogg, "audio/ogg")}
    )
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendVoice",
        data=body,
        headers={"Content-Type": content_type},
    )
    try:
        raw = opener(req, timeout)
        payload = json.loads(raw.decode())
    except Exception as exc:
        raise TelegramError("telegram sendVoice failed") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise TelegramError("telegram sendVoice refused")
    return payload.get("result", {})


def tg_download(token: str, file_path: str, opener=None) -> bytes:
    """Download a Bot API file (voice note). Raises TelegramError."""
    opener = opener or _urlopen_bytes
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    try:
        return opener(urllib.request.Request(url), 30.0)
    except Exception as exc:
        raise TelegramError("telegram download failed") from exc


def bridge_base() -> str:
    """Bridge root from env (bind + port, loopback default). Pure (env)."""
    bind = os.environ.get("JARVIS_BRIDGE_BIND", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.environ.get("JARVIS_BRIDGE_PORT", "4317"))
    except ValueError:
        port = 4317
    return f"http://{bind}:{port}"


def bridge_token(home: Path | None = None) -> str:
    """Bearer token: env first, then $JARVIS_HOME/bridge_token. Never logs."""
    token = os.environ.get("JARVIS_BRIDGE_TOKEN", "").strip()
    if token:
        return token
    try:
        base = home or jarvis_home()
        return (base / "bridge_token").read_text().strip().split()[0]
    except (OSError, IndexError):
        return ""


def bridge_call(
    method: str, path: str, body: dict | None = None, opener=None
) -> dict | None:
    """One JSON call to the local bridge. None on any failure."""
    opener = opener or _urlopen_bytes
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{bridge_base()}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    token = bridge_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        raw = opener(req, 15.0)
        parsed = json.loads(raw.decode())
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _default_run(argv: list[str], payload: bytes, timeout: float) -> bytes:
    proc = subprocess.run(argv, input=payload, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{argv[0]} failed")
    return proc.stdout


def decode_16k_mono(ogg: bytes, run=None) -> bytes:
    """OGG/Opus -> 16 kHz mono int16 PCM via ffmpeg. Raises on failure."""
    run = run or _default_run
    return run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "s16le",
            "pipe:1",
        ],
        ogg,
        30.0,
    )


def encode_voice_ogg(wav: bytes, run=None) -> bytes:
    """WAV -> OGG/Opus (libopus, 48k, mono) via ffmpeg. Raises on failure."""
    run = run or _default_run
    return run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-c:a",
            "libopus",
            "-b:a",
            "48k",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-f",
            "ogg",
            "pipe:1",
        ],
        wav,
        60.0,
    )


_WHISPER = None


def transcribe_ogg(ogg: bytes, run=None, model=None) -> str:
    """Voice note bytes -> English text via local faster-whisper."""
    import numpy as np

    pcm = decode_16k_mono(ogg, run)
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if model is None:
        global _WHISPER
        if _WHISPER is None:
            from faster_whisper import WhisperModel

            from local_stt import whisper_model_name

            _WHISPER = WhisperModel(
                whisper_model_name(), device="cpu", compute_type="int8"
            )
        model = _WHISPER
    segments, _info = model.transcribe(audio, language="en", beam_size=1)
    return " ".join(s.text.strip() for s in segments if s.text.strip()).strip()


def synth_reply_ogg(text: str, synth_wav=None, run=None) -> bytes | None:
    """Reply text -> OGG/Opus voice note via local Piper + ffmpeg.

    None when synthesis/encoding is unavailable — the caller keeps the
    text reply and skips the voice note. Never raises.
    """
    try:
        if synth_wav is None:
            from speak import _synth_wav as synth_wav
        wav = synth_wav((text or "").strip()[:1500])
        return encode_voice_ogg(wav, run)
    except Exception:
        return None


class TelegramBot:
    """Long-polling sidecar for the one paired chat.

    Inject opener/transcribe/synth/run/clock in tests; defaults talk to
    the real Bot API, faster-whisper, Piper and ffmpeg.
    """

    def __init__(
        self,
        token: str = "",
        home: Path | None = None,
        opener=None,
        transcribe=None,
        synth_wav=None,
        run=None,
        clock=None,
    ) -> None:
        self.token = token
        self.home = home or jarvis_home()
        self._opener = opener
        self._transcribe = transcribe or transcribe_ogg
        self._synth_wav = synth_wav
        self._run = run
        self._clock = clock or time.time
        self.state = load_state(self.home)
        self._quiet_until: dict[int, float] = {}

    @classmethod
    def from_env(cls, **kwargs) -> TelegramBot:
        home = kwargs.pop("home", None) or jarvis_home()
        return cls(token=load_token(home), home=home, **kwargs)

    # -- low-level sends (best-effort, never raise) ----------------------

    def _send_text(self, chat_id: int, text: str) -> bool:
        try:
            for chunk in split_message(text):
                tg_call(
                    self.token,
                    "sendMessage",
                    {"chat_id": chat_id, "text": chunk},
                    self._opener,
                )
            return True
        except Exception:
            return False

    def _send_action(self, chat_id: int, action: str = "typing") -> None:
        with contextlib.suppress(Exception):
            tg_call(
                self.token,
                "sendChatAction",
                {"chat_id": chat_id, "action": action},
                self._opener,
            )

    def _send_voice_note(self, chat_id: int, ogg: bytes) -> bool:
        try:
            tg_send_voice(self.token, chat_id, ogg, self._opener)
            return True
        except Exception:
            return False

    def _rate_ok(self, chat_id: int, now: float) -> bool:
        """One reply per REFUSAL_GAP_S for untrusted chats. Pure-ish."""
        if now - self._quiet_until.get(chat_id, 0.0) >= REFUSAL_GAP_S:
            self._quiet_until[chat_id] = now
            return True
        return False

    # -- pairing ----------------------------------------------------------

    def _try_pair(
        self, chat_id: int, user_id: int | None, code: str, now: float
    ) -> bool:
        pair = load_pair(self.home)
        if not pairing_ok(pair, code, now):
            return False
        self.state.update(
            {"chat_id": chat_id, "user_id": user_id, "paired_at": now, "history": []}
        )
        save_state(self.state, self.home)
        with contextlib.suppress(OSError):
            pair_path(self.home).unlink(missing_ok=True)  # one-time code
        self._send_text(chat_id, "Paired, Sir. At your service here.")
        self._log(f"paired chat={chat_id}")
        return True

    def _ask_pair(self, chat_id: int, code_given: bool, now: float) -> None:
        if not self._rate_ok(chat_id, now):
            return
        if code_given and pair_expired(load_pair(self.home), now):
            msg = "That pairing code has expired, Sir. Run scripts/telegram_setup.py again."
        elif code_given:
            msg = "That code doesn't match. Check the setup step and try again."
        else:
            msg = (
                "Hello. This butler is invite-only — send the pairing code "
                "from the setup step: /start 123456."
            )
        self._send_text(chat_id, msg)

    def _refuse(self, chat_id: int, now: float) -> None:
        if not self._rate_ok(chat_id, now):
            return
        self._send_text(
            chat_id, "This butler serves one master, I'm afraid. Pairing is closed."
        )

    # -- chat --------------------------------------------------------------

    def _log(self, detail: str) -> None:
        try:
            from system import log_action

            log_action("telegram", detail[:300])
        except Exception:
            pass

    def _chat_reply(self, text: str) -> str | None:
        """Bridge POST /chat with history. None when unreachable."""
        # History is the turns BEFORE this one: /chat appends `text` itself,
        # so pushing first sent Sir's message to the brain twice.
        prior = list(self.state.get("history", []))
        push_history(self.state, "user", text)
        got = bridge_call(
            "POST",
            "/chat",
            {"text": text[:2000], "history": prior},
            self._opener,
        )
        if not isinstance(got, dict):
            return None
        reply = got.get("reply", "")
        if not isinstance(reply, str) or not reply.strip():
            warning = str(got.get("warning", ""))[:120]
            return f"My mind went blank for a moment, Sir. {warning}".strip()
        push_history(self.state, "jarvis", reply)
        return reply

    def _answer(self, chat_id: int, text: str, want_voice: bool) -> None:
        self._send_action(chat_id, "typing")
        reply = self._chat_reply(text)
        if reply is None:
            reply = "The brain didn't answer just now, Sir."
            push_history(self.state, "jarvis", reply)
        save_state(self.state, self.home)
        self._send_text(chat_id, reply)
        if want_voice or should_send_voice(self.state, want_voice):
            ogg = synth_reply_ogg(reply, self._synth_wav, self._run)
            if ogg:
                self._send_action(chat_id, "record_voice")
                self._send_voice_note(chat_id, ogg)
        self._log(f"chat in={text[:120]} out={reply[:120]}")

    def _handle_voice(self, chat_id: int, file_id: str) -> None:
        self._send_action(chat_id, "typing")
        try:
            info = tg_call(self.token, "getFile", {"file_id": file_id}, self._opener)
            ogg = tg_download(self.token, info.get("file_path", ""), self._opener)
            heard = self._transcribe(ogg)
        except Exception:
            heard = ""
        if not (heard or "").strip():
            self._send_text(chat_id, "I'm afraid I couldn't make that out, Sir.")
            self._log("voice unintelligible")
            return
        self._answer(chat_id, heard.strip()[:2000], want_voice=True)

    def _command(self, chat_id: int, text: str) -> None:
        cmd, arg = parse_command(text)
        if cmd == "start":
            self._send_text(chat_id, "Already paired, Sir. How can I help?")
        elif cmd == "help":
            self._send_text(chat_id, HELP_TEXT)
        elif cmd == "voice":
            mode = arg.strip().lower()
            if mode == "on":
                self.state["voice"] = True
                save_state(self.state, self.home)
                self._send_text(chat_id, "Voice replies on, Sir.")
            elif mode == "off":
                self.state["voice"] = False
                save_state(self.state, self.home)
                self._send_text(chat_id, "Text only from here, Sir.")
            else:
                cur = "on" if self.state.get("voice") else "off"
                self._send_text(chat_id, f"Voice replies are {cur}. Try /voice on|off.")
        elif cmd == "status":
            self._send_action(chat_id, "typing")
            got = bridge_call("GET", "/status", None, self._opener)
            self._send_text(
                chat_id, format_status(got) if got else "Status didn't answer, Sir."
            )
        elif cmd in ("mute", "unmute"):
            got = bridge_call("POST", "/mic", {"muted": cmd == "mute"}, self._opener)
            if got and got.get("ok") is not False:
                self._send_text(
                    chat_id, "Mic muted, Sir." if cmd == "mute" else "Mic live, Sir."
                )
            else:
                self._send_text(chat_id, "The mic switch didn't respond, Sir.")
        else:
            self._send_text(chat_id, f"Unknown command, Sir. {HELP_TEXT}")
        self._log(f"cmd={cmd or '?'}")

    # -- poll loop ----------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        """Route one getUpdates item. Never raises."""
        try:
            if not isinstance(update, dict):
                return
            msg = update.get("message") or {}
            if not isinstance(msg, dict):
                return
            chat = msg.get("chat") or {}
            chat_id = chat.get("id") if isinstance(chat, dict) else None
            if not isinstance(chat_id, int):
                return
            frm = msg.get("from") or {}
            user_id = frm.get("id") if isinstance(frm, dict) else None
            text = msg.get("text") or ""
            text = text if isinstance(text, str) else ""
            voice = msg.get("voice") or {}
            audio = msg.get("audio") or {}
            file_id = None
            if isinstance(voice, dict) and voice.get("file_id"):
                file_id = voice["file_id"]
            elif (
                isinstance(audio, dict)
                and audio.get("file_id")
                and "ogg" in str(audio.get("mime_type", ""))
            ):
                file_id = audio["file_id"]
            now = self._clock()
            paired = self.state.get("chat_id")
            if isinstance(paired, int) and chat_id != paired:
                self._refuse(chat_id, now)
                return
            if not isinstance(paired, int):
                code = extract_pair_code(text) if text else None
                if code and self._try_pair(chat_id, user_id, code, now):
                    return
                self._ask_pair(chat_id, code is not None, now)
                return
            if text.startswith("/"):
                self._command(chat_id, text)
            elif file_id:
                self._handle_voice(chat_id, file_id)
            elif text.strip():
                self._answer(chat_id, text.strip()[:2000], want_voice=False)
            else:
                self._send_text(
                    chat_id, "Text or a voice note, Sir — that's what I read here."
                )
        except Exception:
            pass

    def poll_once(self) -> str:
        """One getUpdates round. 'idle' without a token; raises on net error."""
        if not self.token:
            return "idle"
        params: dict = {"timeout": POLL_TIMEOUT}
        if self.state.get("offset") is not None:
            params["offset"] = self.state["offset"]
        result = tg_call(self.token, "getUpdates", params, self._opener, API_TIMEOUT)
        updates = result if isinstance(result, list) else []
        for update in updates or []:
            try:
                self.handle_update(update)
            finally:
                with contextlib.suppress(AttributeError, TypeError, ValueError):
                    self.state["offset"] = int(update.get("update_id", 0)) + 1
        save_state(self.state, self.home)
        return "ok"

    def run(self, stop: threading.Event) -> None:
        """Poll until stop is set. Fail-soft: backoff, never raises."""
        failures = 0
        while not stop.is_set():
            if not self.token:
                self.token = load_token(self.home)  # setup may run later
                if not self.token:
                    stop.wait(IDLE_S)
                    continue
            try:
                self.poll_once()
                failures = 0
            except Exception:
                failures += 1
                self._log("poll error")
                stop.wait(backoff_s(failures))


def start_thread(
    bot: TelegramBot | None = None,
) -> tuple[threading.Thread, threading.Event]:
    """Run the sidecar in a daemon thread. Returns (thread, stop event)."""
    bot = bot or TelegramBot.from_env()
    stop = threading.Event()

    def _loop() -> None:
        with contextlib.suppress(Exception):
            bot.run(stop)

    thread = threading.Thread(target=_loop, name="telegram", daemon=True)
    thread.start()
    return thread, stop
