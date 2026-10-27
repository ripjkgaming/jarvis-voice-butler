import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/character/models.dart';

void main() {
  test('MotionTokens defaults match yaml', () {
    const d = MotionTokens.defaults;
    expect(d.totalMs, 1800);
    expect(d.easing['snap'], [.9, 0, .1, 1]);
    expect(d.phaseEnd('signature'), 1200);
    expect(d.phaseStart('greeting_at'), 1100);
  });

  test('MotionTokens.fromJson is tolerant', () {
    for (final bad in [null, 5, 'x', <String, dynamic>{}, {'phases': 3}]) {
      final m = MotionTokens.fromJson(bad);
      expect(m.beatMs, 700);
      expect(m.phases['out_orb'], [240, 500]);
    }
    final m = MotionTokens.fromJson({
      'total_ms': 2000,
      'beat_ms': 'oops',
      'easing': {'glide': [1, 2, 3], 'x': [0, 0, 1, 1]},
      'phases': {'greeting_at': 900, 'in_orb': [1, 2]},
      'signatures': {'glitch': {'a': [1, 2], 'b': 'no'}},
    });
    expect(m.totalMs, 2000);
    expect(m.beatMs, 700);
    expect(m.easing['glide'], [.22, .61, .36, 1]);
    expect(m.easing['x'], [0, 0, 1, 1]);
    expect(m.phaseStart('greeting_at'), 900);
    expect(m.signatures['glitch'], {'a': [1.0, 2.0]});
  });

  test('CharacterInfo and snapshot tolerant', () {
    final c = CharacterInfo.fromJson({
      'display_name': 'Ultron',
      'lines': {'greet': ['hi', 3]},
      'wake_names': 'bad',
      'intensity': 2,
    }, 'ultron');
    expect(c.id, 'ultron');
    expect(c.displayName, 'Ultron');
    expect(c.lines['greet'], ['hi']);
    expect(c.wakeNames, isEmpty);
    expect(CharacterInfo.fromJson(null).id, '');
    final s = SwitchSnapshot.fromJson({'active': 'a', 'phase': 'ROLLBACK', 'target': ''});
    expect(s.phase, SwitchPhase.rollback);
    expect(s.target, isNull);
    expect(SwitchSnapshot.fromJson(7).phase, SwitchPhase.idle);
    expect(SwitchSnapshot.fromJson({'phase': 'zzz'}).active, 'jarvis');
  });
}
