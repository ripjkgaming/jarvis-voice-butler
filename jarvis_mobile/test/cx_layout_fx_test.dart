import 'dart:async';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/character/character_ctrl.dart';
import 'package:jarvis_mobile/character/layout.dart';
import 'package:jarvis_mobile/character/models.dart';
import 'package:jarvis_mobile/character/skin_tokens.dart';
import 'package:jarvis_mobile/character/transition.dart';
import 'package:jarvis_mobile/widgets/fx.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _T implements Timer {
  @override
  void cancel() {}
  @override
  bool get isActive => true;
  @override
  int get tick => 0;
}

Map<String, dynamic> _char(String id, String layout, Map<String, dynamic> t) => {
      'id': id,
      'display_name': id.toUpperCase(),
      'spoken_name': id,
      'address': 'x',
      'signature': id == 'ultron' ? 'violent_settle' : 'holo_reassembly',
      'layout': layout,
      'intensity': 1.0,
      'theme_tokens': t,
    };

void main() {
  group('motion tokens', () {
    test('particles, glow and layouts parse with safe defaults', () {
      final m = MotionTokens.fromJson({
        'particles': {'cap_desktop': 1.0, 'cap_low': 0.4, 'low_cores': 4},
        'glow': {'buffer_scale': 0.5, 'budget_ms': 2.0},
        'layouts': {
          'hud_angular': {'flow': 'columns', 'columns': [10, 60, 30], 'reactor_scale': 1.2},
        },
      });
      expect(m.particles.cap(4), 0.4);
      expect(m.particles.cap(8), 1.0);
      expect(m.glow.budgetMs, lessThanOrEqualTo(2));
      expect(m.layouts['hud_angular']!.columns, [10, 60, 30]);
      // Missing layouts fall back to the shipped defaults, never null.
      expect(m.layouts['hud_glass']!.summary, isTrue);
      final bad = MotionTokens.fromJson({'layouts': 'nope', 'particles': 3});
      expect(bad.layouts.keys, containsAll(['hud_round', 'hud_angular', 'hud_glass']));
    });

    test('every default layout is well formed', () {
      for (final e in LayoutSpec.defaults.entries) {
        final s = e.value;
        expect(['columns', 'cards'], contains(s.flow), reason: e.key);
        if (s.flow == 'columns') {
          expect(s.columns.reduce((a, b) => a + b), 100, reason: e.key);
        } else {
          expect(s.cards, inInclusiveRange(2, 6), reason: e.key);
        }
        expect(s.reactorScale, inInclusiveRange(.4, 1.4), reason: e.key);
      }
    });
  });

  group('skin tokens', () {
    test('particle params come from tokens and lerp', () {
      final a = SkinTokens.fromTokens({'particle_count': 0.45, 'particle_size': 1.35, 'particle_drift': 0.6});
      expect(a.pCount, .45);
      expect(SkinTokens.jarvis.pCount, 1);
      final mid = SkinTokens.lerp(SkinTokens.jarvis, a, .5);
      expect(mid.pCount, closeTo(.725, 1e-9));
      expect(mid.pDrift, closeTo(.8, 1e-9));
    });

    test('Jarvis is the icy-blue palette', () {
      expect(SkinTokens.jarvis.cyan.toARGB32(), 0xFFA8EBFF);
      expect(SkinTokens.jarvis.cyanBright.toARGB32(), 0xFFF4FDFF);
    });
  });

  group('particle field', () {
    test('count scales with skin and device cap, bounded', () {
      const size = Size(390, 800);
      final jarvis = ParticleField.countFor(size, 1, 1);
      final ultron = ParticleField.countFor(size, .45, 1);
      final low = ParticleField.countFor(size, 1, .45);
      expect(ultron, lessThan(jarvis));
      expect(low, lessThan(jarvis));
      expect(ParticleField.countFor(const Size(4000, 4000), 2, 1), ParticleField.maxCount);
    });

    test('step wraps inside bounds and never reallocates', () {
      final f = ParticleField()..resize(const Size(300, 600), 40);
      final view = f.view;
      for (var i = 0; i < 600; i++) {
        f.step(.016, 1.5);
      }
      expect(identical(f.view, view), isTrue);
      for (var i = 0; i < f.count; i++) {
        expect(f.pos[i * 2], inInclusiveRange(0, 300));
        expect(f.pos[i * 2 + 1], inInclusiveRange(0, 600));
      }
    });

    test('same-size resize keeps positions (no reset on a skin cross-fade)', () {
      final f = ParticleField()..resize(const Size(300, 600), 40);
      f.step(.5, 1);
      final x = f.pos[0];
      f.resize(const Size(300, 600), 36);
      expect(f.pos[0], x);
      expect(f.count, 36);
    });
  });

  group('glow budget', () {
    test('keeps the shader when it costs under budget', () {
      final b = GlowBudget(budgetMs: 2, frames: 10);
      for (var i = 0; i < 10; i++) {
        b.feed(6.0);
      }
      expect(b.glowOn, isFalse); // A/B phase: now measuring without it
      for (var i = 0; i < 10; i++) {
        b.feed(5.0);
      }
      expect(b.verdict, isTrue);
      expect(b.glowOn, isTrue);
    });

    test('cuts the shader when it costs more than 2 ms per frame', () {
      final b = GlowBudget(budgetMs: 2, frames: 10);
      for (var i = 0; i < 10; i++) {
        b.feed(9.0);
      }
      for (var i = 0; i < 10; i++) {
        b.feed(5.0);
      }
      expect(b.verdict, isFalse);
      expect(b.glowOn, isFalse);
    });
  });

  group('shader asset', () {
    test('glow fragment program compiles', () async {
      final p = await FxGlow.load();
      if (p != null) {
        final s = p.fragmentShader();
        s.setFloat(15, 1); // 16 float uniforms
        s.dispose();
      }
      expect(p, isNotNull);
    });
  });

  group('layout switch + FLIP', () {
    late CharacterCtrl ctrl;
    late CxEngine engine;
    final keys = {for (final k in ['a', 'b']) k: GlobalKey()};

    setUp(() {
      SharedPreferences.setMockInitialValues({});
      Skin.reset();
      ctrl = CharacterCtrl(
        api: CharacterApi(
          characters: () async => {
                'ok': true,
                'motion': {'total_ms': 1800},
                'characters': {
                  'jarvis': _char('jarvis', 'hud_round', {}),
                  'ultron': _char('ultron', 'hud_angular', {'accent': '#ff2a36'}),
                },
              },
          character: () async => {'active': 'jarvis', 'target': null, 'phase': 'idle', 'char_turn': 0},
          switchTo: (t, s) async => {'ok': true},
        ),
        timerFactory: (d, f) => _T(),
        delay: (d) async {},
      );
    });
    tearDown(() => Skin.reset());

    Widget slot(String id, double w) => KeyedSubtree(
          key: keys[id],
          child: CxFlip(child: SizedBox(width: w, height: 40, child: ColoredBox(color: Colors.blue, child: Text(id)))),
        );

    Future<void> pumpMs(WidgetTester t, int ms) async {
      for (var i = 0; i < ms ~/ 16; i++) {
        await t.pump(const Duration(milliseconds: 16));
      }
    }

    testWidgets('sections glide old slot -> new slot, none are lost', (t) async {
      await t.pumpWidget(_Host(ctrl: ctrl, onEngine: (e) => engine = e, build: (id) {
        return id == 'hud_angular'
            ? Row(crossAxisAlignment: CrossAxisAlignment.start, children: [slot('b', 120), slot('a', 100)])
            : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [slot('a', 100), slot('b', 120)]);
      }));
      await ctrl.start();
      await t.pump();
      // Measure through the Transform (the slot's own render box ignores it).
      Rect rect(String k) => t.getRect(find.text(k));

      final oldA = rect('a');
      ctrl.noteSwitch('ultron', 1);
      await pumpMs(t, 800); // swapped, panels still parked on their old slots
      expect(engine.layoutId.value, 'hud_angular');
      final early = rect('a');
      await pumpMs(t, 1200);
      expect(engine.running, isFalse);
      final newA = rect('a');
      expect(newA, isNot(oldA), reason: 'layout really changed slot');
      expect((early.topLeft - oldA.topLeft).distance,
          lessThan((early.topLeft - newA.topLeft).distance),
          reason: 'starts at the old slot (no jump)');
      expect(keys['b']!.currentContext, isNotNull);
      expect(find.text('a'), findsOneWidget);
      expect(find.text('b'), findsOneWidget);
      expect(engine.flip.value, 1);
    });

    testWidgets('rapid switch retargets the layout from the current state', (t) async {
      await t.pumpWidget(_Host(ctrl: ctrl, onEngine: (e) => engine = e, build: (id) {
        return id == 'hud_angular'
            ? Row(crossAxisAlignment: CrossAxisAlignment.start, children: [slot('b', 120), slot('a', 100)])
            : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [slot('a', 100), slot('b', 120)]);
      }));
      await ctrl.start();
      await t.pump();
      ctrl.noteSwitch('ultron', 1);
      await pumpMs(t, 1100);
      ctrl.noteSwitch('jarvis', 2);
      await pumpMs(t, 2200);
      expect(engine.running, isFalse);
      expect(engine.layoutId.value, 'hud_round');
      expect(find.text('a'), findsOneWidget);
    });
  });
}

class _Host extends StatefulWidget {
  final CharacterCtrl ctrl;
  final void Function(CxEngine) onEngine;
  final Widget Function(String id) build;
  const _Host({required this.ctrl, required this.onEngine, required this.build});
  @override
  State<_Host> createState() => _HostState();
}

class _HostState extends State<_Host> with SingleTickerProviderStateMixin {
  late final CxEngine e;
  @override
  void initState() {
    super.initState();
    e = CxEngine(widget.ctrl, this, () => false);
    widget.onEngine(e);
  }

  @override
  void dispose() {
    e.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        home: CxScope(
          engine: e,
          ctrl: widget.ctrl,
          child: Scaffold(
            body: CxLayout(builder: (ctx, id, spec) => widget.build(id)),
          ),
        ),
      );
}
