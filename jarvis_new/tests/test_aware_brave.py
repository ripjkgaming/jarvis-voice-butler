"""Always Aware live Brave runner (PLAN.md §4).

Pure tests for plan parsing, the action guard and predicates, plus `slow`
integration tests that drive a THROWAWAY headless Chromium (temp profile,
test copy of the jarvis-tabs worker) against a local fake checkout server
whose "payment layer" only records orders in memory. Sir's real Brave is
never touched: no test connects to 127.0.0.1:9222.
"""

import asyncio
import json
import os
import shutil
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aware import brave_live as bl
from aware import tabsplit as ts
from aware import transactions as tx
from aware.types import Consequential, PlanStep, TakeoverPlan, TakeoverResult


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jh"))
    monkeypatch.setenv("JARVIS_AWARE", "1")
    bl.st.update(enabled=True)
    return tmp_path


def web_plan(steps=None, actions=None, **targets):
    return TakeoverPlan(
        id="p1",
        opp_id="o1",
        kind="web",
        task_kind="web_form",
        title="t",
        steps=[],
        success_criteria=[],
        time_budget_s=120,
        targets={"web_steps": steps or [], **targets},
        actions=actions or [],
        offer_text="offer",
    )


# ------------------------------------------------------------ ids / probe


def test_extension_id_matches_chromium_scheme():
    eid = bl.extension_id("/tmp/x")
    assert len(eid) == 32 and set(eid) <= set("abcdefghijklmnop")


