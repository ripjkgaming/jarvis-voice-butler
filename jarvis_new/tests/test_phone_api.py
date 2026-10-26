import phone_api as api


def test_fallback_only_on_explicit_no_route():
    calls = []

    def chat(body):
        calls.append(body)
        return 200, {"reply": "hello"}

    body = {"api_version": 1, "text": "hello", "instant": True, "guest": True}
    assert (
        api.command(body, lambda _: (404, {"error": "no-route"}), chat)[1]["reply"]
        == "hello"
    )
    assert calls[0]["guest"] is True
    calls.clear()
    assert api.command(body, lambda _: (503, {"error": "failed"}), chat)[0] == 503
    assert not calls


def test_version_and_bounded_history():
    assert api.command({"api_version": 2}, None, None)[0] == 426
    seen = []
    api.command(
        {"api_version": 1, "text": "hi", "history": [["JARVIS", "x" * 2000]] * 40},
        None,
        lambda b: (seen.append(b) or 200, {}),
    )
    assert len(seen[0]["history"]) == 20
    assert seen[0]["history"][0] == ["jarvis", "x" * 1000]


def test_session_never_dispatches_for_laptop_join():
    seen = []

    def mint(room, dispatch):
        seen.append((room, dispatch))
        return {"url": "wss://voice", "token": "test"}

    assert (
        api.session(
            {"api_version": 1, "mode": "laptop"}, lambda: "room", lambda: False, mint
        )[0]
        == 200
    )
    assert seen == [("room", False)]
    assert (
        api.session(
            {"api_version": 1, "mode": "laptop"}, lambda: None, lambda: True, mint
        )[0]
        == 409
    )
    assert len(seen) == 1


def test_declarative_policy_is_bounded_and_live(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    monkeypatch.setenv("JARVIS_PHONE_POLICY", str(path))
    path.write_text('{"history_messages":999,"instant_routing":false,"code":"ignored"}')
    assert api.policy() == {"history_messages": 20, "instant_routing": False}
    path.write_text('{"history_messages":2,"instant_routing":true}')
    assert api.capabilities()["policy"]["history_messages"] == 2
    path.write_text("invalid")
    assert api.policy()["history_messages"] == 20


def test_http_auth_and_command_wiring(monkeypatch):
    import json
    import urllib.error
    import urllib.request

    import bridge

    monkeypatch.setattr(
        bridge, "handle_route", lambda body: (404, {"error": "no-route"})
    )
    monkeypatch.setattr(
        bridge, "handle_chat", lambda body: (200, {"reply": "server reply"})
    )
    server = bridge.create_server(host="127.0.0.1", port=0, token="test-token")
    import threading

    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        for path in (
            "/phone/v1/capabilities",
            "/phone/v1/command",
            "/phone/v1/session",
        ):
            request = urllib.request.Request(
                base + path, data=None if path.endswith("capabilities") else b"{}"
            )
            try:
                urllib.request.urlopen(request, timeout=3)
                raise AssertionError("missing token accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 401
        req = urllib.request.Request(
            base + "/phone/v1/command",
            data=json.dumps({"api_version": 1, "text": "hi", "instant": True}).encode(),
            headers={"Authorization": "Bearer test-token"},
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            assert json.load(response)["reply"] == "server reply"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
