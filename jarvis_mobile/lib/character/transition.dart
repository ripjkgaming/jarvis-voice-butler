import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'character_ctrl.dart';
import 'models.dart';
import 'signatures.dart';
import 'skin_tokens.dart';

/// Cinematic character handoff. One Ticker drives every animated scalar
/// (group opacity/offset, palette mix, signature beat); everything is
/// transform/opacity/colour on repaint boundaries, never layout. Retargeting
/// builds new tweens from the *current* values, so a second switch never
/// restarts from zero and animations never stack. Mirrors the desktop engine
/// (frontend/lib/character/engine.ts) and reads the same motion tokens.

enum CxGroupId { chrome, panels, text, orb }

const _offPx = 14.0;
const _outKeys = {
  CxGroupId.chrome: 'out_chrome',
  CxGroupId.panels: 'out_panels',
  CxGroupId.text: 'out_text',
  CxGroupId.orb: 'out_orb',
};
const _inKeys = {
  CxGroupId.chrome: 'in_chrome',
  CxGroupId.panels: 'in_panels',
  CxGroupId.text: 'in_text',
  CxGroupId.orb: 'in_orb',
};

class _Seg {
  final double t0, t1, from, to;
  final Curve ease;
  const _Seg(this.t0, this.t1, this.from, this.to, this.ease);
}

class FrameStats {
  int frames = 0, over20 = 0, over34 = 0;
  double worst = 0, total = 0;
  final List<double> trace = [];
}

class CxEngine {
  final CharacterCtrl ctrl;
  final bool Function() reducedProbe;
  late final Ticker _ticker;

  final Map<CxGroupId, ValueNotifier<double>> values = {
    for (final g in CxGroupId.values) g: ValueNotifier<double>(1),
  };
  final Map<CxGroupId, Set<GlobalKey>> _keys = {
    for (final g in CxGroupId.values) g: <GlobalKey>{},
  };
  final Set<GlobalKey> _anchors = {};

  /// Repaints the overlay painter.
  final ValueNotifier<int> repaint = ValueNotifier<int>(0);
  final ValueNotifier<Offset> shake = ValueNotifier(Offset.zero);
  bool sigActive = false;
  Size size = Size.zero;

  MotionTokens _m = MotionTokens.defaults;
  Map<String, Curve> _ease = {};
  SkinTokens _from = SkinTokens.jarvis;
  SkinTokens _to = SkinTokens.jarvis;
  SkinTokens _cur = SkinTokens.jarvis;
  String _toId = 'jarvis';
  String _visual = 'jarvis';
  bool _running = false, _reduced = false, _swapped = false, _skipSig = false;
  double _t = 0, _swapAt = 0, _sigStart = 0, _sigEnd = 0, _endAt = 0;
  int _lastUs = 0, _paletteFrame = 0, _seed = 1;
  final Map<CxGroupId, List<_Seg>> _segs = {
    for (final g in CxGroupId.values) g: [],
  };
  final Map<CxGroupId, double> _v = {for (final g in CxGroupId.values) g: 1};
  FxCtx? _fx;
  List<double> _flashLog = [];
  Shape _fromShape = const Shape(0, 3), _toShape = const Shape(0, 3);
  final FrameStats stats = FrameStats();
  Signature? _sig;
  FxCtx? get fx => _fx;
  double get beatU => clamp01((_t - _sigStart) / math.max(1, _sigEnd - _sigStart));
  double get morphU {
    final a = _m.phaseStart('out_orb'), b = _m.phaseEnd('in_orb');
    return smooth(a, b, _t);
  }

  bool get morphActive {
    final a = _m.phaseStart('out_orb'), b = _m.phaseEnd('in_orb');
    return _running && !_reduced && _t >= a && _t <= b;
  }

  Shape get fromShape => _fromShape;
  Shape get toShape => _toShape;
  SkinTokens get curSkin => _cur;
  SkinTokens get toSkin => _to;
  Curve get outExpo => _ease['out_expo'] ?? Curves.easeOutCubic;
  bool get running => _running;

  CxEngine(this.ctrl, TickerProvider vsync, this.reducedProbe) {
    _ticker = vsync.createTicker(_tick);
    ctrl.events.listen(_run);
  }

  void dispose() {
    _ticker.dispose();
    repaint.dispose();
    for (final v in values.values) {
      v.dispose();
    }
  }

  /* ----------------------------------------------------- registry */

  void register(CxGroupId g, GlobalKey k) => _keys[g]!.add(k);
  void unregister(CxGroupId g, GlobalKey k) => _keys[g]!.remove(k);
  void addAnchor(GlobalKey k) => _anchors.add(k);
  void removeAnchor(GlobalKey k) => _anchors.remove(k);

