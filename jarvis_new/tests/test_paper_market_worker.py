"""Paper worker protocol regressions. All processes and model replies are fake."""

import asyncio
import json

import pytest

from paper_market.worker import (
    PROPOSAL_SCHEMA,
    PaperMarketWorker,
    PaperMarketWorkerError,
)


class FakeStdin:
    def __init__(self, process):
        self.process = process

    def write(self, line):
        message = json.loads(line)
        self.process.messages.append(message)
        if "method" in message and "id" in message:
            self.process.reply(message)

    async def drain(self):
        pass


class FakeProcess:
    pid = None

    def __init__(self):
        self.stdin = FakeStdin(self)
        self.stdout = asyncio.StreamReader(limit=1_048_576)
        self.returncode = None
        self.killed = False
        self.messages = []
        self.config = {}
        self.config_mutation = None
        self.account_type = "chatgpt"
        self.models = ["gpt-6-astra"]
        self.efforts = ["high"]
        self.thread_model = "gpt-6-astra"
        self.thread_effort = "high"
        self.version_text = b"codex-cli 0.159.2\n"
        self.notifications = []
        self.final_text = '{"actions":[],"rationale":"Hold with current cash."}'
        self.final_items = None
        self.hang_turn = False
        self.turn_status = "completed"
        self.turns = 0

    def emit(self, payload):
        self.stdout.feed_data(json.dumps(payload).encode() + b"\n")

    def reply(self, request):
        method = request["method"]
        result = {}
        if method == "config/read":
            if self.config_mutation:
                self.config_mutation(self.config)
            result = {"config": self.config}
        elif method == "account/read":
            result = {"account": {"type": self.account_type}}
        elif method == "model/list":
            result = {
                "data": [
                    {
                        "model": model,
                        "inputModalities": ["text"],
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": effort} for effort in self.efforts
                        ],
                    }
                    for model in self.models
                ]
            }
        elif method == "thread/start":
            result = {
                "model": self.thread_model,
                "reasoningEffort": self.thread_effort,
                "modelProvider": "openai",
                "approvalPolicy": "never",
                "sandbox": {"type": "readOnly", "networkAccess": False},
                "thread": {"id": "paper-thread"},
            }
        elif method == "turn/start":
            self.turns += 1
            turn_id = f"turn-{self.turns}"
            self.emit({"id": request["id"], "result": {"turn": {"id": turn_id}}})
            for notification in self.notifications:
                self.emit(notification)
            if not self.hang_turn:
                items = self.final_items
                if items is None:
                    items = [
                        {
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": self.final_text,
                        }
                    ]
                self.emit(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": "paper-thread",
                            "turn": {
                                "id": turn_id,
                                "status": self.turn_status,
                                "items": items,
                            },
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
            process = FakeProcess()
            process.stdout.feed_data(server.version_text)
            process.stdout.feed_eof()
            return process
        for index, argument in enumerate(args):
            if argument == "-c":
                key, encoded = args[index + 1].split("=", 1)
                cursor = server.config
                for part in key.split(".")[:-1]:
                    cursor = cursor.setdefault(part, {})
                cursor[key.split(".")[-1]] = json.loads(encoded)
        server.config["mcp_servers"] = {"unsafe.local": {"enabled": True}}
        return server

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return server, calls


async def test_isolated_text_only_proposal_and_verified_metadata(
    fake_codex, tmp_path, monkeypatch
):
    server, calls = fake_codex
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.setenv(key, "never-inherit")
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    assert server.turns == 0
    result = await worker.propose({"cash_usd": "100000.00", "quotes": {}})
    assert result["proposal"] == {
        "actions": [],
        "rationale": "Hold with current cash.",
    }
    assert result["model"] == "gpt-6-astra"
    assert result["effort"] == "high"
    assert result["usage"] is None
    assert result["verification"]["model_verified"] is True
    assert result["verification"]["effort_verified"] is True
    assert result["verification"]["tools_disabled"] is True
    assert result["verification"]["turn_id"] == "turn-1"
    start = next(
        m["params"] for m in server.messages if m.get("method") == "thread/start"
    )
    assert start["environments"] == start["dynamicTools"] == []
    assert start["allowProviderModelFallback"] is False
    assert start["config"]["mcp_servers"] == {"unsafe.local": {"enabled": False}}
    assert "paper" in start["baseInstructions"].lower()
    assert "screenshot" not in start["baseInstructions"].lower()
    assert start["developerInstructions"] == ""
    turn = next(m["params"] for m in server.messages if m.get("method") == "turn/start")
    assert turn["model"] == "gpt-6-astra"
    assert turn["effort"] == "high"
    assert turn["environments"] == []
    assert turn["approvalPolicy"] == "never"
    assert turn["outputSchema"] == PROPOSAL_SCHEMA
    assert [item["type"] for item in turn["input"]] == ["text"]
    assert '"cash_usd":"100000.00"' in turn["input"][0]["text"]
    assert (
        not {"OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"} & calls[-1][1]["env"]
    )
    await worker.close()
    assert server.killed


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("models", ["gpt-6-sol"]),
        ("efforts", ["medium"]),
        ("thread_model", "gpt-6-sol"),
        ("thread_effort", "medium"),
        ("thread_effort", None),
        ("account_type", "apiKey"),
        ("version_text", b"codex-cli 999\n"),
    ],
)
async def test_start_failures_never_run_inference(
    fake_codex, tmp_path, attribute, value
):
    server, _ = fake_codex
    setattr(server, attribute, value)
    worker = PaperMarketWorker(binary="/fake/codex")
    with pytest.raises(PaperMarketWorkerError):
        await worker.start(tmp_path)
    assert server.turns == 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.update(model="gpt-6-sol"),
        lambda c: c.update(model_reasoning_effort="ultra"),
        lambda c: c["features"].update(hooks=True),
        lambda c: c.update(openai_base_url="https://example.invalid"),
    ],
)
async def test_config_overrides_fail_before_thread(fake_codex, tmp_path, mutation):
    server, _ = fake_codex
    server.config_mutation = mutation
    with pytest.raises(PaperMarketWorkerError):
        await PaperMarketWorker(binary="/fake/codex").start(tmp_path)
    assert not any(m.get("method") == "thread/start" for m in server.messages)
    assert server.killed


