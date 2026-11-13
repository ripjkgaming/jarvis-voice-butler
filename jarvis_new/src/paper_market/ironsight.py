"""Adapter for the local IRONSIGHT news JSON route."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from typing import Any
from urllib.parse import urlsplit


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class IronSightAdapter:
    """Read the local IRONSIGHT JSON API. No credentials are required by this installation."""

    def __init__(self, url: str | None = None, timeout: float = 12):
        self.url = url or os.environ.get(
            "IRONSIGHT_NEWS_URL", "http://127.0.0.1:3000/api/news"
        )
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("IRONSIGHT_NEWS_URL must be an HTTP(S) URL")
        self.timeout = timeout

    def fetch(self) -> tuple[list[dict[str, Any]], str]:
        request = urllib.request.Request(
            self.url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Jarvis-PaperMarket/1",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"IRONSIGHT returned HTTP {response.status}")
            payload = json.loads(response.read(2_000_001))
        if not isinstance(payload, list):
            raise ValueError("IRONSIGHT news response is not a JSON array")
        items = []
        for item in payload[:1000]:
            if isinstance(item, dict) and isinstance(item.get("title"), str):
                items.append(
                    {
                        k: item.get(k)
                        for k in ("title", "link", "source", "pubDate", "category")
                    }
                )
        # Opaque fingerprint checkpoint; endpoint has no cursor. Full snapshot is replayed safely.
        marker = hashlib.sha256(_json(items).encode()).hexdigest()
        return items, marker
