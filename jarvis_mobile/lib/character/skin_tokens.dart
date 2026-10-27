import 'package:flutter/material.dart';

/// Design tokens for one character skin. A skin is a token set, never a
/// forked widget: [JarvisTheme] reads [Skin.current], so every themed widget
/// follows it. Jarvis wears the icy-blue palette.
class SkinTokens {
  final Color bg, bg2, panel, panelHi, line, lineCyan;
  final Color cyan, cyanBright, cyanDeep, cyanDim;
  final Color text, muted, textFaint, amber, danger, ok;
  final Color bubbleUser, bubbleChar, onAccent;
  final double glow, scan, edge, radius, cut;
  final bool mono, light;

  const SkinTokens({
    required this.bg,
    required this.bg2,
    required this.panel,
    required this.panelHi,
    required this.line,
    required this.lineCyan,
    required this.cyan,
    required this.cyanBright,
    required this.cyanDeep,
    required this.cyanDim,
    required this.text,
    required this.muted,
    required this.textFaint,
    required this.amber,
    required this.danger,
    required this.ok,
    required this.bubbleUser,
    required this.bubbleChar,
    required this.onAccent,
    this.glow = 1,
    this.scan = 0,
    this.edge = 0,
    this.radius = 10,
    this.cut = 0,
    this.mono = true,
    this.light = false,
  });

  /// The J.A.R.V.I.S. look, icy-blue refresh. Must match characters/jarvis.yaml.
  static const jarvis = SkinTokens(
    bg: Color(0xFF04070D),
    bg2: Color(0xFF070D16),
    panel: Color(0xFF0B1420),
    panelHi: Color(0xFF101C2C),
    line: Color(0xFF203543),
    lineCyan: Color(0xFF35607A),
    cyan: Color(0xFFA8EBFF),
    cyanBright: Color(0xFFF4FDFF),
    cyanDeep: Color(0xFF3F9DB8),
    cyanDim: Color(0xFF5DB8D6),
    text: Color(0xFFEAFAFF),
    muted: Color(0xFF93B8CA),
    textFaint: Color(0xFF54788C),
    amber: Color(0xFFFFB648),
    danger: Color(0xFFFF5D5D),
    ok: Color(0xFF5DF2B8),
    bubbleUser: Color(0xFF101C2C),
    bubbleChar: Color(0xFF0B1420),
    onAccent: Color(0xFF031018),
  );

  static Color parseColor(Object? v, [Color fallback = const Color(0xFF000000)]) {
    if (v is! String) return fallback;
    final s = v.trim();
    if (s.startsWith('#')) {
      var h = s.substring(1);
      if (h.length == 3) h = h.split('').map((c) => '$c$c').join();
      final n = int.tryParse(h, radix: 16);
      if (n == null || h.length != 6) return fallback;
      return Color(0xFF000000 | n);
    }
    final m = RegExp(r'^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$')
        .firstMatch(s);
    if (m == null) return fallback;
    final a = m.group(4) == null ? 1.0 : double.parse(m.group(4)!);
    return Color.fromRGBO(double.parse(m.group(1)!).round(),
        double.parse(m.group(2)!).round(), double.parse(m.group(3)!).round(), a);
  }

  /// Maps the shared yaml token roles (characters/*.yaml theme.tokens) onto
  /// the mobile palette roles.
  factory SkinTokens.fromTokens(Map<String, dynamic> t) {
    Color c(String k) => parseColor(t[k], jarvis.cyan);
    double d(String k, double f) => (t[k] is num) ? (t[k] as num).toDouble() : f;
    final accent = c('accent');
    return SkinTokens(
      bg: c('void'),
      bg2: c('ink'),
      panel: c('deep'),
      panelHi: c('raise'),
      line: c('line'),
      lineCyan: c('line_strong'),
      cyan: accent,
      cyanBright: c('accent_hot'),
      cyanDeep: c('accent_dim'),
      cyanDim: c('accent_dim'),
      text: c('text'),
      muted: c('text_dim'),
      textFaint: c('text_faint'),
      amber: c('warn'),
      danger: c('danger'),
      ok: c('ok'),
      bubbleUser: c('bubble_user'),
      bubbleChar: c('bubble_char'),
      onAccent: accent.computeLuminance() > 0.3
          ? const Color(0xFFFFFFFF)
          : const Color(0xFF031018),
      glow: d('glow', 1),
      scan: d('scan', 0),
      edge: d('edge_refract', 0),
      radius: d('radius_panel', 10),
      cut: d('cut_angle', 0),
      mono: t['font_label'] != 'sans',
      light: c('void').computeLuminance() > 0.5,
    );
  }

  static SkinTokens lerp(SkinTokens a, SkinTokens b, double t) {
    Color l(Color x, Color y) => Color.lerp(x, y, t)!;
    double n(double x, double y) => x + (y - x) * t;
    return SkinTokens(
      bg: l(a.bg, b.bg),
      bg2: l(a.bg2, b.bg2),
      panel: l(a.panel, b.panel),
      panelHi: l(a.panelHi, b.panelHi),
      line: l(a.line, b.line),
      lineCyan: l(a.lineCyan, b.lineCyan),
      cyan: l(a.cyan, b.cyan),
      cyanBright: l(a.cyanBright, b.cyanBright),
      cyanDeep: l(a.cyanDeep, b.cyanDeep),
      cyanDim: l(a.cyanDim, b.cyanDim),
      text: l(a.text, b.text),
      muted: l(a.muted, b.muted),
      textFaint: l(a.textFaint, b.textFaint),
      amber: l(a.amber, b.amber),
      danger: l(a.danger, b.danger),
      ok: l(a.ok, b.ok),
      bubbleUser: l(a.bubbleUser, b.bubbleUser),
      bubbleChar: l(a.bubbleChar, b.bubbleChar),
      onAccent: l(a.onAccent, b.onAccent),
      glow: n(a.glow, b.glow),
      scan: n(a.scan, b.scan),
      edge: n(a.edge, b.edge),
      radius: n(a.radius, b.radius),
      cut: n(a.cut, b.cut),
      mono: t < 0.5 ? a.mono : b.mono,
      light: t < 0.5 ? a.light : b.light,
    );
  }
}

/// The live skin. [JarvisTheme] getters read [current]; the transition
/// engine writes it (at most once per frame) and the app root rebuilds from
/// [changes].
class Skin {
  Skin._();
  static SkinTokens current = SkinTokens.jarvis;
  static final ValueNotifier<int> changes = ValueNotifier<int>(0);

  static void set(SkinTokens t) {
    current = t;
    changes.value++;
  }

  static void reset() => set(SkinTokens.jarvis);
}
