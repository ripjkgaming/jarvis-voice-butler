import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/character/character_ctrl.dart';
import 'package:jarvis_mobile/character/skin_tokens.dart';
import 'package:jarvis_mobile/character/transition.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _T implements Timer {
  @override
  void cancel() {}
  @override
  bool get isActive => true;
  @override
  int get tick => 0;
}

Map<String, dynamic> _char(String id, String sig, Map<String, dynamic> t) => {
      'id': id,
      'display_name': id.toUpperCase(),
      'spoken_name': id,
      'address': 'x',
      'signature': sig,
      'intensity': 1.0,
      'theme_tokens': t,
    };

const _ultron = {
  'void': '#050000', 'ink': '#0b0205', 'deep': '#150407', 'raise': '#240a0e',
  'line': 'rgba(255,40,50,0.22)', 'line_strong': 'rgba(255,40,50,0.55)',
  'accent': '#ff2a36', 'accent_hot': '#ffb3b8', 'accent_dim': '#8a1119',
  'text': '#f3dcdd', 'text_dim': '#a37e80', 'text_faint': '#5c3f41',
  'warn': '#ff9a3c', 'danger': '#ff2a36', 'ok': '#8affb0',
  'bubble_user': '#240a0e', 'bubble_char': '#150407',
  'radius_panel': 0, 'cut_angle': 14, 'glow': 0.7, 'scan': 0.35,
  'edge_refract': 0.0, 'font_label': 'mono',
};
const _friday = {
  'void': '#f4f8fb', 'ink': '#eaf1f7', 'deep': '#dde8f1', 'raise': '#ffffff',
  'line': 'rgba(40,110,150,0.20)', 'line_strong': 'rgba(40,110,150,0.45)',
  'accent': '#2e9fd8', 'accent_hot': '#d9731a', 'accent_dim': '#7fb8d6',
  'text': '#16313f', 'text_dim': '#4f6c7c', 'text_faint': '#8ea4b1',
  'warn': '#e8892b', 'danger': '#d94a5a', 'ok': '#2bb67a',
  'bubble_user': '#ffffff', 'bubble_char': '#dde8f1',
  'radius_panel': 18, 'cut_angle': 0, 'glow': 0.4, 'scan': 0.0,
  'edge_refract': 0.8, 'font_label': 'sans',
};

class _Harness extends StatefulWidget {
  final CharacterCtrl ctrl;
  final bool reduced;
  final void Function(CxEngine) onEngine;
  const _Harness({required this.ctrl, required this.onEngine, this.reduced = false});
  @override
  State<_Harness> createState() => _HarnessState();
}

class _HarnessState extends State<_Harness> with SingleTickerProviderStateMixin {
  late final CxEngine e;
  @override
  void initState() {
    super.initState();
    e = CxEngine(widget.ctrl, this, () => widget.reduced);
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
          child: CxOverlay(
            engine: e,
            child: Scaffold(
              body: Column(children: [
                const CxGroup(group: CxGroupId.chrome, child: SizedBox(height: 40, width: 300)),
                CxGroup(group: CxGroupId.panels, child: Container(height: 120, width: 300, color: Colors.blue)),
                const CxAnchor(child: CxGroup(group: CxGroupId.orb, child: SizedBox(height: 100, width: 100))),
              ]),
            ),
          ),
        ),
      );
}

