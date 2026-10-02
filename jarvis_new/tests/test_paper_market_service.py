import json
import threading
from datetime import datetime, timezone
from http.client import HTTPConnection
from http.server import HTTPServer

import pytest

from paper_market.ledger import Ledger
from paper_market.service import handler_for


@pytest.fixture
def endpoint(tmp_path):
    ledger = Ledger(tmp_path / "test.sqlite3")
    ledger.initialize(datetime(2026, 10, 2, tzinfo=timezone.utc))
    server = HTTPServer(("127.0.0.1", 0), handler_for(ledger))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, ledger
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(port, method="GET", path="/api/paper-market", headers=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
        connection.close()


def test_snapshot_is_read_only_and_uncached(endpoint):
    port, ledger = endpoint
    status, payload, headers = request(port)
    assert status == 200
    assert payload["schema_version"] == 1
    assert payload["account"]["cash_usd"] == "1000.00"
    assert payload["fills"] == []
    assert headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in headers
    assert ledger.snapshot()["decisions"] == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_api_has_no_write_endpoints(endpoint, method):
    port, _ = endpoint
    assert request(port, method=method)[0] == 405


@pytest.mark.parametrize(
    "headers",
    [{"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Origin": "null"}],
)
def test_foreign_browser_origin_and_rebinding_host_are_rejected(endpoint, headers):
    port, _ = endpoint
    assert request(port, headers=headers)[0] == 403


def test_health_and_unknown_routes(endpoint):
    port, _ = endpoint
    assert request(port, path="/health")[1]["mode"] == "paper_only"
    assert request(port, path="/api/paper-market?url=https://evil.example")[0] == 404
