import pytest

from system.osint import (
    OsintTools,
    is_valid_domain,
    parse_crt_hosts,
    parse_holehe_hits,
)


def test_valid_domain() -> None:
    assert is_valid_domain("example.com")
    assert is_valid_domain("sub.example.co.uk")
    assert not is_valid_domain("not a domain")
    assert not is_valid_domain("")


def test_parse_crt_hosts_dedupes() -> None:
    data = [
        {"name_value": "a.example.com\nwww.example.com"},
        {"name_value": "www.example.com"},
        {"common_name": "*.example.com"},
    ]
    assert parse_crt_hosts(data) == ["a.example.com", "example.com", "www.example.com"]


def test_parse_crt_hosts_bad_input() -> None:
    assert parse_crt_hosts("not json") == []
    assert parse_crt_hosts({}) == []  # type: ignore[arg-type]


def test_parse_holehe_hits() -> None:
    out = "[+] github user found\n[-] gitlab nothing\n[+] twitter ok"
    hits = parse_holehe_hits(out)
    assert len(hits) == 2


async def test_shodan_degrades_without_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path=None
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.delenv("SHODAN_API_KEY", raising=False)
    monkeypatch.setenv("JARVIS_KEYS_FILE", "/nonexistent-keys.env")
    result = await OsintTools.shodan_lookup(OsintTools(), None, "8.8.8.8")  # type: ignore[arg-type]
    assert result.get("degraded") == "true"
    assert "keys.env" in result["say"]


async def test_censys_degrades_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.delenv("CENSYS_API_ID", raising=False)
    monkeypatch.delenv("CENSYS_API_SECRET", raising=False)
    monkeypatch.setenv("JARVIS_KEYS_FILE", "/nonexistent-keys.env")
    result = await OsintTools.censys_lookup(OsintTools(), None, "8.8.8.8")  # type: ignore[arg-type]
    assert result.get("degraded") == "true"


def test_osint_registers_expected_ids() -> None:
    ids = [tool.id for tool in OsintTools().tools]
    for expected in (
        "dns_recon",
        "whois_lookup",
        "crt_enumerate",
        "email_footprint",
        "shodan_lookup",
        "censys_lookup",
    ):
        assert expected in ids
