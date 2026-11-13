"""Fake-call simulation runner (W7).

Runs scripted (or LLM-simulated) callers through the real ``CallSession`` with
FakeDriver, real W1 stores on a tmp dir, a free OpenRouter model as the call
LLM and Piper for Jarvis's voice. Nothing under ``~/.jarvis`` is touched except
the sim output dir (``~/.jarvis/calls/sim/<run_id>/``) and, with ``hud=True``,
appends to the live ``events.jsonl`` so the HUD can replay the call.

The OpenRouter key is never printed or logged.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from calls import events

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
# Preferred free chat models, tried first (probed good by openrouter_chat).
PREFERRED = (
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
)
LAST_RESORT = "qwen/qwen3.8-27b:free"  # rate limited nearly always: last
PER_MODEL_TIMEOUT_S = 25.0
MAX_DISCOVERED = 5
SCENARIO_DIR = Path(__file__).resolve().parents[2] / "tests" / "calls" / "scenarios"
DEFAULT_PIPER = Path.home() / ".local" / "bin" / "piper"
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


# ------------------------------------------------------------------ key
def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, sep, v = line.partition("=")
        v = v.strip()
        if sep and len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        if sep:
            out[k.strip()] = v
    return out


def openrouter_keys(environ: dict | None = None) -> list[str]:
    """All usable OpenRouter keys (env, keys.env, opencode auth). Never logged."""
    env = os.environ if environ is None else environ
    keys: list[str] = []

    def add(v: str | None) -> None:
        v = (v or "").strip()
        if v and v not in keys:
            keys.append(v)

    for n in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_2"):
        add(env.get(n))
    home = (env.get("JARVIS_HOME") or "").strip()
    kp = (Path(home) if home else Path.home() / ".jarvis") / "keys.env"
    f = _parse_env_file(kp)
    for n in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_2"):
        add(f.get(n))
    try:
        auth = json.loads((Path.home() / ".local/share/opencode/auth.json").read_text())
        add(((auth.get("openrouter") or {}) or {}).get("key"))
    except (OSError, ValueError, AttributeError):
        pass
    return keys


# ------------------------------------------------------------------ LLM
def discover_free_models(key: str, opener=None) -> list[str]:
    """Free text-chat model ids from OpenRouter /models. [] on any failure."""
    req = urllib.request.Request(MODELS_URL, headers={"Authorization": f"Bearer {key}"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception:
        return []
    out: list[tuple[int, str]] = []
    for m in data.get("data", []):
        mid = str(m.get("id", ""))
        if not mid.endswith(":free"):
            continue
        arch = m.get("architecture") or {}
        if "text" not in (arch.get("output_modalities") or ["text"]):
            continue
        if "text" not in (arch.get("input_modalities") or ["text"]):
            continue
        if re.search(r"embed|moderation|guard|vision|image|audio", mid, re.I):
            continue
        out.append((int(m.get("context_length") or 0), mid))
    out.sort(reverse=True)
    return [m for _, m in out]


def build_chain(primary: str | None, discovered: list[str]) -> list[str]:
    chain: list[str] = []
    for m in [primary, *PREFERRED, *discovered[:MAX_DISCOVERED]]:
        if m and m != LAST_RESORT and m not in chain:
            chain.append(m)
    chain.append(LAST_RESORT)
    return chain


def to_chat_messages(history: list[tuple[str, str]]) -> list[dict]:
    """history [(caller|jarvis|owner, text)] -> alternating user/assistant."""
    msgs: list[dict] = []
    for who, text in history:
        role = "assistant" if who == "jarvis" else "user"
        label = {"caller": "Caller", "owner": "Owner"}.get(who, "")
        part = f"{label}: {text}" if label else text
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + part
        else:
            msgs.append({"role": role, "content": part})
    if not msgs or msgs[0]["role"] != "user":
        msgs.insert(0, {"role": "user", "content": "(call connected)"})
    if msgs[-1]["role"] != "user":
        msgs.append({"role": "user", "content": "(continue)"})
    return msgs


class OpenRouterLLM:
    """async llm(system, history) -> str down a free-model fallback chain."""

    def __init__(
        self,
        keys: list[str],
        chain: list[str],
        *,
        opener: Callable | None = None,
        max_tokens: int = 220,
    ) -> None:
        self._keys = keys
        self.chain = chain
        self._opener = opener or urllib.request.urlopen
        self.max_tokens = max_tokens
        self.records: list[dict] = []  # {ms, model, ok, tried, kind}
        self._cool: dict[str, float] = {}  # model -> monotonic time to retry
        self.cooldown_s = 90.0

    def _one(self, system: str, msgs: list[dict], model: str, key: str):
        payload = json.dumps(
            {
                "model": model,
                "messages": [{"role": "system", "content": system}, *msgs],
                "max_tokens": self.max_tokens,
                "temperature": 0.4,
            }
        ).encode()
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "X-Title": "Jarvis call sim",
            },
        )
        try:
            resp = self._opener(req, timeout=PER_MODEL_TIMEOUT_S)
            try:
                raw = resp.read()
            finally:
                getattr(resp, "close", lambda: None)()
        except urllib.error.HTTPError as exc:
            return "", f"HTTP {exc.code}"
        except Exception as exc:
            return "", type(exc).__name__
        try:
            data = json.loads(raw)
            text = data["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return "", "bad response"
        text = _THINK.sub("", str(text)).strip()
        return (text, "") if text else ("", "empty")

    def _sync(self, system: str, history: list[tuple[str, str]], kind: str) -> str:
        msgs = to_chat_messages(history)
        t0 = time.perf_counter()
        if all(self._cool.get(m, 0.0) > time.monotonic() for m in self.chain):
            self._cool.clear()
        tried: list[str] = []
        for model in self.chain:
            if self._cool.get(model, 0.0) > time.monotonic():
                continue
            for key in self._keys:
                text, err = self._one(system, msgs, model, key)
                if text:
                    self.records.append(
                        {
                            "ms": round((time.perf_counter() - t0) * 1000),
                            "model": model,
                            "ok": True,
                            "tried": tried,
                            "kind": kind,
                        }
                    )
                    return text
                tried.append(f"{model}:{err}")
                self._cool[model] = time.monotonic() + self.cooldown_s
                if err == "HTTP 401":
                    break  # this key is bad: next key, same model
                if err != "HTTP 429":
                    break  # other error: next model
        self.records.append(
            {
                "ms": round((time.perf_counter() - t0) * 1000),
                "model": None,
                "ok": False,
                "tried": tried,
                "kind": kind,
            }
        )
        raise RuntimeError("all free models failed: " + ", ".join(tried)[:300])

    async def __call__(self, system: str, history: list[tuple[str, str]]) -> str:
        kind = "summary" if system.startswith("Summarise") else "reply"
        return await asyncio.to_thread(self._sync, system, history, kind)


SIM_PREFIX = "sim-"


# ------------------------------------------------------------------ TTS
def find_piper() -> tuple[str, str]:
    """(binary, voice onnx). Env JARVIS_SIM_PIPER / JARVIS_SIM_VOICE override."""
    exe = os.environ.get("JARVIS_SIM_PIPER") or (
        str(DEFAULT_PIPER) if DEFAULT_PIPER.exists() else shutil.which("piper") or ""
    )
    voice = os.environ.get("JARVIS_SIM_VOICE", "")
    if not voice:
        for d in sorted(Path.home().glob(".local/share/piper*")):
            hits = sorted(d.rglob("en_US-lessac-medium.onnx"))
            if hits:
                voice = str(hits[0])
                break
    return exe, voice


def synth(text: str, out: Path, piper: str, voice: str) -> float:
    """Run piper -> wav. Returns seconds taken. Raises on failure."""
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    p = subprocess.run(
        [piper, "-m", voice, "-f", str(out)],
        input=text.encode(),
        capture_output=True,
        timeout=60,
    )
    if p.returncode != 0 or not out.exists():
        raise RuntimeError(f"piper failed rc={p.returncode}: {p.stderr[-200:]!r}")
    return time.perf_counter() - t0


def wav_levels(path: Path, window_s: float = 0.08) -> tuple[list[float], float]:
    """RMS level 0..1 per window and duration seconds. ([], 0.0) if unreadable."""
    try:
        with wave.open(str(path), "rb") as w:
            rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except (wave.Error, OSError, EOFError):
        return [], 0.0
    if width != 2 or not raw:
        return [], 0.0
    import array

    a = array.array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    step = max(1, int(rate * window_s)) * ch
    levels = []
    for i in range(0, len(a), step):
        chunk = a[i : i + step]
        if not chunk:
            break
        rms = math.sqrt(sum(s * s for s in chunk) / len(chunk)) / 32768.0
        levels.append(min(1.0, rms * 4.0))  # speech RMS is small: scale up
    return levels, len(a) / ch / rate


def _player() -> list[str] | None:
    for cmd in (
        ["paplay"],
        ["aplay", "-q"],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
    ):
        if shutil.which(cmd[0]):
            return cmd
    return None


# ------------------------------------------------------------------ scenarios
@dataclass
class Scenario:
    id: str
    title: str
    setup: dict = field(default_factory=dict)
    caller_turns: list = field(default_factory=list)
    llm_replies: list = field(default_factory=list)
    expect: dict = field(default_factory=dict)
    path: Path | None = None


def load_scenarios(directory: Path | None = None) -> dict[str, Scenario]:
    d = directory or SCENARIO_DIR
    out: dict[str, Scenario] = {}
    for p in sorted(d.glob("*.json")):
        try:
            raw = json.loads(p.read_text())
            sc = Scenario(
                id=str(raw.get("id") or p.stem),
                title=str(raw.get("title") or p.stem),
                setup=raw.get("setup") or {},
                caller_turns=list(raw.get("caller_turns") or []),
                llm_replies=list(raw.get("llm_replies") or []),
                expect=raw.get("expect") or {},
                path=p,
            )
        except (OSError, ValueError, AttributeError):
            continue
        out[sc.id] = sc
    return out


def _parse_now(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        return None


def _turn_text(t: Any) -> str:
    return str(t.get("text", "")) if isinstance(t, dict) else str(t)


def _turn_cmd(t: Any) -> str:
    return str(t.get("command", "")) if isinstance(t, dict) else ""


# ------------------------------------------------------------------ evaluate
def evaluate(
    scn: Scenario,
    jarvis_lines: list[str],
    evs: list[dict],
    outcome: str,
    booking: Any = None,
    audits: list[str] | None = None,
    lenient_outcome: bool = False,
) -> list[dict]:
    """Check expect{} -> [{check, status PASS|FAIL|SKIP, detail}].

    events: ``state:<s>``, ``audit:<kind>``, or a bare event name.
    booking: dict {status, calendar_events_added} compared to ``booking``
    (the observed dict), or None when the calendar is unavailable.
    """
    ex = scn.expect or {}
    text = "\n".join(jarvis_lines).lower()
    names = {e.get("event") for e in evs}
    names |= {f"state:{e['state']}" for e in evs if e.get("event") == "state"}
    names |= {f"audit:{a}" for a in (audits or [])}
    res: list[dict] = []

    def add(check: str, ok: bool | None, detail: str = "") -> None:
        res.append(
            {
                "check": check,
                "status": "SKIP" if ok is None else ("PASS" if ok else "FAIL"),
                "detail": detail,
            }
        )

    for s in ex.get("must_contain") or []:
        add(f"must_contain {s!r}", str(s).lower() in text)
    for s in ex.get("must_not_contain") or []:
        add(f"must_not_contain {s!r}", str(s).lower() not in text)
    for n in ex.get("events") or []:
        add(f"event {n}", n in names)
    if "escalation" in ex:
        add("escalation", ("escalation" in names) == bool(ex["escalation"]))
    if ex.get("outcome"):
        want = ex["outcome"] if isinstance(ex["outcome"], list) else [ex["outcome"]]
        if lenient_outcome and "completed" in want:
            want = [*want, "hangup"]  # live model may not emit END
        add(f"outcome in {want}", outcome in want, f"got {outcome!r}")
    if ex.get("booking"):
        want = ex["booking"]
        if booking is None:
            add("booking", None, "calendar backend unavailable")
        elif isinstance(want, dict):
            bad = {
                k: (v, booking.get(k)) for k, v in want.items() if booking.get(k) != v
            }
            add(
                "booking",
                not bad,
                f"observed={booking}" + (f" mismatch={bad}" if bad else ""),
            )
        else:
            add("booking", bool(booking), f"observed={booking}")
    return res


class ScriptedLLM:
    """Replays scenario ``llm_replies`` in order (summary prompts excluded)."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.records: list[dict] = []

    async def __call__(self, system: str, history: list) -> str:
        kind = "summary" if system.startswith("Summarise") else "reply"
        if kind == "summary":
            return "Scripted call summary."
        self.records.append(
            {"ms": 0, "model": "scripted", "ok": True, "tried": [], "kind": kind}
        )
        return self.replies.pop(0) if self.replies else "Okay."


