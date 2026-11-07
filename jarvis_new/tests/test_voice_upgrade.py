import json
import sys

import voice_upgrade as upgrade


def test_quota_requires_both_explicit_unlimited_and_correct_tier():
    html = """<h2>Free tier</h2><table><tr><th>Model</th><th>RPM</th>
    <th>TPM</th><th>RPD</th></tr>
    <tr><td>gemini-4.0-live</td><td>Unlimited</td><td>1000</td><td>Unlimited</td></tr>
    <tr><td>gemini-4.1-live</td><td>10</td><td>Unlimited</td><td>Unlimited</td></tr>
    <tr><td>gemini-4.2-live</td><td>-</td><td>Unlimited</td><td>Unlimited</td></tr>
    </table><h2>Tier 1</h2><table><tr><th>Model</th><th>RPM</th><th>RPD</th></tr>
    <tr><td>gemini-5.0-live</td><td>Unlimited</td><td>Unlimited</td></tr></table>"""
    assert upgrade.public_unlimited(html, "free") == {"gemini40live"}
    assert upgrade.public_unlimited(html, "tier1") == {"gemini50live"}
    assert upgrade.public_unlimited("<p>All models are unlimited</p>", "free") == set()


def test_candidates_must_be_newer_live_and_explicitly_unlimited():
    models = [
        {
            "name": "models/gemini-4.0-live",
            "supportedGenerationMethods": ["bidiGenerateContent"],
        },
        {
            "name": "models/gemini-5.0-flash",
            "supportedGenerationMethods": ["generateContent"],
        },
        {
            "name": "models/gemini-4.1-live",
            "supportedGenerationMethods": ["bidiGenerateContent"],
        },
    ]
    assert upgrade.candidates(models, ["gemini-3.8-live"], {"gemini40live"}) == [
        "gemini-4.0-live"
    ]
    assert upgrade.candidates(models, ["gemini-4.0-live"], {"gemini40live"}) == []


def test_selection_is_atomic_preserves_fallbacks_and_manual_override(
    tmp_path, monkeypatch
):
    path = tmp_path / "selected.json"
    monkeypatch.setenv("JARVIS_VOICE_SELECTION_FILE", str(path))
    monkeypatch.delenv("JARVIS_VOICE_MODELS", raising=False)
    upgrade.write_json(path, {"model": "gemini-4.0-live"})
    assert upgrade.selected_chain(["gemini-3.8-live"]) == [
        "gemini-4.0-live",
        "gemini-3.8-live",
    ]
    monkeypatch.setenv("JARVIS_VOICE_MODELS", "custom-live,backup-live")
    assert upgrade.selected_chain(["gemini-3.8-live"]) == ["custom-live", "backup-live"]
    monkeypatch.delenv("JARVIS_VOICE_MODELS")
    path.write_text("{broken")
    assert upgrade.selected_chain(["gemini-3.8-live"]) == ["gemini-3.8-live"]
    path.write_text(json.dumps({"model": "gemini-2.5-flash-native-audio-latest"}))
    assert upgrade.selected_chain(["gemini-3.8-live"]) == ["gemini-3.8-live"]


def test_failed_probe_does_not_change_selection(tmp_path):
    path = tmp_path / "selection.json"
    upgrade.write_json(path, {"model": "gemini-3.8-live"})
    before = path.read_bytes()
    assert (
        upgrade.promote(["gemini-4.0-live"], ["gemini-3.8-live"], path, lambda _: False)
        is None
    )
    assert path.read_bytes() == before


def test_promotion_retains_previous_selection(tmp_path):
    path = tmp_path / "selection.json"
    assert (
        upgrade.promote(["gemini-4.0-live"], ["gemini-3.8-live"], path, lambda _: True)
        == "gemini-4.0-live"
    )
    assert json.loads(path.read_text())["previous_chain"] == ["gemini-3.8-live"]


def test_live_check_skips_when_quotas_are_not_published(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_VOICE_SELECTION_FILE", str(tmp_path / "selection.json"))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("JARVIS_VOICE_MODELS", raising=False)
    monkeypatch.setattr(sys, "argv", ["voice_upgrade"])
    monkeypatch.setattr(upgrade, "fetch", lambda _: "<p>See AI Studio</p>")
    monkeypatch.setattr(upgrade, "list_models", lambda _: [])
    assert upgrade.main() == 0
    assert (
        json.loads((tmp_path / "last-check.json").read_text())["status"]
        == "quota_unverified"
    )
    assert not (tmp_path / "selection.json").exists()


def test_network_failure_preserves_selection_and_redacts_error(tmp_path, monkeypatch):
    path = tmp_path / "selection.json"
    upgrade.write_json(path, {"model": "gemini-4.0-live"})
    before = path.read_bytes()
    monkeypatch.setenv("JARVIS_VOICE_SELECTION_FILE", str(path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("JARVIS_VOICE_MODELS", raising=False)
    monkeypatch.setattr(sys, "argv", ["voice_upgrade"])

    def fail(_):
        raise RuntimeError("secret-key-in-provider-message")

    monkeypatch.setattr(upgrade, "fetch", fail)
    assert upgrade.main() == 1
    assert path.read_bytes() == before
    assert "secret-key" not in (tmp_path / "last-check.json").read_text()


def test_agent_reads_selection_between_sessions(tmp_path, monkeypatch):
    import agent

    path = tmp_path / "selection.json"
    monkeypatch.setenv("JARVIS_VOICE_SELECTION_FILE", str(path))
    monkeypatch.delenv("JARVIS_VOICE_MODELS", raising=False)
    assert agent._voice_chain() == agent.VOICE_MODEL_CHAIN
    upgrade.write_json(path, {"model": "gemini-4.0-live"})
    assert agent._voice_chain() == ["gemini-4.0-live", *agent.VOICE_MODEL_CHAIN]
