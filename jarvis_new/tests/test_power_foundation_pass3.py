"""Crash reconciliation uses native locks and never dispatches an action."""
import agent_tasks as tasks
from power_foundation.recovery import reconcile


def _task(task_id, **updates):
    state = {'id': task_id, 'goal': 'secret payload', 'title': 'Private task',
                 'status': 'running', 'created': 1.0, 'executor_started': True,
                 'steps': [{'kind': 'write', 'title': 'draft', 'status': 'running',
                             'output': 'secret output'}]}
    state.update(updates)
    assert tasks.save(state)
    return state


def test_crashed_step_requires_review_and_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    _task('task-crash')
    changed = reconcile()
    assert len(changed) == 1
    assert changed[0]['reason'] == 'executor_interrupted'
    recovered = tasks.get('task-crash')
    assert recovered['status'] == 'recovery_required'
    assert recovered['recovery']['review_required'] is True
    assert recovered['steps'][0]['status'] == 'running'
    assert reconcile() == []
    assert tasks.advance('task-crash') == recovered
    assert tasks.approve('task-crash') is None


def test_orphaned_cancelling_settled_and_approval_cleared(monkeypatch, tmp_path):
    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    _task('task-stop', status='cancelling',
          steps=[{'kind': 'consequential', 'status': 'pending', 'approved': True}])
    assert reconcile()[0]['status'] == 'cancelled'
    state = tasks.get('task-stop')
    assert state['status'] == 'cancelled'
    assert not state['steps'][0].get('approved')
    assert reconcile() == []


def test_clean_pending_remains_resumable(monkeypatch, tmp_path):
    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    _task('task-clean', executor_started=False,
          steps=[{'kind': 'think', 'status': 'pending'}])
    assert reconcile() == []
    assert tasks.get('task-clean')['status'] == 'running'


def test_held_lease_does_not_quarantine_live_executor(monkeypatch, tmp_path):
    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    _task('task-live')
    with tasks._execution_lock('task-live') as acquired:
        assert acquired
        assert reconcile() == []
    assert tasks.get('task-live')['status'] == 'running'


def test_recovery_audit_is_redacted(monkeypatch, tmp_path):
    from power_foundation.engine import FoundationStore
    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    monkeypatch.setenv('JARVIS_POWER_FOUNDATION', '1')
    monkeypatch.setenv('JARVIS_POWER_TASK_AUDIT', '1')
    _task('task-audited')
    reconcile()
    events = FoundationStore(tmp_path / 'foundation' / 'state.sqlite3').events()
    assert any(event['kind'] == 'native_task_recovery' for event in events)
    assert 'secret' not in str(events)
