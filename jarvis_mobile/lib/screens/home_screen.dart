import 'dart:async';

import 'package:flutter/material.dart';

import '../core/link_api.dart';
import '../core/prefs.dart';
import '../core/theme.dart';
import '../widgets/orb.dart';

/// Phase 1 home: live connection dot, hero orb (shared-element ready),
/// caption tail, quick actions. Voice/Chat/Control tabs land in later
/// phases; the API + theme + orb ship now and compile green.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  bool _online = false;
  String _version = '';
  List<Map<String, String>> _captions = [];
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    _refresh();
    _poll = Timer.periodic(const Duration(seconds: 5), (_) => _refresh());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<LinkApi> _api() async =>
      LinkApi(base: await Prefs.host(), token: await Prefs.token());

  Future<void> _refresh() async {
    final api = await _api();
    if (api.base.isEmpty) {
      if (mounted) setState(() => _online = false);
      return;
    }
    final h = await api.health();
    final caps = await api.captions(limit: 6);
    if (!mounted) return;
    setState(() {
      _online = h['ok'] == true;
      _version = '${h['version'] ?? ''}';
      final raw = caps['captions'];
      _captions = raw is List
          ? raw
              .whereType<Map>()
              .map((m) => {
                    'role': '${m['role'] ?? ''}',
                    'text': '${m['text'] ?? ''}',
                  })
              .toList()
              .reversed
              .toList()
          : [];
    });
  }

  Future<void> _settings() async {
    final host = await Prefs.host();
    final token = await Prefs.token();
    final hostCtl = TextEditingController(text: host.isEmpty ? Prefs.defaultHost : host);
    final tokenCtl = TextEditingController(text: token);
    if (!mounted) return;
    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: JarvisTheme.panel,
        title: const Text('Link to home',
            style: TextStyle(color: JarvisTheme.text)),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(
              controller: hostCtl,
              decoration: const InputDecoration(
                  labelText: 'Bridge URL (Tailscale)')),
          const SizedBox(height: 12),
          TextField(
              controller: tokenCtl,
              decoration:
                  const InputDecoration(labelText: 'Bearer token'),
              obscureText: true),
          const SizedBox(height: 8),
          const Text(
              'Token lives in ~/.jarvis/bridge_token on the laptop.',
              style: TextStyle(color: JarvisTheme.muted, fontSize: 12)),
        ]),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Save')),
        ],
      ),
    );
    if (saved == true) {
      await Prefs.save(hostCtl.text, tokenCtl.text);
      _refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('JARVIS'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: Row(children: [
              Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: _online ? JarvisTheme.cyan : Colors.redAccent,
                  boxShadow: _online
                      ? [
                          BoxShadow(
                              color: JarvisTheme.cyan.withAlpha(150),
                              blurRadius: 8)
                        ]
                      : null,
                ),
              ),
              IconButton(
                  icon: const Icon(Icons.link),
                  tooltip: 'Link settings',
                  onPressed: _settings),
            ]),
          ),
        ],
      ),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Center(
          child: Hero(
            tag: 'jarvis-orb',
            child: JarvisOrb(
                energy: _online ? 0.55 : 0.2, size: 220),
          ),
        ),
        const SizedBox(height: 8),
        Center(
          child: Text(
            _online
                ? 'Linked${_version.isEmpty ? '' : ' · $_version'}'
                : 'Not linked — tap the link icon',
            style:
                const TextStyle(color: JarvisTheme.muted, fontSize: 13),
          ),
        ),
        const SizedBox(height: 16),
        const Text('LATEST',
            style: TextStyle(
                color: JarvisTheme.muted,
                fontSize: 11,
                letterSpacing: 2)),
        const SizedBox(height: 8),
        ..._captions.map((c) => Card(
              child: ListTile(
                dense: true,
                leading: Icon(
                    c['role'] == 'jarvis'
                        ? Icons.smart_toy_outlined
                        : Icons.person_outline,
                    color: JarvisTheme.cyan,
                    size: 20),
                title: Text(c['text'] ?? '',
                    style: const TextStyle(
                        color: JarvisTheme.text, fontSize: 13)),
              ),
            )),
        if (_captions.isEmpty)
          const Card(
            child: ListTile(
              dense: true,
              title: Text('Nothing yet — captions appear here.',
                  style: TextStyle(
                      color: JarvisTheme.muted, fontSize: 13)),
            ),
          ),
      ]),
    );
  }

}
