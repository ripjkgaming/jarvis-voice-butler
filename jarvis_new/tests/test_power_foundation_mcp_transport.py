"""Local-only MCP protocol integration with no discovered-tool execution."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from power_foundation.mcp_transport import _validate_url, inspect_server


@pytest.mark.parametrize('url', [
    'https://127.0.0.1:9999/mcp', 'http://localhost:9999/mcp',
    'http://169.254.169.254:80/mcp', 'http://127.0.0.1:9999/redirect',
    'http://127.0.0.1:9999/mcp?x=1',
])
def test_reject_nonliteral_or_nonlocal(url):
    with pytest.raises(ValueError):
        _validate_url(url)


def test_mcp_handshake_local_server_only():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(n))
            calls.append(data['method'])
            if data['method'] == 'notifications/initialized':
                self.send_response(202)
                self.end_headers()
                return
            if data['method'] == 'initialize':
                result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'local-test', 'version': '1'}}
            elif data['method'] == 'tools/list':
                result = {'tools': [{'name': 'inspect', 'inputSchema': {'type': 'object'},
                         'annotations': {'readOnlyHint': True}},
                         {'name': 'delete', 'inputSchema': {'type': 'object'}}]}
            else:
                self.send_error(400)
                return
            out = json.dumps({'jsonrpc': '2.0', 'id': data['id'], 'result': result}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        found = inspect_server(f'http://127.0.0.1:{server.server_port}/mcp')
        assert found['status'] == 'metadata_received'
        assert all(not t['executable'] for t in found['tools'])
        assert calls == ['initialize', 'notifications/initialized', 'tools/list']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
