import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:jarvis_mobile/core/voice_ctrl.dart';
import 'package:jarvis_mobile/screens/voice_screen.dart';

class FakeTransport implements VoiceTransport {
  @override
  Future<void> connect(String u, String t, void Function(VoiceEvent) e) async {}
  @override
  Future<void> microphone(bool enabled) async {}
  @override
  Future<void> close() async {}
}

void main() {
  for (final size in [const Size(320, 640), const Size(640, 360)]) {
    testWidgets('call controls fit $size and retain state in reduced motion', (
      tester,
    ) async {
      SharedPreferences.setMockInitialValues({});
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final c = VoiceCtrl(
        permission: () async => true,
        tokenProvider: () async => {
          'serverUrl': 'wss://example.livekit.cloud',
          'participantToken': 'mock',
        },
        transportFactory: FakeTransport.new,
      );
      await tester.pumpWidget(
        MaterialApp(
          home: MediaQuery(
            data: MediaQueryData(size: size, disableAnimations: true),
            child: VoiceScreen(controller: c),
          ),
        ),
      );
      await tester.pump();
      expect(find.text('CALL JARVIS'), findsOneWidget);
      await tester.tap(find.text('CALL JARVIS'));
      await tester.pump(const Duration(milliseconds: 400));
      expect(c.live, true);
      expect(find.text('END CALL'), findsOneWidget);
      expect(tester.takeException(), isNull);
      c.handleEvent(VoiceEvent.reconnecting);
      await tester.pump();
      expect(find.text('RECONNECTING'), findsOneWidget);
      await tester.tap(find.text('END CALL'));
      await tester.pump();
      expect(c.state, CallState.idle);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
    });
  }
}
