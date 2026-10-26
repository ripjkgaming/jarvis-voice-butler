import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/voice_ctrl.dart';

void main() {
  _wsTests();
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
    expect(
      VoiceCtrl.connectParams({'serverUrl': '', 'participantToken': ''}),
      isNull,
    );
    expect(
      VoiceCtrl.connectParams({'serverUrl': 5, 'participantToken': []}),
      isNull,
    );
  });

  test('orb phase follows true assistant states', () {
    final c = VoiceCtrl();
    c.state = CallState.live;
    c.handleEvent(VoiceEvent.loading);
    expect(c.phase, VoicePhase.loading);
    c.handleEvent(VoiceEvent.thinking);
    expect(c.phase, VoicePhase.thinking);
    c.handleEvent(VoiceEvent.speaking);
    expect(c.phase, VoicePhase.speaking);
    expect(c.energy, 1);
    c.handleEvent(VoiceEvent.listening);
    expect(c.phase, VoicePhase.listening);
    expect(c.energy, lessThan(1));
    c.dispose();
  });
  test('cancelled and late turns never reopen the audio sink', () {
    final gate = VoiceTurnFilter();
    Map state(int id, String value) => {
      'type': 'jarvis_turn',
      'event': 'state',
      'turn_id': id,
      'state': value,
    };
    expect(gate.accept(state(10, 'speaking')), isTrue);
    expect(
      gate.accept({'type': 'jarvis_turn', 'event': 'cancel', 'turn_id': 10}),
      isTrue,
    );
    expect(gate.accept(state(10, 'speaking')), isFalse);
    expect(gate.accept(state(9, 'speaking')), isFalse);
    expect(gate.accept(state(11, 'thinking')), isTrue);
    expect(gate.accept(state(11, 'speaking')), isTrue);
    expect(gate.accept(state(11, 'invalid')), isFalse);
    gate.cancel();
    expect(gate.accept(state(11, 'speaking')), isFalse);
  });
  test('starts idle', () {
    final c = VoiceCtrl();
    expect(c.state, CallState.idle);
    expect(c.live, isFalse);
    c.dispose();
  });
}

void _wsTests() {
  Map<String, dynamic> p(String u) => {'serverUrl': u, 'participantToken': 't'};
  test('ws:// allowed for private, Tailscale and localhost hosts', () {
    for (final u in [
      'ws://localhost:7880',
      'ws://127.0.0.1:7880',
      'ws://192.168.1.5:7880',
      'ws://10.0.0.2',
      'ws://172.20.1.1',
      'ws://100.101.102.103:7880',
      'ws://box.tail1234.ts.net:7880',
    ]) {
      expect(VoiceCtrl.connectParams(p(u)), isNotNull, reason: u);
    }
  });
  test('ws:// rejected for public hosts; wss still fine', () {
    for (final u in [
      'ws://example.com',
      'ws://8.8.8.8',
      'ws://172.32.0.1',
      'ws://100.128.0.1',
      'ws://999.1.1.1',
    ]) {
      expect(VoiceCtrl.connectParams(p(u)), isNull, reason: u);
    }
    expect(VoiceCtrl.connectParams(p('wss://example.com')), isNotNull);
  });
}
