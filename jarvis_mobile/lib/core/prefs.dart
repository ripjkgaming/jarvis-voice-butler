import 'package:shared_preferences/shared_preferences.dart';

/// Connection settings: Tailscale host of the home bridge + bearer token.
/// Token lives in ~/.jarvis/bridge_token on the laptop (600 perms).
class Prefs {
  static const _host = 'bridge_host';
  static const _token = 'bridge_token';

  /// Fresh-install hint: the laptop's tailnet address + bridge port.
  static const defaultHost = 'http://100.77.6.93:4317';

  static Future<String> host() async =>
      (await SharedPreferences.getInstance()).getString(_host) ?? '';

  static Future<String> token() async =>
      (await SharedPreferences.getInstance()).getString(_token) ?? '';

  static Future<void> save(String host, String token) async {
    final p = await SharedPreferences.getInstance();
    await p.setString(_host, host.trim());
    await p.setString(_token, token.trim());
  }
}