# ------------------------------------------------------------------ runner
class SimRunner:
    def __init__(
        self,
        *,
        out_root: Path | None = None,
        run_id: str | None = None,
        llm: Callable | None = None,
        model: str | None = None,
        play: bool = False,
        hud: bool = False,
        llm_caller: bool = False,
        piper: str | None = None,
        voice: str | None = None,
        pace: bool | None = None,
        scripted: bool = False,
    ) -> None:
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        base = out_root or (events.calls_dir() / "sim")
        self.run_dir = Path(base) / self.run_id
        self.llm = llm
        self.model = model
        self.play = play
        self.hud = hud
        self.llm_caller = llm_caller
        self.scripted = scripted
        p_exe, p_voice = find_piper()
        self.piper = piper if piper is not None else p_exe
        self.voice = voice if voice is not None else p_voice
        self.pace = (play or hud) if pace is None else pace
        self.live_events = events.calls_dir() / "events.jsonl"  # resolved pre-tmp
        self._llm_obj: Any = llm

    # --- llm setup
    def _get_llm(self) -> Callable:
        if self._llm_obj is None:
            keys = openrouter_keys()
            if not keys:
                raise RuntimeError("no OpenRouter key found")
            chain = build_chain(self.model, discover_free_models(keys[0]))
            self._llm_obj = OpenRouterLLM(keys, chain)
        return self._llm_obj

    def _records(self) -> list[dict]:
        return getattr(self._llm_obj, "records", None) or []

    # --- one scenario
    async def run(self, scn: Scenario) -> dict:
        from calls import agent, contacts, rules, store
        from calls.wa_call import FakeDriver

        sdir = self.run_dir / scn.id
        sdir.mkdir(parents=True, exist_ok=True)
        ev_path = sdir / "events.jsonl"
        ev_path.write_text("")
        env_keep = {
            k: os.environ.get(k)
            for k in ("JARVIS_CALLS_DIR", "JARVIS_HOME", "JARVIS_VAULT")
        }
        tmp = Path(tempfile.mkdtemp(prefix="callsim-"))
        os.environ["JARVIS_CALLS_DIR"] = str(tmp / "calls")
        os.environ["JARVIS_HOME"] = str(tmp / "home")
        os.environ["JARVIS_VAULT"] = str(tmp / "vault")
        store.close_all()
        evs: list[dict] = []
        live = self.live_events if self.hud else None

        def sink(e: dict) -> None:
            cid = e.get("call_id")
            if cid is not None and not str(cid).startswith(SIM_PREFIX):
                e = {
                    **e,
                    "call_id": f"{SIM_PREFIX}{cid}",
                }  # never collide with real ids
            evs.append(e)
            line = json.dumps(e) + "\n"
            with ev_path.open("a", encoding="utf-8") as f:
                f.write(line)
            if live is not None:
                live.parent.mkdir(parents=True, exist_ok=True)
                with live.open("a", encoding="utf-8") as f:
                    f.write(line)

        events.set_sink(sink)
        try:
            return await self._run(scn, sdir, evs, agent, contacts, rules, FakeDriver)
        finally:
            events.set_sink(None)
            events.set_active(None)
            store.close_all()
            for k, v in env_keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(tmp, ignore_errors=True)

    async def _run(self, scn, sdir, evs, agent, contacts, rules, fake_driver) -> dict:
        from calls import calllog

        setup = scn.setup or {}
        number = str(setup.get("number") or "+15550100001")
        direction = str(setup.get("direction") or "in")
        for c in setup.get("contacts") or []:
            try:
                contacts.add_contact(**c)
            except Exception as exc:
                print(f"  contact seed failed: {exc}", file=sys.stderr)
        for r in setup.get("rules") or []:
            try:
                rules.add_rule(**r)
            except Exception as exc:
                print(f"  rule seed failed: {exc}", file=sys.stderr)
        base_now = _parse_now(setup.get("now"))
        t_start = time.time()
        clock = (lambda: base_now + (time.time() - t_start)) if base_now else time.time

        seed = list(setup.get("calendar_seed") or [])
        tools, backend = self._calendar(seed, setup.get("now"))
        if self.scripted:
            self._llm_obj = ScriptedLLM(scn.llm_replies)
        llm = self._get_llm()

        n_turn = [0]
        tts_log: list[dict] = []
        player = _player() if self.play else None

        async def speaker(text: str, persona: str) -> None:
            n_turn[0] += 1
            wav = sdir / f"turn-{n_turn[0]:02d}.wav"
            rec: dict = {"turn": n_turn[0], "wav": str(wav), "tts_ms": None}
            tts_log.append(rec)
            try:
                dur = await asyncio.to_thread(synth, text, wav, self.piper, self.voice)
                rec["tts_ms"] = round(dur * 1000)
            except Exception as exc:
                rec["error"] = str(exc)[:200]
                return
            levels, _secs = wav_levels(wav)
            proc = None
            if player:
                proc = await asyncio.create_subprocess_exec(
                    *player,
                    str(wav),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            if self.hud or self.pace:
                for lv in levels:
                    events.emit(events.speaking_event(self.call_id(), lv))
                    if self.pace:
                        await asyncio.sleep(0.08)
            if proc:
                await proc.wait()

        drv = fake_driver()
        if direction == "in":
            drv.ring("Sim caller")
        sess = agent.CallSession(
            direction=direction,
            number=number,
            llm=llm,
            calendar=tools,
            speaker=speaker,
            driver=drv,
            goal=str(setup.get("goal") or ""),
            clock=clock,
            settings=lambda n, d=None: {
                "disclose_ai": True,
                "record_calls": False,
                "max_call_s": 600.0,
            }.get(n, d),
        )
        self.call_id = lambda: sess.call_id
        turns_out: list[dict] = []
        t0 = time.perf_counter()
        if direction == "in":
            await sess.start_inbound()
        else:
            await sess.start_outbound()
        turns_out.append(
            {
                "turn": 0,
                "caller": None,
                "jarvis": _last(sess, "jarvis"),
                "llm_ms": 0,
                "tts_ms": _tts(tts_log, 0),
            }
        )

        script = list(scn.caller_turns)
        caller_llm = self._caller_llm(scn) if self.llm_caller else None
        for i, t in enumerate(script):
            if sess.state == "ended":
                break
            cmd = _turn_cmd(t)
            if cmd:
                await sess.handle_command(cmd)
                continue
            text = _turn_text(t)
            if caller_llm is not None:
                try:
                    text = await caller_llm(sess, text, i)
                except Exception as exc:
                    print(f"  llm-caller failed, using script: {exc}", file=sys.stderr)
            r0, w0 = len(self._records()), len(sess.turns)
            n_before = len(tts_log)
            if self.pace:
                words = text.split()
                for k in range(1, len(words), 2):
                    await sess.on_caller_text(" ".join(words[:k]), partial=True)
                    await asyncio.sleep(0.15)
            await sess.on_caller_text(text)
            new = self._records()[r0:]
            reply = [x.text for x in sess.turns[w0:] if x.speaker == "jarvis"]
            turns_out.append(
                {
                    "turn": len(turns_out),
                    "caller": text,
                    "jarvis": " ".join(reply),
                    "llm_ms": sum(x["ms"] for x in new if x["kind"] == "reply"),
                    "model": (new[-1]["model"] if new else None),
                    "tts_ms": sum(x["tts_ms"] or 0 for x in tts_log[n_before:]),
                }
            )
        outcome = sess.outcome
        if sess.state == "ringing":
            outcome = "ringing"  # never answered: leave it ringing
        elif sess.state != "ended":
            await sess.end("completed")  # script over: caller finished
            outcome = sess.outcome
        try:
            audits = [a["kind"] for a in calllog.list_audit(500)]
        except Exception:
            audits = []
        jarvis_lines = [t.text for t in sess.turns if t.speaker == "jarvis"]
        booking = self._booking(tools, backend, len(seed))
        checks = evaluate(
            scn,
            jarvis_lines,
            evs,
            outcome,
            booking,
            audits,
        )
        failed = [c for c in checks if c["status"] == "FAIL"]
        errors = [r["error"] for r in tts_log if r.get("error")]
        return {
            "scenario": scn.id,
            "title": scn.title,
            "status": "FAIL" if failed else "PASS",
            "outcome": outcome,
            "summary": sess.summary,
            "checks": checks,
            "turns": turns_out,
            "models_used": sorted({r["model"] for r in self._records() if r["model"]}),
            "llm_failures": [r["tried"] for r in self._records() if not r["ok"]],
            "tts_errors": errors,
            "wall_s": round(time.perf_counter() - t0, 2),
            "dir": str(sdir),
        }

    # --- calendar (W6, lazy)
    def _calendar(self, seed, now):
        """(CalendarTools, InMemoryCalendar) or (None, None) if W6 is absent."""
        try:
            from calls import calendar_tools as ct

            backend = ct.InMemoryCalendar(seed)
            base = _parse_now(now)
            clock = None
            if base is not None:
                fixed = ct.as_dt(datetime.fromtimestamp(base, ct.TZ))
                clock = lambda: fixed  # noqa: E731
            return ct.CalendarTools(backend, clock=clock), backend
        except Exception as exc:
            print(f"  calendar tools unavailable: {exc}", file=sys.stderr)
            return None, None

    def _booking(self, tools, backend, seeded: int):
        """{status, calendar_events_added} of the last proposal, or None."""
        if tools is None or backend is None:
            return None
        props = list(getattr(tools, "proposals", {}).values())
        return {
            "status": props[-1].status if props else "none",
            "calendar_events_added": len(backend) - seeded,
        }

    # --- llm caller
    def _caller_llm(self, scn: Scenario):
        llm = self._get_llm()
        intent = " | ".join(_turn_text(t) for t in scn.caller_turns if _turn_text(t))
        system = (
            "You role-play a phone caller in a test. Reply with ONLY the caller's "
            "next spoken line, one or two short sentences, no quotes or stage "
            f"directions. Scenario: {scn.title}. Intent, in order: {intent}"
        )

        async def gen(sess, scripted: str, i: int) -> str:
            hist = [
                ("jarvis" if t.speaker == "caller" else "caller", t.text)
                for t in sess.turns
            ]  # swapped: model plays the caller
            hist.append(("caller", f"(your next line; intent: {scripted})"))
            out = await llm(system, [(w, x) for w, x in hist])
            return " ".join(out.split())[:300] or scripted

        return gen

    # --- all
    async def run_many(self, scns: list[Scenario]) -> dict:
        results = []
        for s in scns:
            print(f"== {s.id}: {s.title}")
            try:
                results.append(await self.run(s))
            except Exception as exc:
                results.append(
                    {
                        "scenario": s.id,
                        "title": s.title,
                        "status": "FAIL",
                        "error": f"{type(exc).__name__}: {exc}"[:300],
                        "checks": [],
                        "turns": [],
                    }
                )
        report = {
            "run_id": self.run_id,
            "passed": sum(r["status"] == "PASS" for r in results),
            "total": len(results),
            "results": results,
        }
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "report.json").write_text(json.dumps(report, indent=2))
        (self.run_dir / "report.md").write_text(render_md(report))
        return report


