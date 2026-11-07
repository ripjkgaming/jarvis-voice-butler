"""Always Aware send/submit/payment rules (PLAN.md §4.4). Pure, no browser."""

import json
from decimal import Decimal

import pytest

from aware import payments as pay
from aware import state as st
from aware import transactions as tx
from aware.types import Consequential


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


CHECKOUT = """Scan Computers - Checkout
Your basket
Samsung 990 EVO 2TB SSD  x1  £94.99
Subtotal £94.99
Delivery FREE
Pay with saved card: Visa ending 4242
Order total
£94.99
Place order"""


def payment(**kw):
    base = {
        "kind": "payment",
        "site": "www.scan.co.uk",
        "merchant": "Scan Computers",
        "amount": "94.99",
        "currency": "GBP",
        "items": ["Samsung 990 EVO 2TB SSD"],
        "payment_method": "saved Visa ending 4242",
    }
    base.update(kw)
    return Consequential(**base)


def snap(text=CHECKOUT, url="https://www.scan.co.uk/checkout", **kw):
    return pay.PageSnapshot(url=url, title="Checkout | Scan", text=text, **kw)


# ------------------------------------------------------------ vocabulary


@pytest.mark.parametrize(
    "name",
    [
        "Pay now",
        "Place order",
        "Buy now",
        "Send",
        "Submit",
        "Post",
        "Delete",
        "Confirm purchase",
        "Subscribe",
        "Checkout",
        "Complete order",
    ],
)
def test_consequential_words_match(name):
    assert pay.consequential_words(name)


@pytest.mark.parametrize(
    "name", ["Next", "Continue", "Payment options", "Search", "Close", "Postcode"]
)
def test_benign_controls_do_not_match(name):
    assert not pay.consequential_words(name)


def test_weak_only_words():
    assert pay.only_weak(pay.consequential_words("Go to checkout"))
    assert not pay.only_weak(pay.consequential_words("Pay now"))


# ------------------------------------------------------------ never-allowed pages


@pytest.mark.parametrize(
    "url",
    [
        "brave://settings",
        "chrome://extensions",
        "chrome-extension://abc/options.html",
        "https://chromewebstore.google.com/detail/x",
        "https://myaccount.google.com/security",
        "https://github.com/settings/security",
        "https://example.com/account/password",
        "https://example.com/settings/two-factor",
        "https://shop.example/account/recovery",
        "https://passwords.google.com/",
    ],
)
def test_forbidden_urls(url):
    assert pay.forbidden_url(url)


def test_ordinary_checkout_url_is_allowed():
    assert pay.forbidden_url("https://www.scan.co.uk/checkout") is None


# ------------------------------------------------------------ needs-user


@pytest.mark.parametrize(
    "text",
    [
        "3-D Secure: confirm this payment",
        "Approve the payment in your banking app",
        "Enter the one-time passcode we sent",
        "Verified by Visa",
        "Enter the verification code sent to your phone",
        "Mastercard Identity Check",
    ],
)
def test_needs_user_text(text):
    assert pay.needs_user(pay.PageSnapshot(url="https://x/", text=text))


def test_needs_user_otp_field_and_acs_frame():
    otp = pay.PageSnapshot(
        url="https://x/",
        text="",
        fields=[{"autocomplete": "one-time-code", "visible": True}],
    )
    assert pay.needs_user(otp)
    acs = pay.PageSnapshot(
        url="https://x/", text="", frame_urls=["https://acs.bank.example/3ds/challenge"]
    )
    assert pay.needs_user(acs)


def test_needs_user_clean_page():
    assert pay.needs_user(snap()) is None


# ------------------------------------------------------------ money


def test_parse_amounts():
    got = pay.parse_amounts("£1,094.99 and $5.00 and 12.50 EUR and GBP 3.10")
    assert (Decimal("1094.99"), "GBP") in got
    assert (Decimal("5.00"), "USD") in got
    assert (Decimal("12.50"), "EUR") in got
    assert (Decimal("3.10"), "GBP") in got


def test_page_totals_label_on_next_line_and_ignores_subtotal():
    totals = pay.page_totals(CHECKOUT)
    assert (Decimal("94.99"), "GBP") in [(a, c) for a, c, _ in totals]
    assert all("sub" not in lbl.lower() for _, _, lbl in totals)


# ------------------------------------------------------------ final check: payment


def test_matching_payment_passes():
    v = pay.check_final(payment(), snap())
    assert v.ok, v.reason
    assert v.observed["amount"] == "94.99" and v.observed["currency"] == "GBP"


def test_total_drift_above_offer_blocks():
    v = pay.check_final(
        payment(), snap(CHECKOUT.replace("Order total\n£94.99", "Order total\n£99.98"))
    )
    assert not v.ok and "amount" in v.reason and not v.needs_user


def test_lower_total_also_blocks_exact_match_required():
    v = pay.check_final(
        payment(), snap(CHECKOUT.replace("Order total\n£94.99", "Order total\n£89.99"))
    )
    assert not v.ok and "amount" in v.reason


