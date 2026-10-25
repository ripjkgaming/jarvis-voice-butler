"""Wizard knows about the chess extras: OpenRouter key and Stockfish."""

from wizard import deps, keys


def test_openrouter_key_is_optional_and_goes_to_keys_env():
    assert "OPENROUTER_API_KEY" in keys.OPTIONAL_KEYS
    assert "OPENROUTER_API_KEY" not in keys.REQUIRED_KEYS
    k = keys.CollectedKeys(google="g", openrouter=" sk-or-abc ")
    assert k.osint_updates() == {"OPENROUTER_API_KEY": "sk-or-abc"}
    assert "OPENROUTER_API_KEY" not in k.worker_updates()  # never in .env.local


def test_key_file_is_0600_and_summary_sees_it(tmp_path, monkeypatch):
    import os
    import stat

    monkeypatch.setenv("JARVIS_KEYS_FILE", str(tmp_path / "keys.env"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    keys.write_keys(tmp_path, keys.CollectedKeys(google="g", openrouter="sk-or-test"))
    mode = stat.S_IMODE(os.stat(tmp_path / "keys.env").st_mode)
    assert mode == 0o600
    assert keys.keys_summary(tmp_path)["openrouter"] is True
    assert keys.keys_summary(tmp_path)["google"] is True


def test_stockfish_is_a_checked_optional_tool():
    t = deps.tool_by_name("stockfish")
    assert t is not None and not t.required
    assert t.dnf_packages == ("stockfish",)
    assert t.install_command() == ["sudo", "dnf", "install", "-y", "stockfish"]
    assert deps.check_summary(which=lambda b: None)["stockfish"]["installed"] is False
    assert (
        deps.check_summary(which=lambda b: "/usr/bin/stockfish")["stockfish"][
            "installed"
        ]
        is True
    )


def test_commentary_finds_the_key_in_keys_env(tmp_path, monkeypatch):
    from games.chess import commentary

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "keys.env").write_text("OPENROUTER_API_KEY=sk-or-from-file\n")
    assert commentary.api_key() == "sk-or-from-file"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    assert commentary.api_key() == "sk-or-env"
