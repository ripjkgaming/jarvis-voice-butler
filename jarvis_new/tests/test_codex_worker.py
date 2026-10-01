"""Fake-process regressions plus an opt-in local fake-provider smoke; no paid turns."""

import asyncio
import json
import os

import pytest

from system.codex_worker import CodexWorker, CodexWorkerError


class FakeStdin:
    def __init__(self, process):
        self.process = process

    def write(self, line):
        request = json.loads(line)
        self.process.messages.append(request)
        if "id" in request and "method" in request:
            self.process.reply(request)

    async def drain(self):
        pass


class FakeProcess:
    pid = None

    def __init__(self, *, version=False):
        self.messages = []
        self.stdout = asyncio.StreamReader(limit=1_048_576)
        self.stdin = FakeStdin(self)
        self.returncode = None
        self.killed = False
        self.models = ["gpt-5.6-terra"]
        self.actual_model = "gpt-5.6-terra"
        self.account_type = "chatgpt"
        self.notifications = []
        self.final_text = '{"action":"wait"}'
        self.config_mutation = None
        self.version_text = b"codex-cli 0.159.2\n"
        self.hang_turn = False
        self.config = {}
        self.turns = 0
        self.version = version
        if version:
            self.stdout.feed_data(b"codex-cli 0.159.2\n")
            self.stdout.feed_eof()

    def emit(self, payload):
        self.stdout.feed_data(json.dumps(payload).encode() + b"\n")

    def reply(self, request):
        method = request["method"]
        result = {}
        if method == "config/read":
            if self.config_mutation:
                self.config_mutation(self.config)
            result = {
                "config": {
                    **self.config,
                    "mcp_servers": {"unsafe.local": {"enabled": True}},
                }
            }
        elif method == "account/read":
            result = {"account": {"type": self.account_type}}
        elif method == "model/list":
            result = {
                "data": [
                    {
                        "model": model,
                        "inputModalities": ["image", "text"],
                        "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
                    }
                    for model in self.models
                ],
                "nextCursor": None,
            }
        elif method == "thread/start":
            result = {
                "model": self.actual_model,
                "modelProvider": "openai",
                "sandbox": {"type": "readOnly"},
                "approvalPolicy": "never",
                "thread": {"id": "stable-thread"},
            }
        elif method == "turn/start":
            self.turns += 1
            turn_id = f"turn-{self.turns}"
            result = {"turn": {"id": turn_id}}
            self.emit({"id": request["id"], "result": result})
            for note in self.notifications:
                self.emit(note)
            if not self.hang_turn:
                self.emit(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "stable-thread",
                            "turnId": turn_id,
                            "item": {
                                "id": "message",
                                "type": "agentMessage",
                                "phase": "final_answer",
                                "text": self.final_text,
                            },
                        },
                    }
                )
                self.emit(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": "stable-thread",
                            "turn": {"id": turn_id, "status": "completed", "items": []},
                        },
                    }
                )
            return
        self.emit({"id": request["id"], "result": result})

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.stdout.feed_eof()

    async def wait(self):
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode


@pytest.fixture
async def fake_codex(monkeypatch):
    server = FakeProcess()
    calls = []

    async def spawn(*args, **kwargs):
        calls.append((args, {**kwargs, "env": set(kwargs.get("env", {}))}))
        if "--version" in args:
            version_process = FakeProcess()
            version_process.stdout.feed_data(server.version_text)
            version_process.stdout.feed_eof()
            return version_process
        # Reflect CLI settings as config/read does. No real configuration is read.
        for index, argument in enumerate(args):
            if argument == "-c":
                key, encoded = args[index + 1].split("=", 1)
                value = json.loads(encoded)
                cursor = server.config
                for part in key.split(".")[:-1]:
                    cursor = cursor.setdefault(part, {})
                cursor[key.split(".")[-1]] = value
        return server

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return server, calls


