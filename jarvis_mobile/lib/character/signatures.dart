import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/painting.dart';

import 'skin_tokens.dart';

/// One-time signature moments, drawn on a transient overlay canvas
/// (transform/opacity/colour only, no layout). Ported 1:1 from the desktop
/// engine (frontend/lib/character/signatures.ts): same keyframes, same caps.

const _tau = math.pi * 2;

double clamp01(double t) => t < 0 ? 0 : (t > 1 ? 1 : t);
double smooth(double a, double b, double t) {
  final u = clamp01((t - a) / (b - a == 0 ? 1 : b - a));
  return u * u * (3 - 2 * u);
}

double easeOut(double t) => 1 - math.pow(1 - clamp01(t), 3).toDouble();
double snap(double t) {
  final x = clamp01(t);
  return x * x * x * (x * (6 * x - 15) + 10);
}

double bell(double u, double c, double w) =>
    math.exp(-math.pow((u - c) / w, 2).toDouble());

/// Deterministic PRNG (matches mulberry32 on the desktop for the same seed).
class Rng {
  int _a;
  Rng(int seed) : _a = seed & 0xFFFFFFFF;
  double next() {
    _a = (_a + 0x6D2B79F5) & 0xFFFFFFFF;
    var t = _a;
    t = _imul(t ^ (t >> 15), t | 1);
    t ^= (t + _imul(t ^ (t >> 7), t | 61)) & 0xFFFFFFFF;
    return (((t ^ (t >> 14)) & 0xFFFFFFFF)) / 4294967296;
  }

  static int _imul(int a, int b) => (a * b) & 0xFFFFFFFF;
}

class FxCtx {
  final Size size;
  final Offset anchor;
  final double anchorR;
  final List<Rect> panels;
  final SkinTokens colors; // target skin
  final double intensity;
  final double maxShake;
  final double maxGlitch;
  final Rng rng;
  final double Function(double amp) flash;
  double sec = 0;
  final Map<String, Object> scratch = {};
  FxCtx({
    required this.size,
    required this.anchor,
    required this.anchorR,
    required this.panels,
    required this.colors,
    required this.intensity,
    required this.maxShake,
    required this.maxGlitch,
    required this.rng,
    required this.flash,
  });
}

abstract class Signature {
  void draw(Canvas g, double u, FxCtx c);

  /// Spatial reveal for a rect; null = staggered timeline.
  double? reveal(Rect r, double u, FxCtx c) => null;
  Offset shake(double u, FxCtx c) => Offset.zero;
}

Paint _fill(Color c, [double a = 1]) =>
    Paint()..color = c.withValues(alpha: (c.a * a).clamp(0.0, 1.0));

Paint _stroke(Color c, double w, [double a = 1]) => Paint()
  ..color = c.withValues(alpha: (c.a * a).clamp(0.0, 1.0))
  ..style = PaintingStyle.stroke
  ..strokeWidth = w;

void _glow(Canvas g, Offset o, double r, Color c, double a) {
  if (a <= 0.003 || r <= 1) return;
  final shader = ui.Gradient.radial(o, r, [
    c.withValues(alpha: a),
    c.withValues(alpha: a * 0.35),
    c.withValues(alpha: 0),
  ], [0, 0.45, 1]);
  g.drawCircle(
      o,
      r,
      Paint()
        ..shader = shader
        ..blendMode = BlendMode.plus);
}

/* ------------------------------------------------------------ JARVIS */

class _P {
  final double sx, sy, tx, ty, d, z;
  _P(this.sx, this.sy, this.tx, this.ty, this.d, this.z);
}

class HoloReassembly extends Signature {
  List<_P> _parts(FxCtx c) {
    final cached = c.scratch['p'];
    if (cached != null) return cached as List<_P>;
    final n = math.min(160, 50 + c.panels.length * 18); // lighter on phones
    final list = <_P>[];
    for (var i = 0; i < n; i++) {
      final p = c.panels.isEmpty
          ? Offset.zero & c.size
          : c.panels[i % c.panels.length];
      final tx = p.left + c.rng.next() * p.width;
      final ty = p.top + c.rng.next() * p.height;
      final a = c.rng.next() * _tau;
      final d = 60 + c.rng.next() * 180;
      list.add(_P(tx + math.cos(a) * d, ty + math.sin(a) * d, tx, ty,
          c.rng.next() * 0.25, 1.4 + c.rng.next() * 1.4));
    }
    c.scratch['p'] = list;
    return list;
  }

