"""Website conversion never claims transfer until a real Download event."""

import asyncio
from pathlib import Path

import pytest

import youtube_download as yt


def test_mp4_choice_prefers_reasonable_combined_quality_not_ads_or_mp3():
    rows = [
        {"index": 0, "text": "2160p mp4 Download"},
        {"index": 1, "text": "1080p mp4 video only Download"},
        {"index": 2, "text": "720p mp4 Download"},
        {"index": 3, "text": "128kbps mp3 Download"},
        {"index": 4, "text": "Download Free Now"},
    ]
    assert yt.choose_mp4_row(rows) == 2
    assert (
        yt.choose_mp4_row(
            [
                {"index": 0, "text": "1080p mp4 Download"},
                {"index": 1, "text": "720p mp4 Download"},
            ]
        )
        == 0
    )
    assert yt.choose_mp4_row([{"index": 0, "text": "mp3 Download"}]) is None


def test_filename_cannot_escape_downloads_or_overwrite_existing_file(tmp_path):
    (tmp_path / "film.mp4").write_bytes(b"existing")
    target = yt.reserve_target(tmp_path, "../../film.mp4", "fallback")
    assert target.parent == tmp_path
    assert target.name == "film (1).mp4"
    assert (tmp_path / "film.mp4").read_bytes() == b"existing"
    with pytest.raises(yt.DownloadError, match="MP4"):
        yt.reserve_target(tmp_path, "setup.exe", "fallback")


def test_unicode_filename_fits_filesystem_byte_limit(tmp_path):
    path = yt.reserve_target(tmp_path, "视频" * 150 + ".mp4", "fallback")
    assert len(path.name.encode()) < 220
    assert yt._reservation(path).exists()


class FakeDownload:
    suggested_filename = "film.mp4"

    def __init__(
        self, failure=None, body=b"\x00\x00\x00\x18ftypmp42synthetic-test-video"
    ):
        self.error = failure
        self.body = body
        self.save_started = asyncio.Event()
        self.finish = asyncio.Event()

    async def save_as(self, path):
        self.save_started.set()
        await self.finish.wait()
        Path(path).write_bytes(self.body)

    async def failure(self):
        return self.error


@pytest.mark.asyncio
async def test_start_notifies_before_completion_and_partial_file_is_saved_atomically(
    tmp_path,
):
    download = FakeDownload()
    seen = []

    async def started(path):
        seen.append(path)

    task = asyncio.create_task(yt.save_download(download, tmp_path, "Film", started))
    await download.save_started.wait()
    assert len(seen) == 1
    assert not seen[0].exists()
    assert not task.done()
    download.finish.set()
    target = await task
    assert target == seen[0]
    assert target.read_bytes() == download.body
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,body",
    [
        ("net::ERR_FAILED", b"\x00\x00\x00\x18ftypmp42"),
        (None, b"<html>verify you are human</html>"),
    ],
)
async def test_failed_or_html_download_is_not_reported_as_complete(
    tmp_path, failure, body
):
    download = FakeDownload(failure, body)
    download.finish.set()
    with pytest.raises(yt.DownloadError):
        await yt.save_download(download, tmp_path, "Film", lambda p: None)
    assert not list(tmp_path.iterdir())


def test_verification_failure_is_honest_without_false_download_phase():
    assert (
        "verification"
        in yt.classify_site_error(
            "Verification is taking longer than expected.", {403}
        ).lower()
    )
    assert (
        yt.classify_site_error("Video unavailable", set())
        == "YT1s says this video is unavailable."
    )