@pytest.fixture
def picture(tmp_path):
    image = tmp_path / "observation.png"
    image.write_bytes(b"fake screenshot")
    return image


async def test_stable_thread_isolated_proposals_and_schema(
    fake_codex, tmp_path, picture, monkeypatch
):
    server, calls = fake_codex
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    worker = CodexWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    schema = {"type": "object"}
    assert await worker.propose("First observation", picture, schema) == {
        "action": "wait"
    }
    assert await worker.propose(
        "Action result and new observation", picture, schema
    ) == {"action": "wait"}
    requests = [m for m in server.messages if m.get("method") == "thread/start"]
    assert len(requests) == 1
    start = requests[0]["params"]
    assert start["model"] == "gpt-5.6-terra"
    assert start["allowProviderModelFallback"] is False
    assert start["environments"] == start["dynamicTools"] == []
    assert start["config"]["mcp_servers"]["unsafe.local"]["enabled"] is False
    turns = [m["params"] for m in server.messages if m.get("method") == "turn/start"]
    assert all(turn["threadId"] == "stable-thread" for turn in turns)
    assert all(
        turn["model"] == "gpt-5.6-terra"
        and turn["environments"] == []
        and turn["effort"] == "low"
        for turn in turns
    )
    assert turns[1]["input"][0]["text"] == "Action result and new observation"
    assert turns[0]["outputSchema"] == schema
    assert turns[0]["input"][1]["path"] == str(picture)
    assert "OPENAI_API_KEY" not in calls[-1][1]["env"]
    await worker.close()
    assert server.killed


@pytest.mark.parametrize("failure", ["missing_model", "wrong_model", "api_key_auth"])
async def test_start_fails_closed(fake_codex, tmp_path, failure):
    server, _ = fake_codex
    if failure == "missing_model":
        server.models = ["gpt-6-astra"]
    elif failure == "wrong_model":
        server.actual_model = "gpt-6-astra"
    else:
        server.account_type = "apiKey"
    worker = CodexWorker(binary="/fake/codex")
    with pytest.raises(CodexWorkerError):
        await worker.start(tmp_path)
    assert server.killed
    assert not any(m.get("method") == "turn/start" for m in server.messages)


@pytest.mark.parametrize(
    "note",
    [
        {"id": 990, "method": "item/commandExecution/requestApproval", "params": {}},
        {
            "method": "item/started",
            "params": {
                "threadId": "stable-thread",
                "item": {"type": "commandExecution"},
            },
        },
        {
            "method": "model/rerouted",
            "params": {"threadId": "stable-thread", "toModel": "gpt-6-astra"},
        },
    ],
)
async def test_unexpected_tools_and_model_reroutes_kill_worker(
    fake_codex, tmp_path, picture, note
):
    server, _ = fake_codex
    worker = CodexWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.notifications = [note]
    with pytest.raises(CodexWorkerError):
        await worker.propose("propose", picture, {})
    assert server.killed
    assert not any(
        m.get("result", {}).get("decision") == "approved" for m in server.messages
    )


async def test_cancellation_reaps_process(fake_codex, tmp_path, picture):
    server, _ = fake_codex
    worker = CodexWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.hang_turn = True
    task = asyncio.create_task(worker.propose("propose", picture, {}))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert server.killed


async def test_timeout_reaps_process(fake_codex, tmp_path, picture):
    server, _ = fake_codex
    worker = CodexWorker(binary="/fake/codex", timeout=0.02)
    await worker.start(tmp_path)
    server.hang_turn = True
    with pytest.raises(CodexWorkerError, match="timed out"):
        await worker.propose("propose", picture, {})
    assert server.killed


async def test_notification_flood_is_bounded(fake_codex, tmp_path, picture):
    server, _ = fake_codex
    worker = CodexWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.notifications = [{"method": "irrelevant", "params": {}}] * 600
    with pytest.raises(CodexWorkerError, match="notification"):
        await worker.propose("propose", picture, {})
    assert server.killed


