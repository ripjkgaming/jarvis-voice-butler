"""Manually approved, local-only MCP metadata discovery. No tool calls."""

import ipaddress
import json
from pathlib import Path
from urllib.parse import urlsplit


def _safe_local(url):
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.fragment:
        return False
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except (ValueError, TypeError):
        return False


def load_allowlist(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        return []
    return [
        {"name": s["name"], "url": s["url"]}
        for s in data["servers"]
        if isinstance(s, dict)
        and s.get("approved") is True
        and isinstance(s.get("name"), str)
        and isinstance(s.get("url"), str)
        and len(s["name"]) <= 60
        and _safe_local(s["url"])
    ][:10]


def discover_tools(servers, list_tools=None):
    """Injected reviewed metadata fetcher; no built-in network transport.

    Discovery does not imply permission to run any returned tool.
    """
    results = {"servers": [], "tools": []}
    for server in servers[:10]:
        name, url = server.get("name"), server.get("url")
        if not name or not isinstance(url, str) or not _safe_local(url):
            results["servers"].append({"name": str(name)[:60], "status": "blocked"})
            continue
        if list_tools is None:
            results["servers"].append(
                {"name": name, "status": "transport_not_configured"}
            )
            continue
        try:
            found = list_tools(url)
            if not isinstance(found, list):
                raise ValueError("Invalid MCP listing")
            for tool in found[:100]:
                if (
                    not isinstance(tool, dict)
                    or tool.get("annotations", {}).get("readOnlyHint") is not True
                ):
                    continue
                tool_name = tool.get("name")
                if not isinstance(tool_name, str) or not 0 < len(tool_name) <= 100:
                    continue
                results["tools"].append(
                    {
                        "connector": name,
                        "name": tool_name,
                        "description": str(tool.get("description", ""))[:300],
                        "effect": "read",
                        "executable": False,
                    }
                )
            results["servers"].append({"name": name, "status": "metadata_received"})
        except Exception:
            results["servers"].append({"name": name, "status": "discovery_error"})
    return results
