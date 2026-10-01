import 'package:flutter/material.dart';

import '../core/chat_ctrl.dart';
import '../core/theme.dart';
import '../widgets/hud.dart';

/// Chat tab: /chat threads with history, per-reply speak-seed (long-press
/// a Jarvis bubble to summon a voice call seeded with the reply text).
/// Logic untouched — same ChatCtrl send/typing/summonSpoken pipeline.
class ChatScreen extends StatefulWidget {
  final ChatCtrl? ctrl;

  const ChatScreen({super.key, this.ctrl});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen>
    with SingleTickerProviderStateMixin {
  late final ChatCtrl _chat;
  final _input = TextEditingController();
  final _scroll = ScrollController();
  late final AnimationController _pulse;

  @override
  void initState() {
    super.initState();
    _chat = widget.ctrl ?? ChatCtrl();
    _chat.addListener(_jump);
    _pulse = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 1000))
      ..repeat(reverse: true);
  }

  @override
  void dispose() {
    _chat.removeListener(_jump);
    if (widget.ctrl == null) _chat.dispose();
    _input.dispose();
    _scroll.dispose();
    _pulse.dispose();
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
    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('SECURE CHANNEL',
                  style: JarvisTheme.label(JarvisTheme.cyan, 12)),
              const SizedBox(height: 2),
              Text(
                  _chat.typing
                      ? 'Jarvis is thinking…'
                      : '${_chat.messages.length} MESSAGES',
                  style: const TextStyle(
                      color: JarvisTheme.muted, fontSize: 12)),
            ]),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: Center(
              child: FadeTransition(
                opacity: Tween(begin: 0.5, end: 1.0).animate(_pulse),
                child: Container(
                  width: 9,
                  height: 9,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: _chat.typing
                        ? JarvisTheme.amber
                        : JarvisTheme.cyan,
                    boxShadow: [
                      BoxShadow(
                        color: (_chat.typing
                                ? JarvisTheme.amber
                                : JarvisTheme.cyan)
                            .withValues(alpha: 0.6),
                        blurRadius: 8,
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
      body: Column(children: [
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 16),
          child: HudScanline(),
        ),
        Expanded(
          child: _chat.messages.isEmpty
              ? Center(
                  child: HudEmpty(
                    icon: Icons.chat_bubble_outline,
                    title: 'Ask Jarvis anything.',
                    hint:
                        'Long-press a Jarvis reply to speak it aloud via Voice.',
                  ),
                )
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.fromLTRB(14, 12, 14, 8),
                  itemCount:
                      _chat.messages.length + (_chat.typing ? 1 : 0),
                  itemBuilder: (ctx, i) {
                    if (i >= _chat.messages.length) {
                      return const _ThinkingRow();
                    }
                    final m = _chat.messages[i];
                    final mine = m.role == 'user';
                    return Stagger(
                      index: 0,
                      child: Align(
                        alignment: mine
                            ? Alignment.centerRight
                            : Alignment.centerLeft,
                        child: GestureDetector(
                          onLongPress: mine
                              ? null
                              : () async {
                                  final r = await _chat
                                      .summonSpoken(m.text);
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
                                horizontal: 14, vertical: 11),
                            constraints: BoxConstraints(
                                maxWidth:
                                    MediaQuery.of(context).size.width *
                                        0.78),
                            decoration: BoxDecoration(
                              color: mine
                                  ? JarvisTheme.cyan
                                      .withValues(alpha: 0.16)
                                  : JarvisTheme.panel
                                      .withValues(alpha: 0.85),
                              borderRadius: BorderRadius.only(
                                topLeft: const Radius.circular(
                                    JarvisTheme.rBubble),
                                topRight: const Radius.circular(
                                    JarvisTheme.rBubble),
                                bottomLeft: Radius.circular(
                                    mine ? JarvisTheme.rBubble : 4),
                                bottomRight: Radius.circular(
                                    mine ? 4 : JarvisTheme.rBubble),
                              ),
                              border: Border.all(
                                color: mine
                                    ? JarvisTheme.cyan
                                        .withValues(alpha: 0.55)
                                    : JarvisTheme.lineCyan
                                        .withValues(alpha: 0.55),
                              ),
                              boxShadow: mine
                                  ? [
                                      BoxShadow(
                                        color: JarvisTheme.cyan
                                            .withValues(alpha: 0.12),
                                        blurRadius: 14,
                                      )
                                    ]
                                  : null,
                            ),
                            child: Column(
                                crossAxisAlignment:
                                    CrossAxisAlignment.start,
                                children: [
                                  Text(mine ? 'YOU' : 'J.A.R.V.I.S.',
                                      style: JarvisTheme.label(
                                          mine
                                              ? JarvisTheme.cyan
                                              : JarvisTheme.muted,
                                          8)),
                                  const SizedBox(height: 3),
                                  Text(m.text,
                                      style: const TextStyle(
                                          color: JarvisTheme.text,
                                          fontSize: 14,
                                          height: 1.5)),
                                ]),
                          ),
                        ),
                      ),
                    );
                  },
                ),
        ),
        SafeArea(
          top: false,
          child: Container(
            decoration: BoxDecoration(
              color: JarvisTheme.bg.withValues(alpha: 0.85),
              border: Border(
                  top: BorderSide(
                      color: JarvisTheme.cyan.withValues(alpha: 0.25))),
            ),
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
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
              GestureDetector(
                onTap: _send,
                child: Container(
                  padding: const EdgeInsets.all(13),
                  decoration: BoxDecoration(
                    color: JarvisTheme.cyan,
                    borderRadius: BorderRadius.circular(
                        JarvisTheme.rPanel),
                    boxShadow: [
                      BoxShadow(
                        color: JarvisTheme.cyan
                            .withValues(alpha: 0.4),
                        blurRadius: 16,
                      ),
                    ],
                  ),
                  child: const Icon(Icons.send,
                      color: Color(0xFF031018), size: 20),
                ),
              ),
            ]),
          ),
        ),
      ]),
    );
  }
}

class _ThinkingRow extends StatefulWidget {
  const _ThinkingRow();
  @override
  State<_ThinkingRow> createState() => _ThinkingRowState();
}

class _ThinkingRowState extends State<_ThinkingRow>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c;
  @override
  void initState() {
    super.initState();
    _c = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 900))
      ..repeat();
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
        decoration: BoxDecoration(
          color: JarvisTheme.panel.withValues(alpha: 0.85),
          borderRadius: const BorderRadius.only(
            topLeft: Radius.circular(JarvisTheme.rBubble),
            topRight: Radius.circular(JarvisTheme.rBubble),
            bottomRight: Radius.circular(JarvisTheme.rBubble),
            bottomLeft: Radius.circular(4),
          ),
          border: Border.all(
              color: JarvisTheme.lineCyan.withValues(alpha: 0.55)),
        ),
        child: AnimatedBuilder(
          animation: _c,
          builder: (_, _) => Row(mainAxisSize: MainAxisSize.min, children: [
            for (var i = 0; i < 3; i++)
              Container(
                margin: EdgeInsets.only(right: i == 2 ? 0 : 5),
                width: 7,
                height: 7,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: JarvisTheme.cyan.withValues(
                      alpha: 0.35 +
                          0.65 *
                              (0.5 +
                                  0.5 *
                                      (1 + _c.value * 2 - i * 0.5)
                                          .clamp(-1.0, 1.0)
                                          .abs() /
                                          2)),
                ),
              ),
            const SizedBox(width: 10),
            Text('THINKING',
                style: JarvisTheme.label(JarvisTheme.muted, 9)),
          ]),
        ),
      ),
    );
  }
}