def test_extra_total_above_offer_blocks():
    v = pay.check_final(payment(), snap(CHECKOUT + "\nTotal incl. shipping £104.98"))
    assert not v.ok


def test_no_total_on_page_blocks():
    no_total = "\n".join(
        ln
        for ln in CHECKOUT.splitlines()
        if "total" not in ln.lower() and ln != "£94.99"
    ).replace("£94.99", "")
    v = pay.check_final(payment(), snap(no_total))
    assert not v.ok and "total" in v.reason


def test_currency_mismatch_blocks():
    v = pay.check_final(payment(), snap(CHECKOUT.replace("£94.99", "$94.99")))
    assert not v.ok


def test_wrong_host_blocks():
    v = pay.check_final(
        payment(), snap(url="https://scan-co-uk.checkout-secure.example/pay")
    )
    assert not v.ok and "host" in v.reason


def test_subdomain_of_declared_site_ok():
    v = pay.check_final(payment(site="scan.co.uk"), snap())
    assert v.ok, v.reason


def test_missing_item_blocks():
    v = pay.check_final(payment(items=["Samsung 990 EVO 4TB SSD"]), snap())
    assert not v.ok and "item" in v.reason


def test_unknown_items_merchant_or_amount_block():
    assert not pay.check_final(payment(items=[]), snap()).ok
    assert not pay.check_final(payment(merchant=""), snap()).ok
    assert not pay.check_final(payment(amount=""), snap()).ok
    assert not pay.check_final(payment(currency=""), snap()).ok


def test_saved_card_must_be_shown():
    v = pay.check_final(payment(payment_method="saved Visa ending 1111"), snap())
    assert not v.ok and "payment method" in v.reason
    v = pay.check_final(payment(payment_method="my card"), snap())
    assert not v.ok


def test_card_entry_form_is_needs_user():
    s = snap(fields=[{"autocomplete": "cc-number", "visible": True, "value": ""}])
    v = pay.check_final(payment(), s)
    assert not v.ok and v.needs_user


def test_cvc_request_is_needs_user():
    s = snap(fields=[{"name": "cvc", "visible": True, "value": ""}])
    v = pay.check_final(payment(), s)
    assert not v.ok and v.needs_user


def test_3ds_on_page_is_needs_user():
    v = pay.check_final(payment(), snap(CHECKOUT + "\n3D Secure verification required"))
    assert not v.ok and v.needs_user


def test_password_field_blocks():
    s = snap(fields=[{"type": "password", "visible": True, "value": ""}])
    assert not pay.check_final(payment(), s).ok


def test_spend_cap_blocks_above_and_allows_at_or_below():
    assert not pay.check_final(payment(), snap(), cap=(Decimal("50"), "GBP")).ok
    assert pay.check_final(payment(), snap(), cap=(Decimal("94.99"), "GBP")).ok
    # unknown cap currency vs payment currency: refuse rather than guess
    assert not pay.check_final(payment(), snap(), cap=(Decimal("500"), "USD")).ok


# ------------------------------------------------------------ final check: send / submit


def send(**kw):
    base = {
        "kind": "send",
        "site": "mail.example.com",
        "recipient": "anna@example.com",
        "content": "Running ten minutes late, see you at 3.",
    }
    base.update(kw)
    return Consequential(**base)


def compose(to="anna@example.com", body="Running ten minutes late, see you at 3."):
    return pay.PageSnapshot(
        url="https://mail.example.com/compose",
        text="New message",
        fields=[
            {"name": "to", "label": "To", "value": to, "visible": True},
            {
                "name": "body",
                "label": "Message",
                "type": "textarea",
                "value": body,
                "visible": True,
            },
        ],
    )


def test_send_matching_passes():
    assert pay.check_final(send(), compose()).ok


def test_send_different_recipient_blocks():
    v = pay.check_final(send(), compose(to="boss@example.com"))
    assert not v.ok and "recipient" in v.reason


def test_send_extra_recipient_blocks():
    assert not pay.check_final(
        send(), compose(to="anna@example.com, eve@evil.example")
    ).ok


def test_send_changed_text_blocks():
    v = pay.check_final(
        send(), compose(body="Running ten minutes late, see you at 3. PS wire £500")
    )
    assert not v.ok and "content" in v.reason


def test_send_whitespace_difference_ok():
    assert pay.check_final(
        send(), compose(body="Running ten minutes late,\n see you at 3. ")
    ).ok


def test_submit_host_and_content():
    act = Consequential(
        kind="submit", site="council.example.gov.uk", content="SW1A 1AA"
    )
    s = pay.PageSnapshot(
        url="https://council.example.gov.uk/form",
        text="Bin collection request",
        fields=[{"name": "postcode", "value": "SW1A 1AA", "visible": True}],
    )
    assert pay.check_final(act, s).ok
    s.url = "https://other.example/form"
    assert not pay.check_final(act, s).ok


def test_unknown_kind_blocks():
    assert not pay.check_final(Consequential(kind="transfer"), snap()).ok


# ------------------------------------------------------------ confirmation


