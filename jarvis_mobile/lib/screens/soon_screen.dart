import 'package:flutter/material.dart';

import '../core/theme.dart';

/// Placeholder tab for the next phase (Chat, then Control).
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
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 56, color: JarvisTheme.cyanDim),
        const SizedBox(height: 12),
        Text(title,
            style: const TextStyle(
                color: JarvisTheme.text, fontSize: 18)),
        const SizedBox(height: 4),
        Text(subtitle,
            style: const TextStyle(
                color: JarvisTheme.muted, fontSize: 13)),
      ]),
    );
  }
}
