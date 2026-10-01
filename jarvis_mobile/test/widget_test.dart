import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/main.dart';

void main() {
  testWidgets('boots to the link screen', (tester) async {
    await tester.pumpWidget(const JarvisApp());
    expect(find.text('JARVIS'), findsOneWidget);
  });
}