  Rect? _rectOf(GlobalKey k) {
    final ro = k.currentContext?.findRenderObject();
    if (ro is! RenderBox || !ro.attached || !ro.hasSize) return null;
    final o = ro.localToGlobal(Offset.zero);
    return o & ro.size;
  }

  List<Rect> _rects(CxGroupId g) => [
        for (final k in _keys[g]!)
          if (_rectOf(k) case final r? when r.width > 8 && r.height > 8) r,
      ];

  /* ----------------------------------------------------- run */

  Curve _curve(String n) {
    final c = _m.easing[n];
    if (c == null || c.length < 4) return Curves.easeInOut;
    return Cubic(c[0], c[1], c[2], c[3]);
  }

  void _run(SwitchEvent ev) {
    final to = ctrl.characters[ev.to];
    if (to == null) return;
    _m = ctrl.motion;
    _ease = {for (final k in _m.easing.keys) k: _curve(k)};
    final from = ctrl.characters[_visual];
    final retarget = _running;
    _reduced = reducedProbe() || !ctrl.effects;
    _from = _cur;
    _to = ev.to == 'jarvis' ? SkinTokens.jarvis : SkinTokens.fromTokens(to.themeTokens);
    _toId = ev.to;
    _fromShape = from == null ? const Shape(0, 3) : Shape.of(from);
    _toShape = Shape.of(to);
    _seed = ev.turn * 7919 + 13;
    _fx = null;
    _skipSig = retarget && _t > _sigEnd - 200;
    _swapped = false;
    _t = 0;
    _flashLog = [];
    _build(retarget);
    _sig = signatures[to.signature];
    stats
      ..frames = 0
      ..over20 = 0
      ..over34 = 0
      ..worst = 0
      ..total = 0
      ..trace.clear();
    ctrl.setEngineBusy(true);
    _lastUs = 0;
    if (!_running) {
      _running = true;
      _ticker.start();
    }
  }

  void _build(bool retarget) {
    final m = _m;
    if (_reduced) {
      final d = m.reducedMs.toDouble();
      _swapAt = 0;
      _sigStart = _sigEnd = 0;
      _endAt = d;
      for (final g in CxGroupId.values) {
        _v[g] = math.min(_v[g]!, 0.4);
        _segs[g] = [_Seg(0, d, _v[g]!, 1, Curves.easeInOutCubic)];
      }
      return;
    }
    final k = _skipSig ? 0.55 : 1.0;
    double sc(double x) => x * k;
    var swap = 0.0;
    for (final g in CxGroupId.values) {
      final o0 = m.phaseStart(_outKeys[g]!), o1 = m.phaseEnd(_outKeys[g]!);
      final i0 = m.phaseStart(_inKeys[g]!), i1 = m.phaseEnd(_inKeys[g]!);
      swap = math.max(swap, sc(o1));
      final sv = _v[g]!;
      _segs[g] = [
        sv < 0.02
            ? const _Seg(0, 0.001, 0, 0, Curves.linear)
            : _Seg(sc(o0), sc(o1), sv, 0, _ease['in_out_cubic'] ?? Curves.easeInOut),
        _Seg(sc(i0), sc(i1), 0, 1, _ease['out_expo'] ?? Curves.easeOut),
      ];
    }
    _swapAt = swap;
    _sigStart = _skipSig ? swap : sc(m.phaseStart('signature'));
    _sigEnd = _skipSig ? swap + 1 : sc(m.phaseEnd('signature'));
    _endAt = sc(m.totalMs.toDouble());
  }

  double _eval(CxGroupId g) {
    var v = _v[g]!;
    for (final s in _segs[g]!) {
      if (_t < s.t0) break;
      final u = clamp01((_t - s.t0) / math.max(1, s.t1 - s.t0));
      v = s.from + (s.to - s.from) * s.ease.transform(u);
    }
    return v;
  }

  void _swap() {
    _swapped = true;
    _visual = _toId;
    ctrl.commitVisual(_toId);
  }

  double _flash(double amp) {
    _flashLog = _flashLog.where((x) => _t - x < 1000).toList();
    if (_flashLog.length >= _m.maxFlashHz.floor()) return 0;
    if (_flashLog.isEmpty || _t - _flashLog.last > 90) _flashLog.add(_t);
    return amp;
  }

