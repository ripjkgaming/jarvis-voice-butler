import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/character/character_ctrl.dart';
import 'package:jarvis_mobile/character/models.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _T implements Timer {
  @override
  void cancel() {}
  @override
  bool get isActive => true;
  @override
  int get tick => 0;
}

Map<String, dynamic> snap(String a, {String? t, String p = 'idle', int n = 0}) =>
    {'active': a, 'target': t, 'phase': p, 'char_turn': n};

void main() {
  late Map<String, dynamic> current;
  late Map<String, dynamic> switchReply;
  late List<Duration> timers;
  late CharacterCtrl c;
  late List<SwitchEvent> ev;

  setUp(() {
    SharedPreferences.setMockInitialValues({'cx_recent': 'ultron'});
    current = snap('jarvis');
    switchReply = {'ok': true, 'switched': true, 'handled': true, 'char_turn': 1};
    timers = [];
    c = CharacterCtrl(
      api: CharacterApi(
        characters: () async => {
          'ok': true,
          'motion': {'total_ms': 1800},
          'characters': {'ultron': {}, 'jarvis': {}, 'friday': {}},
        },
        character: () async => current,
        switchTo: (t, s) async => switchReply,
      ),
      timerFactory: (d, f) {
        timers.add(d);
        return _T();
      },
      delay: (_) async {},
    );
    ev = [];
    c.events.listen(ev.add);
  });

  test('boot is jarvis even with stored ultron; order jarvis first', () async {
    await c.start();
    expect(c.ready, isTrue);
    expect(c.recent, 'ultron');
    expect(c.active, 'jarvis');
    expect(c.order, ['jarvis', 'friday', 'ultron']);
    expect(timers.last, kPollIdle);
  });

  test('turns are deduped and never replayed', () async {
    await c.start();
    current = snap('jarvis', t: 'ultron', p: 'exiting', n: 1);
    await c.pollOnce();
    await c.pollOnce();
    expect(ev.length, 1);
    expect(ev.single.to, 'ultron');
    expect(timers.last, kPollBusy);
    c.noteSwitch('ultron', 1);
    c.noteSwitch('friday', 0);
    expect(ev.length, 1);
  });

  test('retarget A->B->C emits both', () async {
    await c.start();
    c.applySnapshot(SwitchSnapshot.fromJson(snap('jarvis', t: 'ultron', p: 'exiting', n: 1)));
    c.applySnapshot(SwitchSnapshot.fromJson(snap('jarvis', t: 'friday', p: 'preparing', n: 2)));
    expect(ev.map((e) => e.to), ['ultron', 'friday']);
    expect(ev.map((e) => e.turn), [1, 2]);
  });

  test('rollback reconcile after 2 idle polls, not while busy', () async {
    await c.start();
    c.commitVisual('ultron');
    current = snap('jarvis');
    c.setEngineBusy(true);
    await c.pollOnce();
    await c.pollOnce();
    await c.pollOnce();
    expect(ev, isEmpty);
    c.setEngineBusy(false);
    await c.pollOnce();
    expect(ev, isEmpty);
    await c.pollOnce();
    expect(ev.single.source, 'reconcile');
    expect(ev.single.to, 'jarvis');
    expect(ev.single.from, 'ultron');
  });

  test('requestSwitch emits, and notices for already-active', () async {
    await c.start();
    await c.requestSwitch('ultron', 'ui');
    expect(ev.single.to, 'ultron');
    expect(ev.single.source, 'ui');
    switchReply = {'ok': true, 'handled': true, 'switched': false, 'reply': 'Already Jarvis.'};
    await c.requestSwitch('jarvis');
    expect(c.notice, 'Already Jarvis.');
    expect(ev.length, 1);
    switchReply = {'ok': false, 'error': 'unreachable'};
    await c.requestSwitch('x');
    expect(c.notice, 'Bridge unreachable.');
  });

  test('effects persist', () async {
    await c.setEffects(false);
    await c.setReducedSetting(true);
    final c2 = CharacterCtrl(
      api: CharacterApi(
        characters: () async => {'ok': false},
        character: () async => {},
        switchTo: (t, s) async => {},
      ),
      maxLoadTries: 1,
      delay: (_) async {},
    );
    await c2.start();
    expect(c2.effects, isFalse);
    expect(c2.reducedSetting, isTrue);
    expect(c2.ready, isFalse);
    c2.dispose();
  });
}
