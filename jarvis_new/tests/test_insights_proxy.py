"""Headless contract tests for the two read-only local dashboard proxies."""

import io
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import monotonic
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, "src")

import bridge
import insights_proxy

REPORT = b"""{
  "source_mode": "ccusage",
  "updated_at": "2026-10-02T00:00:00Z",
  "source_info": {"name":"ccusage", "version":"20.0.26", "status":"stale",
    "snapshot_at":"2026-10-01T23:00:00Z", "attempted_at":"2026-10-02T00:00:00Z"},
  "summary": {"total_tokens":9007199254740993, "input_tokens":3,
    "estimated_cost_usd":0.30000000000000004, "actual_cost_usd":null,
    "reasoning_tokens":null},
  "future_report_field": {"preserved":true}
}"""


class _Response(io.BytesIO):
    def __init__(
        self, raw=REPORT, status=200, content_type="application/json", length=None
    ):
        super().__init__(raw)
        self.status = status
        self.content_type = content_type
        self.length = length

    def getheader(self, name, default=None):
        return {
            "Content-Type": self.content_type,
            "Content-Length": self.length,
        }.get(name, default)

    def isclosed(self):
        return self.closed


@pytest.fixture
def upstream(monkeypatch):
    calls = []
    settings = {"raw": REPORT, "status": 200, "content_type": "application/json"}

    class SourceSocket:
        def settimeout(self, timeout):
            assert 0 < timeout <= 8

    class Connection:
        def __init__(self, host, port, timeout):
            self.call = {"host": host, "port": port, "timeout": timeout}
            calls.append(self.call)
            self.sock = SourceSocket()

        def connect(self):
            pass

        def request(self, method, path, headers):
            self.call.update(method=method, path=path, headers=headers)
            if "error" in settings:
                raise settings["error"]

        def getresponse(self):
            return _Response(**settings)

        def close(self):
            self.call["closed"] = True

    monkeypatch.setattr(insights_proxy, "HTTPConnection", Connection)
    return calls, settings


@pytest.mark.parametrize("selected_range", ["today", "week", "month", "all"])
def test_usage_preserves_authoritative_bytes_and_fixed_destination(
    upstream, monkeypatch, selected_range
):
    calls, _ = upstream
    # Proxy configuration and bridge auth must never escape to the source.
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.example:80")
    monkeypatch.setenv("JARVIS_BRIDGE_TOKEN", "private-bridge-token")
    code, raw = insights_proxy.usage(
        f"range={selected_range}&timezone=Asia%2FSingapore"
    )
    assert (code, raw) == (200, REPORT)
    call = calls[0]
    assert (call["host"], call["port"], call["method"]) == (
        "127.0.0.1",
        42731,
        "GET",
    )
    assert 0 < call["timeout"] <= 10
    assert call["closed"]
    assert "Authorization" not in call["headers"]
    target = urlsplit(call["path"])
    assert target.path == "/api/dashboard"
    assert parse_qs(target.query) == {
        "source": ["ccusage"],
        "range": [selected_range],
        "timezone": ["Asia/Singapore"],
    }


def test_usage_defaults_to_today_singapore(upstream):
    calls, _ = upstream
    assert insights_proxy.usage() == (200, REPORT)
    assert parse_qs(urlsplit(calls[0]["path"]).query) == {
        "source": ["ccusage"],
        "range": ["today"],
        "timezone": ["Asia/Singapore"],
    }


@pytest.mark.parametrize(
    "query",
    [
        "range=yesterday",
        "range=",
        "timezone=UTC",
        "range=today&range=all",
        "timezone=Asia/Singapore&timezone=Asia/Singapore",
        "source=supplemental",
        "url=http://127.0.0.1:22/",
        "app=Codex",
        "range=today%26source%3Dsupplemental",
        "range=today&timezone=Asia/Singapore&source=ccusage",
        "timezone=Asia/Singapore%0D%0AHost%3Aevil.example",
        "range",
        "timezone=%FF",
        "x=" + "a" * 300,
    ],
)
def test_bad_usage_queries_never_contact_upstream(upstream, query):
    calls, _ = upstream
    code, body = insights_proxy.usage(query)
    assert code == 400
    assert json.loads(body)["ok"] is False
    assert calls == []


