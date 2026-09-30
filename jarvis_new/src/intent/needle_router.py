"""Needle 2 semantic routing: messy voice transcript -> action+params.

Needle 2 (Cactus Compute, Apache-2.0) is a 45M-parameter, 14MB
tool-calling model that runs fully offline in ~28MB RAM at hundreds of
tokens/sec on plain CPU. Its entire job is mapping a sentence onto a
typed function signature with a calibrated confidence score — exactly
the router's problem, and nothing else (it never chats, reasons, or
sees the full prompt; the 256-token window only ever holds the
utterance plus the routing tool catalogue).

Role in the resolver: semantic layer between exact aliases and the
fuzzy matcher. Exact hits (1.0) and strong fuzzy hits (>=0.8) win
unchanged; Needle is consulted when they fall short, and its
confidence drops straight into the existing tiers (>=0.8 act, 0.5-0.8
confirm, <0.5 ask). Low-confidence abstains escalate exactly like an
unknown — the model was verified to abstain rather than misfire.

Safety:
- Read-only routing suggestions only: this module calls complete(),
  never run(), so Needle can never execute anything itself.
- No power tools in the catalogue: shutdown/reboot stay on the
  transfer_to_system_control handoff path, behind the voice-confirm
  gate, unreachable from here.
- Optional dependency: disabled unless the ``cactus-needle`` package
  is installed (``uv sync --extra needle``) and JARVIS_NEEDLE is not
  "0". A failed engine init disables it for the session (no repeated
  download stalls). Telemetry is forced off.

Tuning path: the base model covers crisp phrasing; Jarvis-specific
slang ("crank it up") is covered by seed aliases today and by LoRA
fine-tuning on the Jarvis vocabulary later (``needle finetune`` on a
JSONL of query->tool pairs, ``needle build`` to a .cact, point
JARVIS_NEEDLE_WEIGHTS at it). Hard lessons from the first tuning round
(Sep-2026, 101 examples, RTX 2050 / CPU fallback):
- Per-example tool subsets (target + ~3 distractors), NOT the full
  catalogue: full-catalogue examples overflow the training context,
  the answers truncate away, and loss sits at exactly 0.0000.
- Finetuning does NOT update the confidence head: tuned weights report
  confidence None, which breaks the act/confirm/ask tiers below. Do
  not deploy tuned weights until calibration is solved.
- Off-topic negatives must dominate, or tuning trades abstention for
  trigger-happiness (first .cact fired open_app on gibberish).
- 4GB VRAM cannot train even at batch 1; CPU trains fine (~9 min/epoch
  for 101 examples). Dataset generator kept at /tmp/opencode/needle_data.py
  (recreate on demand); first artifact archived untracked at
  ~/.jarvis/voice-butler/needle/jarvis.cact (NOT deployed).
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Literal

os.environ.setdefault("NEEDLE_TELEMETRY", "0")

WEIGHTS_ENV = "JARVIS_NEEDLE_WEIGHTS"

_agent = None
_dead = False


def needle_enabled() -> bool:
    """True when the layer may run: opted in and package importable."""
    if os.environ.get("JARVIS_NEEDLE", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False
    try:
        __import__("needle")
    except ImportError:
        return False
    return True


def _build_agent():
    """Declare the routing catalogue and compile the decode grammar."""
    import needle

    @needle.tool
    def tell_time():
        "Read the clock. Use when the user asks for the current time or date."
        return {"ok": True}

    @needle.tool
    def set_volume(action: Literal["up", "down", "mute", "unmute", "status"]):
        "Control the speaker volume: up raises it, down lowers it, mute silences it."
        return {"action": action}

    @needle.tool
    def media_control(action: Literal["play", "pause", "next", "previous", "stop"]):
        "Control music playback: play, pause, skip, or stop."
        return {"action": action}

    @needle.tool
    def open_app(
        app: Literal[
            "files",
            "terminal",
            "calculator",
            "browser",
            "whatsie",
            "code",
            "settings",
            "discord",
        ],
    ):
        "Launch a desktop program: the files manager, terminal, calculator, web browser, the whatsie messenger, code editor, settings, or discord."
        return {"app": app}

    @needle.tool
    def open_website(
        site: Literal[
            "google",
            "youtube",
            "amazon",
            "wikipedia",
            "github",
            "netflix",
            "spotify",
            "bbc",
        ],
    ):
        "Open a well-known website by name."
        return {"site": site}

    @needle.tool
    def search_the_web(query: str):
        "General internet search when no specific website was named. Repeat the query verbatim."
        return {"query": query}

    @needle.tool
    def weather_now(place: str):
        "Current weather for a place. Repeat the place name verbatim."
        return {"place": place}

    @needle.tool
    def daily_briefing():
        "Morning bundle of classes, study blocks, and open todos."
        return {"ok": True}

    @needle.tool
    def check_inbox():
        "Read the Gmail inbox. Use for mail, email, or inbox requests."
        return {"ok": True}

    @needle.tool
    def whatsapp_read(chat: str):
        "Read recent WhatsApp messages from a chat. Repeat the chat name verbatim."
        return {"chat": chat}

    @needle.tool
    def whatsapp_draft(chat: str, text: str):
        "Queue a WhatsApp reply draft for phone approval. Repeat both verbatim."
        return {"chat": chat, "text": text}

    @needle.tool
    def manage_todo(action: Literal["add", "list", "done", "clear"], text: str = ""):
        "Add, list, finish, or clear todos. Repeat the item text verbatim."
        return {"action": action, "text": text}

    @needle.tool
    def do_math(expr: str):
        "Evaluate safe arithmetic. Repeat the expression verbatim."
        return {"expr": expr}

    @needle.tool
    def system_handoff(task: str):
        "Hand rare laptop actions (open desktop programs, shutdown, reboot, windows, brightness, monitors, games) to system control. Repeat the task verbatim."
        return {"task": task}

    @needle.tool
    def research_handoff(topic: str):
        "Hand multi-page comparison or citation gathering to deep research. Repeat the topic verbatim."
        return {"topic": topic}

    tools = [
        tell_time,
        set_volume,
        media_control,
        open_app,
        open_website,
        search_the_web,
        weather_now,
        daily_briefing,
        check_inbox,
        whatsapp_read,
        whatsapp_draft,
        manage_todo,
        do_math,
        system_handoff,
        research_handoff,
    ]
    weights = os.environ.get(WEIGHTS_ENV, "").strip()
    if weights:
        return needle.Needle(weights=weights, tools=tools)
    return needle.Needle(tools=tools)


def _get_agent():
    """Lazily-built singleton; None when unavailable (memoized)."""
    global _agent, _dead
    if _dead:
        return None
    if _agent is None:
        try:
            _agent = _build_agent()
        except Exception:
            _dead = True
            return None
    return _agent


# Needle tool name -> (Jarvis action, params mapping).
_ACTION_MAP: dict[str, str] = {
    "tell_time": "tell_time",
    "set_volume": "set_volume",
    "media_control": "media_control",
    "open_app": "open_app",
    "open_website": "open_url",
    "search_the_web": "search_the_web",
    "weather_now": "weather_now",
    "daily_briefing": "daily_briefing",
    "check_inbox": "gmail_inbox",
    "whatsapp_read": "whatsapp_read",
    "whatsapp_draft": "whatsapp_draft",
    "manage_todo": "manage_todo",
    "do_math": "do_math",
    "system_handoff": "transfer_to_system_control",
    "research_handoff": "transfer_to_deep_research",
}


def _to_result(raw: dict) -> dict | None:
    """Raw complete() response -> {action, params, confidence} or None."""
    try:
        if not isinstance(raw, dict) or raw.get("type") != "call":
            return None
        calls = raw.get("function_calls") or []
        if not calls:
            return None
        first = calls[0]
        name = first.get("name", "")
        if name not in _ACTION_MAP:
            return None
        conf = float(raw.get("confidence") or 0.0)
        conf = max(0.0, min(1.0, conf))
        args = first.get("arguments") or {}
        params = {k: v for k, v in args.items() if isinstance(v, (str, int, float))}
        # open_website yields a site name; the router resolves it to a URL.
        if name == "open_website" and "site" in params:
            params = {"url": params["site"]}
        return {"action": _ACTION_MAP[name], "params": params, "confidence": conf}
    except Exception:
        return None


def _route_in_process(text: str) -> dict | None:
    agent = _get_agent()
    if agent is None:
        return None
    try:
        return _to_result(agent.complete(text.strip()[:200]))
    except Exception:
        return None


# --- Crash isolation -------------------------------------------------------
# libneedle's native complete() segfaults on some utterances (coredumps, exit
# code -11). In the agent process that kills the whole LiveKit job, which is
# re-dispatched: Jarvis restarts mid-answer and loops. So the model lives in a
# persistent child process instead. A crash costs one abstention (the turn
# falls through to Gemini) and a respawn; repeated crashes retire the layer
# for the session.

ISOLATE_ENV = "JARVIS_NEEDLE_ISOLATE"
_REPLY_TIMEOUT = 3.0  # s; over this the turn abstains and the worker is recycled
_MAX_CRASHES = 4  # within _CRASH_WINDOW seconds -> retire for the session
_CRASH_WINDOW = 600.0

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_replies: "queue.Queue[dict]" = queue.Queue()
_ready = threading.Event()
_crashes: list[float] = []
_retired = False


def _pump(proc: subprocess.Popen, replies: "queue.Queue[dict]") -> None:
    """Reader thread: worker stdout lines -> queue; EOF -> death sentinel."""
    try:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("ready"):
                _ready.set()
            else:
                replies.put(msg)
    except Exception:
        pass
    replies.put({"dead": True})


def _kill_worker(crashed: bool) -> None:
    global _proc, _retired
    proc, _proc = _proc, None
    _ready.clear()
    if proc is not None:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
    if crashed:
        now = time.monotonic()
        _crashes[:] = [t for t in _crashes if now - t < _CRASH_WINDOW] + [now]
        if len(_crashes) >= _MAX_CRASHES:
            _retired = True


def _spawn() -> None:
    global _proc, _replies
    _replies = queue.Queue()
    _ready.clear()
    _proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    threading.Thread(target=_pump, args=(_proc, _replies), daemon=True).start()


def _route_isolated(text: str) -> dict | None:
    with _lock:
        if _retired:
            return None
        if _proc is None or _proc.poll() is not None:
            if _proc is not None:
                _kill_worker(crashed=True)
                if _retired:
                    return None
            _spawn()
        # Still loading the model: abstain rather than stall the turn.
        if not _ready.is_set():
            return None
        try:
            _proc.stdin.write(json.dumps({"text": text.strip()[:200]}) + "\n")
            _proc.stdin.flush()
            reply = _replies.get(timeout=_REPLY_TIMEOUT)
        except (OSError, ValueError, queue.Empty):
            _kill_worker(crashed=True)
            return None
        if reply.get("dead"):
            _kill_worker(crashed=True)
            return None
        return _to_result(reply.get("raw"))


def warm_up() -> None:
    """Start the worker early so the first real turn already has a model."""
    if needle_enabled() and _isolated():
        with _lock:
            if not _retired and (_proc is None or _proc.poll() is not None):
                _spawn()


def _isolated() -> bool:
    return os.environ.get(ISOLATE_ENV, "1").strip().lower() not in ("0", "false", "no", "off")


def route_with_needle(text: str) -> dict | None:
    """Semantic route for text, or None when abstaining/unavailable. Pure I/O."""
    if not needle_enabled():
        return None
    if _isolated():
        return _route_isolated(text)
    return _route_in_process(text)


def _worker_main() -> None:
    """Child process: own fd 1 for the protocol, send everything else to stderr."""
    out = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    try:
        agent = _build_agent()
    except Exception:
        return
    out.write(json.dumps({"ready": True}) + "\n")
    for line in sys.stdin:
        try:
            raw = agent.complete(json.loads(line)["text"])
            out.write(json.dumps({"raw": raw}, default=str) + "\n")
        except Exception:
            out.write(json.dumps({"raw": None}) + "\n")


def reset_agent() -> None:
    """Forget cached state (tests only)."""
    global _agent, _dead, _retired
    _agent = None
    _dead = False
    _retired = False
    _crashes.clear()
    with _lock:
        _kill_worker(crashed=False)


if __name__ == "__main__" and "--worker" in sys.argv:
    _worker_main()
