import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/chat_ctrl.dart';

void main() {
  test('tags speakers, sends them in history, fires onSwitch', () async {
    var active = 'ultron';
    final sw = <List<Object>>[];
    List<List<String>>? seen;
    var n = 0;
    final c = ChatCtrl(
      activeId: () => active,
      onSwitch: (t, turn, s) => sw.add([t, turn, s]),
      sender: (text, h) async {
        seen = h;
        return ++n == 1
            ? {'ok': true, 'reply': 'Switching.', 'switched': true, 'target': 'friday', 'char_turn': 4}
            : {'ok': true, 'reply': 'Hi.', 'speaker': 'friday'};
      },
    );
    await c.send('be friday');
    expect(c.messages[0].speaker, 'ultron');
    expect(c.messages[1].speaker, 'friday');
    expect(sw, [['friday', 4, 'text']]);
    active = 'friday';
    await c.send('hello');
    expect(seen, [
      ['user', 'be friday', 'ultron'],
      ['jarvis', 'Switching.', 'friday'],
      ['user', 'hello', 'friday'],
    ]);
    expect(c.messages.length, 4);
    c.dispose();
  });

  test('default speaker is jarvis and history stays capped, never cleared', () async {
    final c = ChatCtrl(sender: (t, h) async => {'ok': true, 'reply': 'ok'});
    for (var i = 0; i < 15; i++) {
      await c.send('m$i');
    }
    expect(c.messages.length, 30);
    expect(c.messages.first.speaker, 'jarvis');
    expect(ChatCtrl.historyOf(c.messages).length, 20);
    expect(ChatCtrl.historyOf(c.messages).first.length, 2);
    c.dispose();
  });
}
