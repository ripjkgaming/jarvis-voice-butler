import 'package:flutter/material.dart';

import '../core/theme.dart';
import '../widgets/hud.dart';

/// Cinematic placeholder for future phases. Kept for the "soon" slot.
class SoonScreen extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;

  const SoonScreen(
      {super.key,
      required this.icon,
      required this.title,
      required this.subtitle});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: HudPanel(
          glow: true,
          padding: const EdgeInsets.symmetric(
              horizontal: 24, vertical: 32),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Container(
              width: 72,
              height: 72,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(
                    color:
                        JarvisTheme.cyan.withValues(alpha: 0.5)),
                boxShadow: [
                  BoxShadow(
                      color: JarvisTheme.cyan
                          .withValues(alpha: 0.2),
                      blurRadius: 24),
                ],
              ),
              child: Icon(icon,
                  size: 34, color: JarvisTheme.cyan),
            ),
            const SizedBox(height: 16),
            Text(title.toUpperCase(),
                style: JarvisTheme.label(JarvisTheme.text, 14)),
            const SizedBox(height: 8),
            Text(subtitle,
                textAlign: TextAlign.center,
                style: JarvisTheme.caption),
            const SizedBox(height: 16),
            const HudLoading(label: 'COMING ONLINE'),
          ]),
        ),
      ),
    );
  }
}
