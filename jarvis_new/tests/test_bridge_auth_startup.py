import sys
from pathlib import Path

import pytest

sys.path.insert(0, "src")
import bridge


def test_existing_file_token(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "bridge_token").write_text("existing-test-token\n")
    assert bridge.resolve_token() == "existing-test-token"


@pytest.mark.parametrize("content", [None, "", "  ", "invalid token"])
def test_startup_fails_closed(monkeypatch, tmp_path, content):
    monkeypatch.delenv("JARVIS_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    if content is not None:
        (tmp_path / "bridge_token").write_text(content)
    monkeypatch.setattr(
        bridge, "serve_forever", lambda **kw: pytest.fail("must not bind")
    )
    with pytest.raises(SystemExit) as error:
        bridge.main()
    assert error.value.code == 2


def test_environment_token(monkeypatch):
    monkeypatch.setenv("JARVIS_BRIDGE_TOKEN", "existing-environment-token")
    assert bridge.resolve_token() == "existing-environment-token"
