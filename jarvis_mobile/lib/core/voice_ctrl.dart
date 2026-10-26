import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:livekit_client/livekit_client.dart';
import 'package:record/record.dart';

import 'link_api.dart';
import 'prefs.dart';

enum CallState { idle, joining, live, reconnecting, ending, error }

enum VoiceEvent { speaking, quiet, reconnecting, reconnected, disconnected }

/// Injectable transport: tests never request a token or publish real audio.
abstract class VoiceTransport {
  Future<void> connect(
    String url,
    String token,
    void Function(VoiceEvent) onEvent,
  );
  Future<void> microphone(bool enabled);
  Future<void> close();
}

class LiveKitVoiceTransport implements VoiceTransport {
  final Room _room = Room();
  CancelListenFunc? _events;
  @override
  Future<void> connect(
    String url,
    String token,
    void Function(VoiceEvent) onEvent,
  ) async {
    _events = _room.events.listen((event) {
      if (event is ActiveSpeakersChangedEvent) {
        onEvent(
          event.speakers.isEmpty ? VoiceEvent.quiet : VoiceEvent.speaking,
        );
      } else if (event is RoomReconnectingEvent || event is RoomResumingEvent) {
        onEvent(VoiceEvent.reconnecting);
      } else if (event is RoomReconnectedEvent) {
        onEvent(VoiceEvent.reconnected);
      } else if (event is RoomDisconnectedEvent) {
        onEvent(VoiceEvent.disconnected);
      }
    });
    await _room.connect(url, token);
  }

  @override
  Future<void> microphone(bool enabled) async {
    final participant = _room.localParticipant;
    if (participant == null) throw StateError('Voice participant unavailable');
    await participant.setMicrophoneEnabled(enabled);
  }

  @override
  Future<void> close() async {
    final cancel = _events;
    _events = null;
    if (cancel != null) await cancel();
    try {
      await _room.disconnect();
    } finally {
      await _room.dispose();
    }
  }
}

Future<bool> _microphonePermission() async {
  final recorder = AudioRecorder();
  try {
    return await recorder.hasPermission();
  } finally {
    await recorder.dispose();
  }
}

class VoiceCtrl extends ChangeNotifier {
  VoiceCtrl({
    Future<Map<String, dynamic>> Function()? tokenProvider,
    Future<bool> Function()? permission,
    VoiceTransport Function()? transportFactory,
  }) : _tokenProvider = tokenProvider,
       _permission = permission ?? _microphonePermission,
       _transportFactory = transportFactory ?? LiveKitVoiceTransport.new;
  final Future<Map<String, dynamic>> Function()? _tokenProvider;
  http.Client? _client;

  Future<Map<String, dynamic>> _mint() async {
    final api = LinkApi(
        base: await Prefs.host(),
        token: await Prefs.token(),
        httpClient: _client ??= http.Client());
    if (api.base.isEmpty || api.token.isEmpty) {
      throw StateError('Link the home bridge and bearer token first.');
    }
    return api.livekitToken(dispatch: true);
  }

  final Future<bool> Function() _permission;
  final VoiceTransport Function() _transportFactory;
  CallState state = CallState.idle;
  String detail = '';
  double energy = .35;
  bool muted = false;
  VoiceTransport? _transport;
  int _generation = 0;
  bool _disposed = false;
  bool get live => state == CallState.live || state == CallState.reconnecting;
  bool get active => live || state == CallState.joining;
  void _notify() {
    if (!_disposed) notifyListeners();
  }

