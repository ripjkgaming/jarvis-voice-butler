# Portions derived from livekit-agents (Copyright 2023 LiveKit, Inc.),
# licensed under the Apache License, Version 2.0.
"""Tool-call execution: argument parsing/validation, errors, handoff results."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any, get_type_hints

import pydantic

from .chat_context import FunctionCall, FunctionCallOutput
from .tools import (
    FunctionTool,
    RawFunctionTool,
    RunContext,
    StopResponse,
    ToolError,
    _is_context_type,
    arguments_model,
)

log = logging.getLogger("jarvis_rt.executor")

_VALID = (str, int, float, bool, complex, type(None))


def _valid_output(value: Any) -> bool:
    if isinstance(value, _VALID):
        return True
    if isinstance(value, (list, set, frozenset, tuple)):
        return all(_valid_output(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, _VALID) and _valid_output(v) for k, v in value.items())
    return False


def parse_arguments(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value: Any = json.loads(raw or "{}")
    except ValueError as strict_err:
        try:
            import json_repair

            value = json_repair.loads(raw)
        except Exception:
            value = ""
        if value == "":
            raise ValueError(
                f"could not parse function arguments as JSON: {strict_err}: {raw[:200]}"
            ) from strict_err
    while isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            raise ValueError(
                f"function arguments decoded to a non-JSON string: {value[:200]}"
            ) from None
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(
            f"function arguments must be a JSON object, got {type(value).__name__}"
        )
    return value


def prepare_arguments(
    tool: FunctionTool | RawFunctionTool, args: dict[str, Any], ctx: RunContext | None
) -> tuple[tuple, dict]:
    sig = inspect.signature(tool)
    hints = get_type_hints(tool, include_extras=True)
    if isinstance(tool, RawFunctionTool):
        fields: dict[str, Any] = {"raw_arguments": args}
    else:
        model = arguments_model(tool)(**args)
        fields = {n: getattr(model, n) for n in type(model).model_fields}
    for pname in sig.parameters:
        if ctx is not None and _is_context_type(hints[pname]):
            fields[pname] = ctx
    bound = sig.bind(**fields)
    bound.apply_defaults()
    return bound.args, bound.kwargs


@dataclass
class ToolResult:
    call: FunctionCall
    output: FunctionCallOutput
    agent: Any = None
    raw_output: Any = None
    raw_exception: BaseException | None = None


def _error_output(call: FunctionCall, text: str, **kw: Any) -> FunctionCallOutput:
    return FunctionCallOutput(
        name=call.name, call_id=call.call_id, output=text, is_error=True, **kw
    )


def make_result(
    call: FunctionCall, output: Any, exception: BaseException | None
) -> ToolResult:
    from .agent import Agent

    if isinstance(output, BaseException):
        exception, output = output, None
    if isinstance(exception, ToolError):
        return ToolResult(
            call, _error_output(call, exception.message), None, output, exception
        )
    if isinstance(exception, StopResponse):
        out = FunctionCallOutput(
            name=call.name,
            call_id=call.call_id,
            output="",
            is_error=False,
            reply_required=False,
        )
        return ToolResult(call, out, None, output, exception)
    if exception is not None:
        return ToolResult(
            call,
            _error_output(call, "An internal error occurred"),
            None,
            output,
            exception,
        )

    agent = None
    value = output
    if isinstance(output, (list, set, frozenset, tuple)):
        agents = [i for i in output if isinstance(i, Agent)]
        others = [i for i in output if not isinstance(i, Agent)]
        if len(agents) > 1:
            return ToolResult(
                call,
                _error_output(call, "the tool returned more than one agent"),
                None,
                output,
            )
        agent = agents[0] if agents else None
        value = (
            others
            if agent is None
            else (None if not others else others[0] if len(others) == 1 else others)
        )
    elif isinstance(output, Agent):
        agent, value = output, None

    if not _valid_output(value):
        return ToolResult(
            call,
            _error_output(call, "the tool returned an invalid output"),
            None,
            output,
        )
    out = FunctionCallOutput(
        name=call.name,
        call_id=call.call_id,
        output="" if value is None else str(value),
        is_error=False,
        reply_required=value is not None,
    )
    return ToolResult(call, out, agent, output)


async def execute_call(
    call: FunctionCall, tools: dict[str, Any], ctx: RunContext | None = None
) -> ToolResult:
    tool = tools.get(call.name)
    if tool is None:
        msg = f"Unknown function: {call.name} - available tools: {', '.join(tools)}"
        return ToolResult(call, _error_output(call, msg), None, None, ValueError(msg))
    try:
        args = parse_arguments(call.arguments or "{}")
    except ValueError as e:
        return make_result(
            call, None, ToolError(f"Error parsing arguments for `{call.name}`: {e}")
        )
    try:
        try:
            fargs, fkwargs = prepare_arguments(tool, args, ctx)
        except ToolError:
            raise
        except (pydantic.ValidationError, ValueError, TypeError) as e:
            raise ToolError(f"Error parsing arguments for `{call.name}`: {e}") from e
        result = tool(*fargs, **fkwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return make_result(call, result, None)
    except Exception as e:
        if not isinstance(e, (ToolError, StopResponse)):
            log.exception("exception executing tool %s", call.name)
        return make_result(call, None, e)
