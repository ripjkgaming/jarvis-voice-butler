import 'dart:math' as math;
import 'dart:io' show Platform;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import '../character/character_ctrl.dart';
import '../character/models.dart';
import '../character/skin_tokens.dart';
import '../character/transition.dart';

/// Shared particle field. Same code for every character: colour, count, size
/// and drift come from the live [Skin] tokens (which the transition engine
/// lerps), device caps from _motion.yaml. Positions live in one preallocated
/// Float32List; a frame is one `drawRawPoints` call and allocates nothing.
class ParticleField {
  /// Particles per logical pixel at count multiplier 1.
  static const density = 1 / 9000.0;
  static const maxCount = 160;
  static const speed = 14.0; // logical px / s at drift 1

  final Float32List pos = Float32List(maxCount * 2);
  final Float32List _vel = Float32List(maxCount * 2);
  Float32List view = Float32List(0);
  int count = 0;
  double w = 0, h = 0;

  /// Target count for a [size] under skin multiplier [mult] and device [cap].
  static int countFor(Size size, double mult, double cap) {
    final raw = size.width * size.height * density * mult * cap;
    // Bucket by 4 so a cross-fade does not re-slice the view every frame.
    return ((raw / 4).round() * 4).clamp(0, maxCount);
  }

  void resize(Size size, int n) {
    final grew = size.width != w || size.height != h;
    w = size.width;
    h = size.height;
    if (grew) {
      final r = math.Random(7);
      for (var i = 0; i < maxCount; i++) {
        pos[i * 2] = r.nextDouble() * w;
        pos[i * 2 + 1] = r.nextDouble() * h;
        final a = r.nextDouble() * math.pi * 2;
        final v = .4 + r.nextDouble() * .6;
        _vel[i * 2] = math.cos(a) * v;
        _vel[i * 2 + 1] = -.3 - r.nextDouble() * .7; // slow rise
      }
    }
    if (n != count || view.length != n * 2) {
      count = n;
      view = Float32List.sublistView(pos, 0, n * 2);
    }
  }

  void step(double dt, double drift) {
    if (w <= 0) return;
    final k = dt * speed * drift;
    for (var i = 0; i < count; i++) {
      var x = pos[i * 2] + _vel[i * 2] * k;
      var y = pos[i * 2 + 1] + _vel[i * 2 + 1] * k;
      if (x < 0) x += w;
      if (x > w) x -= w;
      if (y < 0) y += h;
      if (y > h) y -= h;
      pos[i * 2] = x;
      pos[i * 2 + 1] = y;
    }
  }
}

class _ParticlePainter extends CustomPainter {
  final _FxState s;
  _ParticlePainter(this.s) : super(repaint: s.tick);

  @override
  void paint(Canvas canvas, Size size) {
    final f = s.field;
    if (f.count == 0) return;
    canvas.drawRawPoints(ui.PointMode.points, f.view, s.pointPaint);
  }

  @override
  bool shouldRepaint(covariant _ParticlePainter old) => false;
}

/// Rolling A/B raster budget: glow on for [frames], off for [frames]; if the
/// median raster time with glow exceeds the median without by more than
/// [budgetMs], the shader layer cuts itself. Pure, testable.
class GlowBudget {
  final double budgetMs;
  final int frames;
  GlowBudget({required this.budgetMs, required this.frames});

  final List<double> _on = [], _off = [];
  bool glowOn = true;
  bool? verdict; // true = keep, false = cut, null = still measuring

  static double _median(List<double> v) {
    final s = [...v]..sort();
    return s[s.length >> 1];
  }

  /// Returns whether the glow layer should draw next frame.
  bool feed(double rasterMs) {
    if (verdict != null) return verdict!;
    (glowOn ? _on : _off).add(rasterMs);
    if (glowOn && _on.length >= frames) glowOn = false;
    if (!glowOn && _off.length >= frames) {
      verdict = _median(_on) - _median(_off) <= budgetMs;
      glowOn = verdict!;
    }
    return glowOn;
  }
}

/// Glow layer: `FragmentProgram` (Impeller) with a one-gradient CustomPainter
/// fallback when the program is unavailable or over budget.
class FxGlow {
  static Future<ui.FragmentProgram?>? _loading;

  static Future<ui.FragmentProgram?> load() => _loading ??= () async {
        try {
          return await ui.FragmentProgram.fromAsset('shaders/glow.frag');
        } catch (_) {
          return null;
        }
      }();

  /// Voice/orb energy 0..1 read by the shader (set by the orb widget).
  static double energy = .35;
}

class _GlowPainter extends CustomPainter {
  final _FxState s;
  _GlowPainter(this.s) : super(repaint: s.tick);

  @override
  void paint(Canvas canvas, Size size) {
    final k = Skin.current;
    final scale = s.bufferScale;
    final a = s.orbCenter();
    final cx = (a?.dx ?? s.size.width / 2) * scale;
    final cy = (a?.dy ?? s.size.height * .35) * scale;
    final r = (a == null ? math.min(s.size.width, s.size.height) * .3 : s.orbR) * scale;
    final shader = s.shader;
    final rect = Offset.zero & size;
    if (shader != null && s.budget.glowOn) {
      shader
        ..setFloat(0, size.width)
        ..setFloat(1, size.height)
        ..setFloat(2, s.time)
        ..setFloat(3, k.glow)
        ..setFloat(4, k.edge)
        ..setFloat(5, FxGlow.energy)
        ..setFloat(6, k.light ? 1 : 0)
        ..setFloat(7, k.cyan.r)
        ..setFloat(8, k.cyan.g)
        ..setFloat(9, k.cyan.b)
        ..setFloat(10, k.cyanBright.r)
        ..setFloat(11, k.cyanBright.g)
        ..setFloat(12, k.cyanBright.b)
        ..setFloat(13, cx)
        ..setFloat(14, cy)
        ..setFloat(15, r);
      canvas.drawRect(rect, s.shaderPaint..shader = shader);
    } else if (s.budget.verdict != false || shader == null) {
      // CustomPainter fallback: one radial gradient at the orb.
      final p = s.fallbackPaint
        ..shader = ui.Gradient.radial(
          Offset(cx, cy),
          r * 3.2,
          [
            k.cyan.withValues(alpha: .35 * k.glow * FxGlow.energy * (k.light ? .5 : 1)),
            k.cyan.withValues(alpha: 0),
          ],
        );
      canvas.drawRect(rect, p);
    }
  }

