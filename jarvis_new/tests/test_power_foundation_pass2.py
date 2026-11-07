"""Safe integration contracts: no live voice, browser, or external actions."""
import json


def test_status_default_off_is_inert(monkeypatch, tmp_path):
    from power_foundation.integration import status

    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    monkeypatch.delenv('JARVIS_POWER_FOUNDATION', raising=False)
    assert status() == {'ok': False, 'enabled': False, 'status': 'disabled'}
    assert not (tmp_path / 'foundation').exists()


def test_native_tasks_are_read_only_and_redacted(monkeypatch, tmp_path):
    import agent_tasks
    from power_foundation.integration import status

    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    monkeypatch.setenv('JARVIS_POWER_FOUNDATION', '1')
    task = {'id': 'task-123', 'status': 'waiting', 'title': 'Draft reply',
            'goal': 'secret goal', 'steps': [
                {'title': 'Research', 'kind': 'research', 'status': 'done', 'output': 'private output'},
                {'title': 'Send it', 'kind': 'consequential', 'status': 'waiting', 'detail': 'secret'}],
            'revision': 7, 'created': 123.0, 'updated': 123.0}
    assert agent_tasks.save(task)
    output = status()
    assert output['enabled'] is True
    assert output['native_tasks']['count'] == 1
    public = output['native_tasks']['items'][0]
    assert public['status'] == 'waiting'
    assert public['completed_steps'] == 1
    assert public['requires_approval'] is True
    assert public['verification'] == 'not_checked'
    assert 'secret' not in json.dumps(output)
    assert agent_tasks.get('task-123')['revision'] == 8


def test_native_checkpoints_opt_in_only(monkeypatch, tmp_path):
    import agent_tasks
    from power_foundation.engine import FoundationStore

    monkeypatch.setenv('JARVIS_HOME', str(tmp_path))
    monkeypatch.setenv('JARVIS_POWER_FOUNDATION', '1')
    task = {'id': 'task-456', 'goal': 'private contents', 'title': 'read',
            'status': 'planning', 'steps': [], 'created': 1.0}
    assert agent_tasks.save(task)
    dbpath = tmp_path / 'foundation' / 'state.sqlite3'
    assert not dbpath.exists()
    monkeypatch.setenv('JARVIS_POWER_TASK_AUDIT', '1')
    task['status'] = 'running'
    assert agent_tasks.save(task)
    events = FoundationStore(dbpath).events()
    assert len(events) == 1
    assert events[0]['kind'] == 'native_task_checkpoint'
    assert 'private' not in json.dumps(events)


def test_claim_validation_never_equates_attribution_with_truth(tmp_path):
    from power_foundation.engine import FoundationStore
    from power_foundation.integration import claim_evidence

    store = FoundationStore(tmp_path / 'test.db')
    store.remember('The window was moved to monitor 2', 'tool:window_observer')
    supported = claim_evidence(store, 'The window was moved to monitor 2')
    assert supported['status'] == 'attributed_unverified'
    assert supported['source'] == 'tool:window_observer'
    assert claim_evidence(store, 'WhatsApp image sent successfully')['status'] == 'unsupported'
    assert claim_evidence(store, '')['status'] == 'unsupported'


def test_mcp_discovery_requires_manual_local_allowlist(tmp_path):
    from power_foundation.mcp_discovery import load_allowlist

    path = tmp_path / 'approved.json'
    assert load_allowlist(path) == []
    path.write_text(json.dumps({'servers': [
        {'name': 'local', 'url': 'http://127.0.0.1:6767/mcp', 'approved': True},
        {'name': 'remote', 'url': 'https://example.com/mcp', 'approved': True},
        {'name': 'pending', 'url': 'http://localhost:9090/mcp', 'approved': False}]}))
    approved = load_allowlist(path)
    assert len(approved) == 1
    assert approved[0]['name'] == 'local'


def test_mcp_discovery_does_not_execute_tools(tmp_path):
    from power_foundation.mcp_discovery import discover_tools

    def fake_listing(url):
        assert url == 'http://127.0.0.1:6767/mcp'
        return [{'name': 'read_asset', 'description': 'Read a local asset',
                 'annotations': {'readOnlyHint': True}},
                {'name': 'remove_asset', 'description': 'Delete',
                 'annotations': {'readOnlyHint': False}}]

    result = discover_tools([{'name': 'local', 'url': 'http://127.0.0.1:6767/mcp'}],
                            list_tools=fake_listing)
    assert [x['name'] for x in result['tools']] == ['read_asset']
    assert result['tools'][0]['executable'] is False
    assert result['servers'][0]['status'] == 'metadata_received'
