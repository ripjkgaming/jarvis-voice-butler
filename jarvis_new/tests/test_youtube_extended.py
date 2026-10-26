from types import SimpleNamespace

import pytest

import youtube_direct as direct
import youtube_download as yt
import youtube_resolve as resolve

URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


@pytest.fixture(autouse=True)
def no_direct_network(monkeypatch):
    monkeypatch.setattr(
        resolve,
        "video_details",
        lambda _: {"title": "Fixture video", "channel": "Fixture channel"},
    )


def test_audio_options_and_quality(tmp_path):
    audio = direct.worker_options(
        tmp_path, lambda _: None, kind="audio", audio_format="mp3"
    )
    assert audio["postprocessors"][0]["preferredquality"] == "192"
    assert audio["postprocessors"][0]["preferredcodec"] == "mp3"
    assert audio["writethumbnail"]
    video = direct.worker_options(tmp_path, lambda _: None, quality=720)
    assert "height<=720" in video["format"]
    assert ">=" not in video["format"]


def test_directories_and_audio_reservation(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_YOUTUBE_AUDIO_DIR", str(tmp_path / "music"))
    assert yt.downloads_directory("audio") == tmp_path / "music"
    for ext in ("mp3", "m4a"):
        path = yt.reserve_target(tmp_path, "song." + ext, "song")
        assert path.suffix == "." + ext


def test_search_request_passes_choice_and_formats(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    entry = {
        "id": "jNQXAC9IVRw",
        "title": "The Kickflip | Roadster",
        "channel": "Tesla",
    }
    monkeypatch.setattr(
        resolve,
        "resolve_query",
        lambda r: resolve.Decision("confirm", [resolve.Scored(entry, 0.8)]),
    )
    result = yt.start_download("download Tesla Roadster kickflip as mp3")
    assert result["status"] == "confirm"
    assert "this one?" in result["say"]
    assert "Tesla" in result["say"]
    assert result["confirmation_id"]


def test_no_results_says_query(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(
        resolve, "resolve_query", lambda r: resolve.Decision("none", [])
    )
    with pytest.raises(yt.DownloadError, match="zxyqq"):
        yt.start_download("download zxyqq")


def test_audio_and_video_jobs_are_distinct(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    jobs = yt.DownloadJobs(tmp_path)
    monkeypatch.setattr(jobs, "_spawn", lambda _: None)
    v = jobs.start({"url": URL, "kind": "video"})
    a = jobs.start({"url": URL, "kind": "audio"})
    assert v["id"] != a["id"]
    assert a["kind"] == "audio"


@pytest.mark.asyncio
async def test_tool_handles_confirmation_without_watcher(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    tools = yt.YouTubeDownloadTools()
    job = {
        "status": "confirm",
        "say": "Film by Tesla, this one?",
        "confirmation_id": "token",
    }
    monkeypatch.setattr(yt, "_bridge_call", lambda *a: {"ok": True, "job": job})
    assert (
        await tools.download_youtube_video(
            SimpleNamespace(session=None), "download Film"
        )
    )["status"] == "confirm"
    assert not tools._tasks


def test_all_lowercase_video_id():
    assert resolve.parse_request("aaaaaaaaaaa").url.endswith("aaaaaaaaaaa")


def test_confirmation_accepts_selected_title_once(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    manager = yt.DownloadJobs(tmp_path)
    monkeypatch.setattr(manager, "_spawn", lambda _: None)
    monkeypatch.setattr(yt, "jobs", lambda: manager)
    item = {"id": "jNQXAC9IVRw", "title": "Chosen", "channel": "Channel"}
    monkeypatch.setattr(
        resolve,
        "resolve_query",
        lambda r: resolve.Decision("confirm", [resolve.Scored(item, 0.8)]),
    )
    choice = yt.start_download("download chosen as m4a")
    assert not list(tmp_path.glob("*.json"))
    job = yt.start_download(confirmation_id=choice["confirmation_id"], confirm=True)
    assert (job["title"], job["kind"], job["audio_format"]) == (
        "Chosen",
        "audio",
        "m4a",
    )
    with pytest.raises(yt.DownloadError, match="expired"):
        yt.start_download(confirmation_id=choice["confirmation_id"], confirm=True)


def test_playlist_only_when_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    manager = yt.DownloadJobs(tmp_path)
    monkeypatch.setattr(manager, "_spawn", lambda _: None)
    monkeypatch.setattr(yt, "jobs", lambda: manager)
    monkeypatch.setattr(
        resolve,
        "playlist_entries",
        lambda u: [
            {"id": "jNQXAC9IVRw", "title": "One"},
            {"id": "dQw4w9WgXcQ", "title": "Two"},
        ],
    )
    collection = yt.start_download(
        "download playlist https://youtube.com/playlist?list=PLabc"
    )
    assert len(collection["entries"]) == 2
    single = yt.start_download("download " + URL + "&list=PLabc")
    assert single["url"] == URL and not single["entries"]
    assert single["playlist"] is False


def test_mobile_download_returns_status_without_file_transfer(monkeypatch, tmp_path):
    import bridge
    import phone_session

    monkeypatch.setattr(phone_session, "guest_required", lambda: False)
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    manager = yt.DownloadJobs(tmp_path)
    monkeypatch.setattr(manager, "_spawn", lambda _: None)
    monkeypatch.setattr(yt, "jobs", lambda: manager)
    result = bridge.run_phone_tool("download_youtube_video", {"url": URL})
    assert result["ok"] and result["job"]["quiet"]
    status = bridge.run_phone_tool(
        "youtube_download_status", {"job_id": result["job"]["id"]}
    )
    assert status["job"]["status"] == "preparing"
    assert "bytes" not in status


def test_job_cancellation_waits_for_cleanup(monkeypatch, tmp_path):
    import asyncio
    import threading

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    manager = yt.DownloadJobs(tmp_path / "jobs")
    ready = threading.Event()
    partial = tmp_path / ".test.partial"

    async def slow(*a, **k):
        partial.write_text("partial")
        ready.set()
        try:
            await asyncio.sleep(60)
        finally:
            partial.unlink(missing_ok=True)

    monkeypatch.setattr(yt, "download_video", slow)
    job = manager.start({"url": URL, "quiet": True})
    assert ready.wait(5)
    result = manager.cancel(job["id"])
    assert result["status"] == "cancelled"
    assert not partial.exists()
    assert job["id"] not in manager._threads


@pytest.mark.asyncio
async def test_retry_once_for_extraction_but_not_private(monkeypatch, tmp_path):
    calls = []
    updates = []

    async def worker(*a, **k):
        calls.append(True)
        if len(calls) == 1:
            err = yt.DownloadError("extractor failed")
            err.code = "failed"
            raise err
        return tmp_path / "ok.mp4"

    async def update():
        updates.append(True)

    monkeypatch.setattr(direct, "download_video_direct", worker)
    monkeypatch.setattr(yt, "check_downloader_update", update)
    await yt.download_video(URL, tmp_path, "Film", lambda _: None)
    assert len(calls) == 2 and len(updates) == 1

    async def private(*a, **k):
        err = yt.DownloadError("private")
        err.code = "private"
        raise err

    monkeypatch.setattr(direct, "download_video_direct", private)
    with pytest.raises(yt.DownloadError, match="private"):
        await yt.download_video(URL, tmp_path, "Film", lambda _: None)
    assert len(updates) == 1


@pytest.mark.asyncio
async def test_audio_validation_allows_chapter_data_and_attached_art(
    monkeypatch, tmp_path
):
    import json

    class Probe:
        returncode = 0

        async def communicate(self):
            return json.dumps(
                {
                    "streams": [
                        {"codec_type": "audio"},
                        {"codec_type": "data"},
                        {"codec_type": "video", "disposition": {"attached_pic": 1}},
                    ]
                }
            ).encode(), b""

    async def probe(*a, **k):
        return Probe()

    monkeypatch.setattr(direct.asyncio, "create_subprocess_exec", probe)
    await direct.validate_media(tmp_path / "tagged.m4a", "audio")


def test_mobile_chat_search_yes_and_status(monkeypatch, tmp_path):
    import bridge
    import phone_session

    monkeypatch.setattr(phone_session, "guest_required", lambda: False)
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    manager = yt.DownloadJobs(tmp_path)
    monkeypatch.setattr(manager, "_spawn", lambda _: None)
    monkeypatch.setattr(yt, "jobs", lambda: manager)
    candidate = {"id": "jNQXAC9IVRw", "title": "The Kickflip", "channel": "Tesla"}
    monkeypatch.setattr(
        resolve,
        "resolve_query",
        lambda _: resolve.Decision("confirm", [resolve.Scored(candidate, 0.8)]),
    )
    cmd = bridge._match_voice_tool(
        "download Tesla Roadster kickflip as mp3", "youtube-test-client"
    )
    assert cmd[0] == "download_youtube_video"
    result = bridge.run_phone_tool(cmd[0], cmd[1])
    assert result["job"]["status"] == "confirm"
    assert "this one?" in bridge._dynamic_voice_reply(cmd[0], cmd[1], result)
    assert bridge._match_voice_tool("yes", "another-client") is None
    yes = bridge._match_voice_tool("yes", "youtube-test-client")
    job = bridge.run_phone_tool(yes[0], yes[1])["job"]
    assert job["kind"] == "audio"
    status = bridge._match_voice_tool("download status", "youtube-test-client")
    assert bridge.run_phone_tool(status[0], status[1])["job"]["id"] == job["id"]


def test_playlist_url_does_not_grant_playlist_consent(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    url = "https://www.youtube.com/playlist?list=PLabc"
    assert resolve.parse_request(url).playlist is False
    with pytest.raises(yt.DownloadError, match="explicitly"):
        yt.start_download(url)


def test_non_youtube_url_never_becomes_a_search(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(
        resolve, "resolve_query", lambda _: pytest.fail("Invalid URL became a search")
    )
    with pytest.raises(yt.DownloadError, match="YouTube video link"):
        yt.start_download("download https://example.com/a-video")
