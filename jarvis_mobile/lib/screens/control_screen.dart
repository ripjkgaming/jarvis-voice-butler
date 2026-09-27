import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/control_ctrl.dart';
import '../core/theme.dart';
import '../widgets/hud.dart';

/// Remote control: volume, media keys, screens viewer + power, app
/// launcher chips, type/enter. Destructive actions ask first.
/// Logic untouched — same ControlCtrl pipeline, new HUD skin.
class ControlScreen extends StatefulWidget {
  final ControlCtrl? ctrl;

  const ControlScreen({super.key, this.ctrl});

  @override
  State<ControlScreen> createState() => _ControlScreenState();
}

class _ControlScreenState extends State<ControlScreen> {
  late final ControlCtrl _c;
  final _type = TextEditingController();

  @override
  void initState() {
    super.initState();
    _c = widget.ctrl ?? ControlCtrl();
    _c.addListener(_update);
    _c.refreshVolume();
  }

  @override
  void dispose() {
    _c.removeListener(_update);
    if (widget.ctrl == null) _c.dispose();
    _type.dispose();
    super.dispose();
  }

  void _update() {
    if (mounted) setState(() {});
  }

  Future<bool> _confirm(String title, String body) async =>
      await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title:
              Text(title.toUpperCase(), style: JarvisTheme.label(JarvisTheme.amber, 12)),
          content: Text(body,
              style: const TextStyle(color: JarvisTheme.text, fontSize: 14)),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('Cancel')),
            FilledButton(
                style: FilledButton.styleFrom(
                    backgroundColor: JarvisTheme.danger,
                    foregroundColor: Colors.black),
                onPressed: () => Navigator.pop(ctx, true),
                child: const Text('DO IT')),
          ],
        ),
      ) ==
      true;

  void _say(String msg) {
    if (!mounted) return;
    HapticFeedback.selectionClick();
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _reload() async => _c.refreshVolume();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('SYSTEMS CONTROL',
                  style: JarvisTheme.label(JarvisTheme.cyan, 12)),
              const SizedBox(height: 2),
              Text(
                  _c.muted
                      ? 'Audio muted · ${_c.volume}%'
                      : 'Audio live · ${_c.volume}%',
                  style: const TextStyle(
                      color: JarvisTheme.muted, fontSize: 12)),
            ]),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: IconButton(
              tooltip: 'Refresh volume',
              icon: const Icon(Icons.sync),
              onPressed: _reload,
            ),
          ),
        ],
      ),
      body: RefreshIndicator(
        color: JarvisTheme.cyan,
        backgroundColor: JarvisTheme.panel,
        onRefresh: _reload,
        child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
            physics: const AlwaysScrollableScrollPhysics(),
            children: [
              const HudScanline(),
              const SizedBox(height: 12),
              _section(0, 'Volume', Icons.volume_up, [
                Row(children: [
                  HudPanel(
                    padding: const EdgeInsets.all(8),
                    onTap: () async => _c.toggleMute(),
                    child: Icon(
                        _c.muted ? Icons.volume_off : Icons.volume_up,
                        color: _c.muted
                            ? JarvisTheme.amber
                            : JarvisTheme.cyan),
                  ),
                  Expanded(
                    child: Slider(
                      value: _c.volume.toDouble(),
                      max: 100,
                      divisions: 20,
                      label: '${_c.volume}%',
                      onChanged: (v) =>
                          setState(() => _c.volume = v.toInt()),
                      onChangeEnd: (v) => _c.setVolume(v.toInt()),
                    ),
                  ),
                  SizedBox(
                      width: 48,
                      child: Text('${_c.volume}%',
                          textAlign: TextAlign.right,
                          style: JarvisTheme.label(
                              JarvisTheme.cyan, 12))),
                ]),
              ]),
              _section(1, 'Media', Icons.play_arrow, [
                Row(
                    mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                    children: [
                      _MediaBtn(
                          icon: Icons.skip_previous,
                          tip: 'Previous',
                          onTap: () => _c.media('media_prev')),
                      _MediaBtn(
                          icon: Icons.play_arrow,
                          tip: 'Play / pause',
                          hero: true,
                          onTap: () => _c.media('media_play_pause')),
                      _MediaBtn(
                          icon: Icons.skip_next,
                          tip: 'Next',
                          onTap: () => _c.media('media_next')),
                    ]),
                if (_c.nowPlaying != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 10),
                    child: Center(
                      child: Text('♪ ${_c.nowPlaying!}',
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                              color: JarvisTheme.muted, fontSize: 12)),
                    ),
                  ),
              ]),
              _section(2, 'Screens', Icons.monitor, [
                if (_c.shot != null)
                  Container(
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(
                          JarvisTheme.rPanel),
                      border: Border.all(
                          color: JarvisTheme.cyan
                              .withValues(alpha: 0.4)),
                      boxShadow: [
                        BoxShadow(
                          color: JarvisTheme.cyan
                              .withValues(alpha: 0.12),
                          blurRadius: 18,
                        ),
                      ],
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(
                          JarvisTheme.rPanel),
                      child: Image.memory(_c.shot!,
                          fit: BoxFit.contain),
                    ),
                  ),
                if (_c.note.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(_c.note.toUpperCase(),
                        style: JarvisTheme.label(
                            JarvisTheme.muted, 10)),
                  ),
                const SizedBox(height: 10),
                Wrap(spacing: 8, runSpacing: 8, children: [
                  _HudBtn(label: 'CAPTURE', onTap: _c.capture),
                  _HudBtn(
                      label: 'STATE',
                      onTap: () => _c.screens('screens_state')),
                  _HudBtn(
                      label: 'WAKE',
                      onTap: () => _c.screens('screens_restore')),
                  _HudBtn(
                      label: 'SLEEP',
                      danger: true,
                      onTap: () async {
                        if (await _confirm('Blank all displays?',
                            'Turns every monitor off until woken.')) {
                          _say(await _c.screens('screens_off'));
                        }
                      }),
                ]),
              ]),
              _section(3, 'Apps', Icons.apps, [
                Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: ControlCtrl.apps
                        .map((a) => ActionChip(
                              label: Text(a.toUpperCase(),
                                  style: JarvisTheme.label(
                                      JarvisTheme.text, 10)),
                              onPressed: () async =>
                                  _say(await _c.openApp(a)),
                            ))
                        .toList()),
              ]),
              _section(4, 'Remote keys', Icons.keyboard, [
                Row(children: [
                  Expanded(
                    child: TextField(
                      controller: _type,
                      decoration: const InputDecoration(
                          hintText: 'Type on the PC…'),
                      onSubmitted: (_) async {
                        _say(await _c.typeText(_type.text));
                        _type.clear();
                      },
                    ),
                  ),
                  const SizedBox(width: 8),
                  HudPanel(
                    padding: const EdgeInsets.all(4),
                    onTap: () async =>
                        _say(await _c.pressEnter()),
                    child: const Padding(
                      padding: EdgeInsets.all(10),
                      child: Icon(Icons.keyboard_return,
                          color: JarvisTheme.cyan, size: 20),
                    ),
                  ),
                ]),
              ]),
            ]),
      ),
    );
  }

  Widget _section(
      int index, String title, IconData icon, List<Widget> kids) {
    return Stagger(
      index: index,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: HudPanel(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(children: [
                  Icon(icon, color: JarvisTheme.cyan, size: 16),
                  const SizedBox(width: 8),
                  Expanded(child: SectionLabel(title)),
                ]),
                const SizedBox(height: 12),
                ...kids,
              ]),
        ),
      ),
    );
  }
}

