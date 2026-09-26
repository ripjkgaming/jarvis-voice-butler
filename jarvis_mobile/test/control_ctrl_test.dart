import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/backend.dart';
import 'package:jarvis_mobile/core/control_ctrl.dart';

Map<String, dynamic> ok([Map<String, dynamic>? extra]) =>
    {'ok': true, ...?extra};

void main() {
  test('volume walk converges then reads true level', () async {
    var level = 50;
    final calls = <String>[];
    final c = ControlCtrl(api: () async => _FakeLink((tool, args) {
          calls.add(tool);
          if (tool == 'volume_get') return ok({'volume': level, 'muted': false});
          if (tool == 'volume_up') level += 5;
          if (tool == 'volume_down') level -= 5;
          return ok();
        }));
    await c.setVolume(72);
    // Pactl steps in 5s: lands within one step of target by design.
    expect((c.volume - 72).abs(), lessThanOrEqualTo(3));
    expect(calls.where((t) => t == 'volume_up').length, lessThanOrEqualTo(8));
    c.dispose();
  });

  test('capture decodes image bytes', () async {
    final c = ControlCtrl(api: () async => _FakeLink((tool, args) {
          if (tool == 'screenshot') return ok({'image_b64': base64Encode([1, 2, 3])});
          return ok();
        }));
    await c.capture();
    expect(c.shot, [1, 2, 3]);
    c.dispose();
  });

  test('failed tool surfaces note, never throws', () async {
    final c = ControlCtrl(api: () async => _FakeLink((tool, args) => {'ok': false, 'error': 'nope'}));
    await c.capture();
    expect(c.note, 'nope');
    expect(await c.openApp('brave'), 'nope');
    c.dispose();
  });
}

class _FakeLink implements ControlBackend {
  @override
  @override
  String get base => 'x';
  final Map<String, dynamic> Function(String, Map<String, dynamic>?) fn;
  _FakeLink(this.fn);

  @override
  Future<Map<String, dynamic>> tool(String tool,
          [Map<String, dynamic>? args]) async =>
      fn(tool, args ?? {});

  @override
  Future<Map<String, dynamic>> typeText(String text) async =>
      fn('type', {'text': text});

  @override
  Future<Map<String, dynamic>> pressKey(String key) async =>
      fn('press', {'key': key});
}
