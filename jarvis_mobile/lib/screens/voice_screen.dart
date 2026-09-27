import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/link_api.dart';
import '../core/prefs.dart';
import '../core/theme.dart';
import '../core/voice_ctrl.dart';
import '../widgets/hud.dart';
import '../widgets/orb.dart';

/// Voice tab: join via bridge-minted token, mic live, remote audio
/// auto-plays, orb breathes with speakers, captions stream below.
/// Logic untouched — same VoiceCtrl join/hangup/mute pipeline.
class VoiceScreen extends StatefulWidget {
  const VoiceScreen({super.key});

  @override
  State<VoiceScreen> createState() => _VoiceScreenState();
}

class _VoiceScreenState extends State<VoiceScreen> {
  final VoiceCtrl _call = VoiceCtrl();
  bool _muted = false;
  bool _unlinked = false;
  List<Map<String, String>> _captions = [];
  Timer? _poll;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    Prefs.host().then((h) {
      if (mounted) setState(() => _unlinked = h.isEmpty);
    });
  }

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

  String get _status {
    switch (_call.state) {
      case CallState.idle:
        return _unlinked
            ? 'Link the bridge first (Home → link icon)'
            : 'Tap to summon Jarvis';
      case CallState.joining:
        return _call.detail.isEmpty ? 'Joining…' : _call.detail;
      case CallState.live:
        return _muted ? 'Muted — tap mic to speak' : 'Listening…';
      case CallState.error:
        return 'Error: ${_call.detail}';
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _call,
      builder: (ctx, _) {
        final live = _call.state == CallState.live;
        final joining = _call.state == CallState.joining;
        final error = _call.state == CallState.error;
        return Scaffold(
          backgroundColor: Colors.transparent,
          appBar: AppBar(
            title: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('VOICE UPLINK',
                      style: JarvisTheme.label(JarvisTheme.cyan, 12)),
                  const SizedBox(height: 2),
                  const Text('LiveKit · low-latency channel',
                      style: TextStyle(
                          color: JarvisTheme.muted, fontSize: 12)),
                ]),
            actions: [
              Padding(
                padding: const EdgeInsets.only(right: 12),
                child: Center(
                  child: StatusPill(
                    live
                        ? 'LIVE'
                        : joining
                            ? 'JOINING'
                            : error
                                ? 'FAULT'
                                : 'IDLE',
                    good: live,
                    warn: joining,
                  ),
                ),
              ),
            ],
          ),
          body: ListView(
            padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
            children: [
              const HudScanline(),
              const SizedBox(height: 12),
              Center(
                child: Hero(
                  tag: 'jarvis-orb',
                  child: JarvisOrb(energy: _call.energy, size: 250),
                ),
              ),
              const SizedBox(height: 12),
              Center(
                child: joining
                    ? const HudLoading(label: 'ESTABLISHING UPLINK')
                    : Text(
                        _status.toUpperCase(),
                        textAlign: TextAlign.center,
                        style: JarvisTheme.label(
                            error
                                ? JarvisTheme.amber
                                : JarvisTheme.muted,
                            11),
                      ),
              ),
              const SizedBox(height: 18),
              Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                _TalkButton(
                  live: live,
                  joining: joining,
                  onTap: () {
                    HapticFeedback.mediumImpact();
                    if (live) {
                      _call.hangup();
                    } else {
                      _call.join();
                    }
                  },
                ),
                if (live) ...[
                  const SizedBox(width: 12),
                  HudPanel(
                    padding: const EdgeInsets.all(4),
                    onTap: () async {
                      setState(() => _muted = !_muted);
                      await _call.setMuted(_muted);
                    },
                    child: Padding(
                      padding: const EdgeInsets.all(10),
                      child: Icon(
                          _muted ? Icons.mic_off : Icons.mic_none,
                          color: _muted
                              ? JarvisTheme.amber
                              : JarvisTheme.cyan),
                    ),
                  ),
                ],
              ]),
              if (error) ...[
                const SizedBox(height: 12),
                HudError(
                    message: _call.detail.isEmpty
                        ? 'Uplink failed.'
                        : _call.detail,
                    onRetry: _call.join),
              ],
              const SizedBox(height: 20),
              const SectionLabel('Live transcript'),
              const SizedBox(height: 10),
              HudPanel(
                child: _captions.isEmpty
                    ? const Center(
                        child: Text(
                            'No transcript yet — it streams here while live.',
                            style: TextStyle(
                                color: JarvisTheme.muted, fontSize: 13)),
                      )
                    : Column(children: [
                        for (var i = 0; i < _captions.length; i++)
                          Padding(
                            padding: EdgeInsets.only(
                                bottom: i == _captions.length - 1 ? 0 : 10),
                            child: Row(
                                crossAxisAlignment:
                                    CrossAxisAlignment.start,
                                children: [
                                  Icon(
                                      _captions[i]['role'] == 'jarvis'
                                          ? Icons.smart_toy_outlined
                                          : Icons.person_outline,
                                      color: JarvisTheme.cyan,
                                      size: 18),
                                  const SizedBox(width: 8),
                                  Expanded(
                                    child: Text(_captions[i]['text'] ?? '',
                                        style: const TextStyle(
                                            color: JarvisTheme.text,
                                            fontSize: 13,
                                            height: 1.45)),
                                  ),
                                ]),
                          ),
                      ]),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _TalkButton extends StatelessWidget {
  final bool live;
  final bool joining;
  final VoidCallback onTap;
  const _TalkButton(
      {required this.live, required this.joining, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final bg = live ? JarvisTheme.danger : JarvisTheme.cyan;
    return GestureDetector(
      onTap: joining ? null : onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 250),
        padding:
            const EdgeInsets.symmetric(horizontal: 30, vertical: 15),
        decoration: BoxDecoration(
          color: bg.withValues(alpha: joining ? 0.4 : 0.95),
          borderRadius: BorderRadius.circular(JarvisTheme.rPanel),
          border: Border.all(
              color: Colors.white.withValues(alpha: 0.25)),
          boxShadow: [
            BoxShadow(
                color: bg.withValues(alpha: 0.45),
                blurRadius: 24,
                spreadRadius: 1),
          ],
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(live ? Icons.call_end : Icons.mic,
              color: const Color(0xFF031018), size: 20),
          const SizedBox(width: 10),
          Text((live ? 'HANG UP' : joining ? 'JOINING' : 'TALK')
              .toUpperCase(),
              style: const TextStyle(
                  color: Color(0xFF031018),
                  fontWeight: FontWeight.w800,
                  letterSpacing: 2,
                  fontSize: 14)),
        ]),
      ),
    );
  }
}
