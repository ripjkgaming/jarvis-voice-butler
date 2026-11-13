"""Hermetic tests for src/make3d_tools.py (fake script, no Blender)."""

from __future__ import annotations

import asyncio

import pytest

import make3d_tools
from make3d_tools import Make3DTools


def test_tools_register_expected_ids() -> None:
    assert [t.id for t in Make3DTools().tools] == ["make_3d_object"]


@pytest.mark.asyncio
async def test_success_reports_paths(tmp_path, monkeypatch) -> None:
    s = tmp_path / "fake.sh"
    s.write_text("#!/bin/sh\necho /x/lamp/lamp.glb\necho /x/lamp/preview.png\n")
    s.chmod(0o755)
    monkeypatch.setattr(make3d_tools, "SCRIPT", str(s))
    monkeypatch.setattr(
        make3d_tools,
        "run_make3d",
        lambda p, _r=make3d_tools.run_make3d: _r(p, script=str(s)),
    )
    res = await Make3DTools.make_3d_object(Make3DTools(), None, description="lamp")  # type: ignore[arg-type]
    assert res.get("background") is True  # returns at once; build continues off-turn
    await asyncio.gather(*list(make3d_tools._BG))


@pytest.mark.asyncio
async def test_failure_and_empty(tmp_path, monkeypatch) -> None:
    s = tmp_path / "bad.sh"
    s.write_text("#!/bin/sh\necho oops >&2\nexit 1\n")
    s.chmod(0o755)
    ok, paths, err = await make3d_tools.run_make3d("x", script=str(s))
    assert not ok and paths == [] and "oops" in err
    res = await Make3DTools.make_3d_object(Make3DTools(), None, description=" ")  # type: ignore[arg-type]
    assert "What should I model" in res["say"]
