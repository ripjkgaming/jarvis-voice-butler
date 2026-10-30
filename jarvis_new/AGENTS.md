# AGENTS.md

This is the Jarvis voice butler. Its voice runtime is the in-repo package
`src/jarvis_rt`. LiveKit has been removed; do not add LiveKit packages, LiveKit
docs or LiveKit CLI steps. See @README.md for features and setup, and
@docs/voice-transport.md for the WebSocket hub protocol.

## Project structure

This Python project uses the `uv` package manager. Always use `uv` to install
dependencies, run the agent and run tests.

- `src/agent.py` is the entrypoint. Keep it: the Dockerfile runs
  `uv run src/agent.py start`.
- `src/jarvis_rt/` is the voice runtime: agent sessions (`session.py`,
  `agent.py`), tools (`tools.py`, `executor.py`), the Gemini Live client
  (`live.py`), local VAD (`vad.py`), and the WebSocket voice hub
  (`transport/`). The hub listens on `127.0.0.1:$JARVIS_VOICE_PORT`
  (default 7880).
- Other app code lives in `src/`. Tests are in `tests/`; the `rt_` prefixed
  files cover the runtime.

Common commands:

```console
uv sync                              # add --extra chess / --extra needle as needed
uv run python src/agent.py dev       # serve the voice hub (also: console, start)
uv run ruff format && uv run ruff check
uv run pytest                        # -m "not slow" skips real whisper/Piper loads
```

Maintain code formatting with `uv run ruff format` and `uv run ruff check`.

## Voice runtime

- Remote access (phone or another machine) goes through a tunnel to the
  loopback hub. Never bind the hub to a public address or open a public port.
- Changes to frames, topics, call tokens or close codes must update
  `docs/voice-transport.md` in the same change.
- Client URL: `JARVIS_VOICE_URL` (ws:// or wss://) overrides the default
  `ws://127.0.0.1:<port>/voice`.

## Handoffs and tasks

Voice agents are sensitive to latency. Keep the context and tool list of each
step small. Split multi-phase conversations into handoffs between agents (see
`AgentHandoff` in `src/jarvis_rt/chat_context.py` and `session.py`) instead of
writing one long instruction prompt that covers several phases.

## Testing

When possible, add tests for agent behavior, and refer to existing tests in
`tests/` (for example `tests/test_rt_executor.py`).

Important: when modifying core agent behavior such as instructions, tool
descriptions, or tasks/handoffs, never guess what will work. Use test-driven
development (TDD): write the tests for the desired behavior first, then iterate
until they pass. For example, to add a new tool, write tests for its behavior
first.

Tests that call a real Gemini model are marked `live_llm` and are skipped unless
`JARVIS_LIVE_LLM=1`. Run the suite with `uv run pytest`.
