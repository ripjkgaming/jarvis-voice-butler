"""Direct downloads preserve the existing job and transfer notification contract."""

import asyncio
import sys

import pytest

import youtube_direct as direct
import youtube_download as yt

VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
MP4 = b"\x00\x00\x00\x18ftypmp42synthetic-test-video"


def test_direct_options_are_bounded_headless_and_use_local_browser_session(tmp_path):
    opts = direct.worker_options(tmp_path, lambda _: None)
    assert opts["noplaylist"] is True
    assert opts["cookiesfrombrowser"] == ("brave",)
    assert opts["merge_output_format"] == "mp4"
    assert "height<=1080" in opts["format"]
    assert "ba[ext=m4a]" in opts["format"]
    assert opts["concurrent_fragment_downloads"] == 1
    assert not opts.get("remote_components")
    assert opts["js_runtimes"]["node"]["path"]


def test_progress_does_not_expose_urls_or_cookies():
    emitted = []
    hook = direct.progress_reporter(emitted.append)
    hook(
        {"status": "downloading", "downloaded_bytes": 0, "info_dict": {"title": "Film"}}
    )
    hook(
        {
            "status": "downloading",
            "downloaded_bytes": 1024,
            "info_dict": {"title": "Film", "url": "secret", "cookies": "secret"},
        }
    )
    hook({"status": "finished", "downloaded_bytes": 1024})
    assert len(emitted) == 1
    assert emitted[0] == {"event": "transfer", "title": "Film"}


def install_worker(monkeypatch, script):
    monkeypatch.setattr(
        direct,
        "worker_command",
        lambda url, staging: [sys.executable, "-c", script, str(staging)],
    )

    async def valid(_):
        return None

    monkeypatch.setattr(direct, "validate_media", valid)


@pytest.mark.asyncio
async def test_notification_waits_for_actual_bytes_and_final_save_is_exclusive(
    monkeypatch, tmp_path
):
    script = f"""import json,time,sys
from pathlib import Path
p=Path(sys.argv[1]);time.sleep(.15)
print(json.dumps({{'event':'transfer','title':'Film'}}),flush=True)
time.sleep(.15);(p/'video.mp4').write_bytes({MP4!r})
print(json.dumps({{'event':'done'}}),flush=True)
"""
    install_worker(monkeypatch, script)
    (tmp_path / "Film.mp4").write_bytes(b"existing")
    seen = []
    task = asyncio.create_task(
        direct.download_video_direct(VIDEO, tmp_path, "Fallback", seen.append)
    )
    await asyncio.sleep(0.05)
    assert seen == []
    target = await task
    assert seen == [target]
    assert target.name == "Film (1).mp4"
    assert target.read_bytes() == MP4
    assert (tmp_path / "Film.mp4").read_bytes() == b"existing"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Film (1).mp4", "Film.mp4"]


@pytest.mark.asyncio
async def test_preparation_failure_never_announces_transfer(monkeypatch, tmp_path):
    install_worker(
        monkeypatch,
        'import json;print(json.dumps({"event":"error","code":"authentication"}),flush=True);raise SystemExit(1)',
    )
    seen = []
    with pytest.raises(yt.DownloadError, match="Brave"):
        await direct.download_video_direct(VIDEO, tmp_path, "Film", seen.append)
    assert seen == []
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_corrupt_result_not_saved_and_partial_state_removed(
    monkeypatch, tmp_path
):
    install_worker(
        monkeypatch,
        'import json,sys;from pathlib import Path;print(json.dumps({"event":"transfer","title":"Film"}),flush=True);(Path(sys.argv[1])/"video.mp4").write_bytes(b"<html>error</html>");print(json.dumps({"event":"done"}),flush=True)',
    )
    with pytest.raises(yt.DownloadError, match="invalid MP4"):
        await direct.download_video_direct(VIDEO, tmp_path, "Film", lambda _: None)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_timeout_reaps_worker_and_removes_staging(monkeypatch, tmp_path):
    install_worker(monkeypatch, "import time;time.sleep(30)")
    monkeypatch.setattr(direct, "PREPARE_TIMEOUT_S", 0.05)
    with pytest.raises(yt.DownloadError, match="timed out"):
        await direct.download_video_direct(VIDEO, tmp_path, "Film", lambda _: None)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_backend_default_uses_direct_and_site_remains_opt_in(
    monkeypatch, tmp_path
):
    called = []

    async def run_direct(*args):
        called.append("direct")
        return tmp_path / "film.mp4"

    async def run_site(*args):
        called.append("yt1s")
        return tmp_path / "film.mp4"

    monkeypatch.setattr(direct, "download_video_direct", run_direct)
    monkeypatch.setattr(yt, "download_video_site", run_site)
    monkeypatch.delenv("JARVIS_YOUTUBE_BACKEND", raising=False)
    await yt.download_video(VIDEO, tmp_path, "Film", lambda _: None)
    monkeypatch.setenv("JARVIS_YOUTUBE_BACKEND", "yt1s")
    await yt.download_video(VIDEO, tmp_path, "Film", lambda _: None)
    assert called == ["direct", "yt1s"]


