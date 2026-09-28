"""Hermetic tests for src/docgen.py (no network, no browser in unit tests)."""

import json

import pytest

import docgen
from docgen import (
    DocgenError,
    _extract_json,
    _validate_items,
    deliver,
    draft_fields_from_request,
    generate,
    invoice_totals,
    load_template,
    money,
    next_number,
    render,
    template_dir,
)


def test_render_escapes_values() -> None:
    out = render("<p>{{name}}</p>", {"name": "<b>Acme & Co</b>"})
    assert out == "<p>&lt;b&gt;Acme &amp; Co&lt;/b&gt;</p>"


def test_render_loop_items_then_outer_scope() -> None:
    out = render(
        "{{#items}}<li>{{description}} x{{qty}} ({{company}})</li>{{/items}}",
        {"company": "Acme", "items": [{"description": "Logo", "qty": 3}]},
    )
    assert out == "<li>Logo x3 (Acme)</li>"


def test_render_missing_keys_empty_and_non_list_loop() -> None:
    assert render("a{{nope}}b", {}) == "ab"
    assert render("{{#items}}x{{/items}}", {"items": "nope"}) == ""
    assert render("{{#missing}}x{{/missing}}", {}) == ""


def test_money_rounds_half_up() -> None:
    assert money("10.005") == money("10.01")
    assert money("abc") == money(0)
    assert money(None) == money(0)


def test_invoice_totals_math() -> None:
    totals = invoice_totals(
        [
            {"description": "Logo", "qty": 3, "unit_price": 400},
            {"description": "Half", "qty": 1, "unit_price": "10.005"},
        ],
        10,
    )
    assert totals["subtotal"] == "1210.01"
    assert totals["tax"] == "121.00"  # 10% of 1210.01, half-up
    assert totals["total"] == "1331.01"
    assert totals["lines"][0]["line_total"] == "1200.00"
    assert totals["lines"][1]["qty"] == "1"


def test_invoice_totals_bad_tax_is_zero() -> None:
    assert (
        invoice_totals([{"description": "x", "qty": 1, "unit_price": 5}], "junk")["tax"]
        == "0.00"
    )


def test_template_seeds_from_repo(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    tdir = template_dir("invoice")
    assert (tdir / "template.html").exists()
    assert (tdir / "style.json").exists()
    assert (tdir / "rules.md").exists()
    tpl = load_template("invoice")
    assert tpl["style"]["company"] and "{{number}}" in tpl["html"]


def test_next_number_increments_and_formats(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    tdir = template_dir("invoice")
    first = next_number(tdir, {"invoice_number_format": "INV-{YYYY}-{NNNN}"})
    second = next_number(tdir, {"invoice_number_format": "INV-{YYYY}-{NNNN}"})
    assert first.endswith("-0001") and second.endswith("-0002")


def _fields() -> dict:
    return {
        "client": "Acme",
        "items": [{"description": "Logo design", "qty": 3, "unit_price": 400}],
    }


def test_generate_fills_brand_and_totals(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    made = generate("invoice", _fields())
    assert "Acme" in made["html"] and "1200.00" in made["html"]
    assert "Acme Studio" in made["html"]  # brand default from style.json
    assert made["title"].startswith("Invoice INV-")
    # Counter advanced: next invoice differs.
    assert generate("invoice", _fields())["title"] != made["title"]


def test_generate_rejects_unknown_kind_and_bad_items(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    with pytest.raises(DocgenError, match="don't have"):
        generate("contract", {})
    with pytest.raises(DocgenError, match="line item"):
        generate("invoice", {"client": "Acme", "items": []})


def test_validate_items_strict() -> None:
    with pytest.raises(DocgenError, match="line item"):
        _validate_items([])
    with pytest.raises(DocgenError, match="quantity"):
        _validate_items([{"description": "x", "qty": 0, "unit_price": 5}])
    with pytest.raises(DocgenError, match="description"):
        _validate_items([{"description": "", "qty": 1, "unit_price": 5}])
    items = _validate_items([{"description": " x ", "qty": 2, "unit_price": "10.5"}])
    assert items == [{"description": "x", "qty": 2.0, "unit_price": 10.5}]


def test_extract_json_tolerates_fences() -> None:
    got = _extract_json('```json\n{"client": "Acme"}\n```')
    assert got == {"client": "Acme"}
    with pytest.raises(DocgenError, match="unreadable"):
        _extract_json("no json here")


class FakeResp:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, *payloads):
        self.queue = list(payloads)
        self.calls: list[tuple] = []

    def __call__(self, req, timeout=None):
        self.calls.append(
            (req.get_method(), req.full_url, json.loads(req.data.decode()))
        )
        return FakeResp(self.queue.pop(0))


def _gemini_reply(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_draft_fields_from_request_validates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
    opener = FakeOpener(
        _gemini_reply(
            '{"client": "Acme", "items": [{"description": "Logo", "qty": 3, "unit_price": 400}]}'
        )
    )
    fields = draft_fields_from_request(
        "invoice", "invoice Acme for 3 logos at 400 each", opener=opener
    )
    assert fields["client"] == "Acme"
    assert fields["items"][0]["qty"] == 3
    assert "generateContent" in opener.calls[0][1]


def test_draft_fields_rejects_missing_client(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
    opener = FakeOpener(
        _gemini_reply('{"items": [{"description": "x", "qty": 1, "unit_price": 5}]}')
    )
    with pytest.raises(DocgenError, match="who the invoice is for"):
        draft_fields_from_request("invoice", "invoice for someone", opener=opener)


def test_draft_fields_needs_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(DocgenError, match="Gemini key"):
        draft_fields_from_request("invoice", "invoice Acme for 1 x at 5")


def test_deliver_without_google_still_returns_pdf(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(docgen, "save_local_pdf", lambda h, k, t: "/tmp/fake.pdf")
    import google_api

    def _boom(*a, **k):
        raise google_api.GoogleError("Google isn't connected yet, Sir.")

    monkeypatch.setattr(google_api, "ensure_folder", _boom)
    got = deliver("invoice", _fields())
    assert got == {
        "title": got["title"],
        "drive_id": None,
        "pdf": "/tmp/fake.pdf",
        "say": got["say"],
    }
    assert "PDF" in got["say"] and "connected" in got["say"]
