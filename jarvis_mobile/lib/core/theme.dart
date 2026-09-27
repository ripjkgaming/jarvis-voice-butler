import 'package:flutter/material.dart';

/// J.A.R.V.I.S. cinematic design system — Iron Man 1-2 HUD.
///
/// Near-black deep-navy base, cyan signal glow (#5FE3FF), amber warnings,
/// glassy panels with hairline cyan borders + bracket corners (painted by
/// [HudPanel] in widgets/hud.dart), mono uppercase telemetry labels.
class JarvisTheme {
  // ── Palette ──────────────────────────────────────────────
  static const cyan = Color(0xFF5FE3FF);
  static const cyanBright = Color(0xFFBDF3FF);
  static const cyanDeep = Color(0xFF0E7E96);
  static const cyanDim = Color(0xFF2A7E96);
  static const bg = Color(0xFF04070D);
  static const bg2 = Color(0xFF070D16);
  static const panel = Color(0xFF0B1420);
  static const panelHi = Color(0xFF101C2C);
  static const line = Color(0xFF1B2A35);
  static const lineCyan = Color(0xFF2B4A5E);
  static const text = Color(0xFFE8F6FC);
  static const muted = Color(0xFF7B93A3);
  static const amber = Color(0xFFFFB648);
  static const danger = Color(0xFFFF5D5D);
  static const ok = Color(0xFF5DF2B8);

  // ── Spacing scale ────────────────────────────────────────
  static const s4 = 4.0;
  static const s8 = 8.0;
  static const s12 = 12.0;
  static const s16 = 16.0;
  static const s20 = 20.0;
  static const s24 = 24.0;
  static const s32 = 32.0;

  // ── Radii (rounded-but-sharp) ────────────────────────────
  static const rPanel = 10.0;
  static const rBubble = 14.0;
  static const rPill = 999.0;

  // ── Type ─────────────────────────────────────────────────
  /// Monospaced uppercase telemetry label.
  static TextStyle label([Color color = muted, double size = 11]) =>
      TextStyle(
        color: color,
        fontSize: size,
        letterSpacing: 2.2,
        fontWeight: FontWeight.w600,
        fontFamily: 'monospace',
        fontFamilyFallback: const ['Menlo', 'Consolas', 'monospace'],
      );

  static const title = TextStyle(
    color: text,
    fontSize: 20,
    fontWeight: FontWeight.w700,
    letterSpacing: 0.4,
  );
  static const subtitle = TextStyle(color: muted, fontSize: 13, height: 1.45);
  static const body = TextStyle(color: text, fontSize: 14, height: 1.5);
  static const caption = TextStyle(color: muted, fontSize: 12, height: 1.4);

  static BoxDecoration panelDeco({bool glow = false}) => BoxDecoration(
        color: panel.withValues(alpha: 0.72),
        borderRadius: BorderRadius.circular(rPanel),
        border: Border.all(color: lineCyan.withValues(alpha: 0.55)),
        boxShadow: glow
            ? [
                BoxShadow(
                  color: cyan.withValues(alpha: 0.14),
                  blurRadius: 22,
                  spreadRadius: 0,
                ),
              ]
            : null,
      );

  static ThemeData get dark {
    final scheme = const ColorScheme.dark(
      primary: cyan,
      secondary: cyanDeep,
      tertiary: amber,
      error: danger,
      surface: panel,
      onSurface: text,
    );
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: bg,
      colorScheme: scheme,
      splashFactory: InkSparkle.splashFactory,
      appBarTheme: const AppBarTheme(
        backgroundColor: Colors.transparent,
        foregroundColor: text,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
      ),
      cardTheme: CardThemeData(
        color: panel.withValues(alpha: 0.72),
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(rPanel),
          side: BorderSide(color: lineCyan.withValues(alpha: 0.55)),
        ),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: panelHi,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(rPanel),
          side: BorderSide(color: lineCyan.withValues(alpha: 0.7)),
        ),
        titleTextStyle: const TextStyle(
            color: text, fontSize: 16, fontWeight: FontWeight.w700),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: panel.withValues(alpha: 0.8),
        hintStyle: const TextStyle(color: muted, fontSize: 14),
        labelStyle: const TextStyle(color: muted, fontSize: 13),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(rPanel),
          borderSide: BorderSide(color: lineCyan.withValues(alpha: 0.5)),
        ),
        focusedBorder: const OutlineInputBorder(
          borderRadius: BorderRadius.all(Radius.circular(rPanel)),
          borderSide: BorderSide(color: cyan, width: 1.2),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: cyan,
          foregroundColor: const Color(0xFF031018),
          textStyle:
              const TextStyle(fontWeight: FontWeight.w700, fontSize: 14),
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(rPanel)),
          padding:
              const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(foregroundColor: cyan),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(foregroundColor: cyan),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: panel.withValues(alpha: 0.8),
        labelStyle: const TextStyle(color: text, fontSize: 13),
        side: BorderSide(color: lineCyan.withValues(alpha: 0.6)),
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(8)),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      ),
      sliderTheme: SliderThemeData(
        activeTrackColor: cyan,
        inactiveTrackColor: line,
        thumbColor: cyanBright,
        overlayColor: cyan.withValues(alpha: 0.15),
        valueIndicatorColor: panelHi,
        valueIndicatorTextStyle: const TextStyle(color: text, fontSize: 12),
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: cyan,
        linearTrackColor: line,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: panelHi,
        contentTextStyle: const TextStyle(color: text, fontSize: 13),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(rPanel),
          side: BorderSide(color: lineCyan.withValues(alpha: 0.6)),
        ),
        behavior: SnackBarBehavior.floating,
      ),
      dividerColor: line,
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: bg.withValues(alpha: 0.9),
        indicatorColor: cyan.withValues(alpha: 0.16),
        labelTextStyle: WidgetStatePropertyAll(label(muted, 10)),
      ),
    );
  }
}
