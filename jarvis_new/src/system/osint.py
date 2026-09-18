"""Passive OSINT tools: DNS, whois, certificate transparency, email footprint.

No scope gate, no voice confirm — public read-only data. Still local-only
(JARVIS_LOCAL=1) and logged. Shodan/Censys degrade gracefully until keys
land in ~/.jarvis/keys.env (never logged, never spoken).
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

from system import LocalSystemError, log_action, require_local, run_cmd

KEYS_DEFAULT = Path.home() / ".jarvis" / "keys.env"
CRT_URL = "https://crt.sh/?q={domain}&output=json"

_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _keys_path() -> Path:
    override = os.environ.get("JARVIS_KEYS_FILE", "").strip()
    return Path(override) if override else KEYS_DEFAULT


def _read_keys() -> dict[str, str]:
    """Keys from env, supplemented by keys.env file. Never logged."""
    out = {
        "SHODAN_API_KEY": os.environ.get("SHODAN_API_KEY", "").strip(),
        "CENSYS_API_ID": os.environ.get("CENSYS_API_ID", "").strip(),
        "CENSYS_API_SECRET": os.environ.get("CENSYS_API_SECRET", "").strip(),
    }
    try:
        for line in _keys_path().read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("\"'")
            if k in out and not out[k] and v:
                out[k] = v
    except OSError:
        pass
    return out


def is_valid_domain(domain: str) -> bool:
    return bool(_DOMAIN_RE.match((domain or "").strip().lower().rstrip(".")))


def parse_crt_hosts(data: list[dict] | str) -> list[str]:
    """Deduplicated sorted hostnames from crt.sh JSON. Pure."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return []
    if not isinstance(data, list):
        return []
    hosts: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        for key in ("name_value", "common_name"):
            val = row.get(key) or ""
            for part in str(val).splitlines():
                h = part.strip().lower().lstrip("*.")
                if h and " " not in h:
                    hosts.add(h[:253])
    return sorted(hosts)


def parse_holehe_hits(output: str) -> list[str]:
    """Service names with [+] hits from holehe text. Pure."""
    hits = []
    for line in (output or "").splitlines():
        if "[+]" in line:
            name = line.split("[+]", 1)[1].strip().split()[0:2]
            hits.append(" ".join(name)[:80])
    return hits[:50]


