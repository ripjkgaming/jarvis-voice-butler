"""Wizard pure-logic tests: state, deps catalog, livekit builders,
venv commands, model paths, HF cache audit. No network, no sounddevice."""

from __future__ import annotations

from wizard import deps, gate, livekit, models, steps, venvs
from wizard.state import WizardState, state_path

# ------------------------------------------------------------------ state


def test_state_roundtrips(tmp_path) -> None:
    state = WizardState(tmp_path / "state.json")
    state.mark("sysdeps", "green", "ok")
    state.save()

    reloaded = WizardState(tmp_path / "state.json")
    assert reloaded.is_green("sysdeps")
    assert reloaded.get("sysdeps").detail == "ok"
    assert reloaded.get("nope").status == "pending"
    assert not reloaded.is_green("nope")


def test_state_missing_file_is_empty(tmp_path) -> None:
    state = WizardState(tmp_path / "missing.json")
    assert state.steps == {}
    assert not state.is_green("anything")


def test_state_path_honors_jarvis_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    assert state_path() == tmp_path / "home" / "wizard-state.json"
    assert (
        state_path(tmp_path / "explicit") == tmp_path / "explicit" / "wizard-state.json"
    )


def test_reset_forgets_steps(tmp_path) -> None:
    state = WizardState(tmp_path / "state.json")
    state.mark("a", "green")
    state.mark("b", "green")
    state.reset("a")
    assert not state.is_green("a")
    assert state.is_green("b")
    state.reset()
    assert state.steps == {}


def test_state_file_is_0600(tmp_path) -> None:
    state = WizardState(tmp_path / "state.json")
    state.mark("a", "green")
    mode = (tmp_path / "state.json").stat().st_mode & 0o777
    assert mode == 0o600


# ------------------------------------------------------------------ deps


def test_deps_catalog_covers_audited_binaries() -> None:
    # Every binary the code invokes must be findable in the catalog.
    audited = {
        "spectacle",
        "convert",
        "import",
        "tesseract",
        "pactl",
        "playerctl",
        "wmctrl",
        "wl-copy",
        "wl-paste",
        "wtype",
        "fdfind",
        "fd",
        "brave-browser",
        "notify-send",
        "nmap",
        "nikto",
        "gobuster",
        "upower",
        "kdeconnect-cli",
        "qdbus",
        "sober",
    }
    covered = {b for t in deps.TOOLS for b in t.binaries}
    assert audited <= covered


def test_every_tool_has_install_route() -> None:
    for t in deps.TOOLS:
        assert t.dnf_packages or t.flatpak_app, f"{t.name} has no install route"


def test_brave_is_native_only() -> None:
    """Regression: flatpak Brave satisfies the checklist while breaking
    browser.py (PATH binaries only), so it must never be the route."""
    brave = next(t for t in deps.TOOLS if t.name == "brave")
    assert brave.flatpak_app is None
    assert "brave-browser" in brave.binaries
    assert "brave-browser" in brave.dnf_packages


def test_flatpak_tools_install_via_flatpak() -> None:
    whatsie = deps.tool_by_name("whatsie")
    assert whatsie is not None
    cmd = whatsie.install_command()
    assert cmd[0] == "flatpak"
    assert "com.ktechpit.whatsie" in cmd


def test_install_command_uses_sudo_dnf() -> None:
    t = deps.tool_by_name("nmap")
    cmd = t.install_command()
    assert cmd[:2] == ["sudo", "dnf"]


def test_missing_tools_uses_which() -> None:
    t = deps.tool_by_name("spectacle")
    assert t.installed(which=lambda _: None) is False
    assert t.installed(which=lambda b: b) is True


def test_uinput_rule_text() -> None:
    assert "uinput" in deps.UINPUT_RULE
    assert 'MODE="0660"' in deps.UINPUT_RULE


def test_flatpak_only_tool_detects_flatpak(monkeypatch) -> None:
    whatsie = deps.tool_by_name("whatsie")
    assert whatsie is not None
    assert whatsie.binaries == ()

    monkeypatch.setattr(
        deps, "_flatpak_installed", lambda app: app == "com.ktechpit.whatsie"
    )
    assert whatsie.installed() is True

    monkeypatch.setattr(deps, "_flatpak_installed", lambda app: False)
    assert whatsie.installed() is False


def test_tool_installed_via_flatpak_or_binary(monkeypatch) -> None:
    sober = deps.tool_by_name("sober")
    assert sober is not None

    # binary missing, flatpak present -> installed
    monkeypatch.setattr(deps, "_flatpak_installed", lambda app: True)
    assert sober.installed(which=lambda b: None) is True

    # neither -> missing
    monkeypatch.setattr(deps, "_flatpak_installed", lambda app: False)
    assert sober.installed(which=lambda b: None) is False


