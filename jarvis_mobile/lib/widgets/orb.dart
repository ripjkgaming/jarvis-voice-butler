import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import '../core/theme.dart';

/// Arc-reactor hero orb: concentric rotating segmented rings, tick marks,
/// sweeping radar line and a glow core that pulses with [energy] (0..1).
///
/// Same public API as before — `energy` idle cyan … speaking bright — so
/// Home/Voice keep driving it from link + call state with no logic change.
class JarvisOrb extends StatefulWidget {
  final double energy;
  final double size;

  const JarvisOrb({super.key, this.energy = 0.35, this.size = 220});

  @override
  State<JarvisOrb> createState() => _JarvisOrbState();
}

class _JarvisOrbState extends State<JarvisOrb>
    with SingleTickerProviderStateMixin {
  late final Ticker _ticker;
  double _t = 0;
  double _smooth = 0.35;

  @override
  void initState() {
    super.initState();
    _smooth = widget.energy;
    _ticker = createTicker((elapsed) {
      setState(() {
        _t = elapsed.inMicroseconds / 1e6;
        // Ease displayed energy toward target: no jank on state flips.
        _smooth += (widget.energy - _smooth) * 0.06;
      });
    });
    _ticker.start();
  }

  @override
  void dispose() {
    _ticker.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      size: Size.square(widget.size),
      painter: _ReactorPainter(t: _t, energy: _smooth.clamp(0.0, 1.0)),
    );
  }
}

class _ReactorPainter extends CustomPainter {
  final double t, energy;

  _ReactorPainter({required this.t, required this.energy});

  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2, cy = size.height / 2;
    final c = Offset(cx, cy);
    final base = math.min(size.width, size.height) / 2;
    final pulse = 1 + 0.045 * math.sin(t * 2.2) * (0.4 + energy);
    final p = Paint()
      ..isAntiAlias = true
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    final coreColor =
        Color.lerp(JarvisTheme.cyan, JarvisTheme.cyanBright, energy)!;
    final ringColor =
        Color.lerp(JarvisTheme.cyanDeep, JarvisTheme.cyan, 0.35 + energy * 0.65)!;

    // ── Outer halo washes (fill) ──
    final wash = Paint()..isAntiAlias = true;
    for (var i = 4; i >= 1; i--) {
      wash.color = JarvisTheme.cyan
          .withValues(alpha: (0.05 + energy * 0.05) * (5 - i) / 2);
      canvas.drawCircle(c, base * 0.98 * i / 4 * pulse, wash);
    }

    // ── Tick ring (60 ticks, every 5th long) ──
    final tickR = base * 0.96;
    for (var i = 0; i < 60; i++) {
      final a = i * math.pi * 2 / 60;
      final long = i % 5 == 0;
      final r1 = tickR - (long ? 10 : 5);
      final lit = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * 1.5 + i * 0.7));
      p
        ..color = ringColor.withValues(
            alpha: (long ? 0.75 : 0.4) * (0.35 + energy * 0.65) * lit + 0.12)
        ..strokeWidth = long ? 2 : 1;
      canvas.drawLine(
        c + Offset(math.cos(a), math.sin(a)) * r1,
        c + Offset(math.cos(a), math.sin(a)) * tickR,
        p,
      );
    }

    // ── Segmented rotating rings ──
    void ring(double r, double width, double speed, List<double> gaps,
        Color color, double alpha) {
      var a0 = t * speed;
      for (var k = 0; k < gaps.length; k++) {
        final sweep = (math.pi * 2 / gaps.length) - gaps[k];
        p
          ..color = color.withValues(alpha: alpha)
          ..strokeWidth = width;
        canvas.drawArc(Rect.fromCircle(center: c, radius: r * pulse), a0,
            sweep, false, p);
        a0 += math.pi * 2 / gaps.length;
      }
    }

    ring(base * 0.82, 3.5, 0.55, const [0.5, 0.9, 0.5], ringColor, 0.9);
    ring(base * 0.70, 2, -0.9, const [0.35, 0.35, 1.1, 0.35], coreColor, 0.75);
    ring(base * 0.58, 6, 0.35, const [1.4, 1.4, 1.4], ringColor, 0.28);

    // ── Radar sweep ──
    final sweepA = t * 1.4;
    final grad = SweepGradient(
      startAngle: sweepA,
      endAngle: sweepA + 1.2,
      colors: [coreColor.withValues(alpha: 0.0), coreColor.withValues(alpha: 0.35)],
    );
    final sweepPaint = Paint()
      ..isAntiAlias = true
      ..shader = grad.createShader(Rect.fromCircle(center: c, radius: base * 0.56 * pulse));
    canvas.drawCircle(c, base * 0.56 * pulse, sweepPaint);
    p
      ..color = coreColor.withValues(alpha: 0.5)
      ..strokeWidth = 1.5;
    canvas.drawLine(
        c, c + Offset(math.cos(sweepA), math.sin(sweepA)) * base * 0.56 * pulse, p);

    // ── Inner coil dots (10, counter-rotating) ──
    for (var i = 0; i < 10; i++) {
      final a = -t * 0.8 + i * math.pi * 2 / 10;
      final rr = base * 0.42 * pulse;
      final dot = c + Offset(math.cos(a), math.sin(a)) * rr;
      wash.color = coreColor.withValues(alpha: 0.55 + energy * 0.4);
      canvas.drawCircle(dot, 3 + energy * 1.6, wash);
    }

    // ── Core glow + hot center ──
    wash
      ..color = JarvisTheme.cyan.withValues(alpha: 0.16 + energy * 0.22)
      ..style = PaintingStyle.fill;
    canvas.drawCircle(c, base * 0.30 * pulse, wash);
    wash.color = coreColor.withValues(alpha: 0.85);
    canvas.drawCircle(c, base * (0.13 + energy * 0.05) * pulse, wash);
    wash.color = Colors.white.withValues(alpha: 0.92);
    canvas.drawCircle(c, base * 0.055 * pulse, wash);
  }

  @override
  bool shouldRepaint(_ReactorPainter old) => true;
}
