import 'package:flutter/material.dart';

import '../character/skin_tokens.dart';

/// J.A.R.V.I.S. cinematic design system — Iron Man 1-2 HUD.
///
/// Near-black deep-navy base, cyan signal glow (#5FE3FF), amber warnings,
/// glassy panels with hairline cyan borders + bracket corners (painted by
/// [HudPanel] in widgets/hud.dart), mono uppercase telemetry labels.
class JarvisTheme {
  // ── Palette ──────────────────────────────────────────────
  static Color get cyan => Skin.current.cyan;
  static Color get cyanBright => Skin.current.cyanBright;
  static Color get cyanDeep => Skin.current.cyanDeep;
  static Color get cyanDim => Skin.current.cyanDim;
  static Color get bg => Skin.current.bg;
  static Color get bg2 => Skin.current.bg2;
  static Color get panel => Skin.current.panel;
  static Color get panelHi => Skin.current.panelHi;
  static Color get line => Skin.current.line;
  static Color get lineCyan => Skin.current.lineCyan;
  static Color get text => Skin.current.text;
  static Color get muted => Skin.current.muted;
  static Color get amber => Skin.current.amber;
  static Color get danger => Skin.current.danger;
  static Color get ok => Skin.current.ok;

  // ── Spacing scale ────────────────────────────────────────
  static const s4 = 4.0;
  static const s8 = 8.0;
  static const s12 = 12.0;
  static const s16 = 16.0;
  static const s20 = 20.0;
  static const s24 = 24.0;
  static const s32 = 32.0;

  // ── Radii (rounded-but-sharp) ────────────────────────────
  static double get rPanel => Skin.current.radius;
  static const rBubble = 14.0;
  static const rPill = 999.0;

  // ── Type ─────────────────────────────────────────────────
  /// Monospaced uppercase telemetry label.
  static TextStyle label([Color? color, double size = 11]) =>
      TextStyle(
        color: color ?? muted,
        fontSize: size,
        letterSpacing: 2.2,
        fontWeight: FontWeight.w600,
        fontFamily: Skin.current.mono ? 'monospace' : null,
        fontFamilyFallback: Skin.current.mono
            ? const ['Menlo', 'Consolas', 'monospace']
            : null,
      );

  static TextStyle get title => TextStyle(
    color: text,
    fontSize: 20,
    fontWeight: FontWeight.w700,
    letterSpacing: 0.4,
  );
  static TextStyle get subtitle => TextStyle(color: muted, fontSize: 13, height: 1.45);
  static TextStyle get body => TextStyle(color: text, fontSize: 14, height: 1.5);
  static TextStyle get caption => TextStyle(color: muted, fontSize: 12, height: 1.4);

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
    final k = Skin.current;
    final scheme = ColorScheme(
      brightness: k.light ? Brightness.light : Brightness.dark,
      primary: cyan,
      onPrimary: k.onAccent,
      secondary: cyanDeep,
      onSecondary: k.onAccent,
      tertiary: amber,
      error: danger,
      onError: Colors.white,
      surface: panel,
      onSurface: text,
    );
    return ThemeData(
      useMaterial3: true,
      brightness: Skin.current.light ? Brightness.light : Brightness.dark,
      scaffoldBackgroundColor: bg,
      colorScheme: scheme,
      splashFactory: InkSparkle.splashFactory,
      appBarTheme: AppBarTheme(
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
        titleTextStyle: TextStyle(
            color: text, fontSize: 16, fontWeight: FontWeight.w700),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: panel.withValues(alpha: 0.8),
        hintStyle: TextStyle(color: muted, fontSize: 14),
        labelStyle: TextStyle(color: muted, fontSize: 13),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(rPanel),
          borderSide: BorderSide(color: lineCyan.withValues(alpha: 0.5)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.all(Radius.circular(rPanel)),
          borderSide: BorderSide(color: cyan, width: 1.2),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: cyan,
          foregroundColor: Skin.current.onAccent,
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
        labelStyle: TextStyle(color: text, fontSize: 13),
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
        valueIndicatorTextStyle: TextStyle(color: text, fontSize: 12),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: cyan,
        linearTrackColor: line,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: panelHi,
        contentTextStyle: TextStyle(color: text, fontSize: 13),
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