async def test_image_must_belong_to_private_workspace(fake_codex, tmp_path):
    server, _ = fake_codex
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private data")
    worker = CodexWorker(binary="/fake/codex")
    await worker.start(workspace)
    with pytest.raises(CodexWorkerError):
        await worker.propose("propose", outside, {})
    assert server.turns == 0
    await worker.close()


async def test_unverified_version_never_starts_server(fake_codex, tmp_path):
    server, calls = fake_codex
    server.version_text = b"codex-cli 999.0\n"
    with pytest.raises(CodexWorkerError, match="version"):
        await CodexWorker(binary="/fake/codex").start(tmp_path)
    assert len(calls) == 1


async def test_unsupported_effort_never_starts_turn(fake_codex, tmp_path):
    server, _ = fake_codex
    with pytest.raises(CodexWorkerError, match="effort"):
        await CodexWorker(binary="/fake/codex", effort="ultra").start(tmp_path)
    assert server.killed
    assert not any(m.get("method") == "thread/start" for m in server.messages)


async def test_forced_hook_enablement_fails_before_thread(fake_codex, tmp_path):
    server, _ = fake_codex
    server.config_mutation = lambda config: config["features"].update(hooks=True)
    with pytest.raises(CodexWorkerError, match="isolation"):
        await CodexWorker(binary="/fake/codex").start(tmp_path)
    assert server.killed
    assert not any(m.get("method") == "thread/start" for m in server.messages)


@pytest.mark.parametrize(
    "text", ["not json", "[]", '{"secret":"' + "x" * 17_000 + '"}']
)
async def test_invalid_final_output_fails_closed(fake_codex, tmp_path, picture, text):
    server, _ = fake_codex
    worker = CodexWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.final_text = text
    with pytest.raises(CodexWorkerError) as error:
        await worker.propose("propose", picture, {})
    assert "secret" not in str(error.value)
    assert server.killed


async def test_thread_and_turn_ids_filter_stale_messages(fake_codex, tmp_path, picture):
    server, _ = fake_codex
    worker = CodexWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.notifications = [
        {
            "method": "item/completed",
            "params": {
                "threadId": "different-thread",
                "turnId": "turn-1",
                "item": {"type": "agentMessage", "text": "untrusted old output"},
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": "stable-thread",
                "turn": {"id": "old-turn", "status": "failed"},
            },
        },
    ]
    assert await worker.propose("propose", picture, {}) == {"action": "wait"}
    await worker.close()


async def test_large_notifications_have_total_byte_budget(
    fake_codex, tmp_path, picture
):
    server, _ = fake_codex
    worker = CodexWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.notifications = [
        {"method": "unknown", "params": {"data": "x" * 800_000}}
    ] * 3
    with pytest.raises(CodexWorkerError, match="notification"):
        await worker.propose("propose", picture, {})
    assert server.killed