class _MediaBtn extends StatelessWidget {
  final IconData icon;
  final String tip;
  final bool hero;
  final VoidCallback onTap;
  const _MediaBtn(
      {required this.icon,
      required this.tip,
      required this.onTap,
      this.hero = false});

  @override
  Widget build(BuildContext context) {
    return HudPanel(
      glow: hero,
      padding: EdgeInsets.all(hero ? 12 : 9),
      onTap: () {
        HapticFeedback.selectionClick();
        onTap();
      },
      child: Tooltip(
        message: tip,
        child: Icon(icon,
            color: JarvisTheme.cyan, size: hero ? 30 : 24),
      ),
    );
  }
}

class _HudBtn extends StatelessWidget {
  final String label;
  final VoidCallback onTap;
  final bool danger;
  const _HudBtn(
      {required this.label, required this.onTap, this.danger = false});

  @override
  Widget build(BuildContext context) {
    final c = danger ? JarvisTheme.danger : JarvisTheme.cyan;
    return InkWell(
      borderRadius: BorderRadius.circular(8),
      onTap: () {
        HapticFeedback.selectionClick();
        onTap();
      },
      child: Container(
        padding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
        decoration: BoxDecoration(
          color: c.withValues(alpha: 0.10),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: c.withValues(alpha: 0.55)),
        ),
        child: Text(label, style: JarvisTheme.label(c, 10)),
      ),
    );
  }
}