  @override
  void draw(Canvas g, double u, FxCtx c) {
    final a = c.anchor;
    final r0 = c.anchorR;
    final acc = c.colors.cyan;
    final hot = c.colors.cyanBright;
    // arc-reactor rings spin up
    for (var i = 0; i < 3; i++) {
      final spin = easeOut(u / 0.6);
      final r = r0 * (0.5 + 0.3 * i) * (0.55 + 0.45 * easeOut(u / 0.45));
      final al = smooth(0, 0.12, u) * (1 - 0.65 * smooth(0.78, 1, u));
      final paint = _stroke(acc, 2.2 - i * 0.5, al * (0.9 - i * 0.18));
      final rot = spin * _tau * (1.3 + i * 0.45) * (i.isOdd ? -1 : 1);
      final segs = 10 + i * 4;
      for (var k = 0; k < segs; k++) {
        final s0 = rot + (k / segs) * _tau;
        g.drawArc(Rect.fromCircle(center: a, radius: r), s0,
            (_tau / segs) * 0.62, false, paint);
      }
    }
    // tick ring
    const ticks = 72;
    final lit = (clamp01(u * 1.6) * ticks).floor();
    final tp = _stroke(hot, 1.4, 0.7 * (1 - smooth(0.8, 1, u)));
    for (var k = 0; k < lit; k++) {
      final th = (k / ticks) * _tau;
      final ra = r0 * 1.12;
      final rb = ra + (k % 6 == 0 ? 10 : 5);
      g.drawLine(a + Offset(math.cos(th) * ra, math.sin(th) * ra),
          a + Offset(math.cos(th) * rb, math.sin(th) * rb), tp);
    }
    // HUD lines draw in around each panel
    for (var i = 0; i < c.panels.length; i++) {
      final p = c.panels[i];
      final prog = smooth(0.1 + i * 0.03, 0.55 + i * 0.03, u);
      if (prog <= 0) continue;
      final path = Path()..addRect(p.deflate(0.5));
      final metric = path.computeMetrics().first;
      g.drawPath(metric.extractPath(0, metric.length * prog),
          _stroke(acc, 1.2, 0.8 * (1 - smooth(0.85, 1, u))));
    }
    // panels resolve out of light particles
    final pp = _fill(hot);
    for (final p in _parts(c)) {
      final t = easeOut(smooth(0.22 + p.d, 0.82, u));
      final x = p.sx + (p.tx - p.sx) * t;
      final y = p.sy + (p.ty - p.sy) * t;
      final al = math.sin(clamp01(t) * math.pi) * 0.85 +
          0.1 * (1 - smooth(0.9, 1, u)) * t;
      if (al < 0.02) continue;
      pp.color = hot.withValues(alpha: al.clamp(0.0, 1.0));
      g.drawRect(Rect.fromLTWH(x, y, p.z, p.z), pp);
    }
    // soft blue-cyan bloom
    _glow(g, a, r0 * 2.6, acc, 0.34 * bell(u, 0.86, 0.22));
  }
}

/* ------------------------------------------------------------ FRIDAY */

class _M {
  final double x0, y0, vx, vy, tx, ty, z;
  _M(this.x0, this.y0, this.vx, this.vy, this.tx, this.ty, this.z);
}

double _easeGlide(double t) => 1 - math.pow(1 - clamp01(t), 2.4).toDouble();

class LuminousSweep extends Signature {
  double _waveX(double u, FxCtx c) =>
      -c.size.width * 0.18 +
      c.size.width * 1.36 * _easeGlide(smooth(0, 0.82, u));

  List<_M> _motes(FxCtx c) {
    final cached = c.scratch['m'];
    if (cached != null) return cached as List<_M>;
    final out = <_M>[];
    for (var i = 0; i < 90; i++) {
      final p = c.panels.isEmpty
          ? Offset.zero & c.size
          : c.panels[i % c.panels.length];
      final edge = c.rng.next();
      final tx = p.left + c.rng.next() * p.width;
      final ty = edge < 0.5
          ? p.top + (c.rng.next() < 0.5 ? 0 : p.height)
          : p.top + c.rng.next() * p.height;
      out.add(_M(tx - 50 - c.rng.next() * 120, ty + (c.rng.next() - 0.5) * 160,
          30 + c.rng.next() * 70, -20 - c.rng.next() * 50, tx, ty,
          1.4 + c.rng.next() * 1.6));
    }
    c.scratch['m'] = out;
    return out;
  }