@pytest.mark.parametrize("reader", [insights_proxy.usage, insights_proxy.paper_market])
@pytest.mark.parametrize("failure", [ConnectionRefusedError(), TimeoutError()])
def test_unavailable_source_fails_soft_and_closes(upstream, reader, failure):
    calls, settings = upstream
    settings["error"] = failure
    code, raw = reader()
    assert code == 503
    assert json.loads(raw)["error"].endswith("is unavailable")
    assert calls[0]["closed"]


def test_source_error_status_and_json_remain_unchanged(upstream):
    _, settings = upstream
    settings.update(status=503, raw=b'{"error":"No successful ccusage report"}')
    assert insights_proxy.usage() == (503, settings["raw"])


@pytest.mark.parametrize(
    "changes",
    [
        {"status": 302},
        {"content_type": "text/html", "raw": b"<html>Error</html>"},
        {"raw": b"{truncated"},
        {"raw": b"[]"},
        {"raw": b"\xff"},
        {"raw": b'{"value":NaN}'},
        {"raw": b'{"value":Infinity}'},
        {"raw": b'{"value":-Infinity}'},
        {"length": "-1"},
        {"length": "n/a"},
        {"length": "10000"},
    ],
)
def test_redirects_and_invalid_reports_are_not_relayed(upstream, changes):
    calls, settings = upstream
    settings.update(changes)
    code, raw = insights_proxy.usage()
    assert code == 502
    assert json.loads(raw)["ok"] is False
    assert len(calls) == 1
    assert calls[0]["closed"]


def test_response_size_is_bounded(upstream, monkeypatch):
    _, settings = upstream
    monkeypatch.setattr(insights_proxy, "_MAX_RESPONSE_BYTES", 32)
    settings["raw"] = b'{"report":"' + b"x" * 33 + b'"}'
    code, raw = insights_proxy.usage()
    assert code == 502
    assert "too large" in json.loads(raw)["error"]


def test_declared_oversized_response_is_rejected_before_body_read(
    upstream, monkeypatch
):
    _, settings = upstream
    settings["length"] = str(insights_proxy._MAX_RESPONSE_BYTES + 1)

    def unexpected_read(self, size):
        pytest.fail("Oversized Content-Length must be rejected without reading")

    monkeypatch.setattr(_Response, "read1", unexpected_read)
    code, raw = insights_proxy.usage()
    assert code == 502 and "too large" in json.loads(raw)["error"]


def test_trickling_response_cannot_extend_request_deadline(upstream, monkeypatch):
    calls, _ = upstream
    now = [100.0]
    reads = []
    monkeypatch.setattr(insights_proxy, "monotonic", lambda: now[0])

    def trickle(self, size):
        reads.append(size)
        now[0] += 5.0
        return b" "

    monkeypatch.setattr(_Response, "read1", trickle)
    code, raw = insights_proxy.usage()
    assert code == 503 and "unavailable" in json.loads(raw)["error"]
    assert len(reads) == 2
    assert calls[0]["closed"]


@pytest.mark.parametrize("failure", [None, TimeoutError()])
def test_watchdog_is_cancelled_and_joined_on_every_exit(upstream, monkeypatch, failure):
    calls, settings = upstream
    timers = []
    real_timer = insights_proxy.Timer

    def track_timer(*args):
        timer = real_timer(*args)
        timers.append(timer)
        return timer

    monkeypatch.setattr(insights_proxy, "Timer", track_timer)
    if failure is not None:
        settings["error"] = failure
    assert insights_proxy.usage()[0] == (503 if failure else 200)
    assert len(timers) == 1
    assert timers[0].finished.is_set()
    assert not timers[0].is_alive()
    assert calls[0]["closed"]


def test_paper_market_uses_only_fixed_read_endpoint(upstream):
    calls, settings = upstream
    settings["raw"] = b'{"mode":"paper", "as_of":"2026-10-02", "positions":[]}'
    assert insights_proxy.paper_market() == (200, settings["raw"])
    assert (calls[0]["host"], calls[0]["port"]) == ("127.0.0.1", 8767)
    assert (calls[0]["method"], calls[0]["path"]) == ("GET", "/api/paper-market")
    assert insights_proxy.paper_market("url=http://evil.example")[0] == 400
    assert len(calls) == 1


