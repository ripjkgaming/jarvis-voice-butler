import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'core/theme.dart';
import 'screens/chat_screen.dart';
import 'screens/control_screen.dart';
import 'screens/home_screen.dart';
import 'screens/voice_screen.dart';
import 'widgets/hud.dart';

/// Jarvis mobile: Home (link+captions) · Voice (LiveKit) · Chat · Control.
/// Presentation shell: cinematic HUD background, smooth tab transitions,
/// glowing bottom command bar. Tab logic unchanged.
void main() => runApp(const JarvisApp());

class JarvisApp extends StatelessWidget {
  const JarvisApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Jarvis',
      debugShowCheckedModeBanner: false,
      theme: JarvisTheme.dark,
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

  static const _pages = [
    HomeScreen(),
    VoiceScreen(),
    ChatScreen(),
    ControlScreen(),
  ];

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
          child: AnimatedSwitcher(
            duration: const Duration(milliseconds: 320),
            switchInCurve: Curves.easeOutCubic,
            switchOutCurve: Curves.easeInCubic,
            transitionBuilder: (child, anim) => FadeTransition(
              opacity: anim,
              child: SlideTransition(
                position: Tween(
                        begin: const Offset(0.04, 0), end: Offset.zero)
                    .animate(anim),
                child: child,
              ),
            ),
            child: KeyedSubtree(key: ValueKey(_i), child: _pages[_i]),
          ),
        ),
      ),
      bottomNavigationBar: _CommandBar(index: _i, onTap: _select),
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
