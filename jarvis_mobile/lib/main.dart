import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'dart:io' show Platform;

import 'character/character_ctrl.dart';
import 'character/skin_tokens.dart';
import 'character/transition.dart';
import 'core/theme.dart';
import 'core/voice_ctrl.dart';
import 'screens/chat_screen.dart';
import 'screens/control_screen.dart';
import 'screens/home_screen.dart';
import 'screens/voice_screen.dart';
import 'widgets/hud.dart';

/// Jarvis mobile: Home (link+captions) · Voice (LiveKit) · Chat · Control.
/// Presentation shell: cinematic HUD background, smooth tab transitions,
/// glowing bottom command bar. Tab logic unchanged.
void main() => runApp(const JarvisApp());

class JarvisApp extends StatefulWidget {
  /// Characters poll the bridge; widget tests run without them.
  final bool enableCharacters;
  const JarvisApp({super.key, this.enableCharacters = true});

  @override
  State<JarvisApp> createState() => _JarvisAppState();
}

class _JarvisAppState extends State<JarvisApp>
    with SingleTickerProviderStateMixin {
  late final CharacterCtrl _chars = CharacterCtrl();
  late final CxEngine _engine;
  bool _live = false;

  @override
  void initState() {
    super.initState();
    _live = widget.enableCharacters && !Platform.environment.containsKey('FLUTTER_TEST');
    _engine = CxEngine(_chars, this, () {
      final v = WidgetsBinding.instance.platformDispatcher.accessibilityFeatures;
      return v.disableAnimations || _chars.reducedSetting;
    });
    Skin.changes.addListener(_onSkin);
    if (_live) _chars.start();
  }

  /// Themed widgets read [JarvisTheme] statics, so a skin change re-builds the
  /// subtree (colour/opacity only; no layout work) at most once per frame.
  void _onSkin() {
    if (!mounted) return;
    setState(() {});
    (context as Element).visitChildren(_markAll);
  }

  static void _markAll(Element e) {
    e.markNeedsBuild();
    e.visitChildren(_markAll);
  }

  @override
  void dispose() {
    Skin.changes.removeListener(_onSkin);
    _chars.stop();
    _engine.dispose();
    _chars.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Jarvis',
      debugShowCheckedModeBanner: false,
      theme: JarvisTheme.dark,
      themeAnimationDuration: Duration.zero,
      builder: (context, child) => CxScope(
        engine: _engine,
        ctrl: _chars,
        child: CxOverlay(engine: _engine, child: child ?? const SizedBox()),
      ),
      home: const _Tabs(),
    );
  }
}

class _Tabs extends StatefulWidget {
  const _Tabs();

  @override
  State<_Tabs> createState() => _TabsState();
}

class _TabsState extends State<_Tabs> {
  int _i = 0;

  final VoiceCtrl _call = VoiceCtrl();
  late final List<Widget> _pages;
  @override
  void initState() {
    super.initState();
    _pages = [HomeScreen(onCall: _startCall), VoiceScreen(controller: _call), const ChatScreen(), const ControlScreen()];
  }
  void _startCall() { _select(1); _call.join(); }
  @override
  void dispose() { _call.dispose(); super.dispose(); }

  static const _tabs = [
    (Icons.home_outlined, Icons.home, 'Home'),
    (Icons.mic_none, Icons.mic, 'Voice'),
    (Icons.chat_bubble_outline, Icons.chat_bubble, 'Chat'),
    (Icons.tune, Icons.settings_remote, 'Control'),
  ];

  void _select(int v) {
    if (v == _i) return;
    HapticFeedback.selectionClick();
    setState(() => _i = v);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: JarvisTheme.bg,
      body: HudBackground(
        child: SafeArea(
          bottom: false,
          child: IndexedStack(
            index: _i,
            children: List.generate(_pages.length, (i) => TickerMode(
              enabled: i == _i,
              child: ExcludeSemantics(excluding: i != _i, child: _pages[i]),
            )),
          ),
        ),
      ),
      bottomNavigationBar: CxGroup(
        group: CxGroupId.chrome,
        child: _CommandBar(index: _i, onTap: _select),
      ),
    );
  }
}

/// Glowing bottom command bar with mono labels + active pip.
class _CommandBar extends StatelessWidget {
  final int index;
  final ValueChanged<int> onTap;
  const _CommandBar({required this.index, required this.onTap});

  @override
  Widget build(BuildContext context) {
    const tabs = _TabsState._tabs;
    return Container(
      decoration: BoxDecoration(
        color: JarvisTheme.bg.withValues(alpha: 0.92),
        border: Border(
          top: BorderSide(
              color: JarvisTheme.cyan.withValues(alpha: 0.35)),
        ),
        boxShadow: [
          BoxShadow(
            color: JarvisTheme.cyan.withValues(alpha: 0.08),
            blurRadius: 18,
            offset: const Offset(0, -4),
          ),
        ],
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding:
              const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
          child: Row(
            children: List.generate(tabs.length, (i) {
              final selected = i == index;
              final c = selected ? JarvisTheme.cyan : JarvisTheme.muted;
              return Expanded(
                child: InkWell(
                  borderRadius: BorderRadius.circular(10),
                  onTap: () => onTap(i),
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 220),
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    decoration: BoxDecoration(
                      color: selected
                          ? JarvisTheme.cyan.withValues(alpha: 0.12)
                          : Colors.transparent,
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                        color: selected
                            ? JarvisTheme.cyan.withValues(alpha: 0.5)
                            : Colors.transparent,
                      ),
                    ),
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                      Icon(selected ? tabs[i].$2 : tabs[i].$1,
                          color: c, size: 22),
                      const SizedBox(height: 3),
                      Text(tabs[i].$3.toUpperCase(),
                          style: JarvisTheme.label(c, 9)),
                      const SizedBox(height: 4),
                      AnimatedContainer(
                        duration: const Duration(milliseconds: 220),
                        width: selected ? 18 : 4,
                        height: 2,
                        decoration: BoxDecoration(
                          color: selected
                              ? JarvisTheme.cyan
                              : Colors.transparent,
                          borderRadius: BorderRadius.circular(2),
                          boxShadow: selected
                              ? [
                                  BoxShadow(
                                    color: JarvisTheme.cyan
                                        .withValues(alpha: 0.8),
                                    blurRadius: 6,
                                  )
                                ]
                              : null,
                        ),
                      ),
                    ]),
                  ),
                ),
              );
            }),
          ),
        ),
      ),
    );
  }
}