  @override
  double? reveal(Rect r, double u, FxCtx c) {
    final band = c.size.width * 0.18;
    return clamp01((_waveX(u, c) - r.left - r.width * 0.1) / band);
  }

  @override
  void draw(Canvas g, double u, FxCtx c) {
    final w = c.size.width, h = c.size.height;
    final band = w * 0.18;
    final x = _waveX(u, c);
    final warm = c.colors.cyanBright;
    final cool = c.colors.cyan;
    // warm-to-cool gradient bloom
    final bloom = 0.17 * bell(u, 0.62, 0.34);
    g.drawRect(
        Offset.zero & c.size,
        Paint()
          ..shader = ui.Gradient.linear(Offset.zero, Offset(w, h * 0.4), [
            warm.withValues(alpha: bloom),
            cool.withValues(alpha: bloom),
          ]));
    // wavefront
    g.drawRect(
        Rect.fromLTWH(x - band, 0, band * 1.12, h),
        Paint()
          ..shader = ui.Gradient.linear(Offset(x - band, 0), Offset(x + band * 0.12, 0), [
            cool.withValues(alpha: 0),
            cool.withValues(alpha: 0.2),
            const Color(0xFFFFFFFF).withValues(alpha: 0.75),
            warm.withValues(alpha: 0),
          ], [0, 0.55, 0.9, 1]));
    // refractive hairlines
    g.drawLine(Offset(x + 3, 0), Offset(x + 3, h), _stroke(warm, 1.5, 0.8));
    g.drawLine(Offset(x - 3, 0), Offset(x - 3, h), _stroke(cool, 1.5, 0.8));
    // motes drift off the wave and settle on panel edges
    final mp = Paint();
    for (final m in _motes(c)) {
      final born = clamp01((x - m.x0) / band);
      if (born <= 0) continue;
      final t = easeOut(smooth(0.2, 0.95, u) * born);
      final px = m.x0 + m.vx * c.sec * (1 - t) + (m.tx - m.x0) * t;
      final py = m.y0 + m.vy * c.sec * (1 - t) + (m.ty - m.y0) * t;
      mp.color = (t > 0.8 ? cool : warm).withValues(
          alpha: (math.sin(born.clamp(0.0, 1.0) * math.pi * 0.5) * (1 - 0.7 * t))
              .clamp(0.0, 1.0));
      g.drawRect(Rect.fromLTWH(px, py, m.z, m.z), mp);
    }
    // glass bevel highlights once revealed
    final al = smooth(0.5, 0.85, u) * (1 - smooth(0.9, 1, u));
    final bp = _stroke(const Color(0xFFFFFFFF), 1, 0.8 * al);
    for (final p in c.panels) {
      if (clamp01((x - p.left) / band) <= 0.3) continue;
      g.drawLine(Offset(p.left + 6, p.top + 0.5),
          Offset(p.right - 6, p.top + 0.5), bp);
      g.drawLine(Offset(p.left + 0.5, p.top + 6),
          Offset(p.left + 0.5, p.bottom - 6), bp);
    }
  }
}

/* ------------------------------------------------------------ ULTRON */

class _S {
  final double x, y, len, th, ox, oy, d;
  final bool horiz;
  _S(this.x, this.y, this.len, this.th, this.horiz, this.ox, this.oy, this.d);
}

class ViolentSettle extends Signature {
  List<_S> _shards(FxCtx c) {
    final cached = c.scratch['s'];
    if (cached != null) return cached as List<_S>;
    final list = <_S>[];
    final far = math.max(c.size.width, c.size.height);
    for (final p in c.panels) {
      final n = 4 + (c.rng.next() * 3).floor();
      for (var i = 0; i < n; i++) {
        final horiz = c.rng.next() < 0.55;
        final len = 24 + c.rng.next() * 70;
        final side = c.rng.next() < 0.5;
        final x = horiz
            ? p.left + c.rng.next() * math.max(1, p.width - len)
            : (side ? p.left : p.right - 4);
        final y = horiz
            ? (side ? p.top : p.bottom - 4)
            : p.top + c.rng.next() * math.max(1, p.height - len);
        final a = c.rng.next() * _tau;
        final d = far * (0.35 + c.rng.next() * 0.5);
        list.add(_S(x, y, len, 3 + c.rng.next() * 3, horiz, math.cos(a) * d,
            math.sin(a) * d, c.rng.next() * 0.12));
      }
    }
    c.scratch['s'] = list;
    return list;
  }