  FxCtx _buildFx() {
    Rect? r;
    for (final k in _anchors) {
      r = _rectOf(k);
      if (r != null) break;
    }
    if (size == Size.zero) {
      final v = WidgetsBinding.instance.platformDispatcher.views.first;
      size = v.physicalSize / v.devicePixelRatio;
    }
    final anchor = r?.center ?? Offset(size.width / 2, size.height * 0.4);
    final ar = r == null ? 110.0 : math.min(r.width, r.height) * 0.36;
    return FxCtx(
      size: size,
      anchor: anchor,
      anchorR: ar,
      panels: [..._rects(CxGroupId.panels), ..._rects(CxGroupId.chrome)],
      colors: _to,
      intensity: ctrl.characters[_toId]?.intensity ?? 1,
      maxShake: _m.maxShakePx,
      maxGlitch: _m.maxGlitchAlpha,
      rng: Rng(_seed),
      flash: _flash,
    );
  }

  void _tick(Duration d) {
    final us = d.inMicroseconds;
    final raw = _lastUs == 0 ? 16.7 : (us - _lastUs) / 1000.0;
    _lastUs = us;
    final dt = raw.clamp(0.0, 34.0);
    _t += dt;
    stats
      ..frames += 1
      ..total += raw
      ..worst = math.max(stats.worst, raw);
    if (raw > 20) stats.over20++;
    if (raw > 34) stats.over34++;
    if (stats.trace.length < 600) stats.trace.add((raw * 10).round() / 10);

    final m = _m;
    final p0 = _reduced ? 0.0 : m.phaseStart('palette');
    final p1 = _reduced ? m.reducedMs.toDouble() : m.phaseEnd('palette');
    final pk = (_ease['snap'] ?? Curves.easeInOut).transform(smooth(p0, p1, _t));
    if ((_paletteFrame++ & 1) == 0 || pk >= 1) {
      _cur = SkinTokens.lerp(_from, _to, pk);
      Skin.set(_cur);
    }
    if (!_swapped && _t >= _swapAt) _swap();
    for (final g in CxGroupId.values) {
      _v[g] = _eval(g);
    }

    var sh = Offset.zero;
    if (!_reduced) {
      final inBeat = _t >= _sigStart && _t <= _sigEnd && !_skipSig;
      sigActive = inBeat || morphActive;
      if (sigActive) {
        _fx ??= _buildFx();
        final fx = _fx!;
        if (inBeat && _sig != null) {
          final u = beatU;
          fx.sec = (_t - _sigStart) / 1000;
          sh = _sig!.shake(u, fx);
          for (final g in CxGroupId.values) {
            final rs = _rects(g);
            if (rs.isEmpty) continue;
            final rv = _sig!.reveal(rs.first, u, fx);
            if (rv != null) _v[g] = math.max(_v[g]!, rv);
          }
        }
      }
      repaint.value++;
    }
    shake.value = sh;
    for (final g in CxGroupId.values) {
      if (values[g]!.value != _v[g]) values[g]!.value = _v[g]!;
    }
    if (_t >= _endAt) _finish();
  }

  void paint(Canvas c, Size s) {
    size = s;
    if (!sigActive || _fx == null) return;
    final fx = _fx!;
    if (morphActive) _drawMorph(c, fx);
    if (_t >= _sigStart && _t <= _sigEnd && !_skipSig) {
      _sig?.draw(c, beatU, fx);
    }
  }

  double _polyR(double th, int sides) {
    if (sides < 3) return 1;
    final seg = math.pi * 2 / sides;
    final a = ((th % seg) + seg) % seg;
    return math.cos(math.pi / sides) / math.cos(a - seg / 2);
  }

  void _drawMorph(Canvas g, FxCtx fx) {
    final u = morphU;
    final k = Curves.easeInOutCubic.transform(smooth(0.2, 0.62, u));
    final rings = (_fromShape.rings + (_toShape.rings - _fromShape.rings) * k).round();
    final col = SkinTokens.lerp(_from, _to, k).cyan;
    final al = math.sin(clamp01(u) * math.pi) * 0.85;
    final pulse = 1 - 0.08 * math.sin(clamp01(u) * math.pi);
    for (var i = 0; i < rings; i++) {
      final base = fx.anchorR * (0.62 + i * 0.22) * pulse;
      final rot = u * (i.isOdd ? -1 : 1) * 0.9;
      final path = Path();
      const n = 72;
      for (var j = 0; j <= n; j++) {
        final th = j / n * math.pi * 2;
        final r0 = _polyR(th, _fromShape.sides), r1 = _polyR(th, _toShape.sides);
        final r = base * (r0 + (r1 - r0) * k);
        final x = fx.anchor.dx + math.cos(th + rot) * r;
        final y = fx.anchor.dy + math.sin(th + rot) * r;
        j == 0 ? path.moveTo(x, y) : path.lineTo(x, y);
      }
      path.close();
      g.drawPath(
          path,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.6
            ..color = col.withValues(alpha: al * (0.95 - i * 0.18)));
    }
  }

