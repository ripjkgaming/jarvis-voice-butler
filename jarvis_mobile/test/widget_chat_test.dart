import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/chat_ctrl.dart';
import 'package:jarvis_mobile/screens/chat_screen.dart';

void main() {
  testWidgets('chat renders input and empty state', (tester) async {
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(body: ChatScreen(ctrl: ChatCtrl()))));
    expect(find.text('Ask Jarvis anything.'), findsOneWidget);
    expect(find.byType(TextField), findsOneWidget);
  });
}
