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
        lambda url, **opts: (
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
    def missing(url, **opts):
        raise ValueError("I haven't seen a recent YouTube video")

    monkeypatch.setattr(yt, "start_download", missing)
    assert call(server, "/youtube/download", {"url": ""}) == (
        400,
        {"ok": False, "error": "I haven't seen a recent YouTube video"},
    )
    from system import LocalSystemError

    def locked(url, **opts):
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


def test_cancel_endpoint_preserves_auth_guest_and_local_guards(
    server, monkeypatch, tmp_path
):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    manager = yt.DownloadJobs(tmp_path)
    monkeypatch.setattr(manager, "_spawn", lambda _: None)
    monkeypatch.setattr(yt, "jobs", lambda: manager)
    job = manager.start({"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"})
    body = {"job_id": job["id"]}
    assert call(server, "/youtube/download/cancel", body, token="bad")[0] == 401
    assert call(server, "/youtube/download/cancel", dict(body, guest=True))[0] == 403
    monkeypatch.setenv("JARVIS_LOCAL", "0")
    assert call(server, "/youtube/download/cancel", body)[0] == 403
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    code, result = call(server, "/youtube/download/cancel", body)
    assert code == 200 and result["job"]["status"] == "cancelled"


def test_search_and_confirmation_roundtrip_over_http(server, monkeypatch, tmp_path):
    import youtube_resolve as resolve

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    manager = yt.DownloadJobs(tmp_path)
    monkeypatch.setattr(manager, "_spawn", lambda _: None)
    monkeypatch.setattr(yt, "jobs", lambda: manager)
    choice = {"id": "jNQXAC9IVRw", "title": "Film", "channel": "Channel"}
    monkeypatch.setattr(
        resolve,
        "resolve_query",
        lambda _: resolve.Decision("confirm", [resolve.Scored(choice, 0.8)]),
    )
    code, result = call(server, "/youtube/download", {"url": "download Film as mp3"})
    assert code == 200 and result["job"]["status"] == "confirm"
    token = result["job"]["confirmation_id"]
    assert not list(tmp_path.glob("*.json"))
    code, result = call(
        server, "/youtube/download", {"confirmation_id": token, "confirm": True}
    )
    assert code == 200 and result["job"]["kind"] == "audio"
    assert result["job"]["channel"] == "Channel"