  /// True for localhost, loopback, RFC1918, link-local, Tailscale CGNAT
  /// (100.64.0.0/10), *.ts.net and *.local hosts. Plain ws:// is only
  /// allowed for these; public hosts must use wss://.
  static bool isPrivateHost(String host) {
    final h = host.toLowerCase();
    if (h == 'localhost' || h == '::1' || h == '[::1]') return true;
    if (h.endsWith('.ts.net') || h.endsWith('.local')) return true;
    final m = RegExp(r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$')
        .firstMatch(h);
    if (m == null) return false;
    final o = [for (var i = 1; i <= 4; i++) int.parse(m.group(i)!)];
    if (o.any((v) => v > 255)) return false;
    return o[0] == 127 ||
        o[0] == 10 ||
        (o[0] == 172 && o[1] >= 16 && o[1] <= 31) ||
        (o[0] == 192 && o[1] == 168) ||
        (o[0] == 169 && o[1] == 254) ||
        (o[0] == 100 && o[1] >= 64 && o[1] <= 127);
  }

  static ({String url, String token})? connectParams(
    Map<String, dynamic> payload,
  ) {
    final url = payload['serverUrl'], token = payload['participantToken'];
    if (url is! String ||
        token is! String ||
        token.isEmpty ||
        payload['ok'] == false)
      return null;
    final uri = Uri.tryParse(url);
    if (uri == null ||
        !(uri.scheme == 'wss' ||
            (uri.scheme == 'ws' && isPrivateHost(uri.host))) ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty)
      return null;
    return (url: url, token: token);
  }

  bool _current(int id) => !_disposed && id == _generation;
  Future<void> join() async {
    if (_disposed || active || state == CallState.ending) return;
    final id = ++_generation;
    state = CallState.joining;
    detail = 'Checking microphone permission…';
    muted = false;
    _notify();
    VoiceTransport? transport;
    try {
      final allowed = await _permission();
      if (!_current(id)) return;
      if (!allowed)
        throw StateError(
          'Microphone permission denied. Enable it in phone settings to call Jarvis.',
        );
      detail = 'Starting Jarvis on your home bridge…';
      _notify();
      final payload = await (_tokenProvider ?? _mint)().timeout(
        const Duration(seconds: 35),
      );
      if (!_current(id)) return;
      final params = connectParams(payload);
      if (params == null)
        throw StateError(
          '${payload['error'] ?? 'Session token refused. Check the bridge link.'}',
        );
      transport = _transportFactory();
      _transport = transport;
      detail = 'Connecting voice…';
      _notify();
      await transport
          .connect(params.url, params.token, (event) {
            if (_current(id)) handleEvent(event);
          })
          .timeout(const Duration(seconds: 25));
      if (!_current(id)) {
        await _close(transport);
        return;
      }
      await transport.microphone(true);
      if (!_current(id)) {
        await _close(transport);
        return;
      }
      state = CallState.live;
      detail = '';
      energy = .45;
      _notify();
    } catch (e) {
      if (!_current(id)) return;
      final errorId = ++_generation;
      _transport = null;
      await _close(transport);
      if (!_current(errorId)) return;
      state = CallState.error;
      detail = '$e'.replaceFirst(RegExp(r'^Bad state: '), '');
      energy = .2;
      _notify();
    }
  }

  Future<bool> setMuted(bool value) async {
    final transport = _transport;
    final id = _generation;
    if (_disposed || !live || transport == null) return false;
    try {
      await transport.microphone(!value);
      if (!_current(id) || !identical(_transport, transport)) return false;
      muted = value;
      _notify();
      return true;
    } catch (_) {
      if (!_current(id) || !identical(_transport, transport)) return false;
      detail = 'Microphone change failed. Try again.';
      _notify();
      return false;
    }
  }

  void handleEvent(VoiceEvent event) {
    if (_disposed || !active) return;
    switch (event) {
      case VoiceEvent.speaking:
        energy = 1;
      case VoiceEvent.quiet:
        energy = .45;
      case VoiceEvent.reconnecting:
        state = CallState.reconnecting;
        detail = 'Connection interrupted — reconnecting…';
      case VoiceEvent.reconnected:
        state = CallState.live;
        detail = '';
      case VoiceEvent.disconnected:
        ++_generation;
        final transport = _transport;
        _transport = null;
        unawaited(_close(transport));
        state = CallState.error;
        detail = 'Call disconnected. Tap Call Jarvis to reconnect.';
        energy = .2;
    }
    _notify();
  }

  Future<void> _close(VoiceTransport? transport) async {
    try {
      await transport?.close();
    } catch (_) {
      /* Best-effort local cleanup. */
    }
  }

  Future<void> hangup() async {
    if (_disposed) return;
    final id = ++_generation;
    state = CallState.ending;
    detail = 'Ending call…';
    _notify();
    final transport = _transport;
    _transport = null;
    await _close(transport);
    if (!_current(id)) return;
    state = CallState.idle;
    detail = '';
    energy = .35;
    muted = false;
    _notify();
  }

  @override
  void dispose() {
    _disposed = true;
    ++_generation;
    final transport = _transport;
    _transport = null;
    unawaited(_close(transport));
    _client?.close();
    _client = null;
    super.dispose();
  }
}