def event(method, **params):
    return {
        "method": method,
        "params": {"threadId": "paper-thread", "turnId": "turn-1", **params},
    }


@pytest.mark.parametrize(
    "notification",
    [
        event("model/rerouted", toModel="gpt-6-sol"),
        event(
            "thread/settings/updated",
            threadSettings={"model": "gpt-6-astra", "effort": "ultra"},
        ),
        event("item/started", item={"type": "commandExecution"}),
        {"id": 900, **event("item/commandExecution/requestApproval")},
    ],
)
async def test_unexpected_tools_or_settings_close_worker(
    fake_codex, tmp_path, notification
):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.notifications = [notification]
    with pytest.raises(PaperMarketWorkerError):
        await worker.propose({})
    assert server.killed
    if "id" in notification:
        assert any(m.get("id") == 900 and "error" in m for m in server.messages)


async def test_completed_turn_tool_item_rejected(fake_codex, tmp_path):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.final_items = [
        {"type": "commandExecution", "command": "secret"},
        {"type": "agentMessage", "text": server.final_text},
    ]
    with pytest.raises(PaperMarketWorkerError) as error:
        await worker.propose({})
    assert "secret" not in str(error.value)
    assert server.killed


@pytest.mark.parametrize(
    "output",
    [
        "not-json",
        "[]",
        '{"actions":[],"actions":[],"rationale":"hold"}',
        '{"actions":[],"rationale":"hold","command":"secret"}',
        '{"actions":[],"rationale":""}',
        '{"actions":[],"rationale":"' + "s" * 2100 + '"}',
        json.dumps(
            {
                "actions": [
                    {
                        "symbol": "BTC",
                        "side": "buy",
                        "notional_usd": "10",
                        "quantity": None,
                    }
                ],
                "rationale": "buy",
            }
        ),
        json.dumps(
            {
                "actions": [
                    {
                        "symbol": "AAPL",
                        "side": "buy",
                        "notional_usd": "10",
                        "quantity": "1",
                    }
                ],
                "rationale": "buy",
            }
        ),
        json.dumps(
            {
                "actions": [
                    {
                        "symbol": "AAPL",
                        "side": "sell",
                        "notional_usd": None,
                        "quantity": "-1",
                    }
                ],
                "rationale": "sell",
            }
        ),
        json.dumps(
            {
                "actions": [
                    {
                        "symbol": "AAPL",
                        "side": "buy",
                        "notional_usd": "NaN",
                        "quantity": None,
                    }
                ],
                "rationale": "buy",
            }
        ),
        json.dumps(
            {
                "actions": [
                    {
                        "symbol": "AAPL",
                        "side": "buy",
                        "notional_usd": 10,
                        "quantity": None,
                    }
                ],
                "rationale": "buy",
            }
        ),
    ],
)
async def test_invalid_proposals_fail_closed(fake_codex, tmp_path, output):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.final_text = output
    with pytest.raises(PaperMarketWorkerError) as error:
        await worker.propose({})
    assert "secret" not in str(error.value)
    assert server.killed


