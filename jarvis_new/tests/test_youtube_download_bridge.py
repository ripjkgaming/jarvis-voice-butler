import json
import urllib.error
import urllib.request

import pytest

import bridge
import youtube_download as yt


@pytest.fixture
def server():
    server, _ = bridge._run_in_thread(token="test-token")
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def call(server, path, body=None, token="test-token"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_address[1]}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.load(response)


def test_youtube_download_api_is_authenticated_and_validates_input(server, monkeypatch):
    started = []
    monkeypatch.setattr(
        yt,
        "start_download",
        lambda url: (
            started.append(url) or {"id": "youtube-aaaaaaaaaaaa", "status": "preparing"}
        ),
    )
    assert call(server, "/youtube/download", {"url": ""}, token="bad")[0] == 401
    assert call(server, "/youtube/download", {"url": "", "guest": True})[0] == 403
    assert call(server, "/youtube/download", {"url": []})[0] == 400
    assert (
        call(server, "/youtube/download", {"url": "", "directory": "/tmp/arbitrary"})[0]
        == 400
    )
    assert not started
    code, result = call(server, "/youtube/download", {"url": ""})
    assert code == 200 and result["job"]["status"] == "preparing"
    assert started == [""]


def test_youtube_download_api_reports_missing_recent_video_and_local_guard(
    server, monkeypatch
):
    def missing(url):
        raise ValueError("I haven't seen a recent YouTube video")

    monkeypatch.setattr(yt, "start_download", missing)
    assert call(server, "/youtube/download", {"url": ""}) == (
        400,
        {"ok": False, "error": "I haven't seen a recent YouTube video"},
    )
    from system import LocalSystemError

    def locked(url):
        raise LocalSystemError("Local system control is disabled")

    monkeypatch.setattr(yt, "start_download", locked)
    assert call(server, "/youtube/download", {"url": ""})[0] == 403


def test_youtube_job_poll_requires_auth_and_cannot_read_arbitrary_paths(
    server, monkeypatch, tmp_path
):
    jobs = yt.DownloadJobs(tmp_path)
    monkeypatch.setattr(yt, "jobs", lambda: jobs)
    job_id = "youtube-aaaaaaaaaaaa"
    (tmp_path / f"{job_id}.json").write_text(
        json.dumps({"id": job_id, "status": "downloading"})
    )
    assert call(server, f"/youtube/download/{job_id}", token="bad")[0] == 401
    code, result = call(server, f"/youtube/download/{job_id}")
    assert code == 200 and result["job"]["status"] == "downloading"
    assert call(server, "/youtube/download/not-a-job")[0] == 404


def test_recent_video_observer_registered_as_bridge_service():
    assert ("youtube_history", "JARVIS_YOUTUBE_HISTORY") in bridge.SIDECAR_THREADS
