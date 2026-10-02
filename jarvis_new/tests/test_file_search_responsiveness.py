"""A slow private index must not stall voice control or publish after cancel."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

import second_brain
from system.files_tools import FilesTools


@pytest.mark.parametrize("folder", [False, True])
async def test_index_lookup_keeps_loop_responsive_and_cancels(
    tmp_path, monkeypatch, folder
):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_FILES_ROOTS", str(tmp_path))
    entered = threading.Event()
    release = threading.Event()
    exited = threading.Event()
    target = tmp_path / "notes"
    target.mkdir()

    def blocked_search(*args):
        entered.set()
        try:
            release.wait(1.0)  # bounded even against the old blocking code
            return [{"path": str(target), "type": "folder"}]
        finally:
            exited.set()

    async def empty_locate(*args):
        return []

    monkeypatch.setattr(second_brain, "search", blocked_search)
    monkeypatch.setattr(FilesTools, "_via_locate", empty_locate)
    tool = FilesTools()
    tool._last_results = ["earlier result"]
    operation = (
        tool._find_folder("notes")
        if folder
        else tool.find_files(SimpleNamespace(session=None), "notes")
    )
    task = asyncio.create_task(operation)
    try:
        assert await asyncio.to_thread(entered.wait, 2.0)
        assert not task.done(), "index lookup blocked voice control until completion"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(exited.wait, 2.0)
    assert tool._last_results == ["earlier result"]
