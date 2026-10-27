import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'models.dart';
import 'transition.dart';

/// Layout preset currently on screen plus its spec (from _motion.yaml).
/// Without a [CxScope] (isolated widget tests) this is the Jarvis reference.
class CxLayout extends StatelessWidget {
  final Widget Function(BuildContext, String id, LayoutSpec spec) builder;
  const CxLayout({super.key, required this.builder});

  @override
  Widget build(BuildContext context) {
    final sc = CxScope.maybeOf(context);
    if (sc == null) {
      return builder(context, 'hud_round', LayoutSpec.defaults['hud_round']!);
    }
    return ValueListenableBuilder<String>(
      valueListenable: sc.engine.layoutId,
      builder: (ctx, id, _) => builder(
        ctx,
        id,
        sc.ctrl.motion.layouts[id] ??
            LayoutSpec.defaults[id] ??
            LayoutSpec.defaults['hud_round']!,
      ),
    );
  }
}

/// Shared-element FLIP for one section. When the layout preset changes, the
/// section's rect is captured before the rebuild (First), measured after it
/// (Last) and then glides from the old slot to the new one on the engine's
/// `flip` progress (transform only, no layout work). The orb passes
/// `uniform: true` so it scales without distortion.
class CxFlip extends StatefulWidget {
  final Widget child;
  final bool uniform;
  const CxFlip({super.key, required this.child, this.uniform = false});

  @override
  State<CxFlip> createState() => _CxFlipState();
}

class _CxFlipState extends State<CxFlip> {
  final GlobalKey _k = GlobalKey();
  CxEngine? _e;
  Rect? _first;
  Offset _d = Offset.zero;
  Offset _s = const Offset(1, 1);

  Rect? _rect() {
    final ro = _k.currentContext?.findRenderObject();
    if (ro is! RenderBox || !ro.attached || !ro.hasSize) return null;
    return ro.localToGlobal(Offset.zero) & ro.size;
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final e = CxScope.maybeOf(context)?.engine;
    if (e != _e) {
      _e?.layoutId.removeListener(_onLayout);
      _e = e;
      _e?.layoutId.addListener(_onLayout);
    }
  }

  /// Fires synchronously at the swap, before this frame's rebuild: the render
  /// tree still shows the old layout (including any glide in flight).
  void _onLayout() {
    if (!mounted || !TickerMode.valuesOf(context).enabled) return;
    _first = _rect();
    // Clear our own transform so Last is measured in the new slot.
    setState(() {
      _d = Offset.zero;
      _s = const Offset(1, 1);
    });
    SchedulerBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final a = _first, b = _rect();
      _first = null;
      if (a == null || b == null || a.width < 1 || b.width < 1) return;
      var sx = a.width / b.width, sy = a.height / b.height;
      if (widget.uniform) sy = sx;
      final dx = a.left - b.left, dy = a.top - b.top;
      if (dx.abs() + dy.abs() + (sx - 1).abs() + (sy - 1).abs() < .5) return;
      setState(() {
        _d = Offset(dx, dy);
        _s = Offset(sx, sy);
      });
    });
  }

  @override
  void dispose() {
    _e?.layoutId.removeListener(_onLayout);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final e = _e;
    final body = KeyedSubtree(key: _k, child: widget.child);
    if (e == null) return body;
    return ValueListenableBuilder<double>(
      valueListenable: e.flip,
      child: body,
      builder: (_, p, c) {
        final k = 1 - p;
        if (k <= 0.001 || (_d == Offset.zero && _s == const Offset(1, 1))) {
          return Transform(transform: Matrix4.identity(), child: c);
        }
        return Transform(
          alignment: Alignment.topLeft,
          transform: Matrix4.identity()
            ..translateByDouble(_d.dx * k, _d.dy * k, 0, 1)
            ..scaleByDouble(1 + (_s.dx - 1) * k, 1 + (_s.dy - 1) * k, 1, 1),
          child: c,
        );
      },
    );
  }
}
