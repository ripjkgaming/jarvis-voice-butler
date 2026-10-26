"""Headless Codex CLI (ChatGPT plan) as a LangChain chat model for TradingAgents.

Every call is one ``codex exec`` turn in an empty scratch directory with a read-only
sandbox; the prompt forbids tool use. Tool calling and structured output are
emulated with JSON replies. No API key; auth is the existing ~/.codex login.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

CODEX = os.environ.get("CODEX_BIN", "/usr/lib/chatgpt/resources/codex")
_slots = threading.Semaphore(int(os.environ.get("CODEX_PARALLEL", "2")))


def _render(messages: list[BaseMessage]) -> str:
    parts = []
    for m in messages:
        role = {"human": "USER", "ai": "ASSISTANT", "system": "SYSTEM", "tool": "TOOL RESULT"}.get(m.type, m.type.upper())
        text = m.content if isinstance(m.content, str) else json.dumps(m.content, default=str)
        if m.type == "ai" and getattr(m, "tool_calls", None):
            text += "\n[tool calls: " + json.dumps(m.tool_calls, default=str) + "]"
        parts.append(f"## {role}\n{text}")
    return "\n\n".join(parts)


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise
        return json.JSONDecoder().raw_decode(text[start:])[0]


class CodexChat(BaseChatModel):
    model: str = "gpt-6.1-sol"
    effort: str = "medium"
    timeout: int = 600
    tools: list[dict] | None = None

    @property
    def _llm_type(self) -> str:
        return "codex-exec"

    def _run(self, prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="codex-llm-") as scratch:
            out = os.path.join(scratch, "last.txt")
            command = [
                CODEX, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                "-m", self.model, "-c", f"model_reasoning_effort={self.effort}",
                "-C", scratch, "-o", out, "-",
            ]
            for attempt in range(8):
                with _slots:
                    done = subprocess.run(
                        command, input=prompt, text=True, capture_output=True,
                        timeout=self.timeout, cwd=scratch,
                    )
                if os.path.exists(out) and os.path.getsize(out):
                    return open(out, encoding="utf-8").read()
                time.sleep(min(30 * (attempt + 1), 180))  # 429 / transient: back off
            raise RuntimeError(f"codex exec failed: {done.stderr[-200:]}")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        tools = kwargs.get("tools") or self.tools
        head = "Answer directly from the conversation below. Never run shell commands or use any tool of your own.\n\n"
        if tools:
            specs = [
                {"name": t["function"]["name"], "description": t["function"].get("description", ""),
                 "parameters": t["function"].get("parameters", {})}
                for t in tools
            ]
            head += (
                "You may call these data tools:\n" + json.dumps(specs) + "\n\n"
                'Reply with ONLY a JSON object: {"tool_calls":[{"name":"<tool>","args":{...}}]} to call tools '
                '(several allowed), or {"content":"<your final answer>"} when you need no more data.\n\n'
            )
        text = self._run(head + _render(messages))
        if not tools:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text.strip()))])
        try:
            data = _extract_json(text)
            calls = [
                {"name": c["name"], "args": c.get("args") or {}, "id": f"call_{i}_{abs(hash(text)) % 10**6}", "type": "tool_call"}
                for i, c in enumerate(data.get("tool_calls") or [])
            ]
            message = AIMessage(content=data.get("content") or "", tool_calls=calls)
        except (ValueError, AttributeError, KeyError, TypeError):
            message = AIMessage(content=text.strip())
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        from langchain_core.utils.function_calling import convert_to_openai_tool

        return self.model_copy(update={"tools": [convert_to_openai_tool(t) for t in tools]})

    def with_structured_output(self, schema, **kwargs):
        json_schema = schema.model_json_schema() if hasattr(schema, "model_json_schema") else schema

        def run(value):
            messages = value.to_messages() if hasattr(value, "to_messages") else value
            if isinstance(messages, str):
                from langchain_core.messages import HumanMessage

                messages = [HumanMessage(content=messages)]
            prompt = (
                "Answer directly. Never run commands or use tools.\n"
                "Reply with ONLY one JSON object matching this JSON schema (no prose, no fences):\n"
                + json.dumps(json_schema) + "\n\n" + _render(messages)
            )
            last = None
            for _ in range(2):
                try:
                    data = _extract_json(self._run(prompt))
                    return schema.model_validate(data) if hasattr(schema, "model_validate") else data
                except Exception as error:  # retry once on malformed JSON
                    last = error
            raise last

        return RunnableLambda(run)


class CodexClient:
    """Duck-types TradingAgents' BaseLLMClient."""

    def __init__(self, model: str, base_url=None, **kwargs):
        self.model = model

    def get_llm(self):
        name, _, effort = self.model.partition(":")
        return CodexChat(model=name, effort=effort or "medium")

    def validate_model(self) -> bool:
        return True

    def warn_if_unknown_model(self) -> None:
        return None


def install() -> None:
    """Route every TradingAgents tier through the headless Codex CLI."""
    from tradingagents.llm_clients import factory

    factory.create_llm_client = lambda provider, model, base_url=None, **kw: CodexClient(model)
