import 'package:flutter/material.dart';

import '../core/chat_ctrl.dart';
import '../core/theme.dart';

/// Phase 3 chat tab: /chat threads with history, per-reply speak-seed
/// (summons a voice call seeded with the reply text).
class ChatScreen extends StatefulWidget {
  final ChatCtrl? ctrl;

  const ChatScreen({super.key, this.ctrl});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  late final ChatCtrl _chat;
  final _input = TextEditingController();
  final _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    _chat = widget.ctrl ?? ChatCtrl();
    _chat.addListener(_jump);
  }

  @override
  void dispose() {
    _chat.removeListener(_jump);
    if (widget.ctrl == null) _chat.dispose();
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _jump() {
    if (!mounted) return;
    setState(() {});
    if (_scroll.hasClients) {
      _scroll.animateTo(_scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut);
    }
  }

  Future<void> _send() async {
    final text = _input.text;
    _input.clear();
    await _chat.send(text);
  }

  @override
  Widget build(BuildContext context) {
    return Column(children: [
      Expanded(
        child: _chat.messages.isEmpty
            ? const Center(
                child: Text('Ask Jarvis anything.',
                    style: TextStyle(
                        color: JarvisTheme.muted, fontSize: 14)))
            : ListView.builder(
                controller: _scroll,
                padding: const EdgeInsets.all(12),
                itemCount:
                    _chat.messages.length + (_chat.typing ? 1 : 0),
                itemBuilder: (ctx, i) {
                  if (i >= _chat.messages.length) {
                    return const ListTile(
                      leading: SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                              strokeWidth: 2)),
                      title: Text('Jarvis is thinking…',
                          style: TextStyle(
                              color: JarvisTheme.muted,
                              fontSize: 13)),
                    );
                  }
                  final m = _chat.messages[i];
                  final mine = m.role == 'user';
                  return Align(
                    alignment: mine
                        ? Alignment.centerRight
                        : Alignment.centerLeft,
                    child: GestureDetector(
                      onLongPress: mine
                          ? null
                          : () async {
                              final r =
                                  await _chat.summonSpoken(m.text);
                              if (context.mounted) {
                                ScaffoldMessenger.of(context)
                                    .showSnackBar(SnackBar(
                                        content: Text(r['ok'] == true
                                            ? 'Summoned — switch to Voice.'
                                            : 'Summon failed.')));
                              }
                            },
                      child: Container(
                        margin: const EdgeInsets.symmetric(vertical: 4),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 14, vertical: 10),
                        constraints: BoxConstraints(
                            maxWidth:
                                MediaQuery.of(context).size.width *
                                    0.78),
                        decoration: BoxDecoration(
                          color: mine
                              ? JarvisTheme.cyan.withAlpha(35)
                              : JarvisTheme.panel,
                          borderRadius: BorderRadius.circular(16),

                        ),
                        child: Text(m.text,
                            style: const TextStyle(
                                color: JarvisTheme.text,
                                fontSize: 14)),
                      ),
                    ),
                  );
                },
              ),
      ),
      SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
          child: Row(children: [
            Expanded(
              child: TextField(
                controller: _input,
                minLines: 1,
                maxLines: 4,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => _send(),
                decoration: const InputDecoration(
                    hintText: 'Message Jarvis…'),
              ),
            ),
            const SizedBox(width: 8),
            IconButton.filled(
              onPressed: _send,
              icon: const Icon(Icons.send),
              style: IconButton.styleFrom(
                  backgroundColor: JarvisTheme.cyan,
                  foregroundColor: Colors.black),
            ),
          ]),
        ),
      ),
    ]);
  }
}