# ---------------------------------------------------------------- livekit


def test_arch_golang_maps_common_machines(monkeypatch) -> None:
    monkeypatch.setattr(
        livekit.os, "uname", lambda: type("U", (), {"machine": "x86_64"})
    )
    assert livekit.arch_golang() == "amd64"
    monkeypatch.setattr(
        livekit.os, "uname", lambda: type("U", (), {"machine": "aarch64"})
    )
    assert livekit.arch_golang() == "arm64"


def test_download_url_is_per_arch(monkeypatch) -> None:
    url = livekit.download_url("1.13.7", "arm64")
    assert url.endswith("livekit_1.13.7_linux_arm64.tar.gz")
    assert "releases/download/v1.13.7/" in url


def test_keypair_shapes() -> None:
    key, secret = livekit.generate_keypair(rand=lambda n: b"a" * n)
    assert key == "devkey"
    assert len(secret) == livekit.DEFAULT_KEY_LEN * 2  # hex of 32 bytes


def test_livekit_yaml_content() -> None:
    text = livekit.build_livekit_yaml("devkey", "deadbeef")
    assert "port: 7880" in text
    assert "devkey: deadbeef" in text
    assert "127.0.0.1" in text
    assert "empty_timeout: 60" in text
    assert "enabled: false" in text  # TURN off


def test_livekit_yaml_uses_only_real_server_keys() -> None:
    """Phase 4.5 guard: livekit-server has NO egress-firewall/allowlist
    config (verified against v1.13.7 `help-verbose` + docs: "egress" there
    is the separate     recording service, not a firewall). The generated
    config must therefore stay within the real key set — a fake `egress:`
    stanza would achieve zero restriction while risking a strict-parse
    boot failure. Real containment is OS-level (owner sudo, post-v1)."""
    text = livekit.build_livekit_yaml("devkey", "deadbeef")
    top_keys = {
        line.split(":")[0]
        for line in text.splitlines()
        if line and not line.startswith((" ", "#"))
    }
    assert top_keys <= {"port", "bind_addresses", "keys", "rtc", "room", "turn"}, (
        top_keys
    )


def test_resolve_livekit_bin_prefers_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_LIVEKIT_BIN", str(tmp_path / "custom"))
    assert livekit.resolve_livekit_bin() == tmp_path / "custom"


def test_read_write_env_merge(tmp_path) -> None:
    env = tmp_path / ".env.local"
    env.write_text('A=1\n# comment\nB="quoted"\n')
    livekit.write_env(env, {"A": "2", "C": "new"})
    parsed = livekit.read_env(env)
    assert parsed == {"A": "2", "B": "quoted", "C": "new"}
    # Comment preserved.
    assert "# comment" in env.read_text()


def test_write_env_preserves_unrelated_keys(tmp_path) -> None:
    env = tmp_path / ".env.local"
    env.write_text("GOOGLE_API_KEY=old\nAGENT_NAME=my-agent\n")
    livekit.write_env(env, {"GOOGLE_API_KEY": "new"})
    parsed = livekit.read_env(env)
    assert parsed["GOOGLE_API_KEY"] == "new"
    assert parsed["AGENT_NAME"] == "my-agent"


# ----------------------------------------------------------------- venvs


def test_venv_plan_paths() -> None:
    from pathlib import Path

    plan = venvs.VenvPlan(repo=Path("/repo"))
    assert plan.main_venv == Path("/repo/.venv")
    assert plan.wake_venv == Path("/repo/.venv-wake")


def test_wake_install_drops_tflite() -> None:
    from pathlib import Path

    plan = venvs.VenvPlan(repo=Path("/repo"))
    flat = [c for cmd in venvs.wake_install_commands(plan) for c in cmd]
    assert "tflite" not in " ".join(flat)
    assert "openwakeword" in " ".join(flat)
    assert "onnxruntime==1.30.0" in " ".join(flat)


def test_main_sync_pins_python() -> None:
    from pathlib import Path

    plan = venvs.VenvPlan(repo=Path("/repo"))
    cmd = venvs.main_sync_command(plan)
    assert cmd[:2] == ["uv", "sync"]
    assert "3.14" in cmd


# ----------------------------------------------------------------- models


def test_piper_paths_and_present(tmp_path) -> None:
    onnx, js = models.piper_paths(tmp_path)
    assert onnx.name == "en_GB-alan-medium.onnx"
    assert js.name == "en_GB-alan-medium.onnx.json"
    assert models.piper_present(tmp_path) is False
    onnx.write_bytes(b"x" * (1_000_001))
    js.write_text("{}")
    assert models.piper_present(tmp_path) is True


