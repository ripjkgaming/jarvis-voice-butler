import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/voice_ctrl.dart';

void main() {
  test('connectParams accepts a good /token payload', () {
    final p = VoiceCtrl.connectParams({
      'serverUrl': 'wss://x.livekit.cloud',
      'participantToken': 'abc.def.ghi',
    });
    expect(p, isNotNull);
    expect(p!.url, 'wss://x.livekit.cloud');
  });

  test('connectParams rejects junk without crashing', () {
    expect(VoiceCtrl.connectParams({}), isNull);
    expect(VoiceCtrl.connectParams({'serverUrl': '', 'participantToken': ''}),
        isNull);
    expect(VoiceCtrl.connectParams({'serverUrl': 5, 'participantToken': []}),
        isNull);
  });

  test('starts idle', () {
    final c = VoiceCtrl();
    expect(c.state, CallState.idle);
    expect(c.live, isFalse);
    c.dispose();
  });
}
