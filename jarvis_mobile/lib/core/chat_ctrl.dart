import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'link_api.dart';
import 'prefs.dart';

/// Chat state: user/jarvis message log + send pipeline over bridge /chat.
/// History sent to the server is trimmed to the last 10 exchanges.
class ChatMsg {
  final String role; // 'user' | 'jarvis'
  final String text;
  const ChatMsg(this.role, this.text);
}

typedef ChatSender = Future<Map<String, dynamic>> Function(
  String text,
  List<List<String>> history,
);

class ChatCtrl extends ChangeNotifier {
  final List<ChatMsg> messages = [];
  bool typing = false;
  bool _disposed = false;
  int _turnId = 0;
  String _pending = "";
  final Set<int> _reportedActions = {};

  final ChatSender _send;

  http.Client? _client;
  http.Client get _shared => _client ??= http.Client();

  ChatCtrl({ChatSender? sender}) : _send = sender ?? _noSender {
    if (sender == null) _live = true;
  }
  bool _live = false;

  static Future<Map<String, dynamic>> _noSender(
    String text,
    List<List<String>> history,
  ) async => {'ok': false, 'error': 'not linked'};

  Future<Map<String, dynamic>> _liveSend(
    String text,
    List<List<String>> history,
    int turnId,
  ) async {
    final client = _shared;
    final api = LinkApi(
      base: await Prefs.host(),
      token: await Prefs.token(),
      httpClient: client,
    );
    if (_disposed) return {"ok": true, "cancelled": true};
    if (api.base.isEmpty) return {'ok': false, 'error': 'not linked'};
    return api.chatTurn(text, history, turnId);
  }

  /// Human-readable failure line; distinguishes timeout from unreachable.
  static String errorText(Map<String, dynamic> res) {
    switch (res['error']) {
      case 'timeout':
        return 'The bridge took too long to answer, Sir. Try again.';
      case 'unreachable':
        return 'Cannot reach the bridge, Sir. Check Tailscale and the link.';
      default:
        return 'No answer, Sir. (${res['error'] ?? res['warning'] ?? 'quiet'})';
    }
  }

  /// Last 10 exchanges as [role, text] pairs the bridge expects. Pure.
  static List<List<String>> historyOf(List<ChatMsg> log) => log
      .sublist(log.length > 20 ? log.length - 20 : 0)
      .map((m) => [m.role == 'jarvis' ? 'jarvis' : 'user', m.text])
      .toList();

  Future<void> send(String text) async {
    text = text.trim();
    if (_disposed || text.isEmpty) return;
    final previous = _turnId;
    final wasTyping = typing;
    final id = ++_turnId;
    if (wasTyping && _live) unawaited(_cancel(previous));
    final stop = RegExp(
      r'^(stop|cancel|never mind|nevermind)[.!?]*$',
      caseSensitive: false,
    ).hasMatch(text);
    final continuation =
        wasTyping &&
        RegExp(
          r'^(no\b|also\b|wait\b|i meant\b|actually\b)',
          caseSensitive: false,
        ).hasMatch(text);
    if (continuation) text = '$_pending\n$text';
    _pending = text;
    if (stop) {
      messages.add(ChatMsg('user', text));
      typing = false;
      notifyListeners();
      return;
    }
    messages.add(ChatMsg('user', text));
    typing = true;
    notifyListeners();
    try {
      final res = _live
          ? await _liveSend(text, historyOf(messages), id)
          : await _send(text, historyOf(messages));
      if (_disposed) return;
      if (id != _turnId) {
        final action = res['action'];
        if (action is Map && action['ok'] == true && _reportedActions.add(id)) {
          messages.add(
            ChatMsg(
              'jarvis',
              'Earlier action completed: ${action['tool'] ?? 'request'}.',
            ),
          );
          notifyListeners();
        }
        return;
      }
      if (res['cancelled'] == true ||
          res['stale'] == true ||
          (res['turn_id'] != null && res['turn_id'] != id)) {
        typing = false;
        notifyListeners();
        return;
      }
      final reply = res['reply'];
      messages.add(
        ChatMsg(
          'jarvis',
          reply is String && reply.isNotEmpty ? reply : errorText(res),
        ),
      );
    } catch (_) {
      if (_disposed || id != _turnId) return;
      messages.add(const ChatMsg('jarvis', 'Link failed, Sir. Try again.'));
    }
    typing = false;
    notifyListeners();
  }

  Future<void> _cancel(int turnId) async {
    final client = _shared;
    final api = LinkApi(
      base: await Prefs.host(),
      token: await Prefs.token(),
      httpClient: client,
    );
    if (!_disposed && api.base.isNotEmpty) await api.cancelTurn(turnId);
  }

  @override
  void dispose() {
    _disposed = true;
    _client?.close();
    _client = null;
    super.dispose();
  }

  Future<Map<String, dynamic>> summonSpoken(String text) async {
    final api = LinkApi(
      base: await Prefs.host(),
      token: await Prefs.token(),
      httpClient: _shared,
    );
    if (api.base.isEmpty) return {'ok': false};
    return api.summon(text);
  }
}
