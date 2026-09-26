import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

/// Jarvis orb: ~120 particles on slow decaying orbits around a glowing
/// core, alpha by radius, gentle pulse. Direct port of the native
/// OrbView recipe (phone/.../OrbView.kt) onto a vsync CustomPainter.
/// `energy` 0..1 (idle cyan … speaking bright) is driven by call state.
class JarvisOrb extends StatefulWidget {
  final double energy;
  final double size;

  const JarvisOrb({super.key, this.energy = 0.35, this.size = 220});

  @override
  State<JarvisOrb> createState() => _JarvisOrbState();
}

class _Particle {
  double angle, radius, speed, size, tw;
  _Particle(this.angle, this.radius, this.speed, this.size, this.tw);
}

class _JarvisOrbState extends State<JarvisOrb>
    with SingleTickerProviderStateMixin {
  late final Ticker _ticker;
  late final List<_Particle> _parts;
  double _t = 0;

  @override
  void initState() {
    super.initState();
    final rnd = math.Random(1237);
    _parts = List.generate(
        120,
        (_) => _Particle(
          rnd.nextDouble() * math.pi * 2,
          rnd.nextDouble(),
          rnd.nextDouble() * 0.35 + 0.05,
          rnd.nextDouble() * 4 + 1.5,
          rnd.nextDouble() * 6.28,
        ));
    _ticker = createTicker((elapsed) {
      setState(() => _t = elapsed.inMicroseconds / 1e6);
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
      painter: _OrbPainter(t: _t, energy: widget.energy, parts: _parts),
    );
  }
}

class _OrbPainter extends CustomPainter {
  final double t, energy;
  final List<_Particle> parts;

  _OrbPainter({required this.t, required this.energy, required this.parts});

  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2, cy = size.height / 2;
    final base = math.min(size.width, size.height) * 0.32;
    final pulse = 1 + 0.05 * math.sin(t * 2) * (0.5 + energy);
    final paint = Paint()..isAntiAlias = true;

    for (var i = 4; i >= 1; i--) {
      paint.color = const Color(0xFF1FD5F9).withAlpha((14 * (5 - i)).clamp(0, 60));
      canvas.drawCircle(Offset(cx, cy), base * i * 0.4 * pulse, paint);
    }
    paint.color = const Color(0xFFD9F7FF).withAlpha(200);
    canvas.drawCircle(Offset(cx, cy), base * 0.16 * pulse, paint);

    for (final p in parts) {
      p.angle += p.speed * 0.016 * (0.6 + energy);
      final rr = base * (0.25 + 0.70 * p.radius) * pulse;
      final x = cx + math.cos(p.angle) * rr;
      final y = cy + math.sin(p.angle) * rr * 0.92;
      final alpha =
          (140 * (1 - p.radius * 0.6) * (0.6 + 0.4 * math.sin(p.tw + t)))
              .clamp(0, 255)
              .toInt();
      paint.color = (p.radius > 0.6
              ? const Color(0xFF1FD5F9)
              : const Color(0xFF8FE9FC))
          .withAlpha(alpha);
      canvas.drawCircle(Offset(x, y), p.size, paint);
    }
  }

  @override
  bool shouldRepaint(_OrbPainter old) => true;
}
