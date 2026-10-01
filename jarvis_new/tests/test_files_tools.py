"""FilesTools: search backends, open safety, describe, confirm-gated organise."""

import json
import types

import pytest
from livekit.agents.llm import ToolError

import second_brain
from system import files_tools
from system.files_tools import (
    FilesTools,
    build_organize_plan,
    looks_like_content,
    plan_key,
    rank_results,
    resolve_safe,
    shorten,
)


@pytest.fixture()
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / ".jarvis"))
    monkeypatch.setenv("JARVIS_FILES_ROOTS", str(tmp_path))
    # Hermetic: the real plocate db knows nothing of tmp trees.
    monkeypatch.setattr(files_tools, "_locate_db_fresh", lambda *a, **k: False)
    return tmp_path


def _ctx():
    return types.SimpleNamespace(session=None)


def _tree(tmp_path):
    base = tmp_path / "docs"
    (base / "sub").mkdir(parents=True)
    (base / "invoice-march.pdf").write_text("total 42")
    (base / "deploy.sh").write_text("#!/bin/sh\necho hi")
    (base / "photo.jpg").write_text("IMG")
    (base / "sub" / "deep.txt").write_text("quarterly revenue numbers")
    (base / ".secret").mkdir()
    (base / ".secret" / "h.txt").write_text("x")
    return base


def _write_config(tmp_path, *roots):
    cfg = {
        "roots": [str(r) for r in roots],
        "excludes": list(second_brain.DEFAULT_EXCLUDES),
        "exclude_hidden": True,
        "max_depth": 4,
        "max_nodes": 20000,
    }
    d = tmp_path / ".jarvis" / "brain"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg))


def test_content_query_detection():
    assert (
        looks_like_content("files containing quarterly revenue") == "quarterly revenue"
    )
    assert looks_like_content("find docs that contain hello") == "hello"
    assert looks_like_content("invoice march") is None


def test_rank_results_basename_first():
    paths = ["/a/b/deep/invoice.pdf", "/a/invoice-march.pdf"]
    assert rank_results("invoice-march", paths)[0] == "/a/invoice-march.pdf"


def test_shorten():
    assert shorten("/home/u/a.txt", "/home/u") == "~/a.txt"
    assert shorten("/etc/x", "/home/u") == "/etc/x"


def test_path_safety(local):
    assert resolve_safe("/etc/passwd") is None
    assert resolve_safe("/") is None
    assert resolve_safe(str(local / "docs")) is not None


async def test_find_files_via_brain(local):
    base = _tree(local)
    _write_config(local, base)
    second_brain.build(incremental=False)
    tools = FilesTools()
    out = await FilesTools.find_files(tools, _ctx(), query="invoice-march")
    assert "invoice-march.pdf" in out["say"]
    assert tools._last_results and tools._last_results[0].endswith("invoice-march.pdf")


async def test_find_files_nothing(local):
    _write_config(local, local)
    out = await FilesTools.find_files(FilesTools(), _ctx(), query="zzz-no-such")
    assert "Nothing" in out["say"]


async def test_find_files_kind_filter(local):
    base = _tree(local)
    _write_config(local, base)
    second_brain.build(incremental=False)
    out = await FilesTools.find_files(FilesTools(), _ctx(), query="sub", kind="folder")
    assert out.get("paths", "").endswith("sub")


async def test_open_path_refuses_unsafe(local):
    with pytest.raises(ToolError):
        await FilesTools.open_path(
            FilesTools(), _ctx(), path_or_result_index="/etc/passwd"
        )


async def test_open_path_result_index(local, monkeypatch):
    base = _tree(local)
    f = base / "invoice-march.pdf"
    calls = []

    async def fake(*argv, **kwargs):
        calls.append(argv)
        return 0, "", ""

    monkeypatch.setattr(files_tools, "run_cmd", fake)
    tools = FilesTools()
    tools._last_results = [str(f)]
    out = await FilesTools.open_path(tools, _ctx(), path_or_result_index="1")
    assert "Opening" in out["say"]
    assert calls[0][:1] == ("xdg-open",)


