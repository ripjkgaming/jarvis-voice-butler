import 'package:flutter/foundation.dart';

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
    String text, List<List<String>> history);

class ChatCtrl extends ChangeNotifier {
  final List<ChatMsg> messages = [];
  bool typing = false;

  final ChatSender _send;

  ChatCtrl({ChatSender? sender}) : _send = sender ?? _liveSend;

  static Future<Map<String, dynamic>> _liveSend(
      String text, List<List<String>> history) async {
    final api =
        LinkApi(base: await Prefs.host(), token: await Prefs.token());
    if (api.base.isEmpty) return {'ok': false, 'error': 'not linked'};
    return api.chat(text, history);
  }

  /// Last 10 exchanges as [role, text] pairs the bridge expects. Pure.
  static List<List<String>> historyOf(List<ChatMsg> log) =>
      log.sublist(log.length > 20 ? log.length - 20 : 0).map((m) =>
          [m.role == 'jarvis' ? 'jarvis' : 'user', m.text]).toList();

  Future<void> send(String text) async {
    text = text.trim();
    if (text.isEmpty || typing) return;
    messages.add(ChatMsg('user', text));
    typing = true;
    notifyListeners();
    try {
      final res = await _send(text, historyOf(messages));
      final reply = res['reply'];
      messages.add(ChatMsg('jarvis',
          reply is String && reply.isNotEmpty
              ? reply
              : 'No answer, Sir. (${res['error'] ?? res['warning'] ?? 'quiet'})'));
    } catch (_) {
      messages.add(const ChatMsg('jarvis', 'Link failed, Sir. Try again.'));
    }
    typing = false;
    notifyListeners();
  }

  Future<Map<String, dynamic>> summonSpoken(String text) async {
    final api =
        LinkApi(base: await Prefs.host(), token: await Prefs.token());
    if (api.base.isEmpty) return {'ok': false};
    return api.summon(text);
  }
}
