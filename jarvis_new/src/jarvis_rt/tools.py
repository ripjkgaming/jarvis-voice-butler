# Portions derived from livekit-agents (Copyright 2023 LiveKit, Inc.),
# licensed under the Apache License, Version 2.0.
"""Function-tool decorator, errors and schema generation (no LiveKit)."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Flag, auto
from typing import Any, get_type_hints

from pydantic import BaseModel, create_model
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined


class ToolError(Exception):
    """Raised by a tool; the message is shown to the model."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self._message = message

    @property
    def message(self) -> str:
        return self._message


class StopResponse(Exception):
    """Raised by a tool: run it, but do not speak a follow-up."""


class ToolFlag(Flag):
    NONE = 0
    IGNORE_ON_ENTER = auto()
    CANCELLABLE = auto()


@dataclass
class FunctionToolInfo:
    name: str
    description: str | None
    flags: ToolFlag = ToolFlag.NONE


@dataclass
class RawFunctionToolInfo:
    name: str
    raw_schema: dict[str, Any]
    flags: ToolFlag = ToolFlag.NONE


class _BaseFunctionTool:
    def __init__(self, func: Callable[..., Any], info: Any, instance: Any = None) -> None:
        functools.update_wrapper(self, func)
        self._func = func
        self._info = info
        self._instance = instance

    @property
    def id(self) -> str:
        return self._info.name

    @property
    def info(self):
        return self._info

    def __get__(self, obj: Any, objtype: type | None = None):
        if obj is None:
            return self
        bound = self.__class__(self._func, self._info, instance=obj)
        sig = inspect.signature(self._func)
        bound.__signature__ = sig.replace(parameters=list(sig.parameters.values())[1:])
        return bound

    def __call__(self, *args: Any, **kwargs: Any):
        if self._instance is not None:
            return self._func(self._instance, *args, **kwargs)
        return self._func(*args, **kwargs)


class FunctionTool(_BaseFunctionTool):
    pass


class RawFunctionTool(_BaseFunctionTool):
    pass


class RunContext:
    """Per-call context handed to tools declaring a ``RunContext`` parameter."""

    def __init__(self, *, session=None, speech_handle=None, function_call=None, userdata=None):
        self.session = session
        self.speech_handle = speech_handle
        self.function_call = function_call
        self._userdata = userdata

    @property
    def userdata(self) -> Any:
        return self._userdata if self._userdata is not None else getattr(self.session, "userdata", None)


def function_tool(
    f: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    raw_schema: dict[str, Any] | None = None,
    flags: ToolFlag = ToolFlag.NONE,
):
    def deco_raw(func):
        assert raw_schema is not None
        if not raw_schema.get("name"):
            raise ValueError("raw function name cannot be empty")
        if "parameters" not in raw_schema:
            raise ValueError("raw function description must contain a parameters key")
        return RawFunctionTool(
            func, RawFunctionToolInfo(raw_schema["name"], {**raw_schema}, flags)
        )

    def deco_func(func):
        from docstring_parser import parse_from_object

        doc = parse_from_object(func)
        return FunctionTool(
            func,
            FunctionToolInfo(name or func.__name__, description or doc.description, flags),
        )

    if f is not None:
        return deco_raw(f) if raw_schema is not None else deco_func(f)
    return deco_raw if raw_schema is not None else deco_func


def is_function_tool(f: Any) -> bool:
    return isinstance(f, FunctionTool)


def is_raw_function_tool(f: Any) -> bool:
    return isinstance(f, RawFunctionTool)


def get_function_info(f: FunctionTool) -> FunctionToolInfo:
    return f.info


def find_function_tools(obj: Any) -> list[FunctionTool | RawFunctionTool]:
    out: list[FunctionTool | RawFunctionTool] = []
    for _, member in inspect.getmembers(obj):
        if isinstance(member, (FunctionTool, RawFunctionTool)):
            out.append(member)
    return out


def _is_context_type(hint: Any) -> bool:
    origin = getattr(hint, "__origin__", None)
    if origin is not None and getattr(hint, "__metadata__", None):
        hint = origin
    # name check also covers the legacy livekit RunContext during migration
    return isinstance(hint, type) and any(c.__name__ == "RunContext" for c in hint.__mro__)


def arguments_model(func: Callable[..., Any]) -> type[BaseModel]:
    """Pydantic model of a tool's non-context arguments."""
    from docstring_parser import parse_from_object

    model_name = "".join(x.capitalize() for x in func.__name__.split("_")) + "Args"
    docs = {p.arg_name: p.description for p in parse_from_object(func).params}
    hints = get_type_hints(func, include_extras=True)
    fields: dict[str, Any] = {}
    for pname, param in inspect.signature(func).parameters.items():
        hint = hints[pname]
        if _is_context_type(hint):
            continue
        default = param.default if param.default is not param.empty else ...
        info = FieldInfo.from_annotation(hint)
        if default is not ... and info.default is PydanticUndefined:
            info.default = default
        if info.description is None:
            info.description = docs.get(pname)
        fields[pname] = (info.annotation, info)
    return create_model(model_name, **fields)


def tool_schema(tool: FunctionTool | RawFunctionTool) -> dict[str, Any]:
    """Internally tagged JSON schema: name/description/parameters/type."""
    if isinstance(tool, RawFunctionTool):
        return tool.info.raw_schema
    return {
        "name": tool.info.name,
        "description": tool.info.description or "",
        "parameters": arguments_model(tool).model_json_schema(),
        "type": "function",
    }
