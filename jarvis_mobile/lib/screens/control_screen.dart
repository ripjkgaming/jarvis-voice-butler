import 'package:flutter/material.dart';

import '../core/control_ctrl.dart';
import '../core/theme.dart';

/// Phase 4 remote control: volume, media keys, screens viewer + power,
/// app launcher chips, type/enter. Destructive actions ask first.
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
          backgroundColor: JarvisTheme.panel,
          title: Text(title,
              style: const TextStyle(color: JarvisTheme.text)),
          content: Text(body,
              style: const TextStyle(color: JarvisTheme.muted)),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('Cancel')),
            FilledButton(
                style: FilledButton.styleFrom(
                    backgroundColor: Colors.redAccent),
                onPressed: () => Navigator.pop(ctx, true),
                child: const Text('Do it',
                    style: TextStyle(color: Colors.black))),
          ],
        ),
      ) ==
      true;

  void _say(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    return ListView(padding: const EdgeInsets.all(16), children: [
      _section('VOLUME', [
        Row(children: [
          IconButton(
              icon: Icon(
                  _c.muted ? Icons.volume_off : Icons.volume_up),
              color: JarvisTheme.cyan,
              onPressed: () async {
                await _c.toggleMute();
              }),
          Expanded(
            child: Slider(
              value: _c.volume.toDouble(),
              max: 100,
              divisions: 20,
              label: '${_c.volume}%',
              onChanged: (v) => setState(() => _c.volume = v.toInt()),
              onChangeEnd: (v) => _c.setVolume(v.toInt()),
            ),
          ),
          SizedBox(
              width: 44,
              child: Text('${_c.volume}%',
                  style: const TextStyle(color: JarvisTheme.muted))),
        ]),
      ]),
      _section('MEDIA', [
        Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
          IconButton.filled(
              tooltip: 'Previous',
              onPressed: () => _c.media('media_prev'),
              icon: const Icon(Icons.skip_previous)),
          IconButton.filled(
              tooltip: 'Play / pause',
              onPressed: () => _c.media('media_play_pause'),
              icon: const Icon(Icons.play_arrow)),
          IconButton.filled(
              tooltip: 'Next',
              onPressed: () => _c.media('media_next'),
              icon: const Icon(Icons.skip_next)),
        ]),
        if (_c.nowPlaying != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(_c.nowPlaying!,
                textAlign: TextAlign.center,
                style: const TextStyle(
                    color: JarvisTheme.muted, fontSize: 12)),
          ),
      ]),
      _section('SCREENS', [
        if (_c.shot != null)
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Image.memory(_c.shot!, fit: BoxFit.contain),
          ),
        if (_c.note.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(_c.note,
                style: const TextStyle(
                    color: JarvisTheme.muted, fontSize: 12)),
          ),
        const SizedBox(height: 8),
        Wrap(spacing: 8, runSpacing: 8, children: [
          FilledButton.tonal(
              onPressed: _c.capture, child: const Text('Capture')),
          FilledButton.tonal(
              onPressed: () => _c.screens('screens_state'),
              child: const Text('State')),
          FilledButton.tonal(
              onPressed: () => _c.screens('screens_restore'),
              child: const Text('Wake')),
          FilledButton.tonal(
              style: FilledButton.styleFrom(
                  foregroundColor: Colors.redAccent),
              onPressed: () async {
                if (await _confirm('Blank all displays?',
                    'Turns every monitor off until woken.')) {
                  _say(await _c.screens('screens_off'));
                }
              },
              child: const Text('Sleep')),
        ]),
      ]),
      _section('APPS', [
        Wrap(
            spacing: 8,
            runSpacing: 8,
            children: ControlCtrl.apps
                .map((a) => ActionChip(
                      label: Text(a),
                      onPressed: () async =>
                          _say(await _c.openApp(a)),
                    ))
                .toList()),
      ]),
      _section('TYPE', [
        Row(children: [
          Expanded(
            child: TextField(
              controller: _type,
              decoration:
                  const InputDecoration(hintText: 'Type on the PC…'),
              onSubmitted: (_) async {
                _say(await _c.typeText(_type.text));
                _type.clear();
              },
            ),
          ),
          const SizedBox(width: 8),
          IconButton.filled(
              tooltip: 'Enter',
              onPressed: () async => _say(await _c.pressEnter()),
              icon: const Icon(Icons.keyboard_return)),
        ]),
      ]),
    ]);
  }

  int _order = 0;

  Widget _section(String title, List<Widget> kids) {
    final index = _order++;
    return _EnterOnce(
      delay: Duration(milliseconds: 70 * index.clamp(0, 6)),
      child: Card(
        margin: const EdgeInsets.only(bottom: 12),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(title,
                    style: const TextStyle(
                        color: JarvisTheme.muted,
                        fontSize: 11,
                        letterSpacing: 2)),
                const SizedBox(height: 8),
                ...kids,
              ]),
        ),
      ),
    );
  }
}

/// Entrance that plays exactly once (initState), never on rebuilds.
class _EnterOnce extends StatefulWidget {
  final Duration delay;
  final Widget child;
  const _EnterOnce({required this.delay, required this.child});

  @override
  State<_EnterOnce> createState() => _EnterOnceState();
}

class _EnterOnceState extends State<_EnterOnce>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c;

  @override
  void initState() {
    super.initState();
    _c = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 350));
    Future.delayed(widget.delay, () {
      if (mounted) _c.forward();
    });
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: CurvedAnimation(parent: _c, curve: Curves.easeOutCubic),
      child: SlideTransition(
        position: Tween(begin: const Offset(0, 0.04), end: Offset.zero)
            .animate(
                CurvedAnimation(parent: _c, curve: Curves.easeOutCubic)),
        child: widget.child,
      ),
    );
  }
}
