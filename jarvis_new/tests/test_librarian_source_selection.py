import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_ungranted_find_requests_source_without_grant(monkeypatch):
    path = Path(__file__).parents[1] / "src/system/librarian_tools.py"
    spec = importlib.util.spec_from_file_location("staged_librarian_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service = SimpleNamespace(find=lambda *a, **k: None)
    tools = module.LibrarianTools(service)

    async def call(*a, **k):
        return {
            "status": "needs_approval",
            "say": "Approve a specific source before document discovery.",
            "evidence": [],
        }

    monkeypatch.setattr(tools, "_call", call)
    out = await tools.librarian_find("physics")
    assert out["status"] == "needs_approval"
    assert out["source_selection_required"] is True
    assert out["source_choices"] == ["local_folder", "drive_folder"]
    assert out["granted"] is False
    assert "?" in out["say"]
    assert "separate" in out["say"]
    assert out["evidence"] == []


@pytest.mark.asyncio
async def test_real_ungranted_service_never_discovers_or_approves(monkeypatch):
    import librarian
    import phone_session
    import system

    path = Path(__file__).parents[1] / "src/system/librarian_tools.py"
    spec = importlib.util.spec_from_file_location("staged_librarian_real", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(system, "require_local", lambda: None)
    monkeypatch.setattr(phone_session, "private", lambda: False)
    monkeypatch.setattr(phone_session, "guest_required", lambda: False)
    monkeypatch.setattr(phone_session, "approval_ticket", lambda: None)
    monkeypatch.setattr(phone_session, "check_approval_effect", lambda *a, **k: None)

    def forbidden(*a, **k):
        raise AssertionError("No source operation without grants")

    service = librarian.Librarian(
        adapters={"local": SimpleNamespace(discover=forbidden)}
    )
    monkeypatch.setattr(service, "approve", forbidden)
    try:
        out = await module.LibrarianTools(service).librarian_find("physics")
        assert out["status"] == "needs_approval" and out["granted"] is False
        assert out["evidence"] == [] and service._grants == {}
        assert out["source_selection_required"] is True
    finally:
        service.close()