@pytest.mark.skipif(
    os.getenv("JARVIS_CODEX_OFFLINE_SMOKE") != "1",
    reason="opt in to the installed-CLI local fake-provider isolation smoke",
)
async def test_installed_cli_exposes_no_tools_to_local_fake_provider(tmp_path):
    """No upstream endpoint, authentication, inference or UI action is involved.

    This deliberately uses an auth-free localhost provider which returns HTTP
    400 before inference. Only this opt-in test runs the installed binary.
    """
    import base64
    from pathlib import Path

    from system.codex_worker import ISOLATION_CONFIG, _toml

    binary = Path("/usr/lib/chatgpt/resources/codex")
    if not binary.is_file():
        pytest.skip("Verified installed Codex binary is unavailable")
    captured = asyncio.get_running_loop().create_future()

    async def fake_provider(reader, writer):
        try:
            header = (await reader.readuntil(b"\r\n\r\n")).decode()
            headers = dict(
                line.split(": ", 1) for line in header.split("\r\n")[1:] if ": " in line
            )
            raw = await reader.readexactly(
                int(headers.get("Content-Length", headers.get("content-length", "0")))
            )
            payload = json.loads(raw) if raw else {}
            if payload.get("input") and not captured.done():
                captured.set_result(
                    (payload, any(key.lower() == "authorization" for key in headers))
                )
            body = b'{"error":{"message":"Offline isolation probe only","type":"invalid_request_error","code":"probe"}}'
            writer.write(
                b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    listener = await asyncio.start_server(fake_provider, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    config = {
        **ISOLATION_CONFIG,
        "model_provider": "jarvis_offline_probe",
        "model_providers.jarvis_offline_probe": {
            "name": "Jarvis offline isolation probe",
            "base_url": f"http://127.0.0.1:{port}",
            "wire_api": "responses",
            "requires_openai_auth": False,
            "supports_websockets": False,
            "request_max_retries": 0,
            "stream_max_retries": 0,
        },
    }
    command = [str(binary), "app-server", "--stdio"]
    for key, value in config.items():
        command += ["-c", f"{key}={_toml(value)}"]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"}
    }
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=tmp_path,
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=1_048_576,
    )

    async def rpc(request_id, method, params):
        process.stdin.write(
            json.dumps({"id": request_id, "method": method, "params": params}).encode()
            + b"\n"
        )
        await process.stdin.drain()
        for _ in range(100):
            message = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
            if message.get("id") == request_id:
                assert "error" not in message, (
                    "Installed CLI rejected offline isolation smoke"
                )
                return message["result"]
        pytest.fail("Offline smoke exceeded its notification budget")

    try:
        await rpc(
            1,
            "initialize",
            {
                "clientInfo": {"name": "jarvis_offline_probe", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        process.stdin.write(b'{"method":"initialized"}\n')
        effective = (await rpc(2, "config/read", {"includeLayers": False}))["config"]
        thread_config = {
            **ISOLATION_CONFIG,
            "model_provider": "jarvis_offline_probe",
            "mcp_servers": {
                name: {"enabled": False} for name in effective.get("mcp_servers", {})
            },
        }
        started = await rpc(
            3,
            "thread/start",
            {
                "model": "gpt-5.6-terra",
                "modelProvider": "jarvis_offline_probe",
                "allowProviderModelFallback": False,
                "cwd": str(tmp_path),
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "ephemeral": True,
                "environments": [],
                "dynamicTools": [],
                "config": thread_config,
                "baseInstructions": "Only return JSON.",
                "developerInstructions": "",
            },
        )
        image = tmp_path / "observation.png"
        image.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
            )
        )
        await rpc(
            4,
            "turn/start",
            {
                "threadId": started["thread"]["id"],
                "model": "gpt-5.6-terra",
                "effort": "low",
                "environments": [],
                "input": [
                    {"type": "text", "text": 'Return {"ok":true}.'},
                    {"type": "localImage", "path": str(image)},
                ],
                "outputSchema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            },
        )
        payload, has_auth = await asyncio.wait_for(captured, 20)
        assert not has_auth, "Offline fake provider must never receive authentication"
        assert not payload.get("tools"), "Proposal-only configuration exposed tools"
        assert payload["model"] == "gpt-5.6-terra"
        assert any(
            part.get("type") == "input_image"
            for item in payload["input"]
            for part in item.get("content", [])
        )
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
        listener.close()
        await listener.wait_closed()


async def test_cancelling_close_still_reaps_process(fake_codex, tmp_path):
    server, _ = fake_codex
    worker = CodexWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    permit_reap = asyncio.Event()
    reaped = asyncio.Event()

    async def slow_wait():
        await permit_reap.wait()
        reaped.set()
        return -9

    server.wait = slow_wait
    closing = asyncio.create_task(worker.close())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert server.killed
    permit_reap.set()
    await asyncio.wait_for(reaped.wait(), 1)
    await worker.close()