def test_confirmation_ref():
    assert (
        pay.confirmation_ref("Thanks! Order number: SC-123456 is confirmed")
        == "SC-123456"
    )
    assert pay.confirmation_ref("Your order reference is ABC12345.") == "ABC12345"
    assert pay.confirmation_ref("nothing here") == ""


# ------------------------------------------------------------ spend cap storage


def test_spend_cap_default_unlimited():
    assert pay.spend_cap() == (None, "GBP")


def test_set_and_clear_spend_cap():
    pay.set_spend_cap("50", "gbp")
    assert pay.spend_cap() == (Decimal("50.00"), "GBP")
    assert json.loads(st.state_path().read_text())["spend_cap"] == "50.00"
    pay.set_spend_cap(None)
    assert pay.spend_cap() == (None, "GBP")


def test_set_spend_cap_rejects_nonsense():
    with pytest.raises(ValueError):
        pay.set_spend_cap("-5")
    with pytest.raises(ValueError):
        pay.set_spend_cap("lots")


def test_corrupt_cap_fails_closed():
    st.update(spend_cap="banana")
    amount, _ = pay.spend_cap()
    assert amount == Decimal("0")


# ------------------------------------------------------------ audit


def test_record_writes_transaction_and_audit():
    act = payment()
    row = pay.record(
        "run1",
        act,
        "Shall I check out for £94.99?",
        "ok:verified",
        observed={"amount": "94.99", "currency": "GBP", "host": "www.scan.co.uk"},
        confirmation_ref="SC-1",
    )
    assert row["kind"] == "payment" and row["amount"] == "94.99"
    rows = tx.read_all()
    assert rows[-1]["confirmation_ref"] == "SC-1"
    assert rows[-1]["counterparty"] == "Scan Computers"


def test_record_failed_attempt_also_logged():
    pay.record(
        "run1", payment(), "offer", "blocked:amount", observed={"amount": "99.98"}
    )
    rows = tx.read_all()
    assert rows[-1]["result"] == "blocked:amount" and rows[-1]["amount"] == "99.98"
    assert not tx.completed(rows[-1])


def test_record_send_uses_recipient_and_content():
    row = pay.record("r", send(), "offer", "ok:verified")
    assert row["counterparty"] == "anna@example.com"
    assert row["content_head"].startswith("Running ten")


def test_intent_is_durable_in_transactions_and_audit():
    pay.intent("run1", payment(), "offer")
    rows = tx.read_all()
    assert rows[-1]["result"] == "intent"
    assert rows[-1]["amount"] == "94.99"
    audit = (st.aware_dir() / "audit.jsonl").read_text()
    assert "transaction_intent" in audit


def test_audit_writable_probe(tmp_path):
    assert pay.audit_writable()


# ------------------------------------------------------------ saved methods (P2 gate)


def test_saved_method_fails_closed_without_evidence():
    assert not pay.saved_method("www.scan.co.uk", "saved Visa ending 4242")
    assert not pay.saved_method("www.scan.co.uk", "my card", CHECKOUT)


def test_saved_method_from_checkout_page():
    assert pay.saved_method("www.scan.co.uk", "saved Visa ending 4242", CHECKOUT)
    assert not pay.saved_method("www.scan.co.uk", "saved Visa ending 1111", CHECKOUT)


def test_saved_method_not_when_card_entry_requested():
    fields = [{"autocomplete": "cc-number", "visible": True, "value": ""}]
    assert not pay.saved_method("www.scan.co.uk", "Visa ending 4242", CHECKOUT, fields)


def test_verified_payment_remembers_method_only_last_four():
    pay.record(
        "r", payment(), "offer", "ok:verified", observed={"host": "www.scan.co.uk"}
    )
    assert pay.saved_method("scan.co.uk", "Visa ending 4242")
    assert not pay.saved_method("other.example", "Visa ending 4242")
    raw = (st.aware_dir() / "saved_methods.json").read_text()
    assert "4242" in raw and "Visa" not in raw


def test_failed_payment_does_not_remember_method():
    pay.record("r", payment(), "offer", "blocked:amount")
    assert not pay.saved_method("www.scan.co.uk", "Visa ending 4242")


def test_page_totals_across_blank_line_from_inner_text():
    assert [(a, c) for a, c, _ in pay.page_totals("Order total\n\n£94.99\n")] == [
        (Decimal("94.99"), "GBP")
    ]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "file:///tmp/x",
        "ftp://example.com/x",
        "about:blank",
        "https://[bad",
        "https:///missing-host",
    ],
)
def test_non_web_or_malformed_urls_are_forbidden(url):
    assert pay.forbidden_url(url)


@pytest.mark.parametrize("amount", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_nonfinite_spend_limit_rejected(amount):
    with pytest.raises(ValueError):
        pay.set_spend_cap(amount)


def test_intent_stops_if_transaction_append_fails(monkeypatch):
    append = st.append_private

    def fail_transaction(path, text):
        return False if path == tx.path() else append(path, text)

    monkeypatch.setattr(st, "append_private", fail_transaction)
    with pytest.raises(OSError, match="transaction log"):
        pay.intent("run1", payment(), "offer")
