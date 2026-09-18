"""Tests for the redacted diagnostics bundle (stdlib only, no secrets out)."""

import json
import sys
import tarfile

sys.path.insert(0, "src")

import diagnostics

CANARY_A = "sk-canary-SECRET-alpha-987654321"
CANARY_B = "tok-canary-TOKEN-beta-123456789"


def _plant(monkeypatch, tmp_path):
    """Fake home with canary-laced logs + env secrets. Returns home."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("LIVEKIT_API_SECRET", CANARY_A)
    monkeypatch.setenv("GOOGLE_API_KEY", CANARY_B)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "agent.log").write_text(
        f"worker up\napi_secret={CANARY_A}\n" + "chatty\n" * 500
    )
    (logs / "bridge.log").write_text("bridge up\n")
    return tmp_path


def test_scrub_replaces_env_secret_values(monkeypatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_SECRET", CANARY_A)
    out = diagnostics.scrub(f"leak: {CANARY_A} end")
    assert CANARY_A not in out
    assert diagnostics.REDACTED in out


def test_scrub_redacts_key_value_pairs() -> None:
    text = (
        f"LIVEKIT_API_SECRET={CANARY_A}\n"
        '{"token": "tok-abcdef-1234567890"}\n'
        '"livekit_configured": true\n'
        '"count": 5\n'
        "short=abc\n"
    )
    out = diagnostics.scrub(text, secrets=[])
    assert CANARY_A not in out
    assert "tok-abcdef-1234567890" not in out
    # Presence flags, numbers, and short values are not secrets: untouched.
    assert '"livekit_configured": true' in out
    assert '"count": 5' in out
    assert "short=abc" in out


def test_scrub_redacts_pem_blocks() -> None:
    text = "key:\n-----BEGIN EC PRIVATE KEY-----\nMIIBm aidedata\n-----END EC PRIVATE KEY-----\n"
    out = diagnostics.scrub(text, secrets=[])
    assert "aidedata" not in out
    assert "BEGIN EC PRIVATE KEY" in out  # markers stay, material goes


def test_collect_secret_values_ignores_short_and_plain(monkeypatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_SECRET", "tiny")
    monkeypatch.setenv("JARVIS_PIPELINE", "realtime")
    vals = diagnostics.collect_secret_values()
    assert "tiny" not in vals
    assert "realtime" not in vals


def test_tail_lines_returns_last_n(tmp_path) -> None:
    log = tmp_path / "x.log"
    log.write_text("\n".join(f"line {i}" for i in range(500)))
    tail = diagnostics.tail_lines(log, n=200)
    assert len(tail) == 200
    assert tail[0] == "line 300" and tail[-1] == "line 499"
    assert diagnostics.tail_lines(tmp_path / "missing.log") == []


def test_write_bundle_contains_zero_secret_values(monkeypatch, tmp_path) -> None:
    home = _plant(monkeypatch, tmp_path)
    monkeypatch.setattr(
        diagnostics,
        "bridge_get",
        lambda path, **kw: (
            {"ok": True, "echo": CANARY_B} if path == "/status" else {"ok": True}
        ),
    )
    monkeypatch.setattr(
        diagnostics,
        "run_wizard_check",
        lambda **kw: {"rc": 0, "output": f"[green] keys {CANARY_A} present"},
    )
    monkeypatch.setattr(
        diagnostics, "collect_versions", lambda: {"shell": "0.1.0", "note": CANARY_B}
    )

    bundle = diagnostics.write_bundle(out_dir=home / "bundles")
    assert bundle.suffixes[-2:] == [".tar", ".gz"]

    with tarfile.open(bundle, "r:gz") as tf:
        names = tf.getnames()
        assert any(n.endswith("diagnostics.json") for n in names)
        assert any(n.endswith("logs/agent.log") for n in names)
        members = {n: tf.extractfile(n).read().decode() for n in names}
    text = "".join(members.values())
    assert CANARY_A not in text and CANARY_B not in text
    assert diagnostics.REDACTED in text
    # Tails are bounded per member: the 500 chatty lines ship as 200 in
    # diagnostics.json plus 200 in logs/agent.log — never the full 500.
    for name, body in members.items():
        assert body.count("chatty") <= 200, name


def test_bundle_diagnostics_json_stays_valid(monkeypatch, tmp_path) -> None:
    _plant(monkeypatch, tmp_path)
    monkeypatch.setattr(diagnostics, "bridge_get", lambda path, **kw: None)
    monkeypatch.setattr(
        diagnostics, "run_wizard_check", lambda **kw: {"rc": 1, "output": "skipped"}
    )
    monkeypatch.setattr(diagnostics, "collect_versions", lambda: {})
    bundle = diagnostics.write_bundle(out_dir=tmp_path / "b")
    with tarfile.open(bundle, "r:gz") as tf:
        name = next(n for n in tf.getnames() if n.endswith("diagnostics.json"))
        raw = tf.extractfile(name).read().decode()
    parsed = json.loads(raw)  # scrubbing must not corrupt JSON
    assert set(parsed) >= {"bridge", "logs", "wizard_check", "versions"}
    assert len(parsed["logs"]["agent"]) <= 200


def test_main_prints_only_the_tarball_path(monkeypatch, tmp_path, capsys) -> None:
    _plant(monkeypatch, tmp_path)
    monkeypatch.setattr(diagnostics, "bridge_get", lambda path, **kw: None)
    monkeypatch.setattr(
        diagnostics, "run_wizard_check", lambda **kw: {"rc": 1, "output": "skipped"}
    )
    monkeypatch.setattr(diagnostics, "collect_versions", lambda: {})
    rc = diagnostics.main(["--out", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].endswith(".tar.gz")
