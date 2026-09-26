import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import 'backend.dart';
import 'link_api.dart';
import 'prefs.dart';

/// Remote-control state: volume, media, screens, apps, type. All calls go
/// through bridge /tool (allowlisted server-side). Injectable api factory
/// keeps this unit-testable without a laptop.
typedef ApiFactory = Future<ControlBackend> Function();

Future<ControlBackend> _liveApi() async =>
    LinkApi(base: await Prefs.host(), token: await Prefs.token());

class ControlCtrl extends ChangeNotifier {
  final ApiFactory _api;
  int volume = 50;
  bool muted = false;
  String? nowPlaying;
  Uint8List? shot;
  String note = '';

  ControlCtrl({ApiFactory? api}) : _api = api ?? _liveApi;

  static const apps = [
    'brave',
    'files',
    'terminal',
    'calculator',
    'whatsie'
  ];

  Future<Map<String, dynamic>> _tool(String tool,
      [Map<String, dynamic>? args]) async {
    final api = await _api();
    if (api.base.isEmpty) return {'ok': false, 'error': 'not linked'};
    return api.tool(tool, args);
  }

  Future<void> refreshVolume() async {
    final r = await _tool('volume_get');
    if (r['ok'] == true) {
      final v = r['volume'];
      if (v is int) volume = v.clamp(0, 100);
      final m = r['muted'];
      if (m is bool) muted = m;
      notifyListeners();
    }
  }

  Future<void> setVolume(int v) async {
    // Bridge steps in 5s: walk toward the target (max 8 calls), then
    // re-read the true level. Slider drags call this on release only.
    final target = v.clamp(0, 100);
    volume = target;
    notifyListeners();
    for (var i = 0; i < 8; i++) {
      await refreshVolumeQuiet();
      final diff = target - volume;
      if (diff.abs() < 3) break;
      await _tool(diff > 0 ? 'volume_up' : 'volume_down');
    }
    await refreshVolume();
  }

  Future<void> refreshVolumeQuiet() async {
    final r = await _tool('volume_get');
    if (r['ok'] == true) {
      final v = r['volume'];
      if (v is int) volume = v.clamp(0, 100);
      final m = r['muted'];
      if (m is bool) muted = m;
    }
  }

  Future<void> toggleMute() async {
    muted = !muted;
    notifyListeners();
    await _tool(muted ? 'volume_mute' : 'volume_unmute');
  }

  Future<void> media(String action) async {
    final r = await _tool(action);
    final s = r['state'];
    if (s is String && s.isNotEmpty) {
      nowPlaying = s;
      notifyListeners();
    }
  }

  Future<void> capture() async {
    note = 'capturing…';
    notifyListeners();
    final r = await _tool('screenshot');
    final img = r['image_b64'];
    if (r['ok'] == true && img is String && img.isNotEmpty) {
      try {
        shot = base64Decode(img);
        note = '';
      } catch (_) {
        note = 'bad image data';
      }
    } else {
      note = '${r['error'] ?? 'capture failed'}';
    }
    notifyListeners();
  }

  Future<String> screens(String action) async {
    final r = await _tool(action);
    final msg = r['ok'] == true
        ? 'done'
        : '${r['error'] ?? 'failed'}';
    note = msg;
    notifyListeners();
    return msg;
  }

  Future<String> openApp(String app) async {
    final r = await _tool('open_app', {'app': app});
    final msg = r['ok'] == true
        ? 'opening $app'
        : '${r['error'] ?? 'failed'}';
    note = msg;
    notifyListeners();
    return msg;
  }

  Future<String> typeText(String text) async {
    final api = await _api();
    if (api.base.isEmpty) return 'not linked';
    final r = await api.typeText(text);
    return r['ok'] == true ? 'typed' : '${r['error'] ?? 'failed'}';
  }

  Future<String> pressEnter() async {
    final api = await _api();
    if (api.base.isEmpty) return 'not linked';
    final r = await api.pressKey('Enter');
    return r['ok'] == true ? 'sent' : '${r['error'] ?? 'failed'}';
  }
}
