"""Local-only MCP Streamable HTTP discovery. Never calls discovered tools."""

from __future__ import annotations

import ipaddress
import secrets
from urllib.parse import urlsplit

import httpx


def _validate_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
        or parsed.path not in ("/mcp", "/mcp/")
    ):
        raise ValueError("Only explicit local /mcp endpoints are allowed")
    try:
        if not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise ValueError("MCP endpoint is not loopback")
    except (TypeError, ValueError) as exc:
        raise ValueError("MCP endpoint requires numeric loopback IP") from exc
    if parsed.port is None or not (1 <= parsed.port <= 65535):
        raise ValueError("Explicit local port required")


def inspect_server(url: str, *, timeout: float = 2.0) -> dict:
    """Negotiate and list metadata only. Never invoke tools/call.

    Restrict to numeric loopback with redirects, environment proxies and
    external hosts disabled. A local server itself is still untrusted.
    """
    _validate_url(url)
    timeout = min(max(float(timeout), 0.1), 5.0)
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-03-26",
    }
    with httpx.Client(
        timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:

        def request(method, params=None):
            msg = {"jsonrpc": "2.0", "id": secrets.token_hex(8), "method": method}
            if params is not None:
                msg["params"] = params
            response = client.post(url, json=msg, headers=headers)
            response.raise_for_status()
            if response.headers.get("mcp-session-id"):
                headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
            if "application/json" not in response.headers.get("content-type", ""):
                raise ValueError("Unsupported MCP streaming response; JSON required")
            if len(response.content) > 262144:
                raise ValueError("MCP response too large")
            body = response.json()
            if (
                not isinstance(body, dict)
                or body.get("id") != msg["id"]
                or "error" in body
            ):
                raise ValueError("Malformed or rejected MCP response")
            return body.get("result")

        init = request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "jarvis-readonly-inspector", "version": "0.1"},
            },
        )
        if not isinstance(init, dict) or not isinstance(init.get("capabilities"), dict):
            raise ValueError("Missing MCP capability negotiation")
        negotiated = init.get("protocolVersion")
        if negotiated != "2025-03-26":
            raise ValueError("Unsupported protocol version")
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ready = client.post(url, json=notification, headers=headers)
        ready.raise_for_status()
        if "tools" not in init["capabilities"]:
            return {"protocol": negotiated, "tools": [], "status": "connected_no_tools"}
        result = request("tools/list", {})
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise ValueError("Invalid tools/list response")
        tools = []
        for entry in result["tools"][:100]:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            schema = entry.get("inputSchema")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                continue
            tools.append(
                {
                    "name": entry["name"][:100],
                    "description": str(entry.get("description", ""))[:160],
                    "input_schema_valid": True,
                    "executable": False,
                }
            )
        return {"protocol": negotiated, "status": "metadata_received", "tools": tools}


def inspect_approved(allowlist_path, *, inspector=inspect_server):
    """Explicitly approved numeric-loopback servers only; never run tools/call.

    The caller chooses the configuration file and must verify its provenance.
    Opaque errors are deliberate: server responses may contain secrets.
    """
    from .mcp_discovery import load_allowlist

    results = []
    for server in load_allowlist(allowlist_path):
        name = server["name"]
        try:
            data = inspector(server["url"])
            if not isinstance(data, dict) or data.get("status") not in {
                "metadata_received",
                "connected_no_tools",
            }:
                raise ValueError("invalid inspector result")
            results.append(
                {
                    "name": name,
                    "status": data["status"],
                    "protocol": str(data.get("protocol", "unknown"))[:40],
                    "tools": data.get("tools", [])[:100],
                    "executable": False,
                }
            )
        except Exception:
            results.append(
                {
                    "name": name,
                    "status": "connection_unverified",
                    "tools": [],
                    "executable": False,
                }
            )
    return {"servers": results, "checked": len(results), "status": "inspection_only"}
