"""Prepare Gemini local startup work without blocking the voice loop.

API reference: https://docs.livekit.io/agents/models/realtime/plugins/gemini/
The per-session adapter follows the installed plugin's update_tools contract;
no dependency files or tool execution/permission behavior are changed.
"""

from __future__ import annotations

import asyncio
import os
import ssl
from copy import deepcopy

import certifi
from google.genai import types
from livekit.agents import llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from livekit.plugins.google import utils as _google_utils  # noqa: F401

# Schema conversion lazily imports this plugin. Register it on the importing
# main thread before _prepare runs in a worker; Plugin forbids worker imports.


def http_options() -> types.HttpOptions:
    """Same verified TLS defaults as google-genai, constructed by the worker."""
    context = ssl.create_default_context(
        cafile=os.environ.get("SSL_CERT_FILE", certifi.where()),
        capath=os.environ.get("SSL_CERT_DIR"),
    )
    return types.HttpOptions(
        timeout=int(DEFAULT_API_CONNECT_OPTIONS.timeout * 1000),
        async_client_args={"ssl": context},
    )


def _prepare(tools, behavior):
    context = llm.ToolContext(tools)
    options = {
        "tool_behavior": behavior.value if behavior else None,
        "use_parameters_json_schema": False,
    }
    schemas = context.parse_function_tools("google", **options)
    return context, options, schemas


def build_assistant(factory, browser):
    """Worker entry point: prepare initial schemas before any session exists.

    Initial update_tools must not yield: otherwise the SDK connection task can
    connect with an empty tool set and immediately need another reconnect.
    """
    assistant = factory(browser)
    model = assistant.llm
    models = getattr(model, "_models", [model])
    prepared = {}
    for voice in models:
        if getattr(voice, "_jarvis_startup_adapter", False):
            behavior = voice._opts.tool_behavior
            key = behavior.value if behavior else None
            if key not in prepared:
                prepared[key] = _prepare(assistant.tools, behavior)
            voice._jarvis_prepared_tools = prepared[key]
    return assistant


def prepare_session(session, initial=None):
    """Compile each updated tool set in a worker before requesting reconnect.

    Cache is scoped to this exact ToolContext and provider options. An update
    replaces it; changed provider options fall through to the SDK. Copies stop
    provider conversion from mutating the next reconnect's declarations.
    """
    original = session.update_tools
    lock = asyncio.Lock()

    async def update_tools(tools):
        async with lock:
            incoming = llm.ToolContext(tools)
            behavior = session._opts.tool_behavior
            behavior_key = behavior.value if behavior else None
            if (
                initial is not None
                and incoming == initial[0]
                and initial[1]["tool_behavior"] == behavior_key
            ):
                context, options, schemas = initial
            else:
                context, options, schemas = await asyncio.to_thread(
                    _prepare, list(tools), behavior
                )
            await original(tools)
            if session._tools != context:
                raise RuntimeError("Gemini tools changed during schema preparation")
            parse = getattr(
                session._tools,
                "_jarvis_uncached_parse",
                session._tools.parse_function_tools,
            )
            session._tools._jarvis_uncached_parse = parse

            def cached(provider, **kwargs):
                if provider == "google" and kwargs == options:
                    return deepcopy(schemas)
                return parse(provider, **kwargs)

            session._tools.parse_function_tools = cached

    session.update_tools = update_tools
    return session


def install(model):
    make = model.session

    def session(*args, **kwargs):
        return prepare_session(
            make(*args, **kwargs), getattr(model, "_jarvis_prepared_tools", None)
        )

    model._jarvis_startup_adapter = True
    model.session = session
    return model