@pytest.fixture
def server():
    instance, _ = bridge._run_in_thread(token="test-token")
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()


def _request(server, path, token="test-token", method="GET"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.server_address[1]}{path}", method=method
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        response = urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.read(), response.headers


@pytest.mark.parametrize("path", ["/insights/usage", "/paper-market"])
def test_bridge_routes_preserve_auth_cors_and_read_only_contract(
    server, upstream, path
):
    calls, _ = upstream
    assert _request(server, path, token="")[0] == 401
    assert _request(server, path, token="wrong")[0] == 401
    assert calls == []
    code, raw, headers = _request(server, path)
    assert (code, raw) == (200, REPORT)
    assert headers["Content-Type"].startswith("application/json")
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert headers["Cache-Control"] == "no-store"
    assert _request(server, path, method="POST")[0] == 404
    assert len(calls) == 1


def test_slow_upstream_does_not_block_bridge_health(server, monkeypatch):
    entered, release = Event(), Event()

    def slow_report(query):
        entered.set()
        assert release.wait(timeout=3)
        return 200, REPORT

    monkeypatch.setattr(insights_proxy, "usage", slow_report)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(_request, server, "/insights/usage")
        assert entered.wait(timeout=2)
        try:
            health = pool.submit(_request, server, "/health")
            code, raw, _ = health.result(timeout=1)
            assert code == 200 and json.loads(raw)["ok"]
        finally:
            release.set()
        assert pending.result(timeout=2)[:2] == (200, REPORT)


def test_bridge_rejects_bad_query_and_preserves_source_errors(server, upstream):
    calls, settings = upstream
    assert _request(server, "/insights/usage?source=supplemental")[0] == 400
    assert calls == []
    settings.update(status=503, raw=b'{"error":"Report unavailable"}')
    assert _request(server, "/insights/usage")[:2] == (503, settings["raw"])


@pytest.mark.parametrize("protocol", ["HTTP/1.0", "HTTP/1.1"])
@pytest.mark.parametrize("with_length", [True, False])
@pytest.mark.parametrize("padding", [0, 70000])
def test_real_closing_http_response_preserves_json(protocol, with_length, padding):
    """Real sockets exercise EOF/Content-Length closure that mocks cannot."""
    payload = REPORT + b" " * padding

    class Source(BaseHTTPRequestHandler):
        protocol_version = protocol

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if with_length:
                self.send_header("Content-Length", str(len(payload)))
            if protocol == "HTTP/1.1":
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    source = ThreadingHTTPServer(("127.0.0.1", 0), Source)
    thread = Thread(target=source.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        assert insights_proxy._read_json(
            source.server_address[1], "/api/dashboard", "Test source"
        ) == (200, payload)
    finally:
        source.shutdown()
        source.server_close()
        thread.join(timeout=2)


def test_real_trickled_headers_respect_total_deadline(monkeypatch):
    """Regular bytes must not extend the header parser beyond its budget."""
    stop = Event()
    received = Event()
    response = b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{}"

    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            received.set()
            try:
                for byte in response:
                    self.connection.sendall(bytes([byte]))
                    if stop.wait(0.02):
                        break
            except OSError:
                pass

        def log_message(self, *args):
            pass

    source = ThreadingHTTPServer(("127.0.0.1", 0), Source)
    thread = Thread(target=source.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    monkeypatch.setattr(insights_proxy, "_TIMEOUT_SECONDS", 0.12)
    try:
        started = monotonic()
        code, raw = insights_proxy._read_json(
            source.server_address[1], "/api/dashboard", "Test source"
        )
        elapsed = monotonic() - started
        assert received.is_set()
        assert code == 503 and "unavailable" in json.loads(raw)["error"]
        # Generous scheduler margin, still much less than the >1s trickle.
        assert elapsed < 0.55, f"Header read outlived its 0.12s budget: {elapsed:.3f}s"
    finally:
        stop.set()
        source.shutdown()
        source.server_close()
        thread.join(timeout=2)
