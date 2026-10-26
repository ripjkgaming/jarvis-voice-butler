import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'backend.dart';
import 'phone_telemetry.dart';

/// Dart port of the native LinkApi contract (phone/.../LinkApi.kt)
/// against the home bridge over Tailscale. Every route is bearer-gated;
/// failures come back as {ok:false} maps, never throws (callers stay dumb).
class LinkApi implements ControlBackend, PhoneTelemetryTarget {
  @override
  final String base;
  final String token;
  final http.Client _http;

  LinkApi({required this.base, required this.token, http.Client? httpClient})
    : _http = httpClient ?? http.Client();

  Map<String, String> get _headers => {
    'Content-Type': 'application/json',
    if (token.isNotEmpty) 'Authorization': 'Bearer $token',
  };

  Uri _u(String path, [Map<String, String>? query]) => Uri.parse(
    '${base.endsWith('/') ? base.substring(0, base.length - 1) : base}$path'
    '${query == null ? '' : '?${Uri(queryParameters: query).query}'}',
  );

  Future<Map<String, dynamic>> _get(
    String path, [
    Map<String, String>? query,
  ]) async {
    try {
      final r = await _http
          .get(_u(path, query), headers: _headers)
          .timeout(const Duration(seconds: 8));
      return jsonDecode(r.body) as Map<String, dynamic>;
    } on TimeoutException {
      return {'ok': false, 'error': 'timeout'};
    } catch (_) {
      return {'ok': false, 'error': 'unreachable'};
    }
  }

  Future<Map<String, dynamic>> _post(
    String path,
    Map<String, dynamic> body,
  ) async {
    try {
      final r = await _http
          .post(_u(path), headers: _headers, body: jsonEncode(body))
          .timeout(const Duration(seconds: 30));
      return jsonDecode(r.body) as Map<String, dynamic>;
    } on TimeoutException {
      return {'ok': false, 'error': 'timeout'};
    } catch (_) {
      return {'ok': false, 'error': 'unreachable'};
    }
  }

  void close() => _http.close();

  Future<Map<String, dynamic>> health() => _get('/health');
  Future<Map<String, dynamic>> status() => _get('/status');
  Future<Map<String, dynamic>> sys() => _get('/sys');
  Future<Map<String, dynamic>> mic() => _get('/mic');
  Future<Map<String, dynamic>> captions({int limit = 20}) =>
      _get('/captions', {'limit': '$limit'});
  Future<Map<String, dynamic>> actions({int limit = 50}) =>
      _get('/actions', {'limit': '$limit'});
  Future<Map<String, dynamic>> room() => _get('/room');

  Future<Map<String, dynamic>> setMuted(bool muted) =>
      _post('/mic', {'muted': muted});
  int _summonId = DateTime.now().microsecondsSinceEpoch;
  Future<Map<String, dynamic>> summon([String? text]) {
    final id = ++_summonId;
    final body = <String, dynamic>{
      'turn_id': id,
      'client_id': clientId,
      'idem_key': '$clientId:summon:$id',
    };
    if (text != null && text.isNotEmpty) body['text'] = text;
    return _post('/summon', body);
  }

  Future<Map<String, dynamic>> typeText(String text) =>
      _post('/type', {'text': text});
  Future<Map<String, dynamic>> pressKey(String key) =>
      _post('/type', {'key': key});

  /// Report the phone's own battery so the laptop HUD can show it.
  /// Built by [buildPhoneTelemetryPayload] (core/phone_telemetry.dart);
  /// never throws — unreachable bridges come back as {ok:false}.
  @override
  Future<Map<String, dynamic>> phoneTelemetry({
    required int battery,
    required bool charging,
  }) => _post(
    '/phone/telemetry',
    buildPhoneTelemetryPayload(battery: battery, charging: charging),
  );
  Future<Map<String, dynamic>> tool(
    String tool, [
    Map<String, dynamic>? args,
  ]) => _post('/tool', {'tool': tool, 'args': args ?? {}});
  static final String clientId =
      'mobile-${DateTime.now().microsecondsSinceEpoch}';
  Future<Map<String, dynamic>> cancelTurn(int turnId) =>
      _post('/cancel', {'turn_id': turnId, 'client_id': clientId});
  Future<Map<String, dynamic>> claimSpeaker() =>
      _post('/speaker', {'surface': 'phone', 'action': 'claim'});
  Future<Map<String, dynamic>> releaseSpeaker() =>
      _post('/speaker/release', {'surface': 'phone'});

  Future<Map<String, dynamic>> chatTurn(
    String text,
    List<List<String>> history,
    int turnId,
  ) => _post('/chat', {
    'text': text,
    'history': history,
    'turn_id': turnId,
    'client_id': clientId,
    'idem_key': '$clientId:$turnId',
  });

  Future<Map<String, dynamic>> chat(
    String text, [
    List<List<String>>? history,
  ]) {
    final body = <String, dynamic>{'text': text};
    final h = history;
    if (h != null) body['history'] = h;
    return _post('/chat', body);
  }

  /// Mint a 15-minute LiveKit join token (remote voice phase).
  Future<Map<String, dynamic>> livekitToken({
    String room = '',
    bool dispatch = true,
  }) => _post('/token', {'room': room, 'dispatch': dispatch});
}
