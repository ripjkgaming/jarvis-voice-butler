"""Brand document generation: fill templates, draft fields, deliver PDF + Drive.

Templates live in $JARVIS_HOME/templates/<name>/ (seeded from the repo's
templates/ on first use). A template is template.html ({{placeholders}} +
{{#items}}...{{/items}} loops, HTML-escaped), style.json (brand defaults),
and rules.md (plain-language rules the LLM follows when drafting fields).

Fail-soft: raises DocgenError with a speakable message, never a traceback.
"""

from __future__ import annotations

import contextlib
import datetime
import html as _html
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

GEMINI_MODEL = os.environ.get("JARVIS_TEXT_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK = "gemini-3.7-flash"

NOT_CONNECTED = "Google isn't connected, Sir — the PDF is still ready locally."


class DocgenError(Exception):
    """User-facing docgen failure (speakable message)."""


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def _repo_templates() -> Path:
    return Path(__file__).resolve().parent.parent / "templates"


def template_dir(name: str) -> Path:
    """$JARVIS_HOME/templates/<name>/, seeded from the repo on first use."""
    dest = jarvis_home() / "templates" / name
    if not (dest / "template.html").exists():
        seed = _repo_templates() / name
        if seed.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            for child in seed.iterdir():
                if child.is_file() and not (dest / child.name).exists():
                    shutil.copy2(child, dest / child.name)
    return dest


def load_template(name: str) -> dict:
    """{dir, html, style, rules} for a template. Raises DocgenError."""
    tdir = template_dir(name)
    try:
        page_html = (tdir / "template.html").read_text()
    except OSError as exc:
        raise DocgenError(f"I could not find the {name} template, Sir.") from exc
    try:
        style = json.loads((tdir / "style.json").read_text())
    except (OSError, ValueError):
        style = {}
    try:
        rules = (tdir / "rules.md").read_text()
    except OSError:
        rules = ""
    if not isinstance(style, dict):
        style = {}
    return {"dir": tdir, "html": page_html, "style": style, "rules": rules}


def render(source: str, fields: dict) -> str:
    """Tiny safe renderer: {{key}} escaped + {{#list}}...{{/list}} loops.

    Pure. Missing keys render as "". Loop items resolve against the item
    first, then the outer fields (so brand vars work inside rows).
    """

    def _expand(text: str, scope: dict) -> str:
        def _val(key: str) -> str:
            if isinstance(scope, dict) and key in scope:
                value = scope[key]
            else:
                value = fields.get(key, "")
            if value is None:
                return ""
            return _html.escape(str(value), quote=True)

        return re.sub(r"\{\{(\w+)\}\}", lambda m: _val(m.group(1)), text)

    def _loops(text: str) -> str:
        pattern = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.DOTALL)

        def _rep(match: re.Match) -> str:
            rows = fields.get(match.group(1), [])
            if not isinstance(rows, list):
                return ""
            return "".join(
                _expand(match.group(2), r) if isinstance(r, dict) else "" for r in rows
            )

        return pattern.sub(_rep, text)

    return _expand(_loops(source or ""), fields if isinstance(fields, dict) else {})


def money(value: object) -> Decimal:
    """Anything numeric-ish -> Decimal(0.01). Pure."""
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


