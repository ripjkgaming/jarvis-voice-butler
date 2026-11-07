"""scripts/phone_pair.py: code storage, --list, --revoke."""

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "src")

import phone_device

_SPEC = importlib.util.spec_from_file_location(
    "phone_pair", Path(__file__).resolve().parent.parent / "scripts" / "phone_pair.py"
)
phone_pair = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(phone_pair)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_PHONE_ORIGIN", "https://host.example.ts.net")
    return tmp_path


def test_prints_qr_code_and_url_and_stores_hash(home):
    out = io.StringIO()
    assert phone_pair.main([], out=out) == 0
    text = out.getvalue()
    line = next(row for row in text.splitlines() if "url" in row)
    url = line.split()[-1]
    assert url.startswith("https://host.example.ts.net/#pair=")
    code = url.split("=", 1)[1]
    assert len(code) == 10
    stored = json.loads((home / "phone" / "pair.json").read_text())
    assert hashlib.sha256(code.encode()).hexdigest() in stored
    assert code not in (home / "phone" / "pair.json").read_text()
    assert "█" in text or "▀" in text or "▄" in text  # QR blocks


def test_fourth_pending_code_refused(home):
    for _ in range(3):
        assert phone_pair.main(["--no-qr"], out=io.StringIO()) == 0
    assert phone_pair.main(["--no-qr"], out=io.StringIO()) == 1


def test_list_and_revoke(home):
    store = phone_device.DeviceStore()
    device, _ = store.create("Pixel 7")
    out = io.StringIO()
    phone_pair.main(["--list"], out=out)
    assert device["id"] in out.getvalue() and "Pixel 7" in out.getvalue()
    assert phone_pair.main(["--revoke", device["id"]], out=io.StringIO()) == 0
    assert store.list() == []
    assert phone_pair.main(["--revoke", device["id"]], out=io.StringIO()) == 1
