import json

import event_monitor_bridge as integration
import pytest
from event_monitor.api import MonitorAPI
from event_monitor.core import Store

NOW = 1800000000.0


def body():
    return {
        "source": "jolpica",
        "event": "results",
        "filters": {"season": 2025, "round": 1},
        "expires": NOW + 600,
        "outputs": ["session", "activate"],
        "session_scope": "desktop",
        "request_id": "owner-turn-1",
        "budget": 3,
        "ttl": 300,
    }


def test_endpoint_requires_auth_loopback_and_authority(tmp_path):
    api = MonitorAPI(
        Store(tmp_path / "monitor.sqlite"),
        authorize=lambda *a: "epoch",
        clock=lambda: NOW,
    )
    factory = lambda: api
    for auth, peer in [(False, "127.0.0.1"), (True, "10.0.0.1")]:
        code, _ = integration.endpoint(
            "POST",
            "/event-monitor/create",
            {},
            body(),
            authorized=auth,
            peer=peer,
            api_factory=factory,
        )
        assert code == 403
    code, result = integration.endpoint(
        "POST",
        "/event-monitor/create",
        {},
        body(),
        authorized=True,
        peer="127.0.0.1",
        api_factory=factory,
    )
    assert code == 200
    mid = result["id"]
    code, status = integration.endpoint(
        "GET",
        "/event-monitor/status",
        {"id": [mid]},
        None,
        authorized=True,
        peer="127.0.0.1",
        api_factory=factory,
    )
    assert code == 200 and status["state"] == "active"
    assert (
        integration.endpoint(
            "POST",
            "/event-monitor/stop",
            {},
            {"id": mid},
            authorized=True,
            peer="127.0.0.1",
            api_factory=factory,
        )[0]
        == 200
    )


def test_default_no_worker_start(monkeypatch):
    monkeypatch.delenv("JARVIS_EVENT_MONITOR", raising=False)
    monkeypatch.setattr(integration, "runtime", lambda: pytest.fail("must remain lazy"))
    assert integration.start_service() is False


def test_actual_bridge_handlers_route_authenticated_api(tmp_path, monkeypatch):
    import io

    import bridge

    api = MonitorAPI(
        Store(tmp_path / "monitor.sqlite"),
        authorize=lambda *a: "epoch",
        clock=lambda: NOW,
    )
    monkeypatch.setattr(
        integration, "runtime", lambda: type("Runtime", (), {"api": api})()
    )
    monkeypatch.setattr(integration, "local_owner_guard", lambda: None)
    handler = object.__new__(bridge._Handler)
    handler.token = "synthetic-token"
    handler.headers = {"Authorization": "Bearer synthetic-token"}
    handler.client_address = ("127.0.0.1", 1)
    replies = []
    handler._send_bytes = lambda code, kind, raw, **kw: replies.append(
        (code, json.loads(raw), kw)
    )
    handler.path = "/event-monitor/create"
    raw = json.dumps(body()).encode()
    handler.headers["Content-Length"] = str(len(raw))
    handler.rfile = io.BytesIO(raw)
    handler.do_POST()
    assert replies[-1][0] == 200 and replies[-1][2]["no_store"]
    handler.path = "/event-monitor/list"
    handler.do_GET()
    assert replies[-1][0] == 200 and len(replies[-1][1]) == 1


def test_installed_owner_assistant_registers_lazy_monitor_schemas(
    tmp_path, monkeypatch
):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    import memory
    import phone_session
    import watch_mode

    monkeypatch.setattr(memory, "persona_block", lambda: "")
    monkeypatch.setattr(watch_mode, "guest_required", lambda: False)
    monkeypatch.setattr(phone_session, "guest_required", lambda: False)
    import agent

    monkeypatch.setattr(agent, "_default_agent_llm", lambda: None)
    from types import SimpleNamespace

    assistant = agent.Assistant(browser=SimpleNamespace())
    try:
        from livekit.agents.llm.utils import build_legacy_openai_schema

        targets = {"monitor_create", "monitor_list", "monitor_status", "monitor_stop"}
        actual = {t.id: t for t in assistant.tools if t.id in targets}
        assert set(actual) == targets
        assert all(agent._never_demote(name) for name in targets)
        schema = build_legacy_openai_schema(actual["monitor_create"])
        assert set(schema["function"]["parameters"]["properties"]) == {
            "definition_json"
        }
        assert integration._RUNTIME is None
    finally:
        assistant.librarian_service.close()


def test_host_refuses_unintegrated_private_session_scope(tmp_path):
    api = integration.DesktopAPI(
        Store(tmp_path / "monitor.sqlite"),
        authorize=lambda *a: "epoch",
        clock=lambda: NOW,
    )
    request = body()
    request["session_scope"] = "private-owner"
    with pytest.raises(ValueError, match="desktop"):
        api.execute("create", request)
