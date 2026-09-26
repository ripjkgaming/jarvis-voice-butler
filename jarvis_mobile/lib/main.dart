import 'package:flutter/material.dart';

import 'core/theme.dart';
import 'screens/chat_screen.dart';
import 'screens/control_screen.dart';
import 'screens/home_screen.dart';
import 'screens/soon_screen.dart';
import 'screens/voice_screen.dart';

/// Jarvis mobile: Home (link+captions) · Voice (LiveKit) · Chat · Control.
/// Voice ships in phase 2; Chat/Control placeholders fill in next.
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
    SoonScreen(
        icon: Icons.chat_bubble_outline,
        title: 'Chat',
        subtitle: 'Phase 3 — text side-channel'),
    ControlScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 250),
          child: KeyedSubtree(
              key: ValueKey(_i), child: _pages[_i]),
        ),
      ),
      bottomNavigationBar: NavigationBar(
        backgroundColor: JarvisTheme.bg,
        indicatorColor: JarvisTheme.cyan.withAlpha(40),
        selectedIndex: _i,
        onDestinationSelected: (v) => setState(() => _i = v),
        destinations: const [
          NavigationDestination(
              icon: Icon(Icons.home_outlined),
              selectedIcon: Icon(Icons.home),
              label: 'Home'),
          NavigationDestination(
              icon: Icon(Icons.mic_none),
              selectedIcon: Icon(Icons.mic),
              label: 'Voice'),
          NavigationDestination(
              icon: Icon(Icons.chat_bubble_outline),
              selectedIcon: Icon(Icons.chat_bubble),
              label: 'Chat'),
          NavigationDestination(
              icon: Icon(Icons.tune),
              selectedIcon: Icon(Icons.settings_remote),
              label: 'Control'),
        ],
      ),
    );
  }
}