def _fetch_crt(domain: str, timeout: float = 20.0) -> list[dict]:
    req = urllib.request.Request(
        CRT_URL.format(domain=domain), headers={"User-Agent": "jarvis-butler/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode(errors="replace"))


class OsintTools:
    """Passive recon tools. Register via .tools on Assistant + SystemAgent."""

    @property
    def tools(self) -> list:
        return [
            self.dns_recon,
            self.whois_lookup,
            self.crt_enumerate,
            self.email_footprint,
            self.shodan_lookup,
            self.censys_lookup,
        ]

    @function_tool()
    async def dns_recon(self, context: RunContext, domain: str) -> dict[str, str]:
        """Passive DNS: A/AAAA/MX/TXT/NS records plus an AXFR attempt note.

        Args:
            domain: e.g. example.com.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        domain = (domain or "").strip().lower().rstrip(".")[:253]
        if not is_valid_domain(domain):
            raise ToolError(f"{domain or 'that'} is not a valid domain.")
        sections: dict[str, str] = {}
        for rtype in ("A", "AAAA", "MX", "TXT", "NS"):
            _rc, out, _ = await run_cmd("dig", "+short", domain, rtype, timeout=10.0)
            sections[rtype] = (out or "(none)")[:800]
        _rc, out, _ = await run_cmd(
            "dig", f"@{domain}", domain, "AXFR", "+short", timeout=10.0
        )
        axfr = "refused/empty (expected)" if not (out or "").strip() else out[:200]
        log_action("osint-dns", domain)
        say = f"DNS for {domain}: A {sections['A'][:80]}; MX {sections['MX'][:80]}."
        return {"domain": domain, **sections, "axfr": axfr, "say": say[:500]}

    @function_tool()
    async def whois_lookup(self, context: RunContext, target: str) -> dict[str, str]:
        """Registration data for a domain or IP (expiry watch).

        Args:
            target: Domain or IP address.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        target = (target or "").strip()[:253]
        if not target:
            raise ToolError("Which domain or IP should I look up?")
        _rc, out, _err = await run_cmd("whois", target, timeout=20.0)
        if _rc != 0 or not (out or "").strip():
            raise ToolError(f"Whois gave nothing for {target[:60]}.")
        keep = [ln for ln in out.splitlines() if ln.strip()][:60]
        log_action("osint-whois", target)
        text = "\n".join(keep)[:3000]
        return {"target": target, "text": text, "say": text[:500]}

    @function_tool()
    async def crt_enumerate(self, context: RunContext, domain: str) -> dict[str, str]:
        """Passive subdomains via certificate transparency (crt.sh).

        Args:
            domain: e.g. example.com (leading %. handled automatically).
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        domain = (domain or "").strip().lower().lstrip("%.")[:253]
        if not is_valid_domain(domain):
            raise ToolError(f"{domain or 'that'} is not a valid domain.")
        try:
            data = await __import__("asyncio").to_thread(_fetch_crt, domain)
        except Exception as exc:
            raise ToolError(f"Certificate search failed: {exc}") from exc
        hosts = parse_crt_hosts(data if isinstance(data, list) else [])
        log_action("osint-crt", f"{domain} {len(hosts)}")
        shown = "; ".join(hosts[:20])
        return {
            "domain": domain,
            "count": str(len(hosts)),
            "hosts": shown,
            "say": f"{len(hosts)} names for {domain}: {shown[:300] or 'none'}.",
        }

    @function_tool()
    async def email_footprint(self, context: RunContext, email: str) -> dict[str, str]:
        """Which accounts exist for an email address (holehe).

        Args:
            email: Address to check.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        email = (email or "").strip()[:254]
        if not _EMAIL_RE.match(email):
            raise ToolError(f"{email or 'that'} is not a valid email.")
        _rc, out, _err = await run_cmd("holehe", email, timeout=60.0)
        if _rc != 0 and not (out or "").strip():
            raise ToolError(f"Email check failed for {email[:40]}.")
        hits = parse_holehe_hits(out or "")
        log_action("osint-email", email)
        return {
            "email": email,
            "hits": "; ".join(hits)[:1000],
            "say": (
                f"{len(hits)} accounts use {email}."
                if hits
                else f"No linked accounts found for {email}."
            ),
        }

    @function_tool()
    async def shodan_lookup(self, context: RunContext, ip: str) -> dict[str, str]:
        """Shodan host summary. Needs SHODAN_API_KEY; degrades without it.

        Args:
            ip: IPv4/IPv6 address.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        key = _read_keys()["SHODAN_API_KEY"]
        if not key:
            return {
                "say": (
                    "Shodan needs an API key, Sir — add SHODAN_API_KEY to "
                    "~/.jarvis/keys.env, or try dns_recon and crt_enumerate "
                    "for the free path."
                ),
                "degraded": "true",
            }
        try:
            import shodan as shodan_lib
        except ImportError:
            raise ToolError("The shodan library is not installed.") from None
        try:
            api = shodan_lib.Shodan(key)
            host = await __import__("asyncio").to_thread(api.host, ip.strip())
        except Exception as exc:
            raise ToolError(f"Shodan lookup failed: {exc}") from exc
        log_action("osint-shodan", ip.strip()[:60])
        ports = str(host.get("ports", []))[:300]
        org = str(host.get("org", "?"))[:120]
        return {
            "ip": ip,
            "org": org,
            "ports": ports,
            "say": f"{ip}: {org}, ports {ports}.",
        }

    @function_tool()
    async def censys_lookup(self, context: RunContext, ip: str) -> dict[str, str]:
        """Censys host summary. Needs CENSYS_API_ID/SECRET; degrades without.

        Args:
            ip: IPv4/IPv6 address.
        """
        try:
            require_local()
        except LocalSystemError as exc:
            raise ToolError(str(exc)) from exc
        keys = _read_keys()
        if not keys["CENSYS_API_ID"] or not keys["CENSYS_API_SECRET"]:
            return {
                "say": (
                    "Censys needs API credentials, Sir — add CENSYS_API_ID "
                    "and CENSYS_API_SECRET to ~/.jarvis/keys.env, or try "
                    "dns_recon and crt_enumerate for the free path."
                ),
                "degraded": "true",
            }
        try:
            from censys.search import CensysHosts
        except ImportError:
            raise ToolError("The censys library is not installed.") from None
        try:
            client = CensysHosts(
                api_id=keys["CENSYS_API_ID"], api_secret=keys["CENSYS_API_SECRET"]
            )
            host = await __import__("asyncio").to_thread(client.view, ip.strip())
        except Exception as exc:
            raise ToolError(f"Censys lookup failed: {exc}") from exc
        log_action("osint-censys", ip.strip()[:60])
        summary = json.dumps(host, default=str)[:1500]
        return {"ip": ip, "summary": summary, "say": f"Censys has data for {ip}."}
