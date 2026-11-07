"""Gate -> serialised plan -> real executor, on an isolated local form only."""

import asyncio

import pytest
from test_aware_brave import _home as _home
from test_aware_brave import factory, sir_tabs
from test_aware_brave import shop as shop
from test_aware_brave import throwaway as throwaway
from test_aware_gate import FakeProbes, verifier, web_opp

from aware import brave_live as bl
from aware import cdp, gate, transactions
from aware.types import TakeoverPlan


@pytest.mark.slow
async def test_gate_plan_executes_and_verifies_audited_local_submit(
    tmp_path, throwaway, shop
):
    class LocalProbes(FakeProbes):
        # Real read-only DOM inspection, scoped to this throwaway's target URL.
        page = gate.Probes.page
        dom_check = gate.Probes.dom_check

    pw, _browser, pages, (_user, task) = await sir_tabs(
        throwaway, shop + "/a", shop + "/form"
    )
    try:
        await pages[0].fill("#pc", "user-is-still-working")
        probes = LocalProbes(tmp_path)
        probes.tab_list = await asyncio.to_thread(cdp.targets, throwaway["port"])
        opportunity = web_opp(
            evidence={"tab_id": task, "success_predicate": {"text": "Form received"}},
            success_check='page shows "Form received"',
        )
        review = verifier(
            plan=[
                {
                    "step": "Fill postcode",
                    "tool": "type",
                    "target": "Postcode",
                    "text": "AB1 2CD",
                },
                {
                    "step": "Submit form",
                    "tool": "click",
                    "target": "Submit",
                    "action": 0,
                    "final": True,
                },
            ]
        )
        approved = await gate.evaluate(
            opportunity, probes=probes, ask_model=review, threshold=0.75
        )
        assert approved.passed, approved.reasons
        plan = TakeoverPlan.from_dict(approved.plan.to_dict())
        plan.offer_text = "Submit this local test form with postcode AB1 2CD."
        result = await bl.run(plan, session_factory=factory(throwaway))
        assert result.status == "verified", (result.reason, result.spoken)
        ok, evidence = await bl.verify(plan, result, session_factory=factory(throwaway))
        assert ok and "Form received" in evidence
        assert await pages[0].input_value("#pc") == "user-is-still-working"
        assert await pages[0].evaluate("window.loads") == 1
        receipt = transactions.read_all()[-1]
        assert receipt["kind"] == "submit" and receipt["result"] == "ok:verified"
    finally:
        await pw.stop()