def test_default_speech_and_tool_instructions_do_not_claim_yt1s():
    import prompts

    assert "YT1s" not in yt.job_say({"status": "preparing", "title": "Film"})
    assert "headless YouTube downloader" in prompts.AGENT_INSTRUCTIONS


@pytest.mark.asyncio
async def test_cancellation_kills_worker_children_and_removes_reservation(
    monkeypatch, tmp_path
):
    import os
    from pathlib import Path

    pidfile = tmp_path / "child.pid"
    script = f"""import json,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])
Path({str(pidfile)!r}).write_text(str(child.pid))
print(json.dumps({{"event":"transfer","title":"Film"}}),flush=True)
time.sleep(60)
"""
    install_worker(monkeypatch, script)
    seen = []
    task = asyncio.create_task(
        direct.download_video_direct(VIDEO, tmp_path, "Film", seen.append)
    )
    for _ in range(100):
        if seen:
            break
        await asyncio.sleep(0.01)
    assert seen
    child = int(pidfile.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        status = Path(f"/proc/{child}/stat")
        if not status.exists() or status.read_text().rsplit(") ", 1)[1].startswith("Z"):
            break
        await asyncio.sleep(0.01)
    else:
        os.kill(child, 9)
        pytest.fail("Cancelled downloader left a subprocess running")
    pidfile.unlink()
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_audio_is_required_for_completion(tmp_path):
    import subprocess

    sample = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:d=0.1",
            "-c:v",
            "mpeg4",
            str(sample),
        ],
        check=True,
        timeout=10,
    )
    with pytest.raises(yt.DownloadError, match="audio"):
        await direct.validate_media(sample)


def test_failure_after_transfer_does_not_claim_download_never_started():
    assert "hasn't started" not in direct._failure_say("authentication", started=True)


@pytest.mark.asyncio
async def test_publish_triggers_existing_converter_watch_events(tmp_path):
    import shutil

    if not shutil.which("inotifywait"):
        pytest.skip("inotifywait is needed for the external converter contract")
    watch = tmp_path / "downloads"
    watch.mkdir()
    stage = tmp_path / "stage.mp4"
    stage.write_bytes(MP4)
    process = await asyncio.create_subprocess_exec(
        "inotifywait",
        "-m",
        "-e",
        "close_write",
        "-e",
        "moved_to",
        "--format",
        "%f",
        str(watch),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        while b"Watches established" not in await process.stderr.readline():
            pass
        yt.publish_download(stage, watch / "Film.mp4")
        event = await asyncio.wait_for(process.stdout.readline(), 2)
        assert event.strip() == b"Film.mp4"
        assert (watch / "Film.mp4").read_bytes() == MP4
    finally:
        process.kill()
        await process.wait()


def test_publish_does_not_overwrite_existing_file(tmp_path):
    source = tmp_path / "stage.mp4"
    source.write_bytes(MP4)
    target = tmp_path / "Film.mp4"
    target.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        yt.publish_download(source, target)
    assert target.read_bytes() == b"existing"


def test_job_tracks_mov_after_external_converter_removes_mp4(tmp_path):
    import json

    jobs = yt.DownloadJobs(tmp_path / "jobs")
    jobs.directory.mkdir()
    video = tmp_path / "Film.mp4"
    mov = video.with_suffix(".mov")
    mov.write_bytes(b"converted video")
    job_id = "youtube-" + "a" * 12
    job = {"id": job_id, "status": "done", "path": str(video), "filename": video.name}
    (jobs.directory / (job_id + ".json")).write_text(json.dumps(job))
    got = jobs.get(job_id)
    assert got["path"] == str(mov)
    assert got["filename"] == mov.name
    assert "Film.mov" in got["say"]


def test_download_filename_preserves_existing_converted_mov(tmp_path):
    existing = tmp_path / "Film.mov"
    existing.write_bytes(b"existing edit media")
    target = yt.reserve_target(tmp_path, "Film.mp4", "Film")
    assert target.name == "Film (1).mp4"
    assert existing.read_bytes() == b"existing edit media"
