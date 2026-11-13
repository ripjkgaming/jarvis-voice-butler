import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


@pytest.fixture(autouse=True)
def calls_env(tmp_path, monkeypatch):
    from calls import store

    monkeypatch.setenv("JARVIS_CALLS_DIR", str(tmp_path / "calls"))
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    store.close_all()
    yield tmp_path
    store.close_all()
