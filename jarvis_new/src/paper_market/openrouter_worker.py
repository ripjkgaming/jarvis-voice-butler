"""Exact-model OpenRouter transport. Keys stay on the server; no fallback/tools."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path

import openrouter_chat

from .worker import _PAPER_INSTRUCTIONS, PROPOSAL_SCHEMA, _validate_proposal

MODEL = "dots-studio/dots-3-note-preview:free"
EFFORT = "default"


class OpenRouterWorker:
    def __init__(self, timeout=90, *, opener=None, key=None):
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
        self.key = key

    async def start(self, workspace: Path):
        if not (self.key or openrouter_chat.api_key()):
            raise RuntimeError("OpenRouter API key is not configured.")

    async def close(self):
        pass

    async def propose(self, context):
        return await asyncio.to_thread(self._request, context)

    def _request(self, context):
        key = self.key or openrouter_chat.api_key()
        if not key:
            raise RuntimeError("OpenRouter API key is not configured.")
        payload = {
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": _PAPER_INSTRUCTIONS
                    + "\nFor historical_daily_replay use only supplied daily history; choose a small daily position or hold. Ignore intraday requirements in that mode. The next session is hidden. In preflight mode return actions=[].",
                },
                {"role": "user", "content": json.dumps(context, allow_nan=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "paper_proposal",
                    "strict": True,
                    "schema": PROPOSAL_SCHEMA,
                },
            },
            "max_tokens": 4096,
            "temperature": 0,
            "provider": {"allow_fallbacks": False},
        }
        request = urllib.request.Request(
            openrouter_chat.URL,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read(262145)
            if len(raw) > 262144:
                raise RuntimeError("OpenRouter response exceeded limit.")
            data = json.loads(raw)
            if data.get("model") != MODEL:
                raise RuntimeError(
                    "OpenRouter returned a different model; proposal rejected."
                )
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise RuntimeError("OpenRouter proposal was incomplete.")
            proposal = _validate_proposal(choice["message"]["content"])
            return {
                "model": MODEL,
                "effort": EFFORT,
                "proposal": proposal,
                "usage": data.get("usage"),
                "verification": {
                    "provider": "openrouter",
                    "model_verified": True,
                    "effort_verified": True,
                    "tools_disabled": True,
                    "turn_id": data["id"],
                    "transport": "openrouter-chat-v1",
                },
            }
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"OpenRouter request failed (HTTP {error.code})."
            ) from None
        except (KeyError, ValueError, TypeError, IndexError):
            raise RuntimeError(
                "OpenRouter returned an invalid proposal response."
            ) from None
        except (OSError, TimeoutError):
            raise RuntimeError(
                "OpenRouter request timed out or could not connect."
            ) from None