  @override
  Offset shake(double u, FxCtx c) {
    if (u < 0.37 || u > 0.55) return Offset.zero;
    final k = (u - 0.37) / 0.18;
    final amp = c.maxShake * c.intensity * math.pow(1 - k, 2);
    return Offset(math.sin(k * 31) * amp, math.cos(k * 27) * amp * 0.7);
  }

  @override
  void draw(Canvas g, double u, FxCtx c) {
    final w = c.size.width, h = c.size.height;
    final a = c.anchor;
    final red = c.colors.cyan;
    final hot = c.colors.cyanBright;
    final dark = c.colors.bg;
    final gl = c.maxGlitch * c.intensity;
    // 1. tear + RGB-split + scan-lines
    if (u < 0.19) {
      final k = 1 - smooth(0, 0.19, u);
      final flash = c.flash(0.16) * bell(u, 0.01, 0.05);
      if (flash > 0) {
        g.drawRect(Offset.zero & c.size, _fill(red, flash));
      }
      final rnd = Rng((u * 40).floor() + 7);
      for (var i = 0; i < 9; i++) {
        final y = rnd.next() * h;
        final bh = 6 + rnd.next() * 40;
        final dx = (rnd.next() - 0.5) * 220 * c.intensity * k;
        g.drawRect(Rect.fromLTWH(0, y, w, bh), _fill(dark, 0.85 * k));
        g.drawRect(Rect.fromLTWH(dx - 7, y, w, bh), _fill(red, gl * k));
        g.drawRect(Rect.fromLTWH(dx + 7, y + 1, w, math.max(1, bh - 2)),
            _fill(const Color(0xFF28C8FF), gl * 0.5 * k));
      }
      final sl = _fill(const Color(0xFF000000), 0.28 * k);
      for (var y = 0.0; y < h; y += 3) {
        g.drawRect(Rect.fromLTWH(0, y, w, 1), sl);
      }
    }
    // 2. red core ignites
    final ig = smooth(0.17, 0.4, u);
    if (ig > 0) {
      final pop = c.flash(0.1) * bell(u, 0.24, 0.04);
      if (pop > 0) g.drawRect(Offset.zero & c.size, _fill(red, pop));
      _glow(g, a, c.anchorR * (0.3 + 1.7 * easeOut(ig)), red,
          0.85 * (1 - 0.5 * smooth(0.6, 1, u)));
      _glow(g, a, c.anchorR * 0.6 * easeOut(ig), hot, 0.9 * bell(u, 0.3, 0.12));
      g.drawCircle(a, c.anchorR * (0.4 + 1.5 * easeOut(ig)),
          _stroke(red, 3, 0.8 * (1 - ig)));
    }
    // 3. angular shards snap into the panel layout
    final fade = 1 - smooth(0.72, 0.95, u);
    final sp = Paint();
    for (final s in _shards(c)) {
      final t = snap(smooth(0.31 + s.d, 0.66 + s.d, u));
      final x = s.x + s.ox * (1 - t);
      final y = s.y + s.oy * (1 - t);
      final al = math.min(1.0, 0.25 + t) * fade;
      if (al < 0.02) continue;
      sp.color = (t > 0.98 ? hot : red).withValues(alpha: al);
      final path = Path();
      if (s.horiz) {
        path
          ..moveTo(x, y)
          ..lineTo(x + s.len, y)
          ..lineTo(x + s.len - s.th * 2, y + s.th)
          ..lineTo(x, y + s.th);
      } else {
        path
          ..moveTo(x, y)
          ..lineTo(x + s.th, y)
          ..lineTo(x + s.th, y + s.len - s.th * 2)
          ..lineTo(x, y + s.len);
      }
      path.close();
      g.drawPath(path, sp);
    }
  }
}

final Map<String, Signature> signatures = {
  'holo_reassembly': HoloReassembly(),
  'luminous_sweep': LuminousSweep(),
  'violent_settle': ViolentSettle(),
};
