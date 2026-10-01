import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../core/theme.dart';

/// Shared cinematic HUD chrome: background, glass panel with bracket
/// corners, section labels, staggered entrance, state callouts.
/// Presentation only — no controllers or networking in here.
class HudBackground extends StatelessWidget {
  final Widget child;
  const HudBackground({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0xFF071120), JarvisTheme.bg, Color(0xFF030509)],
          stops: [0.0, 0.45, 1.0],
        ),
      ),
      child: Stack(children: [
        const Positioned.fill(child: _GridGlow()),
        Positioned.fill(
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: RadialGradient(
                center: const Alignment(0, -0.55),
                radius: 1.1,
                colors: [
                  JarvisTheme.cyan.withValues(alpha: 0.10),
                  Colors.transparent,
                ],
                stops: const [0.0, 0.55],
              ),
            ),
          ),
        ),
        child,
      ]),
    );
  }
}

class _GridGlow extends StatelessWidget {
  const _GridGlow();

  @override
  Widget build(BuildContext context) {
    return CustomPaint(painter: _GridPainter());
  }
}

class _GridPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()
      ..color = JarvisTheme.cyan.withValues(alpha: 0.05)
      ..strokeWidth = 1;
    const step = 44.0;
    for (var x = 0.0; x < size.width; x += step) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), p);
    }
    for (var y = 0.0; y < size.height; y += step) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), p);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter old) => false;
}

/// Glassy panel with hairline cyan border + Iron-Man bracket corners.
class HudPanel extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final bool glow;
  final VoidCallback? onTap;

  const HudPanel({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(14),
    this.glow = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final body = CustomPaint(
      painter: _BracketPainter(
        color: JarvisTheme.cyan.withValues(alpha: glow ? 0.9 : 0.45),
      ),
      child: Container(
        decoration: JarvisTheme.panelDeco(glow: glow),
        padding: padding,
        child: child,
      ),
    );
    if (onTap == null) return body;
    return InkWell(
      borderRadius: BorderRadius.circular(JarvisTheme.rPanel),
      onTap: onTap,
      child: body,
    );
  }
}

class _BracketPainter extends CustomPainter {
  final Color color;
  _BracketPainter({required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    const l = 14.0, w = 2.0;
    final p = Paint()
      ..color = color
      ..strokeWidth = w
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;
    void corner(Offset o, double dx, double dy) {
      canvas.drawLine(o, o + Offset(dx * l, 0), p);
      canvas.drawLine(o, o + Offset(0, dy * l), p);
    }

    corner(const Offset(1, 1), 1, 1);
    corner(Offset(size.width - 1, 1), -1, 1);
    corner(Offset(1, size.height - 1), 1, -1);
    corner(Offset(size.width - 1, size.height - 1), -1, -1);
  }

  @override
  bool shouldRepaint(covariant _BracketPainter old) => old.color != color;
}

/// Monospaced uppercase section header with rule line.
class SectionLabel extends StatelessWidget {
  final String text;
  final Color accent;
  const SectionLabel(this.text, {super.key, this.accent = JarvisTheme.cyan});

  @override
  Widget build(BuildContext context) {
    return Row(children: [
      Container(width: 3, height: 12, color: accent),
      const SizedBox(width: 8),
      Text(text.toUpperCase(), style: JarvisTheme.label(accent)),
      const SizedBox(width: 10),
      Expanded(
        child: Container(
          height: 1,
          decoration: BoxDecoration(
            gradient: LinearGradient(colors: [
              accent.withValues(alpha: 0.5),
              Colors.transparent,
            ]),
          ),
        ),
      ),
    ]);
  }
}

/// Small mono telemetry readout, e.g. LINK · ONLINE.
class StatusPill extends StatelessWidget {
  final String text;
  final bool good;
  final bool warn;
  const StatusPill(this.text,
      {super.key, this.good = true, this.warn = false});

  @override
  Widget build(BuildContext context) {
    final c = warn
        ? JarvisTheme.amber
        : good
            ? JarvisTheme.cyan
            : JarvisTheme.danger;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(
        color: c.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(JarvisTheme.rPill),
        border: Border.all(color: c.withValues(alpha: 0.55)),
        boxShadow: [
          BoxShadow(color: c.withValues(alpha: 0.18), blurRadius: 12),
        ],
      ),
      child: Text(text.toUpperCase(),
          style: JarvisTheme.label(c, 10)),
    );
  }
}

/// Staggered fade/slide entrance. Plays once per mount.
///
/// Timer-free on purpose: the stagger offset is an [Interval] on a single
/// controller, so widget tests never see a pending [Future.delayed].
class Stagger extends StatefulWidget {
  final int index;
  final Widget child;
  const Stagger({super.key, this.index = 0, required this.child});

  @override
  State<Stagger> createState() => _StaggerState();
}

class _StaggerState extends State<Stagger>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c;
  late final Animation<double> _opacity;
  late final Animation<Offset> _slide;

  @override
  void initState() {
    super.initState();
    final delay = Duration(
        milliseconds: 60 * widget.index.clamp(0, 8));
    const run = Duration(milliseconds: 380);
    final total = delay + run;
    _c = AnimationController(vsync: this, duration: total);
    final start = delay.inMilliseconds / total.inMilliseconds;
    final curved = CurvedAnimation(
      parent: _c,
      curve: Interval(start, 1.0, curve: Curves.easeOutCubic),
    );
    _opacity = Tween(begin: 0.0, end: 1.0).animate(curved);
    _slide =
        Tween(begin: const Offset(0, 0.05), end: Offset.zero)
            .animate(curved);
    _c.forward();
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: _opacity,
      child: SlideTransition(position: _slide, child: widget.child),
    );
  }
}

