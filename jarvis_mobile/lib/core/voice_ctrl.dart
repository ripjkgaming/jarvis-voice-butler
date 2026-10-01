import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:livekit_client/livekit_client.dart';

import 'link_api.dart';
import 'prefs.dart';

/// Call states for the voice tab. `live` = joined + mic hot.
enum CallState { idle, joining, live, error }

/// Remote-voice controller: token from bridge POST /token, join LiveKit
/// Cloud room, publish mic, remote audio plays automatically. Speaking
/// activity drives orb energy via [energy].
class VoiceCtrl extends ChangeNotifier {
  CallState state = CallState.idle;
  String detail = '';
  double energy = 0.35;
  Room? _room;
  CancelListenFunc? _events;
  Timer? _idle;

  bool get live => state == CallState.live;

  /// Pure-ish: shape a /token payload into connect params. Unit-tested.
  static ({String url, String token})? connectParams(
      Map<String, dynamic> payload) {
    final url = payload['serverUrl'];
    final token = payload['participantToken'];
    if (url is! String || token is! String || url.isEmpty || token.isEmpty) {
      return null;
    }
    return (url: url, token: token);
  }

  Future<void> join() async {
    if (state == CallState.joining || state == CallState.live) return;
    state = CallState.joining;
    detail = 'minting token…';
    notifyListeners();
    try {
      final api = LinkApi(
          base: await Prefs.host(), token: await Prefs.token());
      if (api.base.isEmpty) {
        throw StateError('link the bridge first (link icon)');
      }
      final minted = await api.livekitToken();
      final params = connectParams(minted);
      if (params == null) {
        throw StateError('${minted['error'] ?? 'token refused'}');
      }
      detail = 'joining room…';
      notifyListeners();
      final room = Room();
      _events = room.events.listen(_onEvent);
      await room.connect(params.url, params.token);
      await room.localParticipant?.setMicrophoneEnabled(true);
      _room = room;
      state = CallState.live;
      detail = '';
      notifyListeners();
    } catch (e) {
      await _teardown();
      state = CallState.error;
      detail = '$e'.replaceAll(RegExp(r'^.*Exception:? *'), '');
      notifyListeners();
    }
  }

  Future<void> setMuted(bool muted) async {
    try {
      await _room?.localParticipant?.setMicrophoneEnabled(!muted);
    } catch (_) {}
  }

  Future<void> hangup() async {
    await _teardown();
    state = CallState.idle;
    detail = '';
    energy = 0.35;
    notifyListeners();
  }

  void _onEvent(RoomEvent event) {
    if (event is ActiveSpeakersChangedEvent) {
      final anyone = event.speakers.isNotEmpty;
      energy = anyone ? 1.0 : 0.45;
      notifyListeners();
      _idle?.cancel();
      if (anyone) {
        _idle = Timer(const Duration(seconds: 2), () {
          energy = 0.45;
          notifyListeners();
        });
      }
    } else if (event is RoomDisconnectedEvent) {
      hangup();
    }
  }

  Future<void> _teardown() async {
    _idle?.cancel();
    _idle = null;
    final cancel = _events;
    _events = null;
    if (cancel != null) await cancel();
    _events = null;
    try {
      await _room?.disconnect();
    } catch (_) {}
    try {
      await _room?.dispose();
    } catch (_) {}
    _room = null;
  }

  @override
  void dispose() {
    _teardown();
    super.dispose();
  }
}
