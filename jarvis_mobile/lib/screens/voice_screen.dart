import 'dart:async';

import 'package:flutter/material.dart';

import '../core/link_api.dart';
import '../core/prefs.dart';
import '../core/theme.dart';
import '../core/voice_ctrl.dart';
import '../widgets/orb.dart';

/// Phase 2 voice tab: join via bridge-minted token, mic live, remote
/// audio auto-plays, orb breathes with speakers, captions stream below.
class VoiceScreen extends StatefulWidget {
  const VoiceScreen({super.key});

  @override
  State<VoiceScreen> createState() => _VoiceScreenState();
}

class _VoiceScreenState extends State<VoiceScreen> {
  final VoiceCtrl _call = VoiceCtrl();
  bool _muted = false;
  List<Map<String, String>> _captions = [];
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    _call.addListener(_onCall);
  }

  @override
  void dispose() {
    _poll?.cancel();
    _call.dispose();
    super.dispose();
  }

  void _onCall() {
    if (!mounted) return;
    setState(() {});
    if (_call.live && _poll == null) {
      _poll = Timer.periodic(
          const Duration(seconds: 3), (_) => _pullCaptions());
      _pullCaptions();
    }
    if (!_call.live) {
      _poll?.cancel();
      _poll = null;
    }
  }

  Future<void> _pullCaptions() async {
    final api = LinkApi(
        base: await Prefs.host(), token: await Prefs.token());
    if (api.base.isEmpty) return;
    final caps = await api.captions(limit: 8);
    if (!mounted) return;
    final raw = caps['captions'];
    if (raw is List) {
      setState(() {
        _captions = raw
            .whereType<Map>()
            .map((m) => {
                  'role': '${m['role'] ?? ''}',
                  'text': '${m['text'] ?? ''}',
                })
            .toList()
            .reversed
            .toList();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _call,
      builder: (ctx, _) => ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const SizedBox(height: 8),
          Center(
            child: Hero(
              tag: 'jarvis-orb',
              child: JarvisOrb(energy: _call.energy, size: 240),
            ),
          ),
          const SizedBox(height: 12),
          Center(
            child: Text(
              switch (_call.state) {
                CallState.idle => 'Tap to summon Jarvis',
                CallState.joining => _call.detail.isEmpty
                    ? 'Joining…'
                    : _call.detail,
                CallState.live =>
                  _muted ? 'Muted — tap mic to speak' : 'Listening…',
                CallState.error => 'Error: ${_call.detail}',
              },
              style: const TextStyle(
                  color: JarvisTheme.muted, fontSize: 14),
            ),
          ),
          const SizedBox(height: 16),
          Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            FilledButton.icon(
              onPressed: _call.state == CallState.live
                  ? _call.hangup
                  : _call.join,
              icon: Icon(_call.state == CallState.live
                  ? Icons.call_end
                  : Icons.mic),
              label: Text(
                  _call.state == CallState.live ? 'Hang up' : 'Talk'),
              style: FilledButton.styleFrom(
                backgroundColor: _call.state == CallState.live
                    ? Colors.redAccent
                    : JarvisTheme.cyan,
                foregroundColor: Colors.black,
                padding: const EdgeInsets.symmetric(
                    horizontal: 28, vertical: 14),
              ),
            ),
            if (_call.live) ...[
              const SizedBox(width: 12),
              IconButton.filled(
                onPressed: () async {
                  setState(() => _muted = !_muted);
                  await _call.setMuted(_muted);
                },
                icon: Icon(
                    _muted ? Icons.mic_off : Icons.mic_none),
                style: IconButton.styleFrom(
                    backgroundColor: JarvisTheme.panel),
              ),
            ],
          ]),
          if (_call.state == CallState.error) ...[
            const SizedBox(height: 8),
            Center(
              child: TextButton(
                  onPressed: _call.join,
                  child: const Text('Retry')),
            ),
          ],
          const SizedBox(height: 20),
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
        ],
      ),
    );
  }
}