def test_oww_resources_required() -> None:
    assert "hey_jarvis_v0.1.onnx" in models.OWW_RESOURCES
    assert "melspectrogram.onnx" in models.OWW_RESOURCES


def test_oww_resource_dir_cwd_independent(monkeypatch, tmp_path) -> None:
    """Regression: probing from src/ (or any subdir) must still find the
    checkout venvs instead of reporting models missing."""
    repo = tmp_path / "repo"
    sp = (
        repo
        / ".venv-wake"
        / "lib"
        / "python3.11"
        / "site-packages"
        / "openwakeword"
        / "resources"
        / "models"
    )
    sp.mkdir(parents=True)
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "agent.py").write_text("# fake")
    (repo / "pyproject.toml").write_text("# fake")
    monkeypatch.setenv("JARVIS_REPO", str(repo))
    monkeypatch.chdir(repo / "src")
    assert models.oww_resource_dir() == sp


def test_voice_dir_honors_jarvis_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert models.voice_dir() == tmp_path / "voices"


# ------------------------------------------------------------------ steps


def test_repo_root_prefers_env_and_walks_up(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "agent.py").write_text("# fake")
    (repo / "pyproject.toml").write_text("# fake")
    sub = repo / "src"
    monkeypatch.setenv("JARVIS_REPO", str(repo))
    assert steps.repo_root() == repo
    monkeypatch.delenv("JARVIS_REPO")
    monkeypatch.chdir(sub)
    assert steps.repo_root() == repo


# ------------------------------------------------------------------ gate


def test_hf_cache_summary_marks_used(monkeypatch, tmp_path) -> None:
    used = tmp_path / "models--Cactus-Compute--needle2" / "snapshots" / "x"
    unused = tmp_path / "models--Systran--faster-whisper-base" / "snapshots" / "y"
    used.mkdir(parents=True)
    unused.mkdir(parents=True)
    (used / "m.bin").write_bytes(b"a" * 1024)
    (unused / "m.bin").write_bytes(b"b" * 2048)

    monkeypatch.setattr(gate, "HF_HUB", tmp_path)
    monkeypatch.delenv("JARVIS_WHISPER_MODEL", raising=False)
    data = gate.hf_cache_summary()
    assert data["total_bytes"] == 3072
    assert data["wasted_bytes"] == 0
    by_name = {m["name"]: m for m in data["models"]}
    assert by_name["models--Cactus-Compute--needle2"]["used"] is True
    # Default whisper size (base) is protected: prune-hf must never delete
    # the direct pipeline's ears (regression: stale audit marked it unused).
    assert by_name["models--Systran--faster-whisper-base"]["used"] is True


def test_hf_audit_follows_whisper_model_env(monkeypatch, tmp_path) -> None:
    tiny = tmp_path / "models--Systran--faster-whisper-tiny"
    base = tmp_path / "models--Systran--faster-whisper-base"
    tiny.mkdir(parents=True)
    base.mkdir(parents=True)
    monkeypatch.setattr(gate, "HF_HUB", tmp_path)
    monkeypatch.setenv("JARVIS_WHISPER_MODEL", "tiny")
    data = gate.hf_cache_summary()
    by_name = {m["name"]: m for m in data["models"]}
    assert by_name["models--Systran--faster-whisper-tiny"]["used"] is True
    assert by_name["models--Systran--faster-whisper-base"]["used"] is False


def test_prune_unused_removes_only_unreferenced(monkeypatch, tmp_path) -> None:
    used = tmp_path / "models--Cactus-Compute--needle2"
    unused = tmp_path / "models--Systran--faster-whisper-tiny"
    used.mkdir(parents=True)
    unused.mkdir(parents=True)
    monkeypatch.setattr(gate, "HF_HUB", tmp_path)
    monkeypatch.delenv("JARVIS_WHISPER_MODEL", raising=False)
    removed = gate.prune_unused()
    assert removed == ["models--Systran--faster-whisper-tiny"]
    assert used.exists()
    assert not unused.exists()


def test_probe_bridge_fails_closed() -> None:
    ok, info = gate.probe_bridge(port=1, timeout=0.2)
    assert ok is False
    assert "error" in info or info == {}


def test_bridge_url_honors_bind_env(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_BRIDGE_BIND", raising=False)
    assert gate.bridge_url() == "http://127.0.0.1:4317/health"
    monkeypatch.setenv("JARVIS_BRIDGE_BIND", "100.77.6.93")
    assert gate.bridge_url() == "http://100.77.6.93:4317/health"