def test_job_starts_as_preparing_dedupes_and_failure_does_not_emit_start(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    jobs = yt.DownloadJobs(tmp_path / "jobs")
    calls = []
    monkeypatch.setattr(jobs, "_spawn", lambda job: calls.append(job["id"]))
    resolved = {
        "url": "https://youtube.com/watch?v=aaaaaaaaaaa",
        "title": "Film",
        "source": "recent",
    }
    first = jobs.start(resolved)
    assert first["status"] == "preparing"
    assert "Downloading" not in first["say"]
    assert jobs.start(resolved)["id"] == first["id"]
    assert calls == [first["id"]]
    jobs._change(first["id"], status="failed", error="YT1s verification failed")
    assert jobs.get(first["id"])["status"] == "failed"
    assert jobs.get("../../secret") is None


@pytest.mark.asyncio
async def test_live_voice_watcher_announces_completion_only(monkeypatch):
    from types import SimpleNamespace

    tools = yt.YouTubeDownloadTools()
    monkeypatch.setattr(yt, "VOICE_POLL_S", 0)
    states = iter(
        [
            {"status": "preparing"},
            {"status": "downloading", "filename": "Film.mp4"},
            {"status": "done", "filename": "Film.mp4"},
        ]
    )
    monkeypatch.setattr(
        yt, "_bridge_call", lambda *a: {"ok": True, "job": next(states)}
    )
    lines = []

    class Session:
        async def say(self, line):
            lines.append(line)

    tools._report_start(SimpleNamespace(session=Session()), "youtube-test")
    await asyncio.gather(*tools._tasks)
    assert len(lines) == 1
    assert "saved" in lines[0].lower()


@pytest.mark.asyncio
async def test_repeated_tool_requests_watch_same_job_once(monkeypatch):
    from types import SimpleNamespace

    tools = yt.YouTubeDownloadTools()
    monkeypatch.setattr(yt, "VOICE_POLL_S", 0)
    calls = []
    monkeypatch.setattr(
        yt,
        "_bridge_call",
        lambda *a: (
            calls.append(a)
            or {"ok": True, "job": {"status": "done", "filename": "Film.mp4"}}
        ),
    )
    lines = []

    class Session:
        async def say(self, line):
            lines.append(line)

    context = SimpleNamespace(session=Session())
    tools._report_start(context, "youtube-test")
    tools._report_start(context, "youtube-test")
    await asyncio.gather(*tools._tasks)
    tools._report_start(context, "youtube-test")
    assert len(lines) == 1 and len(calls) == 1


def test_status_speech_never_reuses_stale_preparing_text():
    job = {
        "status": "downloading",
        "filename": "Film.mp4",
        "say": "I'm preparing this video",
    }
    assert "downloading" in yt.job_say(job).lower()
    job["status"] = "done"
    assert "saved" in yt.job_say(job).lower()


def test_bridge_notifications_are_desktop_only_and_phases_follow_real_transfer(
    monkeypatch, tmp_path
):
    import activity
    import notify

    jobs = yt.DownloadJobs(tmp_path / "jobs")
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(jobs, "_spawn", lambda job: None)
    job = jobs.start(
        {"url": "https://youtube.com/watch?v=aaaaaaaaaaa", "title": "Film"}
    )
    notifications = []
    monkeypatch.setattr(
        notify,
        "send",
        lambda text, **kwargs: notifications.append(
            (jobs.get(job["id"])["status"], text, kwargs)
        ),
    )
    monkeypatch.setattr(activity, "start", lambda *a, **k: job["id"])
    monkeypatch.setattr(activity, "update", lambda *a, **k: None)
    monkeypatch.setattr(activity, "finish", lambda *a, **k: None)

    async def download(url, directory, title, started, **options):
        assert jobs.get(job["id"])["status"] == "preparing"
        assert not notifications
        target = tmp_path / "Film.mp4"
        started(target)
        assert jobs.get(job["id"])["status"] == "downloading"
        target.write_bytes(b"synthetic")
        return target

    monkeypatch.setattr(yt, "download_video", download)
    jobs._run(job)
    assert [status for status, _, _ in notifications] == ["downloading", "done"]
    assert all(options["allow_speech"] is False for _, _, options in notifications)


def test_converter_failure_has_no_download_start_notification(monkeypatch, tmp_path):
    import activity
    import notify

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    jobs = yt.DownloadJobs(tmp_path / "jobs")
    monkeypatch.setattr(jobs, "_spawn", lambda job: None)
    job = jobs.start(
        {"url": "https://youtube.com/watch?v=aaaaaaaaaaa", "title": "Film"}
    )
    notifications = []
    monkeypatch.setattr(
        notify, "send", lambda text, **options: notifications.append(options)
    )
    monkeypatch.setattr(activity, "start", lambda *a, **k: job["id"])
    monkeypatch.setattr(activity, "finish", lambda *a, **k: None)

    async def blocked(*args, **options):
        raise yt.DownloadError("YT1s verification failed; download hasn't started")

    monkeypatch.setattr(yt, "download_video", blocked)
    jobs._run(job)
    assert jobs.get(job["id"])["status"] == "failed"
    assert [options["kind"] for options in notifications] == ["download-failed"]
    assert notifications[0]["allow_speech"] is False


@pytest.mark.asyncio
async def test_voice_tool_never_uses_browser_open_or_fake_success(monkeypatch):
    tools = yt.YouTubeDownloadTools()
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    seen = []
    monkeypatch.setattr(
        yt,
        "_bridge_call",
        lambda method, path, body=None: (
            seen.append((method, path, body))
            or {"ok": False, "error": "No recent YouTube video"}
        ),
    )
    with pytest.raises(Exception, match="No recent YouTube video"):
        await tools.download_youtube_video(None)
    assert seen == [("POST", "/youtube/download", {"url": ""})]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_background_download_holds_session_until_watcher_finishes(
    monkeypatch, cancel
):
    from types import SimpleNamespace

    from hud_voice_state import execution_pending

    tools = yt.YouTubeDownloadTools()

    class Session:
        async def say(self, line):
            await asyncio.sleep(0)

    session = Session()
    monkeypatch.setattr(yt, "VOICE_POLL_S", 0)
    monkeypatch.setattr(
        yt,
        "_bridge_call",
        lambda *args: {"job": {"status": "failed", "error": "download failed"}},
    )
    tools._report_start(SimpleNamespace(session=session), "youtube-lease-test")
    assert execution_pending(session)
    tasks = tuple(tools._tasks)
    if cancel:
        for task in tasks:
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert not execution_pending(session)