def test_known_ids_include_locke_ext_and_env(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCKE_EXT_ID", "a" * 32 + ",bogus")
    ids = bl.known_ext_ids()
    assert (
        bl.extension_id(bl.LOCKE_EXT_DIR) in ids
        and "a" * 32 in ids
        and "bogus" not in ids
    )


def test_find_worker_only_known_extension_script():
    eid = "a" * 32
    targets = [
        {
            "type": "service_worker",
            "url": f"chrome-extension://{'b' * 32}/jarvis-tabs.js",
        },
        {"type": "service_worker", "url": f"chrome-extension://{eid}/other.js"},
        {"type": "page", "url": f"chrome-extension://{eid}/jarvis-tabs.js"},
    ]
    assert bl.find_worker(targets, {eid}) is None
    targets.append(
        {"type": "service_worker", "url": f"chrome-extension://{eid}/jarvis-tabs.js"}
    )
    assert bl.find_worker(targets, {eid})["url"].endswith("/jarvis-tabs.js")


def test_available_without_cdp(monkeypatch):
    monkeypatch.setattr(bl.cdp, "version", lambda *a, **k: None)
    assert bl.available() == (False, "cdp_unavailable")
    assert bl.worker_ok() is False


def test_available_without_worker(monkeypatch):
    monkeypatch.setattr(bl.cdp, "version", lambda *a, **k: {"Browser": "x"})
    monkeypatch.setattr(bl.cdp, "targets", lambda *a, **k: [])
    ok, why = bl.available()
    assert not ok and why.startswith("no_tab_move")


# ------------------------------------------------------------ plan parsing


def test_parse_web_steps():
    plan = web_plan(
        [
            {"op": "web.type", "target": "Name", "text": "Tony"},
            {"op": "click", "target": "Place order", "action": 0},
        ],
        actions=[Consequential(kind="payment")],
    )
    steps = bl.parse_steps(plan)
    assert [s.op for s in steps] == ["type", "click"]
    assert steps[1].action == 0 and steps[1].final


def test_parse_plan_steps_fallback():
    plan = web_plan()
    plan.targets = {}
    plan.steps = [
        PlanStep("Postcode => SW1A 1AA", "web.type"),
        PlanStep("Next", "click"),
        PlanStep("Enter", "press"),
        PlanStep("think about it", "reason"),
    ]
    steps = bl.parse_steps(plan)
    assert [(s.op, s.target, s.text) for s in steps] == [
        ("type", "Postcode", "SW1A 1AA"),
        ("click", "Next", ""),
        ("press", "", "Enter"),
    ]


@pytest.mark.parametrize(
    "steps,actions",
    [
        ([], []),
        ([{"op": "hover", "target": "x"}], []),
        ([{"op": "click", "target": "Pay", "action": 0}], []),
        ([{"op": "type", "target": "x", "action": 0}], [Consequential(kind="send")]),
    ],
)
def test_parse_rejects_bad_plans(steps, actions):
    with pytest.raises(bl.PlanError):
        bl.parse_steps(web_plan(steps, actions))


# ------------------------------------------------------------ guards


def desc(**kw):
    base = {
        "name": "",
        "tag": "BUTTON",
        "type": "button",
        "in_form": False,
        "form_action": "",
        "form_method": "",
        "form_submit_text": "",
        "href": "",
        "autocomplete": "",
        "field": "",
    }
    base.update(kw)
    return base


def test_guard_blocks_undeclared_pay_button():
    assert bl.guard_click(desc(name="Pay now"), bl.WebStep("click", "Pay now"))


def test_guard_allows_benign_click():
    assert bl.guard_click(desc(name="Next"), bl.WebStep("click", "Next")) is None


def test_guard_submit_button_with_checkout_form_action():
    d = desc(
        name="Continue",
        type="submit",
        in_form=True,
        form_action="/checkout/place-order",
    )
    assert bl.guard_click(d, bl.WebStep("click", "Continue"))


def test_guard_declared_final_allowed_here():
    assert (
        bl.guard_click(desc(name="Pay now"), bl.WebStep("click", "Pay now", action=0))
        is None
    )


def test_guard_prestep_weak_ok_strong_blocked():
    assert (
        bl.guard_click(
            desc(name="Go to checkout"), bl.WebStep("click", "x", action=0, final=False)
        )
        is None
    )
    assert bl.guard_click(
        desc(name="Pay now"), bl.WebStep("click", "x", action=0, final=False)
    )


def test_guard_forbidden_link():
    assert bl.guard_click(
        desc(name="Security", href="https://myaccount.google.com/security"),
        bl.WebStep("click", "Security"),
    )


def test_guard_type_refuses_password_card_and_otp():
    s = bl.WebStep("type", "x", "y")
    assert bl.guard_type(desc(type="password"), s) == "refused:password_field"
    assert bl.guard_type(desc(autocomplete="cc-number"), s) == "refused:card_details"
    assert bl.guard_type(desc(field="cvc"), s) == "refused:card_details"
    assert bl.guard_type(desc(autocomplete="one-time-code"), s).startswith("needs_user")
    assert bl.guard_type(desc(field="email"), s) is None


def test_guard_enter_in_post_form_is_consequential():
    s = bl.WebStep("press", "", "Enter")
    assert bl.guard_press("Enter", desc(in_form=True, form_method="post"), s)
    assert bl.guard_press("Enter", desc(in_form=True, form_method="get"), s) is None
    assert bl.guard_press("Tab", desc(in_form=True, form_method="post"), s) is None


# ------------------------------------------------------------ predicate


def test_predicate_all_clauses_must_hold():
    ok, ev = bl.predicate_holds(
        {"url_contains": "/done", "text": "Order number"},
        url="http://x/done",
        title="",
        text="Order number 1",
        selector_found=None,
    )
    assert ok and "url_contains" in ev
    ok, _ = bl.predicate_holds(
        {"url_contains": "/done", "text": "nope"},
        url="http://x/done",
        title="",
        text="",
        selector_found=None,
    )
    assert not ok


def test_predicate_empty_cannot_verify():
    assert (
        bl.predicate_holds({}, url="", title="", text="", selector_found=None)[0]
        is False
    )


def test_predicate_unknown_clause_fails():
    assert not bl.predicate_holds(
        {"eval": "1"}, url="", title="", text="", selector_found=None
    )[0]


# ------------------------------------------------------------ never-close wrappers


def test_noclose_proxy_blocks_close_and_context_wide_changes():
    class Inner:
        def close(self):
            raise AssertionError("must not be reached")

        def __init__(self):
            self.pages = ["p"]

    b = bl._NoClose(Inner(), bl._FORBIDDEN_BROWSER)
    c = bl._NoClose(Inner(), bl._FORBIDDEN_CONTEXT)
    for obj in (b, c):
        with pytest.raises(PermissionError):
            obj.close()
    for name in (
        "route",
        "add_init_script",
        "clear_cookies",
        "expose_binding",
        "new_page",
    ):
        with pytest.raises(PermissionError):
            getattr(c, name)
    assert c.pages == ["p"]


async def test_owned_page_refuses_on_sirs_tab():
    class P:
        url = "http://x/"

        async def goto(self, u):
            raise AssertionError

        async def close(self):
            raise AssertionError

    o = bl.OwnedPage(P(), created=False)
    for coro in (o.goto("http://y/"), o.close(), o.reload()):
        with pytest.raises(PermissionError):
            await coro


async def test_owned_page_goto_refuses_forbidden_even_in_own_tab():
    class P:
        url = "about:blank"

    with pytest.raises(PermissionError):
        await bl.OwnedPage(P(), created=True).goto("brave://settings")


def test_livebrave_exposes_no_close():
    assert not hasattr(bl.LiveBrave, "close")


# ------------------------------------------------------------ controls


def test_controls_without_run():
    assert bl.cancel() == "Nothing to stop, Sir."
    assert "nothing paused" in bl.carry_on()
    assert "already back" in bl.leave_there()


def test_controls_with_current_run():
    from aware import state as st

    st.write_private(bl._current_path(), "web-1")
    assert bl.cancel() == "Stopping, Sir."
    assert bl._cancel_requested("web-1")
    bl.carry_on()
    assert bl._flagged("web-1", "CARRY")


def test_p3_cancel_flag_honoured():
    from aware import state as st

    d = st.aware_dir() / "journal" / "web-2"
    d.mkdir(parents=True)
    (d / "CANCEL").write_text("1")
    assert bl._cancel_requested("web-2")


async def test_run_refuses_when_killed():
    from aware import state as st

    st.kill_path().write_text("")
    res = await bl.run(web_plan([{"op": "click", "target": "x"}]))
    assert res.status == "failed" and "off" in res.reason


async def test_run_bad_plan_touches_nothing():
    called = []
    res = await bl.run(web_plan([]), session_factory=lambda: called.append(1))
    assert res.status == "failed" and res.reason.startswith("plan") and not called


async def test_verify_without_record():
    ok, _why = await bl.verify(
        web_plan(), TakeoverResult(run_id="none", plan_id="p1", status="verified")
    )
    assert not ok


# ====================================================================
# Integration: throwaway headless Chromium + local fake checkout server
# ====================================================================

CHROME = Path.home() / ".cache/ms-playwright/chromium-1243/chrome-linux64/chrome"
EXT_SRC = Path(__file__).resolve().parents[1] / "desktop" / "brave" / "locke-ext"
ORDERS: list[dict] = []

FORM = """<!doctype html><title>Council form</title>
<script>let n=+(sessionStorage.getItem('n')||0)+1;sessionStorage.setItem('n',n);window.loads=n;</script>
<form method=post action="/submitted"><label>Postcode <input name=postcode id=pc></label>
<button type=submit>Submit</button></form>
<button onclick="document.getElementById('r').textContent='Ready'">Make ready</button>
<button>Pay now</button><div id=r></div>
<a href="/file.exe">Get file</a>"""

BASKET = """<!doctype html><title>Checkout | Test Shop</title><h1>Test Shop checkout</h1>
<p>Your basket</p><p>Samsung 990 EVO 2TB SSD x1 &pound;94.99</p>
<p>Subtotal &pound;94.99</p><p>Pay with saved card: Visa ending 4242</p>
<p>Order total</p><p>&pound;{total}</p>
<form method=post action="/order?{q}"><button type=submit>Place order</button></form>"""

DONE = """<!doctype html><title>Order confirmed | Test Shop</title>
<p>Thank you. Order number: TS-1001 is confirmed.</p><p>Order total &pound;94.99</p>"""

THREEDS = """<!doctype html><title>Verify payment</title>
<p>3-D Secure: approve this payment in your banking app.</p>"""


class Shop(BaseHTTPRequestHandler):
    def _send(self, body, ctype="text/html", extra=None):
        data = body.encode()
        self.send_response(200)
        self.send_header("content-type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/basket"):
            total = "99.98" if "drift" in self.path else "94.99"
            q = "3ds=1" if "3ds" in self.path else ""
            return self._send(BASKET.format(total=total, q=q))
        if self.path.startswith("/file.exe"):
            return self._send(
                "MZ",
                "application/octet-stream",
                {"content-disposition": "attachment; filename=x.exe"},
            )
        return self._send(FORM)

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode()
        if self.path.startswith("/order"):
            ORDERS.append({"path": self.path, "body": body})  # stubbed payment layer
            return self._send(THREEDS if "3ds=1" in self.path else DONE)
        return self._send("<title>Thanks</title><p>Form received</p>")

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def shop():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Shop)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def throwaway(tmp_path):
    if not CHROME.exists():
        pytest.skip("Playwright Chromium not installed")
    ext = tmp_path / "ext"
    ext.mkdir()
    for f in ("jarvis-tabs.js", "jarvis-tabs.html"):
        shutil.copy(EXT_SRC / f, ext / f)
    (ext / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "jarvis-tabs-test",
                "version": "1",
                "background": {"service_worker": "jarvis-tabs.js"},
                "permissions": ["tabs"],
            }
        )
    )
    proc = subprocess.Popen(
        [
            str(CHROME),
            "--headless=new",
            f"--user-data-dir={tmp_path / 'profile'}",
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            f"--load-extension={ext}",
            f"--disable-extensions-except={ext}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stderr=subprocess.PIPE,
        text=True,
    )
    port = None
    for line in proc.stderr:
        if "DevTools listening on" in line:
            port = int(line.rsplit(":", 1)[1].split("/")[0])
            break
    assert port and port != 9222
    yield {
        "port": port,
        "url": f"http://127.0.0.1:{port}",
        "ext_id": bl.extension_id(ext),
    }
    proc.terminate()
    proc.wait(10)


class NoKwin:
    def jarvis_window(self):
        return None

    def window_for_caption(self, title):
        return None

    def screen_of(self, uuid):
        return None

    def geometry(self, uuid):
        return None

    def place(self, uuid, placement):
        return True

    def move_to_screen(self, uuid, screen):
        return True

    def set_geometry(self, uuid, g):
        return True


def factory(tw):
    return lambda: bl.LiveBrave(
        tw["url"],
        ext_ids={tw["ext_id"]},
        kwin=NoKwin(),
        screens=lambda: [],
        main=lambda s: None,
        hud=lambda s: (None, None),
        active=lambda: {"id": "A"},
    )


async def sir_tabs(tw, *urls):
    """Open 'Sir's' tabs through a separate setup connection; return target ids."""
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    b = await pw.chromium.connect_over_cdp(tw["url"])
    pages = []
    for u in urls:
        p = await b.contexts[0].new_page()
        await p.goto(u)
        pages.append(p)
    await asyncio.sleep(0.5)
    listing = json.loads(urllib.request.urlopen(f"{tw['url']}/json/list").read())
    ids = []
    for p in pages:
        s = await b.contexts[0].new_cdp_session(p)
        ids.append((await s.send("Target.getTargetInfo"))["targetInfo"]["targetId"])
        await s.detach()
    assert all(any(t["id"] == i for t in listing) for i in ids)
    return pw, b, pages, ids


async def ext_tabs(tw):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp(tw["url"], no_defaults=True)
        for _ in range(50):
            sw = [w for w in b.contexts[0].service_workers if tw["ext_id"] in w.url]
            if sw:
                return await sw[0].evaluate(
                    "chrome.tabs.query({}).then(ts=>ts.map(t=>({id:t.id,w:t.windowId,"
                    "i:t.index,a:t.active,title:t.title,url:t.url})))"
                )
            await asyncio.sleep(0.1)
    raise AssertionError("worker not found")


def checkout_plan(shop, tid, path="/basket", amount="94.99", steps=None):
    act = Consequential(
        kind="payment",
        site="127.0.0.1",
        merchant="Test Shop",
        amount=amount,
        currency="GBP",
        items=["Samsung 990 EVO 2TB SSD"],
        payment_method="saved Visa ending 4242",
    )
    return web_plan(
        steps or [{"op": "click", "target": "Place order", "action": 0}],
        actions=[act],
        tab_id=tid,
        url=shop + path,
        host="127.0.0.1",
        success_predicate={"text": "Order number"},
    )


slow = pytest.mark.slow


@slow
async def test_live_takeover_keeps_state_moves_back_and_never_reloads(throwaway, shop):
    pw, _b, pages, (_mine, task, _other) = await sir_tabs(
        throwaway, shop + "/a", shop + "/form", shop + "/c"
    )
    try:
        await pages[1].fill("#pc", "typed-by-sir")
        before = await ext_tabs(throwaway)
        plan = web_plan(
            [
                {"op": "type", "target": "Postcode", "text": "SW1A 1AA"},
                {"op": "click", "target": "Make ready"},
                {"op": "wait_for", "target": "Ready"},
            ],
            tab_id=task,
            url=shop + "/form",
            host="127.0.0.1",
            success_predicate={"text": "Ready"},
        )
        seen = []
        res = await bl.run(
            plan,
            progress=lambda f, t: seen.append(t),
            session_factory=factory(throwaway),
        )
        assert res.status == "verified", (res.reason, res.spoken)
        assert "back in your tab bar" in res.spoken
        assert await pages[1].evaluate("window.loads") == 1  # never reloaded
        assert await pages[1].input_value("#pc") == "SW1A 1AA"
        after = await ext_tabs(throwaway)
        pos = lambda rows: [(r["w"], r["i"], r["url"]) for r in rows]  # noqa: E731
        assert sorted(pos(after)) == sorted(pos(before))  # same window, same index
        task_row = next(r for r in after if r["url"].endswith("/form"))
        assert not task_row["a"] and not task_row["title"].startswith(ts.MARK)
        assert ts.load_journal(res.run_id)["state"] == "handed_back"
        ok, ev = await bl.verify(plan, res, session_factory=factory(throwaway))
        assert ok, ev
    finally:
        await pw.stop()


@slow
async def test_live_undeclared_pay_button_stops_and_hands_back(throwaway, shop):
    pw, _b, _pages, (task,) = await sir_tabs(throwaway, shop + "/form")
    try:
        plan = web_plan(
            [{"op": "click", "target": "Pay now"}],
            tab_id=task,
            url=shop + "/form",
            success_predicate={"text": "x"},
        )
        res = await bl.run(plan, session_factory=factory(throwaway))
        assert res.status == "stuck" and "undeclared_consequential" in res.reason
        assert "Done" not in res.spoken
        assert ts.load_journal(res.run_id)["state"] == "handed_back"
    finally:
        await pw.stop()


@slow
async def test_live_matching_payment_proceeds_and_is_audited(throwaway, shop):
    ORDERS.clear()
    pw, _b, _pages, (task,) = await sir_tabs(throwaway, shop + "/basket")
    try:
        res = await bl.run(
            checkout_plan(shop, task), session_factory=factory(throwaway)
        )
        assert res.status == "verified", (res.reason, res.spoken)
        assert len(ORDERS) == 1
        row = tx.read_all()[-1]
        assert row["result"] == "ok:verified" and row["amount"] == "94.99"
        assert (
            row["confirmation_ref"] == "TS-1001" and row["counterparty"] == "Test Shop"
        )
        assert res.spoken.startswith("Paid £94.99 to Test Shop")
    finally:
        await pw.stop()


@slow
async def test_live_drifted_total_never_clicks(throwaway, shop):
    ORDERS.clear()
    pw, _b, _pages, (task,) = await sir_tabs(throwaway, shop + "/basket?drift")
    try:
        res = await bl.run(
            checkout_plan(shop, task, "/basket?drift"),
            session_factory=factory(throwaway),
        )
        assert res.status == "stuck" and "amount" in res.reason
        assert ORDERS == []  # nothing was clicked
        row = tx.read_all()[-1]
        assert row["result"].startswith("blocked") and row["amount"] == "99.98"
    finally:
        await pw.stop()


@slow
async def test_live_spend_cap_blocks(throwaway, shop):
    from aware import payments as pay

    ORDERS.clear()
    pay.set_spend_cap("50", "GBP")
    pw, _b, _pages, (task,) = await sir_tabs(throwaway, shop + "/basket")
    try:
        res = await bl.run(
            checkout_plan(shop, task), session_factory=factory(throwaway)
        )
        assert res.status == "stuck" and "cap" in res.reason
        assert ORDERS == []
        assert tx.read_all()[-1]["result"].startswith("blocked")
    finally:
        await pw.stop()


@slow
async def test_live_3ds_after_click_fails_safe(throwaway, shop):
    ORDERS.clear()
    pw, _b, _pages, (task,) = await sir_tabs(throwaway, shop + "/basket?3ds")
    try:
        res = await bl.run(
            checkout_plan(shop, task, "/basket?3ds"), session_factory=factory(throwaway)
        )
        assert res.status == "failed" and res.reason.startswith("needs_user")
        assert "bank" in res.spoken
        assert tx.read_all()[-1]["result"].startswith("failed:needs_user")
    finally:
        await pw.stop()


@slow
async def test_live_user_input_pauses_then_carry_on(throwaway, shop):
    pw, _b, pages, (task,) = await sir_tabs(throwaway, shop + "/form")
    try:
        plan = web_plan(
            [
                {"op": "type", "target": "Postcode", "text": "AB1"},
                {"op": "wait_for", "target": "css=#r:not(:empty)"},
            ],
            tab_id=task,
            url=shop + "/form",
            success_predicate={"selector": "#r:not(:empty)", "text": "Ready"},
        )
        seen = []
        job = asyncio.create_task(
            bl.run(
                plan,
                progress=lambda f, t: seen.append(t),
                session_factory=factory(throwaway),
            )
        )
        for _ in range(100):
            if any(t.startswith("step 1") for t in seen):
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        # The throwaway task window is minimised: rAF stability polling stalls.
        # force skips that polling but still dispatches trusted browser input.
        await pages[0].get_by_role("button", name="Make ready").click(force=True)
        for _ in range(100):
            if any(t.startswith("paused:") for t in seen):
                break
            await asyncio.sleep(0.1)
        assert any(t.startswith("paused:") for t in seen)
        bl.carry_on()
        res = await asyncio.wait_for(job, 60)
        assert res.status == "verified", (res.reason, res.spoken)
    finally:
        await pw.stop()


@slow
async def test_live_download_is_cancelled_and_stops(throwaway, shop):
    pw, _b, _pages, (task,) = await sir_tabs(throwaway, shop + "/form")
    try:
        plan = web_plan(
            [{"op": "click", "target": "Get file"}],
            tab_id=task,
            url=shop + "/form",
            success_predicate={"text": "never"},
        )
        res = await bl.run(plan, session_factory=factory(throwaway))
        assert res.status == "stuck" and "download" in res.reason
    finally:
        await pw.stop()


@slow
async def test_live_crash_recovery_returns_tab(throwaway, shop):
    pw, _b, pages, (_mine, task) = await sir_tabs(
        throwaway, shop + "/a", shop + "/form"
    )
    try:
        before = await ext_tabs(throwaway)
        # split, then "crash" (no hand-back)
        async with factory(throwaway)() as lb:
            page = await lb.page_for_target(task)
            await lb.mark(page)
            tab_id = ts.resolve_tab(await lb.tab_api().query(), page.url)
            await lb.tabsplit().split("crashed", [tab_id])
        mid = await ext_tabs(throwaway)
        assert len({r["w"] for r in mid}) == len({r["w"] for r in before}) + 1
        done = await bl.recover(session_factory=factory(throwaway))
        assert done == ["crashed"]
        after = await ext_tabs(throwaway)
        pos = lambda rows: sorted((r["w"], r["i"], r["url"]) for r in rows)  # noqa: E731
        assert pos(after) == pos(before)
        assert not any(r["title"].startswith(ts.MARK) for r in after)
        assert await pages[1].evaluate("window.loads") == 1
    finally:
        await pw.stop()


@slow
async def test_live_new_tab_mode_closed_on_failure(throwaway, shop):
    pw, _b, _pages, (_mine,) = await sir_tabs(throwaway, shop + "/a")
    try:
        before = await ext_tabs(throwaway)
        plan = web_plan(
            [{"op": "wait_for", "target": "Postcode"}],
            new_tab=True,
            url=shop + "/form",
            success_predicate={"text": "not there"},
        )
        res = await bl.run(plan, session_factory=factory(throwaway))
        assert res.status == "failed"
        after = await ext_tabs(throwaway)
        assert sorted(r["url"] for r in after) == sorted(r["url"] for r in before)
    finally:
        await pw.stop()


def test_no_test_targets_live_port():
    assert (
        os.environ.get("JARVIS_BRAVE_CDP_PORT", "9222") == "9222"
    )  # untouched by these tests


@pytest.mark.parametrize("final", [True, False])
def test_neutral_post_submit_requires_final_declared_action(final):
    d = desc(name="Continue", type="submit", in_form=True, form_method="post")
    assert bl.guard_click(d, bl.WebStep("click", "Continue", final=final))
    assert bl.guard_click(d, bl.WebStep("click", "Continue", action=0, final=False))
    assert bl.guard_click(d, bl.WebStep("click", "Continue", action=0)) is None


@pytest.mark.parametrize(
    "key", ["Control+Enter", "Meta+Enter", "Shift+Enter", "Space", " "]
)
def test_activation_keys_cannot_bypass_final_guard(key):
    d = desc(name="Pay now", type="submit", in_form=True, form_method="post")
    assert bl.guard_press(key, d, bl.WebStep("press", "", key))
    assert bl.guard_press(key, d, bl.WebStep("press", "", key, action=0, final=False))


def test_enter_on_button_outside_form_requires_action():
    assert bl.guard_press("Enter", desc(name="Send"), bl.WebStep("press", "", "Enter"))


@pytest.mark.parametrize("action", ["garbage", 0.5, True, [], {}])
def test_action_index_must_be_integer(action):
    with pytest.raises(bl.PlanError):
        bl.parse_steps(
            web_plan(
                [{"op": "click", "target": "Send", "action": action}],
                actions=[Consequential(kind="send")],
            )
        )


@pytest.mark.parametrize("raw", ["oops", {}, False, 10])
def test_explicit_web_steps_cannot_fall_back(raw):
    plan = web_plan()
    plan.targets["web_steps"] = raw
    plan.steps = [PlanStep("Next", "click")]
    with pytest.raises(bl.PlanError):
        bl.parse_steps(plan)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_unmark_uses_title_as_well_as_duplicate_url(ambiguous):
    from types import SimpleNamespace

    class Page:
        url = "https://example.com/same"

        def __init__(self, title):
            self.label = title
            self.changed = False

        async def title(self):
            return self.label

        async def evaluate(self, *args):
            self.changed = True

    wrong, owned = (
        Page(ts.MARK + ("Task" if ambiguous else "Other tab")),
        Page(ts.MARK + "Task"),
    )

    class Api:
        async def query(self):
            return [{"id": 3, "url": owned.url, "title": owned.label}]

    live = bl.LiveBrave()
    live.context = SimpleNamespace(pages=[wrong, owned])
    live.tab_api = lambda: Api()
    asyncio.run(live.unmark_tab(3))
    assert owned.changed is not ambiguous
    assert not wrong.changed


def test_parse_unknown_fallback_op_is_not_silently_skipped():
    plan = web_plan()
    plan.targets = {}
    plan.steps = [PlanStep("x", "web.hover"), PlanStep("Next", "click")]
    with pytest.raises(bl.PlanError):
        bl.parse_steps(plan)


@pytest.mark.parametrize("final", ["false", 0, None])
def test_parse_final_flag_must_be_boolean(final):
    with pytest.raises(bl.PlanError):
        bl.parse_steps(web_plan([{"op": "click", "target": "Next", "final": final}]))


@pytest.mark.parametrize("switch", ["state", "env"])
def test_disabled_run_never_opens_browser(switch, monkeypatch):
    if switch == "state":
        bl.st.update(enabled=False)
    else:
        monkeypatch.setenv("JARVIS_AWARE", "off")

    def unexpected_browser():
        pytest.fail("disabled run opened a browser")

    result = asyncio.run(
        bl.run(
            web_plan([{"op": "click", "target": "Next"}]),
            session_factory=unexpected_browser,
        )
    )
    assert result.status == "failed"
    assert result.reason == "always aware is off"


@pytest.mark.parametrize("switch", ["state", "env"])
@pytest.mark.parametrize("paused", [False, True])
def test_running_browser_stops_when_aware_disabled(switch, paused, monkeypatch):
    live = bl.LiveBrave()
    live.user_input_at = 1 if paused else 0
    runner = bl._Runner(web_plan(), None, "test-run", live)
    if switch == "state":
        bl.st.update(enabled=False)
    else:
        monkeypatch.setenv("JARVIS_AWARE", "off")
    with pytest.raises(bl.StopError) as exc:
        asyncio.run(
            asyncio.wait_for(runner._paused() if paused else runner.checkpoint(), 0.1)
        )
    assert exc.value.status == "cancelled"


async def test_transaction_intent_failure_prevents_final_click(monkeypatch):
    action = Consequential(kind="submit", site="example.com")
    runner = bl._Runner(web_plan(actions=[action]), None, "test-run", bl.LiveBrave())

    async def snapshot():
        return bl.pay.PageSnapshot(url="https://example.com/form")

    class Location:
        async def click(self, **kw):
            pytest.fail("clicked after transaction intent failed")

    runner.page_state = snapshot
    monkeypatch.setattr(bl.pay, "audit_writable", lambda: True)
    append = bl.st.append_private

    def fail_transaction(path, text):
        return False if path == tx.path() else append(path, text)

    monkeypatch.setattr(bl.st, "append_private", fail_transaction)
    with pytest.raises(OSError, match="transaction log"):
        await runner.final_action(bl.WebStep("click", "Continue", action=0), Location())


@pytest.mark.parametrize("key", ["a", "4", "Control+V"])
@pytest.mark.parametrize(
    "field",
    [
        {"type": "password"},
        {"autocomplete": "one-time-code"},
        {"autocomplete": "cc-number"},
    ],
)
def test_keypress_cannot_type_into_sensitive_fields(key, field):
    assert bl.guard_press(key, desc(**field), bl.WebStep("press", "css=input", key))
