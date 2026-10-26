import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/voice_ctrl.dart';

class FakeTransport implements VoiceTransport {
  int closes = 0;
  final List<bool> mic = [];
  Completer<void>? connecting;
  bool denyMic = false;
  Completer<void>? changingMic;
  @override
  Future<void> connect(
    String url,
    String token,
    void Function(VoiceEvent) event,
  ) async {
    await connecting?.future;
  }

  @override
  Future<void> microphone(bool enabled) async {
    await changingMic?.future;
    if (denyMic) throw StateError('denied');
    mic.add(enabled);
  }

  @override
  Future<void> close() async {
    closes++;
  }
}

void main() {
  final token = {
    'ok': true,
    'serverUrl': 'wss://example.livekit.cloud',
    'participantToken': 'mock',
  };
  test('permission denied never mints a token', () async {
    final c = VoiceCtrl(
      permission: () async => false,
      tokenProvider: () async => throw StateError('must not mint'),
    );
    await c.join();
    expect(c.state, CallState.error);
    expect(c.detail, contains('permission denied'));
    c.dispose();
  });
  test('authorization denial never joins', () async {
    final c = VoiceCtrl(
      permission: () async => true,
      tokenProvider: () async => {'ok': false, 'error': 'unauthorized'},
      transportFactory: () => throw StateError('must not join'),
    );
    await c.join();
    expect(c.state, CallState.error);
    expect(c.detail, contains('unauthorized'));
    c.dispose();
  });
  test('call mute reconnect end and retry', () async {
    final f = FakeTransport();
    final c = VoiceCtrl(
      permission: () async => true,
      tokenProvider: () async => token,
      transportFactory: () => f,
    );
    await c.join();
    expect(c.live, true);
    expect(f.mic, [true]);
    expect(await c.setMuted(true), true);
    expect(c.muted, true);
    c.handleEvent(VoiceEvent.reconnecting);
    expect(c.state, CallState.reconnecting);
    c.handleEvent(VoiceEvent.reconnected);
    expect(c.state, CallState.live);
    c.handleEvent(VoiceEvent.disconnected);
    expect(c.state, CallState.error);
    await c.join();
    expect(c.live, true);
    await c.hangup();
    expect(c.state, CallState.idle);
    c.dispose();
  });
  test('cancel pending token cannot resurrect call', () async {
    final minted = Completer<Map<String, dynamic>>();
    final c = VoiceCtrl(
      permission: () async => true,
      tokenProvider: () => minted.future,
      transportFactory: () => throw StateError('must not connect'),
    );
    final joining = c.join();
    await Future<void>.delayed(Duration.zero);
    await c.hangup();
    minted.complete(token);
    await joining;
    expect(c.state, CallState.idle);
    c.dispose();
  });
  test('cancel pending transport releases mic', () async {
    final f = FakeTransport()..connecting = Completer<void>();
    final c = VoiceCtrl(
      permission: () async => true,
      tokenProvider: () async => token,
      transportFactory: () => f,
    );
    final joining = c.join();
    await Future<void>.delayed(Duration.zero);
    await c.hangup();
    f.connecting!.complete();
    await joining;
    expect(f.mic, isEmpty);
    expect(c.state, CallState.idle);
    expect(f.closes, greaterThan(0));
    c.dispose();
  });
  test('dispose during join never notifies after disposal', () async {
    final minted = Completer<Map<String, dynamic>>();
    final c = VoiceCtrl(
      permission: () async => true,
      tokenProvider: () => minted.future,
    );
    final joining = c.join();
    await Future<void>.delayed(Duration.zero);
    c.dispose();
    minted.complete(token);
    await joining;
  });
  for (final fail in [false, true]) {
    test('pending mute ${fail ? 'failure' : 'success'} cannot change a new call', () async {
      final oldTransport = FakeTransport();
      final newTransport = FakeTransport();
      var attempts = 0;
      final c = VoiceCtrl(
        permission: () async => true,
        tokenProvider: () async => token,
        transportFactory: () => attempts++ == 0 ? oldTransport : newTransport,
      );
      await c.join();
      oldTransport.changingMic = Completer<void>();
      final muting = c.setMuted(true);
      await c.hangup();
      await c.join();
      oldTransport.denyMic = fail;
      oldTransport.changingMic!.complete();
      expect(await muting, false);
      expect(c.state, CallState.live);
      expect(c.muted, false);
      expect(c.detail, isEmpty);
      c.dispose();
    });
  }
  test('insecure transport rejected', () {
    expect(
      VoiceCtrl.connectParams({
        'serverUrl': 'ws://host',
        'participantToken': 'x',
      }),
      isNull,
    );
  });
}