async def test_buy_sell_proposals_and_matching_turn_usage(fake_codex, tmp_path):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    actions = [
        {"symbol": "SPY", "side": "buy", "notional_usd": "100.50", "quantity": None},
        {"symbol": "AAPL", "side": "sell", "notional_usd": None, "quantity": "0.25"},
    ]
    server.final_text = json.dumps({"actions": actions, "rationale": "Rebalance."})
    counts = {
        "inputTokens": 200,
        "outputTokens": 80,
        "totalTokens": 280,
        "cachedInputTokens": 20,
        "reasoningOutputTokens": 50,
    }
    usage = {"last": counts, "total": counts, "modelContextWindow": 1000000}
    server.notifications = [
        event("thread/tokenUsage/updated", tokenUsage=usage),
        event("thread/tokenUsage/updated", turnId="stale-turn", tokenUsage={}),
        event(
            "item/completed",
            threadId="stale-thread",
            item={"type": "agentMessage", "text": "not json"},
        ),
    ]
    result = await worker.propose({"cash_usd": "1000.00"})
    assert result["proposal"]["actions"] == actions
    assert result["usage"] == usage
    server.notifications = []
    assert (await worker.propose({}))["usage"] is None
    await worker.close()


@pytest.mark.parametrize(
    "value", [None, "0", "0.00", "1e3", "01", "1.0 ", "Infinity", True]
)
async def test_sizes_must_be_positive_plain_decimal_strings(
    fake_codex, tmp_path, value
):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.final_text = json.dumps(
        {
            "actions": [
                {
                    "symbol": "SPY",
                    "side": "buy",
                    "notional_usd": value,
                    "quantity": None,
                }
            ],
            "rationale": "Buy.",
        }
    )
    with pytest.raises(PaperMarketWorkerError):
        await worker.propose({})
    assert server.killed


async def test_no_more_than_four_actions(fake_codex, tmp_path):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    action = {"symbol": "SPY", "side": "buy", "notional_usd": "10", "quantity": None}
    server.final_text = json.dumps({"actions": [action] * 5, "rationale": "Buy."})
    with pytest.raises(PaperMarketWorkerError):
        await worker.propose({})
    assert server.killed


async def test_failed_turn_returns_no_proposal(fake_codex, tmp_path):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.turn_status = "failed"
    with pytest.raises(PaperMarketWorkerError, match="complete"):
        await worker.propose({})
    assert server.killed


async def test_verified_settings_update_preserves_worker(fake_codex, tmp_path):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.notifications = [
        event(
            "thread/settings/updated",
            threadSettings={
                "model": "gpt-6-astra",
                "effort": "high",
                "modelProvider": "openai",
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            },
        )
    ]
    assert (await worker.propose({}))["proposal"]["actions"] == []
    await worker.close()


@pytest.mark.parametrize(
    "context", [[], {"value": float("nan")}, {"value": "x" * 140000}]
)
async def test_invalid_context_never_starts_turn(fake_codex, tmp_path, context):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    with pytest.raises(PaperMarketWorkerError):
        await worker.propose(context)
    assert server.turns == 0
    assert server.killed


async def test_timeout_kills_worker(fake_codex, tmp_path):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex", timeout=0.05)
    await worker.start(tmp_path)
    server.hang_turn = True
    with pytest.raises(PaperMarketWorkerError, match="timed out"):
        await worker.propose({})
    assert server.killed


async def test_cancellation_kills_worker(fake_codex, tmp_path):
    server, _ = fake_codex
    worker = PaperMarketWorker(binary="/fake/codex")
    await worker.start(tmp_path)
    server.hang_turn = True
    task = asyncio.create_task(worker.propose({}))
    for _ in range(20):
        if server.turns:
            break
        await asyncio.sleep(0)
    assert server.turns == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert server.killed