  @override
  bool shouldRepaint(covariant _GlowPainter old) => false;
}

/// Ambient background fx for every tab: particles + glow, behind content.
/// Pauses on app background, TickerMode off, reduced motion and effects-off.
class SkinFx extends StatefulWidget {
  const SkinFx({super.key});

  @override
  State<SkinFx> createState() => _FxState();
}

class _FxState extends State<SkinFx>
    with SingleTickerProviderStateMixin, WidgetsBindingObserver {
  final ParticleField field = ParticleField();
  final ValueNotifier<int> tick = ValueNotifier<int>(0);
  final Paint pointPaint = Paint()
    ..strokeCap = StrokeCap.round
    ..isAntiAlias = true;
  final Paint shaderPaint = Paint();
  final Paint fallbackPaint = Paint();
  late final Ticker _ticker;
  ui.FragmentShader? shader;
  late GlowBudget budget;
  Size size = Size.zero;
  double time = 0, bufferScale = .5, orbR = 80;
  Duration _prev = Duration.zero;
  bool _resumed = true, _reduced = false, _effects = true;
  CxEngine? _engine;
  CharacterCtrl? _ctrl;
  final int _cores = _cpuCores();

  static int _cpuCores() {
    try {
      return Platform.numberOfProcessors;
    } catch (_) {
      return 8;
    }
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    final g = MotionTokens.defaults.glow;
    budget = GlowBudget(budgetMs: g.budgetMs, frames: g.budgetFrames);
    _ticker = createTicker(_onTick);
    Skin.changes.addListener(_recolor);
    _recolor();
    FxGlow.load().then((p) {
      if (mounted && p != null) shader = p.fragmentShader();
    });
    SchedulerBinding.instance.addTimingsCallback(_onTimings);
  }

  void _onTimings(List<ui.FrameTiming> t) {
    if (shader == null || !_ticker.isActive) return;
    for (final f in t) {
      budget.feed(f.rasterDuration.inMicroseconds / 1000);
    }
  }

  void _recolor() {
    final k = Skin.current;
    final a = (k.light ? .3 : .6);
    pointPaint
      ..color = k.cyan.withValues(alpha: a)
      ..strokeWidth = 1.6 * k.pSize;
  }

  Offset? orbCenter() {
    final r = _engine?.anchorRect();
    if (r == null) return null;
    orbR = math.min(r.width, r.height) * .36;
    return r.center;
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final sc = CxScope.maybeOf(context);
    _engine = sc?.engine;
    if (sc?.ctrl != _ctrl) {
      _ctrl?.removeListener(_sync);
      _ctrl = sc?.ctrl;
      _ctrl?.addListener(_sync);
    }
    final m = _ctrl?.motion ?? MotionTokens.defaults;
    bufferScale = m.glow.bufferScale;
    _effects = _ctrl?.effects ?? true;
    _reduced = MediaQuery.disableAnimationsOf(context) || (_ctrl?.reducedSetting ?? false);
    _sync();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    _resumed = s == AppLifecycleState.resumed;
    _sync();
  }

  void _sync() {
    _effects = _ctrl?.effects ?? true;
    final run = _resumed && _effects && !_reduced && TickerMode.valuesOf(context).enabled;
    if (run && !_ticker.isActive) {
      _prev = Duration.zero;
      _ticker.start();
    } else if (!run && _ticker.isActive) {
      _ticker.stop();
    }
    if (!_effects) {
      field.resize(size, 0);
      tick.value++;
    }
  }

  void _onTick(Duration d) {
    final dt = _prev == Duration.zero
        ? 0.0
        : ((d - _prev).inMicroseconds / 1e6).clamp(0.0, .05);
    _prev = d;
    time += dt;
    final k = Skin.current;
    final m = _ctrl?.motion ?? MotionTokens.defaults;
    final n = ParticleField.countFor(size, k.pCount, m.particles.cap(_cores));
    field.resize(size, n);
    field.step(dt, k.pDrift);
    tick.value++;
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    SchedulerBinding.instance.removeTimingsCallback(_onTimings);
    Skin.changes.removeListener(_recolor);
    _ctrl?.removeListener(_sync);
    _ticker.dispose();
    tick.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: LayoutBuilder(builder: (context, c) {
        size = c.biggest;
        final bs = bufferScale;
        return Stack(children: [
          Positioned.fill(
            child: RepaintBoundary(
              child: Transform.scale(
                scale: 1 / bs,
                alignment: Alignment.topLeft,
                filterQuality: FilterQuality.low,
                child: SizedBox(
                  width: size.width * bs,
                  height: size.height * bs,
                  child: CustomPaint(painter: _GlowPainter(this)),
                ),
              ),
            ),
          ),
          Positioned.fill(
            child: RepaintBoundary(
              child: CustomPaint(painter: _ParticlePainter(this)),
            ),
          ),
        ]);
      }),
    );
  }
}
