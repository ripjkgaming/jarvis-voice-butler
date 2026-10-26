"""Real loopback bridge routing, mocked shopping and browser operations."""

import http.client
import json

import pytest
import bridge
import shopping


@pytest.fixture
def server():
    server, thread = bridge._run_in_thread(token="shopping-test-token")
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(server, path, body=None, authorized=True):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if authorized:
        headers["Authorization"] = "Bearer shopping-test-token"
    connection.request(
        "GET" if body is None else "POST",
        path,
        body=None if body is None else json.dumps(body),
        headers=headers,
    )
    response = connection.getresponse()
    result = response.status, json.loads(response.read())
    connection.close()
    return result


def test_search_is_authenticated_and_shared(server, monkeypatch):
    queries = []

    def search(query):
        queries.append(query)
        return {"query": query, "results": [], "warning": "fixture unavailable"}

    monkeypatch.setattr(shopping, "search_products", search)
    assert request(server, "/shopping?q=headphones", authorized=False)[0] == 401
    assert not queries
    code, result = request(server, "/shopping?q=headphones")
    assert code == 200 and result["warning"] == "fixture unavailable"
    assert queries == ["headphones"]


def test_open_requires_authentication_and_confirmation(server, monkeypatch):
    calls = []
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(shopping, "public_url", lambda url: url)
    original = shopping.open_product_source

    def opener(argv, **kwargs):
        calls.append(argv)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(
        shopping,
        "open_product_source",
        lambda url, confirmed: original(url, confirmed, opener),
    )
    body = {"url": "https://shop.example/item", "confirmed": True}
    assert request(server, "/shopping/open", body, authorized=False)[0] == 401
    assert request(server, "/shopping/open", {**body, "confirmed": False})[0] == 400
    assert request(server, "/shopping/open", {**body, "confirmed": "true"})[0] == 400
    assert not calls
    assert request(server, "/shopping/open", body)[1] == {"ok": True}
    assert calls == [["xdg-open", "https://shop.example/item"]]