/// Premium empty / loading / error states with mono labels.
class HudEmpty extends StatelessWidget {
  final IconData icon;
  final String title;
  final String hint;
  const HudEmpty(
      {super.key, required this.icon, required this.title, required this.hint});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 20),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Container(
          width: 56,
          height: 56,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            border: Border.all(
                color: JarvisTheme.cyan.withValues(alpha: 0.4)),
            boxShadow: [
              BoxShadow(
                  color:
                      JarvisTheme.cyan.withValues(alpha: 0.15),
                  blurRadius: 18),
            ],
          ),
          child: Icon(icon, color: JarvisTheme.cyanDim, size: 26),
        ),
        const SizedBox(height: 12),
        Text(title,
            style: JarvisTheme.label(JarvisTheme.text, 12),
            textAlign: TextAlign.center),
        const SizedBox(height: 6),
        Text(hint,
            style: JarvisTheme.caption, textAlign: TextAlign.center),
      ]),
    );
  }
}

class HudLoading extends StatefulWidget {
  final String label;
  const HudLoading({super.key, this.label = 'SYNCING'});

  @override
  State<HudLoading> createState() => _HudLoadingState();
}

class _HudLoadingState extends State<HudLoading>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c;
  @override
  void initState() {
    super.initState();
    _c = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 900))
      ..repeat();
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Row(mainAxisSize: MainAxisSize.min, children: [
      SizedBox(
        width: 16,
        height: 16,
        child: CircularProgressIndicator(
          strokeWidth: 2,
          valueColor: AlwaysStoppedAnimation<Color>(JarvisTheme.cyan),
          backgroundColor: JarvisTheme.line,
        ),
      ),
      const SizedBox(width: 10),
      AnimatedBuilder(
        animation: _c,
        builder: (_, _) {
          final dots = '.' * ((_c.value * 3).floor() + 1);
          return Text('${widget.label}$dots',
              style: JarvisTheme.label(JarvisTheme.cyan, 11));
        },
      ),
    ]);
  }
}

class HudError extends StatelessWidget {
  final String message;
  final VoidCallback? onRetry;
  const HudError({super.key, required this.message, this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: JarvisTheme.amber.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(JarvisTheme.rPanel),
        border:
            Border.all(color: JarvisTheme.amber.withValues(alpha: 0.5)),
      ),
      child: Row(children: [
        const Icon(Icons.warning_amber_rounded,
            color: JarvisTheme.amber, size: 20),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('ATTENTION',
                    style: JarvisTheme.label(JarvisTheme.amber, 10)),
                const SizedBox(height: 2),
                Text(message,
                    style:
                        const TextStyle(color: JarvisTheme.text, fontSize: 13)),
              ]),
        ),
        if (onRetry != null)
          TextButton(onPressed: onRetry, child: const Text('RETRY')),
      ]),
    );
  }
}

/// Shimmering divider line under headers.
class HudScanline extends StatefulWidget {
  const HudScanline({super.key});
  @override
  State<HudScanline> createState() => _HudScanlineState();
}

class _HudScanlineState extends State<HudScanline>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c;
  @override
  void initState() {
    super.initState();
    _c = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 2600))
      ..repeat();
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _c,
      builder: (_, _) => CustomPaint(
        size: const Size(double.infinity, 2),
        painter: _ScanPainter(_c.value),
      ),
    );
  }
}

class _ScanPainter extends CustomPainter {
  final double t;
  _ScanPainter(this.t);
  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()..strokeWidth = 1;
    p.color = JarvisTheme.lineCyan.withValues(alpha: 0.5);
    canvas.drawLine(Offset(0, 1), Offset(size.width, 1), p);
    final x = size.width * t;
    p
      ..color = JarvisTheme.cyan.withValues(alpha: 0.8)
      ..strokeWidth = 2
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 4);
    canvas.drawLine(Offset(x - 24, 1), Offset(x + 24, 1), p);
  }

  @override
  bool shouldRepaint(covariant _ScanPainter old) => old.t != t;
}

/// Pulsing status dot (online cyan / offline red / warn amber).
class PulseDot extends StatefulWidget {
  final bool online;
  final bool warn;
  const PulseDot({super.key, required this.online, this.warn = false});

  @override
  State<PulseDot> createState() => _PulseDotState();
}

class _PulseDotState extends State<PulseDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c;
  @override
  void initState() {
    super.initState();
    _c = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 1400))
      ..repeat(reverse: true);
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color = widget.warn
        ? JarvisTheme.amber
        : widget.online
            ? JarvisTheme.cyan
            : JarvisTheme.danger;
    return FadeTransition(
      opacity: Tween(begin: 0.45, end: 1.0).animate(
          CurvedAnimation(parent: _c, curve: Curves.easeInOut)),
      child: Container(
        width: 10,
        height: 10,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: color,
          boxShadow: [
            BoxShadow(color: color.withValues(alpha: 0.6), blurRadius: 8)
          ],
        ),
      ),
    );
  }
}

/// Tiny helper for degree-free arc math visuals.
double deg(double d) => d * math.pi / 180;
