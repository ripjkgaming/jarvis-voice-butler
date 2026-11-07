"""Review flow never automatically executes, resumes or retries."""
import agent_tasks as tasks
from power_foundation.recovery import cancel_after_review, reconcile


def test_recovery_requires_fresh_private_review_identity(monkeypatch, tmp_path):
    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    record = {'id': 'task-review', 'status': 'running', 'created': 1,
              'executor_started': True, 'goal': 'private',
              'steps': [{'status': 'running', 'kind': 'consequential',
                         'approved': True, 'approval_request_id': 'private'}]}
    assert tasks.save(record)
    assert reconcile()[0]['status'] == 'recovery_required'
    state = tasks.get('task-review')
    review = state['recovery']['review_id']
    assert not cancel_after_review('task-review', state['revision'] - 1, review)
    assert not cancel_after_review('task-review', state['revision'], '0' * 32)
    assert tasks.get('task-review')['status'] == 'recovery_required'
    done = cancel_after_review('task-review', state['revision'], review)
    assert done['status'] == 'cancelled'
    assert not tasks.get('task-review')['steps'][0].get('approved')
    assert 'approval_request_id' not in tasks.get('task-review')['steps'][0]
    assert cancel_after_review('task-review', state['revision'], review) is None
    assert reconcile() == []
