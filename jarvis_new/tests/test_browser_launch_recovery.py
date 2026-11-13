import asyncio
from pathlib import Path

import pytest

import browser as bm
from browser import BrowserError, BrowserManager


def _proc(root: Path, pid: int, *args: str) -> None:
    d = root / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")


def test_find_profile_pids_exact_match(tmp_path: Path) -> None:
    prof = tmp_path / "brave-research-profile"
    _proc(tmp_path, 111, "brave", f"--user-data-dir={prof}", "--headless")
    _proc(tmp_path, 222, "brave", f"--user-data-dir={prof}-other")
    _proc(tmp_path, 333, "brave")  # user's normal browser
    (tmp_path / "self").mkdir()
    assert bm._find_profile_pids(prof, proc_root=str(tmp_path)) == [111]


def test_clear_removes_locks_and_kills_only_matches(
    tmp_path: Path, monkeypatch
) -> None:
    prof = tmp_path / "p"
    prof.mkdir()
    (prof / "SingletonLock").write_text("x")
    monkeypatch.setattr(bm, "_find_profile_pids", lambda _p: [4242])
    killed_pids: list[int] = []
    monkeypatch.setattr(bm.os, "kill", lambda pid, sig: killed_pids.append(pid))
    monkeypatch.setattr(bm.time, "sleep", lambda _s: None)
    assert bm._clear_stale_profile(prof) == [4242]
    assert killed_pids == [4242]
    assert not (prof / "SingletonLock").exists()


class _FakeChromium:
    def __init__(self, behaviours):
        self.behaviours = list(behaviours)
        self.calls = 0

    async def launch_persistent_context(self, **_kw):
        self.calls += 1
        b = self.behaviours.pop(0)
        if isinstance(b, BaseException):
            raise b
        if b == "hang":
            await asyncio.sleep(60)
        return b


class _FakePW:
    def __init__(self, chromium):
        self.chromium = chromium

    async def stop(self):
        pass


def _patch(monkeypatch, chromium):
    class _Starter:
        async def start(self):
            return _FakePW(chromium)

    monkeypatch.setattr(bm, "async_playwright", lambda: _Starter())
    cleared: list[int] = []
    monkeypatch.setattr(
        bm, "_clear_stale_profile", lambda *_a, **_k: cleared.append(1) or []
    )
    return cleared


def test_launch_failure_retries_then_clean_error(tmp_path: Path, monkeypatch) -> None:
    chromium = _FakeChromium([RuntimeError("Target closed")] * 2)
    cleared = _patch(monkeypatch, chromium)
    mgr = BrowserManager(headless=True, profile_dir=tmp_path / "p", browser_bin=None)
    with pytest.raises(BrowserError):
        asyncio.run(mgr.start())
    assert chromium.calls == 2
    assert len(cleared) == 2  # pre-launch + before retry
    assert mgr._playwright is None


def test_launch_timeout_is_bounded(tmp_path: Path, monkeypatch) -> None:
    chromium = _FakeChromium(["hang", "hang"])
    _patch(monkeypatch, chromium)
    monkeypatch.setattr(bm, "LAUNCH_TIMEOUT_S", 0.05)
    mgr = BrowserManager(headless=True, profile_dir=tmp_path / "p", browser_bin=None)
    with pytest.raises(BrowserError):
        asyncio.run(asyncio.wait_for(mgr.start(), 5))
    assert chromium.calls == 2
