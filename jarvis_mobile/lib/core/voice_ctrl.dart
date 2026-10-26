import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:livekit_client/livekit_client.dart';
import 'package:record/record.dart';

import 'link_api.dart';
import 'prefs.dart';

enum CallState { idle, joining, live, reconnecting, ending, error }

enum VoiceEvent {
  loading,
  listening,
  thinking,
  speaking,
  quiet,
  reconnecting,
  reconnected,
  disconnected,
}

enum VoicePhase { loading, listening, thinking, speaking }

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

/// Ordered control envelopes also gate the physical remote audio sink.
class VoiceTurnFilter {
  int current = -1;
  final Set<int> _cancelled = {};
  bool accept(Map data) {
    if (data['type'] != 'jarvis_turn' ||
        !{'state', 'cancel'}.contains(data['event']))
      return false;
    if (data['event'] == 'state' &&
        !{
          'loading',
          'listening',
          'thinking',
          'speaking',
        }.contains(data['state']))
      return false;
    final id = int.tryParse('${data['turn_id']}');
    if (id == null || id < current) return false;
    current = id;
    if (data['event'] == 'cancel') {
      cancel();
      return true;
    }
    return !_cancelled.contains(id);
  }

  void cancel() {
    _cancelled.add(current);
    _cancelled.removeWhere((id) => id < current);
  }
}

class LiveKitVoiceTransport implements VoiceTransport {
  final Room _room = Room();
  CancelListenFunc? _events;
  final VoiceTurnFilter _turnFilter = VoiceTurnFilter();
  int get _turnId => _turnFilter.current;
  bool _audioAllowed = false;
  int _audioEpoch = 0;

  void _gate(bool allow) {
    _audioAllowed = allow;
    final epoch = ++_audioEpoch;
    for (final participant in _room.remoteParticipants.values) {
      if (participant.kind != ParticipantKind.AGENT) continue;
      for (final publication in participant.audioTrackPublications) {
        final track = publication.track;
        if (track is RemoteAudioTrack) unawaited(_output(track, allow, epoch));
      }
    }
  }

  Future<void> _output(RemoteAudioTrack track, bool allow, int epoch) async {
    try {
      if (allow) {
        await track.mediaStreamTrack.applyConstraints({'volume': 1.0});
        if (epoch == _audioEpoch) await track.start();
      } else {
        // Six small volume ramps preserve the 150 ms interruption budget.
        for (var step = 5; step >= 0; step--) {
          if (epoch != _audioEpoch) return;
          await track.mediaStreamTrack.applyConstraints({'volume': step / 6});
          await Future<void>.delayed(const Duration(milliseconds: 20));
        }
        if (epoch == _audioEpoch) await track.stop();
      }
    } catch (_) {
      if (!allow && epoch == _audioEpoch) await track.stop();
    }
  }

  void _data(DataReceivedEvent event, void Function(VoiceEvent) onEvent) {
    if (event.participant?.kind != ParticipantKind.AGENT) return;
    try {
      final data = jsonDecode(utf8.decode(event.data));
      if (data is! Map || !_turnFilter.accept(data)) return;
      if (data['event'] == 'cancel') {
        _gate(false);
        onEvent(VoiceEvent.listening);
        return;
      }
      final state = data['state'];
      _gate(state == 'speaking');
      switch (state) {
        case 'loading':
          onEvent(VoiceEvent.loading);
        case 'listening':
          onEvent(VoiceEvent.listening);
        case 'thinking':
          onEvent(VoiceEvent.thinking);
        case 'speaking':
          onEvent(VoiceEvent.speaking);
      }
    } catch (_) {
      /* Ignore unrelated room data. */
    }
  }

  @override
  Future<void> connect(
    String url,
    String token,
    void Function(VoiceEvent) onEvent,
  ) async {
    _events = _room.events.listen((event) {
      if (event is DataReceivedEvent) {
        _data(event, onEvent);
      } else if (event is TrackSubscribedEvent) {
        final track = event.track;
        if (track is RemoteAudioTrack && !_audioAllowed)
          unawaited(track.stop());
      } else if (event is ActiveSpeakersChangedEvent) {
        final localSpeaking = event.speakers.any((p) => p is LocalParticipant);
        if (localSpeaking && _audioAllowed && _turnId >= 0) {
          _turnFilter.cancel();
          _gate(false);
          onEvent(VoiceEvent.listening);
          final local = _room.localParticipant;
          if (local != null)
            unawaited(
              local.publishData(
                utf8.encode(
                  jsonEncode({
                    'type': 'jarvis_turn',
                    'event': 'cancel',
                    'turn_id': _turnId,
                  }),
                ),
                reliable: true,
                topic: 'jarvis_turn',
              ),
            );
        }
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
    _gate(false);
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
      httpClient: _client ??= http.Client(),
    );
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
  VoicePhase phase = VoicePhase.loading;
  bool muted = false;
  VoiceTransport? _transport;
  int _generation = 0;
  bool _disposed = false;
  Timer? _speakerHeartbeat;
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
    phase = VoicePhase.loading;
    energy = .2;
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
      _speakerHeartbeat?.cancel();
      if (_tokenProvider == null) {
        _speakerHeartbeat = Timer.periodic(
          const Duration(seconds: 30),
          (_) => unawaited(_refreshSpeaker()),
        );
      }
      detail = '';
      energy = switch (phase) {
        VoicePhase.loading => .2,
        VoicePhase.thinking => .65,
        VoicePhase.speaking => 1,
        VoicePhase.listening => .45,
      };
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
      case VoiceEvent.loading:
        phase = VoicePhase.loading;
        energy = .2;
      case VoiceEvent.listening:
      case VoiceEvent.quiet:
        phase = VoicePhase.listening;
        energy = .45;
      case VoiceEvent.thinking:
        phase = VoicePhase.thinking;
        energy = .65;
      case VoiceEvent.speaking:
        phase = VoicePhase.speaking;
        energy = 1;
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

  Future<void> _refreshSpeaker() async {
    if (_disposed || !live) return;
    final api = LinkApi(
      base: await Prefs.host(),
      token: await Prefs.token(),
      httpClient: _client ??= http.Client(),
    );
    final result = await api.claimSpeaker();
    if (result['ok'] != true &&
        result['error'] != 'unreachable' &&
        result['error'] != 'timeout') {
      await hangup();
    }
  }

  Future<void> _releaseSpeaker() async {
    if (_tokenProvider != null) return;
    final api = LinkApi(base: await Prefs.host(), token: await Prefs.token());
    try {
      if (api.base.isNotEmpty) await api.releaseSpeaker();
    } finally {
      api.close();
    }
  }

  Future<void> _close(VoiceTransport? transport) async {
    _speakerHeartbeat?.cancel();
    _speakerHeartbeat = null;
    try {
      await transport?.close();
      if (transport != null) await _releaseSpeaker();
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