async def test_open_folder_uses_dolphin(local, monkeypatch):
    base = _tree(local)
    calls = []

    async def fake(*argv, **kwargs):
        calls.append(argv)
        return 0, "", ""

    monkeypatch.setattr(files_tools, "run_cmd", fake)
    out = await FilesTools.open_path(
        FilesTools(), _ctx(), path_or_result_index=str(base)
    )
    assert "Showing" in out["say"]
    assert calls[0][0] == "dolphin"


async def test_describe_folder(local):
    base = _tree(local)
    _write_config(local, base)
    second_brain.build(incremental=False)
    out = await FilesTools.describe_folder(FilesTools(), _ctx(), path_or_name=str(base))
    assert "invoice-march" in out["say"] or "docs" in out["say"].lower()
    assert "Tools inside" in out["say"]  # deploy.sh spotted


async def test_describe_folder_by_name(local):
    base = _tree(local)
    _write_config(local, base)
    second_brain.build(incremental=False)
    out = await FilesTools.describe_folder(FilesTools(), _ctx(), path_or_name="docs")
    assert out["path"] == str(base)


def test_build_organize_plan_pure():
    plan = build_organize_plan(
        ["a.py", "notes.md", "pic.jpg", ".hidden", "a.py"],
        {"pic.jpg": 1_700_000_000},
    )
    dsts = [m["dst"] for m in plan["moves"]]
    assert "Code/a.py" in dsts
    assert "Documents/notes.md" in dsts
    assert any(d.startswith("Media/") and d.endswith("pic.jpg") for d in dsts)
    assert not any("hidden" in d for d in dsts)
    assert len(set(dsts)) == len(dsts)  # clash got a suffix
    assert plan_key(plan) == plan_key(dict(plan))


async def test_organize_confirm_gate(local):
    base = _tree(local)
    tools = FilesTools()
    preview = await FilesTools.organize_folder(
        tools, _ctx(), path=str(base), dry_run=True
    )
    assert "confirm organize" in preview["say"]
    # Apply without confirm: refused.
    with pytest.raises(ToolError):
        await FilesTools.apply_organize_plan(tools, _ctx())
    assert (base / "invoice-march.pdf").exists()  # untouched
    await FilesTools.confirm_organize(tools, _ctx())
    out = await FilesTools.apply_organize_plan(tools, _ctx())
    assert "Filed" in out["say"]
    assert not (base / "invoice-march.pdf").exists()
    assert (base / "Finance" / "invoice-march.pdf").exists()
    # Confirm is single-use: second apply refused.
    with pytest.raises(ToolError):
        await FilesTools.apply_organize_plan(tools, _ctx())


async def test_organize_never_touches_hidden_or_git(local):
    base = local / "mess"
    base.mkdir()
    (base / ".git").mkdir()
    (base / ".git" / "HEAD").write_text("ref")
    (base / ".sneaky").write_text("x")
    (base / "loose.txt").write_text("x")
    tools = FilesTools()
    await FilesTools.organize_folder(tools, _ctx(), path=str(base), dry_run=True)
    await FilesTools.confirm_organize(tools, _ctx())
    await FilesTools.apply_organize_plan(tools, _ctx())
    assert (base / ".sneaky").exists()
    assert (base / ".git" / "HEAD").exists()
    assert (base / "Documents" / "loose.txt").exists()


async def test_undo_last_organize(local):
    base = _tree(local)
    tools = FilesTools()
    await FilesTools.organize_folder(tools, _ctx(), path=str(base), dry_run=True)
    await FilesTools.confirm_organize(tools, _ctx())
    await FilesTools.apply_organize_plan(tools, _ctx())
    out = await FilesTools.undo_last_organize(tools, _ctx())
    assert "back" in out["say"]
    assert (base / "invoice-march.pdf").exists()
    with pytest.raises(ToolError):
        await FilesTools.undo_last_organize(tools, _ctx())  # log consumed


async def test_organize_refuses_unsafe(local):
    with pytest.raises(ToolError):
        await FilesTools.organize_folder(
            FilesTools(), _ctx(), path="/etc", dry_run=True
        )
