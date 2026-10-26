import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/chat_ctrl.dart';

void main() {
  test('errorText distinguishes timeout from unreachable', () {
    final t = ChatCtrl.errorText({'ok': false, 'error': 'timeout'});
    final u = ChatCtrl.errorText({'ok': false, 'error': 'unreachable'});
    expect(t, contains('too long'));
    expect(u, contains('reach'));
    expect(t, isNot(u));
    expect(ChatCtrl.errorText({'ok': false}), contains('quiet'));
  });

  test('history trims to last 20 entries as pairs', () {
    final log = List.generate(30, (i) => ChatMsg(i.isEven ? 'user' : 'jarvis', 'm$i'));
    final h = ChatCtrl.historyOf(log);
    expect(h.length, 20);
    expect(h.first, ['user', 'm10']);
    expect(h.last[1], 'm29');
  });

  test('send appends reply and clears typing', () async {
    final c = ChatCtrl(sender: (text, history) async {
      expect(text, 'hi');
      expect(history.length, 1);
      return {'ok': true, 'reply': 'At your service.'};
    });
    await c.send('hi');
    expect(c.typing, isFalse);
    expect(c.messages.length, 2);
    expect(c.messages.last.text, 'At your service.');
    c.dispose();
  });

  for (final fail in [false, true]) {
    test('pending send ${fail ? 'failure' : 'success'} survives disposal', () async {
      final response = Completer<Map<String, dynamic>>();
      final c = ChatCtrl(sender: (_, _) => response.future);
      final sending = c.send('hi');
      c.dispose();
      if (fail) {
        response.completeError(StateError('offline'));
      } else {
        response.complete({'ok': true, 'reply': 'Hello'});
      }
      await sending;
      expect(c.messages.length, 1);
      await c.send('after disposal');
      expect(c.messages.length, 1);
    });
  }

  test('send failure degrades to a line, never throws', () async {
    final c = ChatCtrl(sender: (text, history) async => throw 'down');
    await c.send('hi');
    expect(c.messages.last.role, 'jarvis');
    c.dispose();
  });
}
