"""Wizard step registry + runner.

Each step: id, title, check() -> (status, detail), and run() which
executes the install/probe. The runner consults persisted state and
skips green steps unless ``--force``. A step is marked green only when
its check passes after run; run failures mark it red (so a rerun retries).

Steps (plan order): sysdeps -> livekit -> venvs -> models -> keys ->
audio -> gate. I/O steps shell out via the module helpers above.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from wizard import WIZARD_VERSION
from wizard.state import WizardState

GREEN, AMBER, RED, PENDING = "green", "amber", "red", "pending"


@dataclass
class Step:
    id: str
    title: str
    check: Callable[[], tuple[str, str]]
    run: Callable[[], str] = field(default=lambda: "")
    order: int = 0


def repo_root() -> Path:
    override = os.environ.get("JARVIS_REPO", "").strip()
    if override:
        return Path(override)
    # Walk up from cwd: `python -m wizard` run from src/ (or any subdir)
    # must still find the checkout instead of reporting phantom ambers.
    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "src" / "agent.py").is_file() and (
            candidate / "pyproject.toml"
        ).is_file():
            return candidate
    return cwd


# ---------------------------------------------------------------- steps


def _sysdeps_check() -> tuple[str, str]:
    from wizard.deps import required_missing

    missing = required_missing()
    if not missing:
        return GREEN, "all required system tools present"
    names = ", ".join(t.name for t in missing)
    return AMBER, f"missing: {names}"


def _sysdeps_run() -> str:
    from wizard.deps import missing_tools

    results = []
    for tool in missing_tools():
        cmd = tool.install_command()
        if not cmd:
            continue
        results.append(f"install {tool.name}: {' '.join(cmd)}")
    if not results:
        return ""
    return "run these (sudo may prompt), then rerun the wizard:\n" + "\n".join(results)


def _livekit_check() -> tuple[str, str]:
    from wizard.livekit import resolve_livekit_bin

    bin_path = resolve_livekit_bin()
    if bin_path.is_file() and os.access(bin_path, os.X_OK):
        return GREEN, f"livekit-server present ({bin_path})"
    return AMBER, f"livekit-server missing ({bin_path})"


def _livekit_run() -> str:
    from wizard.livekit import (
        build_livekit_yaml,
        ensure_archive,
        generate_keypair,
        livekit_env_updates,
        resolve_livekit_bin,
        write_env,
    )

    home = os.environ.get("JARVIS_HOME", "").strip() or str(Path.home() / ".jarvis")
    bin_path = resolve_livekit_bin(home)
    try:
        ensure_archive(bin_path)
    except Exception as exc:
        return f"livekit-server download failed: {exc}"

    # Preserve an existing keypair so the yaml + env files stay in sync
    # across reruns; only generate fresh when nothing exists yet.
    yaml_path = Path(home) / "livekit.yaml"
    api_key, api_secret = _read_existing_keypair(yaml_path) or generate_keypair()
    yaml_path.write_text(build_livekit_yaml(api_key, api_secret))
    with contextlib.suppress(OSError):
        os.chmod(yaml_path, 0o600)

    repo = repo_root()
    url = "ws://127.0.0.1:7880"
    updates = livekit_env_updates(url, api_key, api_secret)
    write_env(repo / ".env.local", updates)
    write_env(repo / "frontend" / ".env.local", updates)
    return ""


def _read_existing_keypair(yaml_path: Path) -> tuple[str, str] | None:
    """Parse the api key + secret out of an existing livekit.yaml. Pure."""
    try:
        lines = yaml_path.read_text().splitlines()
    except OSError:
        return None
    in_keys = False
    key = secret = ""
    for line in lines:
        stripped = line.strip()
        if stripped == "keys:":
            in_keys = True
            continue
        if in_keys and stripped and not stripped.startswith("#") and ":" in stripped:
            k, _, v = stripped.partition(":")
            if not key:
                key = k.strip()
            secret = v.strip()
    if key and secret:
        return key, secret
    return None


def _venvs_check() -> tuple[str, str]:
    from wizard.venvs import VenvPlan, main_venv_exists, wake_venv_exists

    plan = VenvPlan(repo_root())
    if main_venv_exists(plan) and wake_venv_exists(plan):
        return GREEN, "both venvs present"
    return AMBER, "venvs missing (uv sync on first run)"


def _venvs_run() -> str:
    from wizard.venvs import VenvPlan, sync_all

    failures = sync_all(VenvPlan(repo_root()))
    if failures:
        return "; ".join(failures)
    return ""


def _models_check() -> tuple[str, str]:
    from wizard.models import model_summary

    s = model_summary()
    if s["voice_present"] and s["oww_present"]:
        return GREEN, f"voice {s['voice']} + OWW resources present"
    return AMBER, "models missing (Piper voice + OWW resources)"


def _models_run() -> str:
    from wizard.models import download_piper, oww_present, voice_dir

    try:
        download_piper(voice_dir())
    except Exception as exc:
        return f"piper voice download failed: {exc}"
    if not oww_present():
        return "openwakeword resources missing — run the wake venv install (venvs step)"
    return ""


def _keys_check() -> tuple[str, str]:
    from wizard.keys import keys_summary

    s = keys_summary(repo_root())
    if s["google"]:
        return GREEN, "GOOGLE_API_KEY present" + (
            ", Brave/You optional" if not (s["brave"] or s["ydc"]) else " + search keys"
        )
    return AMBER, "GOOGLE_API_KEY missing"


def _keys_run() -> str:
    """Interactive key collection. Returns failure message or ''."""
    from wizard.keys import CollectedKeys, keys_summary, write_keys

    current = keys_summary(repo_root())
    # If already green, nothing to do (run only called when not green).
    print("\nAPI keys (input is hidden)")
    print("--------------------------")
    google = _prompt_secret("GOOGLE_API_KEY (required, https://aistudio.google.com): ")
    brave = ""
    ydc = ""
    if not current["brave"]:
        brave = _prompt_secret("BRAVE_API_KEY (optional, free tier — Enter to skip): ")
    if not current["ydc"]:
        ydc = _prompt_secret("YDC_API_KEY (optional You.com — Enter to skip): ")
    shodan = _prompt_secret("SHODAN_API_KEY (optional OSINT — Enter to skip): ")

    if not google.strip():
        return "no GOOGLE_API_KEY entered"
    keys = CollectedKeys(
        google=google.strip(),
        brave=brave.strip(),
        ydc=ydc.strip(),
        shodan=shodan.strip(),
    )
    write_keys(repo_root(), keys)
    return ""


def _prompt_secret(prompt: str) -> str:
    try:
        import getpass

        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def _audio_check() -> tuple[str, str]:
    from wizard.audio import probe_audio

    probe = probe_audio()
    if probe.ok:
        return GREEN, probe.detail
    return AMBER, probe.detail or "no audio devices found"


def _audio_run() -> str:
    return ""  # probe-only; check is authoritative


def _gate_check() -> tuple[str, str]:
    from wizard.gate import probe_bridge

    ok, _ = probe_bridge()
    if ok:
        return GREEN, "bridge /health OK on 127.0.0.1:4317"
    return AMBER, "bridge /health not answering (start the shell, then rerun)"


def _gate_run() -> str:
    return ""


# ------------------------------------------------------------- registry

STEPS: list[Step] = [
    Step(
        "sysdeps",
        "System dependencies (dnf + flatpak)",
        _sysdeps_check,
        _sysdeps_run,
        10,
    ),
    Step(
        "livekit",
        "Bundled livekit-server + local config",
        _livekit_check,
        _livekit_run,
        20,
    ),
    Step("venvs", "Python venvs (uv sync x2)", _venvs_check, _venvs_run, 30),
    Step("models", "Piper voice + wake-word models", _models_check, _models_run, 40),
    Step("keys", "API keys (GOOGLE, Brave/You optional)", _keys_check, _keys_run, 50),
    Step("audio", "Mic/speaker probe", _audio_check, _audio_run, 60),
    Step("gate", "Bridge /health gate (finale)", _gate_check, _gate_run, 70),
]

ORDER = sorted(STEPS, key=lambda s: s.order)

STEP_IDS = [s.id for s in ORDER]


def step_by_id(step_id: str) -> Step | None:
    for s in ORDER:
        if s.id == step_id:
            return s
    return None


@dataclass
class RunResult:
    step_id: str
    status: str
    detail: str
    skipped: bool = False


def run_step(step: Step, state: WizardState, *, force: bool = False) -> RunResult:
    """Execute one step with persist + skip-green semantics. I/O."""
    if state.is_green(step.id) and not force:
        return RunResult(step.id, GREEN, "already green; skipped", skipped=True)

    status, detail = step.check()
    if status == GREEN and not force:
        state.mark(step.id, GREEN, detail)
        return RunResult(step.id, GREEN, detail)

    # Attempt the install/probe, then re-check.
    if step.run:
        failure = step.run()
        if failure:
            state.mark(step.id, RED, failure)
            return RunResult(step.id, RED, failure)
    status, detail = step.check()
    state.mark(step.id, status, detail)
    return RunResult(step.id, status, detail)


def run_all(state: WizardState, *, force: bool = False) -> list[RunResult]:
    results = []
    for step in ORDER:
        result = run_step(step, state, force=force)
        results.append(result)
        if result.status == RED:
            # Stop the chain: later steps depend on earlier ones (e.g.
            # gate needs the venv+server). Report and bail.
            break
    return results


def summary(state: WizardState) -> dict:
    return {
        "wizard_version": WIZARD_VERSION,
        "steps": {
            s.id: {"status": state.get(s.id).status, "detail": state.get(s.id).detail}
            for s in ORDER
        },
        "all_green": all(state.is_green(s.id) for s in ORDER),
    }
