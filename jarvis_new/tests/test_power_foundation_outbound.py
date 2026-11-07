"""Opt-in outbound mail crash ledger is durable, redacted and single-use."""
import sqlite3
import time

import pytest
from jarvis_rt import ToolError

from power_foundation.operations import OutboundLedger, fingerprint
from system.inbox import InboxTools


def test_claim_persists_across_connections_and_no_payload(tmp_path):
    path = tmp_path / 'outbound.sqlite3'
    digest = fingerprint({'to': 'private@example.com', 'body': 'SECRET BODY'})
    a = OutboundLedger(path)
    assert a.claim('request-123456', 'gmail_send', digest)
    assert not OutboundLedger(path).claim('request-123456', 'gmail_send', digest)
    assert a.summary()['uncertain'] == 1
    assert a.settle('request-123456', 'completed_unverified')
    assert not a.settle('request-123456', 'verified')
    assert a.summary()['states']['completed_unverified'] == 1
    data = ' '.join(map(str, sqlite3.connect(path).execute('SELECT * FROM outbound_attempts').fetchone()))
    assert 'private@example.com' not in data
    assert 'SECRET BODY' not in data


@pytest.mark.asyncio
async def test_gmail_gate_ledger_success_is_unverified(monkeypatch, tmp_path):
    import system.inbox as inbox

    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    monkeypatch.setenv('JARVIS_LOCAL', '1')
    monkeypatch.setenv('JARVIS_POWER_OUTBOUND_LEDGER', '1')
    monkeypatch.setattr(inbox, '_gmail_access_token', lambda: 'local-dummy')
    sends = []
    monkeypatch.setattr(inbox, '_gmail_send_api', lambda *args, **kwargs: sends.append(args) or {'id':'id1'})
    obj = InboxTools()
    await InboxTools.confirm_email_action(obj, None, 'me@example.com', 'Sub', 'Body')
    req = obj._confirmed_request
    assert req
    await InboxTools.gmail_send(obj, None, 'me@example.com', 'Sub', 'Body')
    assert len(sends) == 1
    ledger = OutboundLedger(tmp_path / 'foundation' / 'outbound.sqlite3')
    assert ledger.summary()['states'] == {'completed_unverified': 1}
    assert not ledger.claim(req, 'gmail_send', fingerprint({'to':'me@example.com', 'subject':'Sub', 'body':'Body'}))
    with pytest.raises(ToolError):
        await InboxTools.gmail_send(obj, None, 'me@example.com', 'Sub', 'Body')


@pytest.mark.asyncio
async def test_gmail_expiry_and_network_uncertainty(monkeypatch, tmp_path):
    import system.inbox as inbox

    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    monkeypatch.setenv('JARVIS_LOCAL', '1')
    monkeypatch.setenv('JARVIS_POWER_OUTBOUND_LEDGER', '1')
    monkeypatch.setattr(inbox, '_gmail_access_token', lambda: 'dummy')
    obj = InboxTools()
    await InboxTools.confirm_email_action(obj, None, 'me@example.com', 'Sub', 'Body')
    obj._confirmation_expires_at = time.monotonic() - 1
    with pytest.raises(ToolError, match='expired'):
        await InboxTools.gmail_send(obj, None, 'me@example.com', 'Sub', 'Body')
    assert not (tmp_path / 'foundation').exists()
    await InboxTools.confirm_email_action(obj, None, 'me@example.com', 'Sub', 'Body')
    def ambiguous(*_args, **_kwargs):
        raise ConnectionError('no response after possible dispatch')
    monkeypatch.setattr(inbox, '_gmail_send_api', ambiguous)
    with pytest.raises(ConnectionError):
        await InboxTools.gmail_send(obj, None, 'me@example.com', 'Sub', 'Body')
    ledger = OutboundLedger(tmp_path / 'foundation' / 'outbound.sqlite3')
    assert ledger.summary()['states'] == {'failed_unverified': 1}
    assert ledger.summary()['uncertain'] == 1


@pytest.fixture(autouse=True)
def _explicit_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests cover the confirm-phrase gates, so owner pre-approval is off."""
    monkeypatch.setenv("JARVIS_AUTO_APPROVE", "0")