def _last(sess, speaker: str) -> str:
    for t in reversed(sess.turns):
        if t.speaker == speaker:
            return t.text
    return ""


def _tts(log: list[dict], idx: int):
    return log[idx]["tts_ms"] if idx < len(log) else None


def render_md(report: dict) -> str:
    out = [
        f"# Call sim {report['run_id']}",
        "",
        f"{report['passed']}/{report['total']} passed",
        "",
    ]
    for r in report["results"]:
        out += [f"## {r['scenario']}: {r['status']}", r.get("title", ""), ""]
        if r.get("error"):
            out += [f"Error: {r['error']}", ""]
        out += ["| check | status | detail |", "|---|---|---|"]
        for c in r["checks"]:
            out.append(f"| {c['check']} | {c['status']} | {c['detail']} |")
        out += ["", "| turn | LLM ms | TTS ms | model |", "|---|---|---|---|"]
        for t in r["turns"]:
            out.append(
                f"| {t['turn']} | {t.get('llm_ms')} | {t.get('tts_ms')} | {t.get('model') or ''} |"
            )
        out += ["", f"Outcome: {r.get('outcome')}. Summary: {r.get('summary')}", ""]
        for t in r["turns"]:
            if t.get("caller"):
                out.append(f"- Caller: {t['caller']}")
            out.append(f"- Jarvis: {t.get('jarvis')}")
        out.append("")
    return "\n".join(out)


def render_table(report: dict) -> str:
    lines = []
    for r in report["results"]:
        lines.append(
            f"\n[{r['status']}] {r['scenario']}  outcome={r.get('outcome')}  models={r.get('models_used')}"
        )
        if r.get("error"):
            lines.append(f"   error: {r['error']}")
        for c in r["checks"]:
            lines.append(f"   {c['status']:4} {c['check']} {c['detail']}")
        for t in r["turns"]:
            lines.append(
                f"   turn {t['turn']:>2}  llm {t.get('llm_ms')!s:>6} ms  tts {t.get('tts_ms')!s:>6} ms"
            )
        if r.get("tts_errors"):
            lines.append(f"   tts errors: {r['tts_errors'][:1]}")
    lines.append(f"\n{report['passed']}/{report['total']} passed")
    return "\n".join(lines)
