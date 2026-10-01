import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/link_api.dart';
import '../core/phone_telemetry.dart';
import '../core/prefs.dart';
import '../core/theme.dart';
import '../widgets/hud.dart';
import '../widgets/orb.dart';

/// Home: live connection status, arc-reactor hero orb, caption tail,
/// quick actions. Logic untouched — same poll/link/settings pipeline.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  bool _online = false;
  bool _loading = true;
  String _version = '';
  List<Map<String, String>> _captions = [];
  Timer? _poll;
  PhoneTelemetry? _telemetry;
  int? _suitPower;
  bool _suitCharging = false;

  @override
  void initState() {
    super.initState();
    _refresh();
    _poll = Timer.periodic(const Duration(seconds: 5), (_) => _refresh());
    // Real phone battery → bridge HUD. Fail-silent; HUD pill updates live.
    _telemetry = PhoneTelemetry.live(
      apiProvider: _api,
      onUpdate: (level, charging) {
        if (!mounted) return;
        setState(() {
          _suitPower = level;
          _suitCharging = charging;
        });
      },
    )..start();
  }

  @override
  void dispose() {
    _poll?.cancel();
    _telemetry?.dispose();
    super.dispose();
  }

  Future<LinkApi> _api() async =>
      LinkApi(base: await Prefs.host(), token: await Prefs.token());

  Future<void> _refresh() async {
    final api = await _api();
    if (api.base.isEmpty) {
      if (mounted) {
        setState(() {
          _online = false;
          _loading = false;
        });
      }
      return;
    }
    final h = await api.health();
    final caps = await api.captions(limit: 6);
    if (!mounted) return;
    setState(() {
      _loading = false;
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
    final hostCtl =
        TextEditingController(text: host.isEmpty ? Prefs.defaultHost : host);
    final tokenCtl = TextEditingController(text: token);
    if (!mounted) return;
    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('LINK TO HOME',
            style: JarvisTheme.label(JarvisTheme.cyan, 12)),
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
      setState(() => _loading = true);
      _refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('JARVIS',
                  style: TextStyle(
                      fontWeight: FontWeight.w800,
                      letterSpacing: 4,
                      fontSize: 18)),
              Text('MARK II // COMPANION',
                  style: JarvisTheme.label(JarvisTheme.cyanDim, 9)),
            ]),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: Row(children: [
              PulseDot(online: _online),
              IconButton(
                  icon: const Icon(Icons.link),
                  tooltip: 'Link settings',
                  onPressed: _settings),
            ]),
          ),
        ],
      ),
      body: ListView(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
          children: [
            const HudScanline(),
            const SizedBox(height: 12),
            Stagger(
              index: 0,
              child: Center(
                child: Hero(
                  tag: 'jarvis-orb',
                  child: JarvisOrb(
                      energy: _online ? 0.55 : 0.2, size: 230),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Stagger(
              index: 1,
              child: Center(
                child: _loading
                    ? const HudLoading(label: 'PROBING LINK')
                    : StatusPill(
                        _online
                            ? 'LINKED${_version.isEmpty ? '' : ' · $_version'}'
                            : 'NOT LINKED — TAP LINK ICON',
                        good: _online,
                      ),
              ),
            ),
            const SizedBox(height: 8),
            Stagger(
              index: 1,
              child: Center(
                child: _SuitPowerPill(
                    level: _suitPower, charging: _suitCharging),
              ),
            ),
            const SizedBox(height: 16),
            Stagger(
              index: 2,
              child: Row(children: [
                Expanded(
                    child: _QuickTile(
                        icon: Icons.sync,
                        label: 'RESYNC',
                        onTap: () {
                          HapticFeedback.selectionClick();
                          setState(() => _loading = true);
                          _refresh();
                        })),
                const SizedBox(width: 10),
                Expanded(
                    child: _QuickTile(
                        icon: Icons.link,
                        label: 'BRIDGE',
                        onTap: _settings)),
                const SizedBox(width: 10),
                Expanded(
                    child: _QuickTile(
                        icon: Icons.mic_none,
                        label: _online ? 'ONLINE' : 'STANDBY',
                        accent: _online
                            ? JarvisTheme.cyan
                            : JarvisTheme.amber,
                        onTap: () {})),
              ]),
            ),
            const SizedBox(height: 16),
            const Stagger(
                index: 3, child: SectionLabel('Latest transmissions')),
            const SizedBox(height: 10),
            Stagger(
              index: 4,
              child: HudPanel(
                child: _captions.isEmpty
                    ? const Center(
                        child: Text(
                            'Nothing yet — captions appear here.',
                            style: TextStyle(
                                color: JarvisTheme.muted, fontSize: 13)),
                      )
                    : Column(children: [
                        for (var i = 0; i < _captions.length; i++)
                          _CaptionRow(
                              caption: _captions[i],
                              last: i == _captions.length - 1),
                      ]),
              ),
            ),
          ]),
    );
  }
}

