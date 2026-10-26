import json

import pytest

from system import inbox


def message(sender="GitHub <noreply@github.com>", **headers):
    return {
        "payload": {
            "headers": [
                {"name": k, "value": v}
                for k, v in {"From": sender, "Subject": "Urgent", **headers}.items()
            ]
        }
    }


@pytest.mark.parametrize(
    "sender",
    [
        "Google <no-reply@accounts.google.com>",
        "GitHub <noreply@github.com>",
        "Bot <no_reply@example.com>",
        "Mailer <mailer-daemon@example.com>",
    ],
)
def test_display_name_does_not_hide_no_reply(sender):
    full = message(sender)
    assert not inbox.classify_email("Friendly display name", "Urgent", "", full)[
        "human"
    ]
    assert inbox.automatic_reply_recipient(full) == ""


@pytest.mark.parametrize(
    "headers",
    [
        {"Auto-Submitted": "auto-generated"},
        {"Return-Path": "<>"},
        {"Content-Type": "multipart/report; report-type=delivery-status"},
        {"Reply-To": "noreply@example.com"},
        {"X-Auto-Response-Suppress": "All"},
        {"List-Id": "news.example.com"},
    ],
)
def test_automatic_headers_block(headers):
    assert (
        inbox.automatic_reply_recipient(
            message("Person <human@example.com>", **headers)
        )
        == ""
    )


def test_human_reply_to_is_resolved():
    assert (
        inbox.automatic_reply_recipient(
            message(
                "Person <human@example.com>", **{"Reply-To": "Help <help@example.com>"}
            )
        )
        == "help@example.com"
    )


def test_send_boundary_never_opens_network(monkeypatch):
    monkeypatch.setattr(
        inbox.urllib.request, "urlopen", lambda *a, **k: pytest.fail("Network called")
    )
    with pytest.raises(Exception, match="Automatic reply blocked"):
        inbox._gmail_send_api(
            "mock",
            inbox.build_mime_b64("noreply@github.com", "Re: test", "text", auto=True),
        )


@pytest.mark.asyncio
async def test_direct_pass_on_is_guarded(monkeypatch):
    import mail_watch_job as watcher

    monkeypatch.setattr(
        inbox, "_gmail_send_api", lambda *a: pytest.fail("Send attempted")
    )
    result = await watcher._pass_on_reply(
        message(), {"sender": "GitHub", "subject": "Urgent"}, "mock", {}
    )
    assert "replied=no" in result


def test_page_prices_are_grounded():
    import shopping

    page = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "Product",
                "name": "Headphones",
                "offers": {
                    "@type": "Offer",
                    "price": "129.90",
                    "priceCurrency": "SGD",
                    "availability": "https://schema.org/InStock",
                },
            }
        )
        + "</script>"
    )
    p = shopping.parse_prices(page, "https://shop.example/product")[0]
    assert (p["price"], p["currency"], p["source"]) == ("129.90", "SGD", "shop.example")
    assert (
        shopping.parse_prices("Only $99! Buy now", "https://shop.example/product") == []
    )


def test_private_url_is_denied(monkeypatch):
    import shopping

    monkeypatch.setattr(
        shopping.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError):
        shopping.public_url("https://localhost/product")


def test_robots_denial_has_no_page_fetch(monkeypatch):
    import shopping

    monkeypatch.setattr(shopping, "public_url", lambda u: u)
    calls = []

    def fetch(url):
        calls.append(url)
        assert url.endswith("robots.txt")
        return "User-agent: *\nDisallow: /"

    monkeypatch.setattr(shopping, "fetch", fetch)
    r = shopping.inspect_product("https://shop.example/product")
    assert r["status"] == "blocked" and not r["products"] and r["checked_at"]
    assert len(calls) == 1


def test_open_requires_confirmation_and_safe_url(monkeypatch):
    import shopping

    calls = []
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(shopping, "public_url", lambda u: u)

    def opener(argv, **kwargs):
        calls.append(argv)
        return type("Result", (), {"returncode": 0})()

    assert not shopping.open_product_source("https://shop.example/item", False, opener)[
        "ok"
    ]
    assert not calls
    assert shopping.open_product_source("https://shop.example/item", True, opener)["ok"]
    assert calls == [["xdg-open", "https://shop.example/item"]]