def invoice_totals(items: list[dict], tax_rate: object) -> dict:
    """Line totals + subtotal/tax/total, HALF_UP to the cent. Pure."""
    lines = []
    subtotal = Decimal("0.00")
    for item in items or []:
        if not isinstance(item, dict):
            continue
        qty = money(item.get("qty", 0))
        price = money(item.get("unit_price", 0))
        total = (qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        subtotal += total
        lines.append(
            {
                "description": str(item.get("description", ""))[:200],
                "qty": f"{qty.normalize():f}",
                "unit_price": f"{price:.2f}",
                "line_total": f"{total:.2f}",
            }
        )
    try:
        rate = Decimal(str(tax_rate))
    except (InvalidOperation, ValueError, TypeError):
        rate = Decimal("0")
    tax = (subtotal * rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = subtotal + tax
    return {
        "lines": lines,
        "subtotal": f"{subtotal:.2f}",
        "tax": f"{tax:.2f}",
        "total": f"{total:.2f}",
    }


def next_number(tdir: Path, style: dict) -> str:
    """Auto invoice number; persists a counter in counter.json."""
    counter = tdir / "counter.json"
    try:
        last = int(json.loads(counter.read_text()).get("last", 0))
    except (OSError, ValueError, AttributeError, TypeError):
        last = 0
    last += 1
    with contextlib.suppress(OSError):
        counter.write_text(json.dumps({"last": last}))
    fmt = str(style.get("invoice_number_format", "INV-{YYYY}-{NNNN}"))
    year = datetime.date.today().strftime("%Y")
    return fmt.replace("{YYYY}", year).replace("{NNNN}", f"{last:04d}")


def _validate_items(raw: object) -> list[dict]:
    """Strict items validation for LLM-drafted or voice-given fields. Pure-ish."""
    if not isinstance(raw, list) or not raw:
        raise DocgenError("The invoice needs at least one line item, Sir.")
    items = []
    for entry in raw[:50]:
        if not isinstance(entry, dict):
            raise DocgenError(
                "Each line item needs a description, quantity and price, Sir."
            )
        desc = str(entry.get("description", "")).strip()[:200]
        try:
            qty = float(entry.get("qty", 0))
            price = float(entry.get("unit_price", 0))
        except (TypeError, ValueError):
            raise DocgenError(
                f"'{desc or 'an item'}' needs numbers for quantity and price, Sir."
            ) from None
        if not desc or qty <= 0 or price < 0:
            raise DocgenError(
                f"'{desc or 'an item'}' needs a description, quantity over 0 and a price, Sir."
            )
        items.append({"description": desc, "qty": qty, "unit_price": price})
    return items


def generate(kind: str, fields: dict) -> dict:
    """Fill a template -> {html, title}. Pure apart from the counter file."""
    kind = (kind or "").strip().lower()
    if kind != "invoice":
        raise DocgenError(f"I don't have a {kind or 'blank'} template yet, Sir.")
    if not isinstance(fields, dict):
        raise DocgenError("I need the invoice details first, Sir.")
    tpl = load_template("invoice")
    style = tpl["style"]
    items = _validate_items(fields.get("items"))
    totals = invoice_totals(items, fields.get("tax_rate", style.get("tax_rate", 0)))
    today = datetime.date.today()
    try:
        due_days = int(fields.get("due_days", style.get("due_days", 14)))
    except (TypeError, ValueError):
        due_days = 14
    due = today + datetime.timedelta(days=max(0, due_days))
    merged = {
        # Brand defaults from style.json; explicit fields win.
        "company": style.get("company", ""),
        "company_address": style.get("address", ""),
        "brand_color": style.get("brand_color", "#1a56db"),
        "accent_color": style.get("accent_color", "#e8eefc"),
        "currency": style.get("currency", "USD"),
        "payment_terms": style.get("payment_terms", ""),
        "logo": style.get("logo", ""),
        **{k: v for k, v in fields.items() if v not in (None, "")},
        "number": fields.get("number") or next_number(tpl["dir"], style),
        "issue_date": fields.get("issue_date") or today.isoformat(),
        "due_date": fields.get("due_date") or due.isoformat(),
        "items": totals["lines"],
        "subtotal": totals["subtotal"],
        "tax": totals["tax"],
        "total": totals["total"],
        "tax_rate": fields.get("tax_rate", style.get("tax_rate", 0)),
    }
    title = f"Invoice {merged['number']} — {fields.get('client', 'client')}"
    return {"html": render(tpl["html"], merged), "title": title[:150]}


def _gemini_key() -> str:
    direct = os.environ.get("GOOGLE_API_KEY", "").strip()
    if direct:
        return direct
    try:
        for line in (jarvis_home() / "keys.env").read_text().splitlines():
            if line.strip().startswith("GOOGLE_API_KEY="):
                return line.partition("=")[2].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _gemini_text(prompt: str, opener=None) -> str:
    """One free-tier Gemini text call, primary then fallback. Raises DocgenError."""
    key = _gemini_key()
    if not key:
        raise DocgenError("Drafting needs the free Gemini key first, Sir.")
    body = {"contents": [{"parts": [{"text": prompt[:6000]}]}]}
    call = opener or urllib.request.urlopen
    last_err = ""
    for model in (GEMINI_MODEL, GEMINI_FALLBACK):
        if not model:
            continue
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={urllib.parse.quote(key)}"
        )
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with call(req, timeout=30) as resp:
                got = json.loads(resp.read().decode() or "{}")
            parts = (
                (got.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            )
            text = "".join(
                p.get("text", "") for p in parts if isinstance(p, dict)
            ).strip()
            if text:
                return text
            last_err = "empty reply"
        except Exception as exc:
            last_err = str(exc)[:120]
            continue
    raise DocgenError(f"The drafting helper is unavailable ({last_err}).")


def _extract_json(text: str) -> dict:
    """First {...} object in the reply (fences tolerated). Pure-ish."""
    cleaned = re.sub(r"```(?:json)?", "", text or "")
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise DocgenError("The draft came back unreadable, Sir — try plainer words.")
    try:
        got = json.loads(match.group(0))
    except ValueError as exc:
        raise DocgenError(
            "The draft came back unreadable, Sir — try plainer words."
        ) from exc
    return got if isinstance(got, dict) else {}


def draft_fields_from_request(kind: str, request_text: str, opener=None) -> dict:
    """'invoice Acme for 3 logo designs at 400 each' -> validated fields dict."""
    kind = (kind or "").strip().lower()
    if kind != "invoice":
        raise DocgenError(f"I can't draft a {kind or 'blank'} yet, Sir.")
    request_text = (request_text or "").strip()
    if len(request_text) < 3:
        raise DocgenError("What should the invoice say, Sir?")
    try:
        rules = load_template("invoice")["rules"]
    except DocgenError:
        rules = ""
    prompt = (
        "Turn this invoice request into JSON with keys: client (string), "
        "items (list of {description, qty, unit_price numbers}), notes (string, optional). "
        "Reply with JSON only.\n"
        f"Style rules:\n{rules[:2000]}\nRequest: {request_text[:500]}"
    )
    data = _extract_json(_gemini_text(prompt, opener=opener))
    fields = {
        "client": str(data.get("client", "")).strip()[:200],
        "items": _validate_items(data.get("items")),
    }
    if str(data.get("notes", "")).strip():
        fields["notes"] = str(data["notes"]).strip()[:1000]
    if not fields["client"]:
        raise DocgenError("I couldn't tell who the invoice is for, Sir.")
    return fields


def save_local_pdf(
    page_html: str, kind: str, title: str, out_path: str | Path | None = None
) -> str:
    """HTML -> local PDF via Playwright/Chromium print. Returns the path."""
    safe = re.sub(r"[^A-Za-z0-9 _.-]+", "", title or kind).strip() or kind
    dest = (
        Path(out_path)
        if out_path
        else Path.home() / "Documents" / "Jarvis" / f"{kind}s" / f"{safe}.pdf"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright  # lazy: heavy import

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(page_html or "<p>empty</p>")
            page.pdf(path=str(dest), format="A4", print_background=True)
        finally:
            browser.close()
    return str(dest)


def deliver(kind: str, fields: dict, opener=None) -> dict:
    """Generate + upload to Drive ('Jarvis Documents') + save a local PDF.

    When Google isn't connected the local PDF is still produced and the
    reply says so — Sir never loses the document.
    """
    made = generate(kind, fields)
    pdf = save_local_pdf(made["html"], kind, made["title"])
    drive_id: str | None = None
    try:
        import google_api

        folder = google_api.ensure_folder("Jarvis Documents", opener=opener)
        meta = google_api.docs_create(
            made["title"], made["html"], folder, opener=opener
        )
        drive_id = str(meta.get("id", "")) or None
    except Exception as exc:
        from google_api import GoogleError

        note = str(exc) if isinstance(exc, GoogleError) else NOT_CONNECTED
        say = f"{made['title']} is ready as a PDF, Sir. {note}"
        return {"title": made["title"], "drive_id": None, "pdf": pdf, "say": say}
    return {
        "title": made["title"],
        "drive_id": drive_id,
        "pdf": pdf,
        "say": f"{made['title']} is in Drive and saved as a PDF, Sir.",
    }
