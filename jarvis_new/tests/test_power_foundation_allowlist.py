import json

from power_foundation.mcp_transport import inspect_approved


def test_manual_approved_mcp_local_only(tmp_path):
    p = tmp_path / 'allow.json'
    p.write_text(json.dumps({'servers': [
        {'name': 'approved', 'approved': True, 'url': 'http://127.0.0.1:13000/mcp'},
        {'name': 'outside', 'approved': True, 'url': 'http://10.0.0.1:13000/mcp'},
        {'name': 'unreviewed', 'approved': False, 'url': 'http://127.0.0.1:13002/mcp'},
    ]}))
    seen = []
    def fake(url):
        seen.append(url)
        return {'status': 'metadata_received', 'protocol': '2025-03-26',
                'tools': [{'name': 'inspect', 'executable': False}]}
    report = inspect_approved(p, inspector=fake)
    assert seen == ['http://127.0.0.1:13000/mcp']
    assert report['checked'] == 1
    assert report['servers'][0]['executable'] is False


def test_mcp_untrusted_exception_is_redacted(tmp_path):
    p = tmp_path / 'allow.json'
    p.write_text(json.dumps({'servers': [
        {'name': 'local', 'approved': True, 'url': 'http://127.0.0.1:1234/mcp'}]}))
    def error(_):
        raise ValueError('secret-bearing server failure')
    report = inspect_approved(p, inspector=error)
    assert 'secret' not in json.dumps(report)
    assert report['servers'][0]['status'] == 'connection_unverified'
