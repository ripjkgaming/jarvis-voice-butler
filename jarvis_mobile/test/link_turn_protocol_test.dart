import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:jarvis_mobile/core/link_api.dart';

void main() {
  test(
    'retry keeps turn ID and idempotency key, cancel scopes same client',
    () async {
      final requests = <http.Request>[];
      final api = LinkApi(
        base: 'http://127.0.0.1:4320',
        token: 'test',
        httpClient: MockClient((r) async {
          requests.add(r);
          return http.Response('{"ok":true,"turn_id":42}', 200);
        }),
      );
      await api.chatTurn('send once', [], 42);
      await api.chatTurn('send once', [], 42);
      await api.cancelTurn(42);
      final first = jsonDecode(requests[0].body);
      expect(jsonDecode(requests[1].body), first);
      expect(first['turn_id'], 42);
      expect(first['idem_key'], isNotEmpty);
      expect(jsonDecode(requests[2].body), {
        'turn_id': 42,
        'client_id': first['client_id'],
      });
      expect(requests[2].url.path, '/cancel');
      api.close();
    },
  );
}