void main() {
  late CharacterCtrl ctrl;
  late CxEngine engine;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    Skin.reset();
    ctrl = CharacterCtrl(
      api: CharacterApi(
        characters: () async => {
              'ok': true,
              'motion': {'total_ms': 1800},
              'characters': {
                'jarvis': _char('jarvis', 'holo_reassembly', {}),
                'ultron': _char('ultron', 'violent_settle', _ultron),
                'friday': _char('friday', 'luminous_sweep', _friday),
              },
            },
        character: () async => {'active': 'jarvis', 'target': null, 'phase': 'idle', 'char_turn': 0},
        switchTo: (t, s) async => {'ok': true, 'switched': true, 'handled': true, 'char_turn': 1},
      ),
      timerFactory: (d, f) => _T(),
      delay: (d) async {},
    );
  });

  tearDown(() => Skin.reset());

  Future<void> boot(WidgetTester t, {bool reduced = false}) async {
    await t.pumpWidget(_Harness(ctrl: ctrl, reduced: reduced, onEngine: (e) => engine = e));
    await ctrl.start();
    await t.pump();
  }

  Future<void> run(WidgetTester t, int ms) async {
    for (var i = 0; i < ms ~/ 16; i++) {
      await t.pump(const Duration(milliseconds: 16));
    }
  }

  testWidgets('jarvis -> ultron lands on the ultron skin and settles', (t) async {
    await boot(t);
    expect(ctrl.active, 'jarvis');
    ctrl.noteSwitch('ultron', 1);
    await run(t, 700);
    expect(engine.running, isTrue);
    final mid = Skin.current.cyan;
    expect(mid, isNot(SkinTokens.jarvis.cyan));
    await run(t, 1400);
    expect(engine.running, isFalse);
    expect(ctrl.active, 'ultron');
    expect(Skin.current.cyan.toARGB32(), 0xFFFF2A36);
    for (final g in CxGroupId.values) {
      expect(engine.values[g]!.value, 1.0);
    }
    expect(engine.shake.value, Offset.zero);
  });

  testWidgets('retarget mid-flight A->B->C never restarts or stacks', (t) async {
    await boot(t);
    ctrl.noteSwitch('ultron', 1);
    await run(t, 500);
    final before = Skin.current.cyan;
    ctrl.noteSwitch('friday', 2);
    await t.pump(const Duration(milliseconds: 16));
    // palette continues from where it was: no jump back to jarvis
    expect(Skin.current.cyan.toARGB32(), isNot(SkinTokens.jarvis.cyan.toARGB32()));
    expect((Skin.current.cyan.r - before.r).abs() < 0.2, isTrue);
    ctrl.noteSwitch('jarvis', 3);
    await run(t, 2400);
    expect(engine.running, isFalse);
    expect(ctrl.active, 'jarvis');
    expect(Skin.current.cyan.toARGB32(), SkinTokens.jarvis.cyan.toARGB32());
    expect(Skin.current.bg.toARGB32(), SkinTokens.jarvis.bg.toARGB32());
  });

  testWidgets('reduced motion is a plain ~200 ms cross-fade, no overlay', (t) async {
    await boot(t, reduced: true);
    ctrl.noteSwitch('friday', 1);
    await run(t, 300);
    expect(engine.running, isFalse);
    expect(engine.sigActive, isFalse);
    expect(ctrl.active, 'friday');
    expect(Skin.current.light, isTrue);
  });

  testWidgets('effects off uses the cross-fade path', (t) async {
    await boot(t);
    await ctrl.setEffects(false);
    ctrl.noteSwitch('ultron', 1);
    await run(t, 300);
    expect(engine.running, isFalse);
    expect(ctrl.active, 'ultron');
  });

  testWidgets('per-frame engine cost stays well under a 60 fps budget', (t) async {
    await boot(t);
    ctrl.noteSwitch('ultron', 1);
    final sw = Stopwatch();
    var worst = 0, n = 0, sum = 0;
    for (var i = 0; i < 110; i++) {
      sw.reset();
      sw.start();
      await t.pump(const Duration(milliseconds: 16));
      sw.stop();
      final us = sw.elapsedMicroseconds;
      worst = us > worst ? us : worst;
      sum += us;
      n++;
    }
    // build+layout+paint-record on the host CPU; raster is not measured here.
    // ignore: avoid_print
    print('cx frame cpu: mean ${(sum / n / 1000).toStringAsFixed(2)} ms, worst ${(worst / 1000).toStringAsFixed(2)} ms');
    expect(sum / n / 1000, lessThan(8.0));
  });
}