/// Phone's own battery readout ("SUIT POWER 76% · CHARGING") in HUD chrome.
/// Amber when low & unplugged, cyan otherwise; "--" until the first read.
class _SuitPowerPill extends StatelessWidget {
  final int? level;
  final bool charging;
  const _SuitPowerPill({required this.level, required this.charging});

  @override
  Widget build(BuildContext context) {
    final low = level != null && level! <= 20 && !charging;
    final icon = charging
        ? Icons.battery_charging_full
        : level == null
            ? Icons.battery_unknown
            : low
                ? Icons.battery_alert
                : Icons.battery_std;
    final color = low ? JarvisTheme.amber : JarvisTheme.cyan;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(JarvisTheme.rPill),
        border: Border.all(color: color.withValues(alpha: 0.55)),
        boxShadow: [
          BoxShadow(color: color.withValues(alpha: 0.18), blurRadius: 12),
        ],
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, color: color, size: 14),
        const SizedBox(width: 6),
        Text(
          suitPowerLabel(battery: level, charging: charging).toUpperCase(),
          style: JarvisTheme.label(color, 10),
        ),
      ]),
    );
  }
}

class _QuickTile extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final Color accent;
  const _QuickTile(
      {required this.icon,
      required this.label,
      required this.onTap,
      this.accent = JarvisTheme.cyan});

  @override
  Widget build(BuildContext context) {
    return HudPanel(
      padding: const EdgeInsets.symmetric(vertical: 14),
      onTap: onTap,
      child: Column(children: [
        Icon(icon, color: accent, size: 22),
        const SizedBox(height: 6),
        Text(label, style: JarvisTheme.label(accent, 10)),
      ]),
    );
  }
}

class _CaptionRow extends StatelessWidget {
  final Map<String, String> caption;
  final bool last;
  const _CaptionRow({required this.caption, required this.last});

  @override
  Widget build(BuildContext context) {
    final jarvis = caption['role'] == 'jarvis';
    return Column(children: [
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
          margin: const EdgeInsets.only(top: 2),
          padding: const EdgeInsets.all(6),
          decoration: BoxDecoration(
            color: JarvisTheme.cyan.withValues(alpha: 0.10),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
                color: JarvisTheme.cyan.withValues(alpha: 0.35)),
          ),
          child: Icon(
              jarvis ? Icons.smart_toy_outlined : Icons.person_outline,
              color: JarvisTheme.cyan,
              size: 16),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(jarvis ? 'J.A.R.V.I.S.' : 'YOU',
                    style: JarvisTheme.label(
                        jarvis ? JarvisTheme.cyan : JarvisTheme.muted,
                        9)),
                const SizedBox(height: 2),
                Text(caption['text'] ?? '',
                    style: const TextStyle(
                        color: JarvisTheme.text, fontSize: 13, height: 1.45)),
              ]),
        ),
      ]),
      if (!last)
        const Padding(
          padding: EdgeInsets.symmetric(vertical: 10),
          child: Divider(height: 1),
        ),
    ]);
  }
}