  void _finish() {
    _ticker.stop();
    _running = false;
    sigActive = false;
    ctrl.setEngineBusy(false);
    if (!_swapped) _swap();
    _cur = _to;
    Skin.set(_toId == 'jarvis' ? SkinTokens.jarvis : _to);
    for (final g in CxGroupId.values) {
      _v[g] = 1;
      values[g]!.value = 1;
    }
    shake.value = Offset.zero;
    repaint.value++;
  }
}

class Shape {
  final int sides, rings;
  const Shape(this.sides, this.rings);
  factory Shape.of(CharacterInfo c) {
    final cut = (c.themeTokens['cut_angle'] as num?)?.toDouble() ?? 0;
    final edge = (c.themeTokens['edge_refract'] as num?)?.toDouble() ?? 0;
    return Shape(cut > 0 ? 6 : 0, edge > 0 ? 2 : (cut > 0 ? 4 : 3));
  }
}

/* ------------------------------------------------------------ widgets */

class CxScope extends InheritedWidget {
  final CxEngine engine;
  final CharacterCtrl ctrl;
  const CxScope({
    super.key,
    required this.engine,
    required this.ctrl,
    required super.child,
  });

  static CxScope? maybeOf(BuildContext c) =>
      c.dependOnInheritedWidgetOfExactType<CxScope>();

  /// Lookup without subscribing (callbacks, initState).
  static CxScope? peek(BuildContext c) =>
      c.getInheritedWidgetOfExactType<CxScope>();

  @override
  bool updateShouldNotify(CxScope old) => old.engine != engine || old.ctrl != ctrl;
}

/// Wraps a themed region so it eases out/in with the handoff. The widget
/// chain is stable (no state loss when a transition starts or ends).
class CxGroup extends StatefulWidget {
  final CxGroupId group;
  final Widget child;
  const CxGroup({super.key, required this.group, required this.child});

  @override
  State<CxGroup> createState() => _CxGroupState();
}

class _CxGroupState extends State<CxGroup> {
  final GlobalKey _k = GlobalKey();
  CxEngine? _e;
  bool _on = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final e = CxScope.maybeOf(context)?.engine;
    // Only the visible tab takes part in signatures (offstage pages keep
    // their layout but are never painted).
    final on = TickerMode.of(context);
    if (e != _e || on != _on) {
      _e?.unregister(widget.group, _k);
      _e = e;
      _on = on;
      if (on) _e?.register(widget.group, _k);
    }
  }

  @override
  void dispose() {
    _e?.unregister(widget.group, _k);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final e = _e;
    if (e == null) return widget.child;
    return ValueListenableBuilder<double>(
      valueListenable: e.values[widget.group]!,
      child: KeyedSubtree(key: _k, child: widget.child),
      builder: (_, v, child) => Opacity(
        opacity: v.clamp(0.0, 1.0),
        child: Transform.translate(offset: Offset(0, (1 - v) * _offPx), child: child),
      ),
    );
  }
}

/// Marks the orb so signatures and the shape morph centre on it.
class CxAnchor extends StatefulWidget {
  final Widget child;
  const CxAnchor({super.key, required this.child});

  @override
  State<CxAnchor> createState() => _CxAnchorState();
}

class _CxAnchorState extends State<CxAnchor> {
  final GlobalKey _k = GlobalKey();
  CxEngine? _e;
  bool _on = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final e = CxScope.maybeOf(context)?.engine;
    final on = TickerMode.of(context);
    if (e != _e || on != _on) {
      _e?.removeAnchor(_k);
      _e = e;
      _on = on;
      if (on) _e?.addAnchor(_k);
    }
  }

  @override
  void dispose() {
    _e?.removeAnchor(_k);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => KeyedSubtree(key: _k, child: widget.child);
}

/// Full-screen signature layer + camera shake. Pointer-transparent.
class CxOverlay extends StatelessWidget {
  final CxEngine engine;
  final Widget child;
  const CxOverlay({super.key, required this.engine, required this.child});

  @override
  Widget build(BuildContext context) {
    return Stack(children: [
      Positioned.fill(
        child: ValueListenableBuilder<Offset>(
          valueListenable: engine.shake,
          child: child,
          builder: (_, o, c) => Transform.translate(offset: o, child: c),
        ),
      ),
      Positioned.fill(
        child: IgnorePointer(
          child: RepaintBoundary(
            child: CustomPaint(
              painter: _OverlayPainter(engine),
              size: Size.infinite,
            ),
          ),
        ),
      ),
    ]);
  }
}

class _OverlayPainter extends CustomPainter {
  final CxEngine e;
  _OverlayPainter(this.e) : super(repaint: e.repaint);

  @override
  void paint(Canvas canvas, Size size) => e.paint(canvas, size);

  @override
  bool shouldRepaint(covariant _OverlayPainter old) => old.e != e;
}
