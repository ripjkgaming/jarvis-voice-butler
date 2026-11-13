import json

from calls import wa_call
from calls.wa_call import CdpCallDriver, FakeDriver, IncomingCall


def driver(responses):
    seen = []

    async def ev(expr):
        seen.append(expr)
        r = responses.pop(0) if responses else None
        if isinstance(r, Exception):
            raise r
        return r

    return CdpCallDriver(ev), seen


async def test_detect_none_and_some():
    d, _ = driver([json.dumps(None), json.dumps({"caller": "Mum", "text": "x"})])
    assert await d.detect_incoming() is None
    inc = await d.detect_incoming()
    assert inc.caller == "Mum"


async def test_no_page_returns_none_false():
    d, _ = driver([None, None, None])
    assert await d.detect_incoming() is None
    assert await d.accept() is False
    assert await d.is_in_call() is False


async def test_evaluate_exception_swallowed():
    d, _ = driver([RuntimeError("boom")])
    assert await d.accept() is False


async def test_click_ok_and_selector_miss_logs(caplog):
    d, seen = driver(["true", "false"])
    assert await d.accept() is True
    with caplog.at_level("ERROR", logger="jarvis.calls.wa"):
        assert await d.decline() is False
    assert "SELECTOR MISS" in caplog.text and "decline" in caplog.text
    assert "call-accept" in seen[0]


async def test_dial_ok_and_miss(caplog):
    d, seen = driver([json.dumps("ok"), json.dumps("chat_voice_call")])
    assert await d.dial("  Mum ") is True
    assert '"Mum"' in seen[0]
    with caplog.at_level("ERROR", logger="jarvis.calls.wa"):
        assert await d.dial("Dad") is False
    assert "chat_voice_call" in caplog.text
    assert await d.dial("   ") is False


async def test_is_in_call():
    d, _ = driver(["true", "false"])
    assert await d.is_in_call() is True
    assert await d.is_in_call() is False


def test_selector_table_in_js_and_nonempty():
    assert all(v for v in wa_call.SELECTORS.values())
    js = wa_call.js_click("hangup")
    assert "call-end" in js and js.startswith("(async()=>{")


async def test_fake_driver_flow():
    f = FakeDriver()
    assert await f.detect_incoming() is None
    f.ring("Mum")
    assert isinstance(await f.detect_incoming(), IncomingCall)
    assert await f.accept() and await f.is_in_call()
    assert await f.hangup() and not await f.is_in_call()
    f.ring()
    assert await f.decline() and await f.detect_incoming() is None
    assert await f.dial("x") and not await f.dial("y")
    f.fail.add("hangup")
    assert not await f.hangup()
