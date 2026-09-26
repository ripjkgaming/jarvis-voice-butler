import 'package:flutter/material.dart';

/// Jarvis HUD theme: near-black glass, cyan signal color (matches the
/// native OrbView + desktop overlay palette).
class JarvisTheme {
  static const cyan = Color(0xFF1FD5F9);
  static const cyanDim = Color(0xFF0E7E96);
  static const bg = Color(0xFF06090C);
  static const panel = Color(0xFF0C1218);
  static const line = Color(0xFF1B2A35);
  static const text = Color(0xFFD7E8F0);
  static const muted = Color(0xFF6B8291);

  static ThemeData get dark => ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        scaffoldBackgroundColor: bg,
        colorScheme: const ColorScheme.dark(
          primary: cyan,
          surface: panel,
          onSurface: text,
        ),
        appBarTheme: const AppBarTheme(
          backgroundColor: bg,
          foregroundColor: text,
          elevation: 0,
        ),
        cardTheme: CardThemeData(
          color: panel,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: const BorderSide(color: line),
          ),
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: panel,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: const BorderSide(color: line),
          ),
        ),
      );
}
