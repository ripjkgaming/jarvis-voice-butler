import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/theme.dart';
import 'character_ctrl.dart';
import 'models.dart';
import 'skin_tokens.dart';
import 'transition.dart';

/// Display name of the character on screen ("JARVIS" when no scope exists,
/// e.g. in isolated widget tests).
String brandName(BuildContext c, [String fallback = 'JARVIS']) {
  final s = CxScope.maybeOf(c);
  if (s == null) return fallback;
  final info = s.ctrl.characters[s.ctrl.active];
  if (s.ctrl.active == 'jarvis' || info == null) return fallback;
  return info.displayName.toUpperCase();
}

/// J.A.R.V.I.S. -> U.L.T.R.O.N.
String wordmark(BuildContext c) {
  final n = brandName(c);
  if (n == 'JARVIS') return 'J.A.R.V.I.S.';
  return '${n.replaceAll(RegExp(r'[^A-Z]'), '').split('').join('.')}.';
}

SkinTokens skinFor(CharacterInfo? c) => c == null || c.id == 'jarvis'
    ? SkinTokens.jarvis
    : SkinTokens.fromTokens(c.themeTokens);

/// Character chip. Tap opens a large-target picker; selecting runs the same
/// switch path as voice and typed commands.
class CharacterChip extends StatelessWidget {
  const CharacterChip({super.key});

  @override
  Widget build(BuildContext context) {
    final ctrl = CxScope.maybeOf(context)?.ctrl;
    if (ctrl == null) return const SizedBox.shrink();
    return AnimatedBuilder(
      animation: ctrl,
      builder: (context, _) {
        if (!ctrl.ready) return const SizedBox.shrink();
        final cur = ctrl.characters[ctrl.active];
        if (cur == null) return const SizedBox.shrink();
        final tk = skinFor(cur);
        return Semantics(
          button: true,
          label: 'Character ${cur.displayName}. Change character',
          child: InkWell(
            borderRadius: BorderRadius.circular(JarvisTheme.rPill),
            onTap: () => _open(context, ctrl),
            child: Container(
              constraints: const BoxConstraints(minHeight: 36, minWidth: 48),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: JarvisTheme.cyan.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(JarvisTheme.rPill),
                border: Border.all(
                    color: JarvisTheme.cyan.withValues(alpha: 0.55)),
              ),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                _Swatch(tk, 10),
                const SizedBox(width: 8),
                Text(cur.displayName.toUpperCase(),
                    style: JarvisTheme.label(JarvisTheme.text, 11)),
                const SizedBox(width: 4),
                Icon(
                    ctrl.phase == SwitchPhase.idle
                        ? Icons.expand_more
                        : Icons.more_horiz,
                    size: 16,
                    color: JarvisTheme.muted),
              ]),
            ),
          ),
        );
      },
    );
  }

  Future<void> _open(BuildContext context, CharacterCtrl ctrl) async {
    HapticFeedback.selectionClick();
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: JarvisTheme.panelHi,
      showDragHandle: true,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (sheet) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Align(
              alignment: Alignment.centerLeft,
              child: Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Text('CHARACTER', style: JarvisTheme.label(JarvisTheme.muted, 11)),
              ),
            ),
            for (final id in ctrl.order)
              if (ctrl.characters[id] case final c?)
                _PickRow(
                  info: c,
                  active: id == ctrl.active,
                  onTap: () {
                    Navigator.of(sheet).pop();
                    HapticFeedback.mediumImpact();
                    ctrl.requestSwitch(id, 'ui');
                  },
                ),
            if (ctrl.notice != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(ctrl.notice!,
                    style: JarvisTheme.label(JarvisTheme.amber, 10)),
              ),
          ]),
        ),
      ),
    );
  }
}

class _Swatch extends StatelessWidget {
  final SkinTokens t;
  final double size;
  const _Swatch(this.t, this.size);

  @override
  Widget build(BuildContext context) => Transform.rotate(
        angle: 0.785398,
        child: Container(
          width: size,
          height: size,
          decoration: BoxDecoration(
            gradient: LinearGradient(colors: [t.cyanBright, t.cyan]),
          ),
        ),
      );
}

class _PickRow extends StatelessWidget {
  final CharacterInfo info;
  final bool active;
  final VoidCallback onTap;
  const _PickRow({required this.info, required this.active, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final tk = skinFor(info);
    return Semantics(
      button: true,
      selected: active,
      label: '${info.displayName}, calls you ${info.address}',
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 60),
          margin: const EdgeInsets.symmetric(vertical: 3),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: tk.bg2,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
                color: active ? tk.cyan : tk.lineCyan.withValues(alpha: 0.6),
                width: active ? 1.6 : 1),
          ),
          child: Row(children: [
            _Swatch(tk, 14),
            const SizedBox(width: 16),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(info.displayName.toUpperCase(),
                    style: JarvisTheme.label(tk.text, 13)),
                const SizedBox(height: 3),
                Text('Calls you "${info.address}"',
                    style: TextStyle(color: tk.muted, fontSize: 12)),
              ]),
            ),
            if (active)
              Text('ACTIVE', style: JarvisTheme.label(tk.cyan, 10)),
          ]),
        ),
      ),
    );
  }
}

/// Name the character answers to ("Jarvis" outside a scope).
String spokenName(BuildContext c) {
  final s = CxScope.maybeOf(c);
  return s?.ctrl.characters[s.ctrl.active]?.spokenName ?? 'Jarvis';
}

String tagline(BuildContext c, String fallback) {
  final s = CxScope.maybeOf(c);
  final info = s?.ctrl.characters[s.ctrl.active];
  if (s == null || s.ctrl.active == 'jarvis' || info == null) return fallback;
  return info.lines['tagline']?.firstOrNull ?? fallback;
}
