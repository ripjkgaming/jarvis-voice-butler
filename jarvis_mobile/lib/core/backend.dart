/// Minimal backend surface controllers need. LinkApi implements it;
/// tests inject fakes. Keeps HTTP out of unit tests.
abstract class ControlBackend {
  /// Empty when unconfigured (drives 'not linked' states).
  String get base;
  Future<Map<String, dynamic>> tool(String tool,
      [Map<String, dynamic>? args]);
  Future<Map<String, dynamic>> typeText(String text);
  Future<Map<String, dynamic>> pressKey(String key);
}
