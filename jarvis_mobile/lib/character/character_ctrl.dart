import 'dart:async';

import 'package:flutter/foundation.dart';

import '../core/link_api.dart';
import '../core/prefs.dart';
import 'models.dart';

const String kDefaultCharacter = 'jarvis';
const Duration kPollIdle = Duration(milliseconds: 1500);
const Duration kPollBusy = Duration(milliseconds: 250);

/// Bridge calls the controller needs; injectable for tests.
class CharacterApi {
  final Future<Map<String, dynamic>> Function() characters;
  final Future<Map<String, dynamic>> Function() character;
  final Future<Map<String, dynamic>> Function(String target, String source)
  switchTo;
  const CharacterApi({
    required this.characters,
    required this.character,
    required this.switchTo,
  });

  factory CharacterApi.fromLink(LinkApi api) => CharacterApi(
    characters: api.characters,
    character: api.character,
    switchTo: (t, s) => api.characterSwitch(t, s),
  );

  /// Builds a LinkApi from saved prefs on every call (host may change).
  factory CharacterApi.live() {
    Future<LinkApi> mk() async =>
        LinkApi(base: await Prefs.host(), token: await Prefs.token());
    Future<Map<String, dynamic>> run(
      Future<Map<String, dynamic>> Function(LinkApi) f,
    ) async {
      final api = await mk();
      if (api.base.isEmpty) return {'ok': false, 'error': 'not linked'};
      try {
        return await f(api);
      } finally {
        api.close();
      }
    }

    return CharacterApi(
      characters: () => run((a) => a.characters()),
      character: () => run((a) => a.character()),
      switchTo: (t, s) => run((a) => a.characterSwitch(t, s)),
    );
  }
}

/// Port of the web store (frontend/lib/character/store.ts). `active` is the
/// skin on screen and only moves via [commitVisual]; `backend` is what the
/// bridge reports. Boot is always jarvis.
class CharacterCtrl extends ChangeNotifier {
  final CharacterApi _api;
  final Timer Function(Duration, void Function()) _timer;
  final Future<void> Function(Duration) _delay;
  final int maxLoadTries;

  CharacterCtrl({
    CharacterApi? api,
    Timer Function(Duration, void Function())? timerFactory,
    Future<void> Function(Duration)? delay,
    this.maxLoadTries = 20,
  }) : _api = api ?? CharacterApi.live(),
       _timer = timerFactory ?? Timer.new,
       _delay = delay ?? Future.delayed;

  bool ready = false;
  String active = kDefaultCharacter;
  String backend = kDefaultCharacter;
  String? target;
  SwitchPhase phase = SwitchPhase.idle;
  int turn = 0;
  List<String> order = const [kDefaultCharacter];
  Map<String, CharacterInfo> characters = {};
  MotionTokens motion = MotionTokens.defaults;
  bool effects = true;
  bool reducedSetting = false;
  String? notice;

  /// Last character used (hint only; never applied at boot).
  String recent = '';

  String _visualId = kDefaultCharacter;
  int _seenTurn = 0;
  bool _engineBusy = false;
  int _stray = 0;
  bool _started = false;
  bool _disposed = false;
  Timer? _pollTimer;
  final _events = StreamController<SwitchEvent>.broadcast(sync: true);

  Stream<SwitchEvent> get events => _events.stream;

  void _set(void Function() f) {
    f();
    if (!_disposed) notifyListeners();
  }

  Future<void> start() async {
    if (_started) return;
    _started = true;
    try {
      effects = await Prefs.effects();
      reducedSetting = await Prefs.reducedMotion();
      recent = await Prefs.recentCharacter();
    } catch (_) {}
    if (!_started) return;
    notifyListeners();
    for (var i = 0; i < maxLoadTries && !ready && _started; i++) {
      final p = await _api.characters();
      if (!_started) return;
      final cs = p['characters'];
      if (cs is Map && cs.isNotEmpty && p['motion'] is Map) {
        final map = <String, CharacterInfo>{
          for (final e in cs.entries)
            '${e.key}': CharacterInfo.fromJson(e.value, '${e.key}'),
        };
        final ids = map.keys.toList();
        final rest = ids.where((x) => x != kDefaultCharacter).toList()..sort();
        _set(() {
          characters = map;
          order = [if (ids.contains(kDefaultCharacter)) kDefaultCharacter, ...rest];
          motion = MotionTokens.fromJson(p['motion']);
          ready = true;
        });
        break;
      }
      await _delay(const Duration(seconds: 2));
    }
    if (ready && _started) await pollOnce();
  }

  void stop() {
    _started = false;
    _pollTimer?.cancel();
    _pollTimer = null;
  }

  /// One poll of GET /character, then schedules the next.
  @visibleForTesting
  Future<void> pollOnce() async {
    final r = await _api.character();
    if (!_started) return;
    if (r['active'] is String && (r['active'] as String).isNotEmpty) {
      applySnapshot(SwitchSnapshot.fromJson(r));
    }
    _pollTimer?.cancel();
    _pollTimer = _timer(
      phase != SwitchPhase.idle ? kPollBusy : kPollIdle,
      () => unawaited(pollOnce()),
    );
  }

  void _emit(SwitchEvent e, {bool force = false}) {
    if (!force) {
      if (e.turn <= _seenTurn) return;
      _seenTurn = e.turn;
    }
    if (!_events.isClosed) _events.add(e);
  }

  @visibleForTesting
  void applySnapshot(SwitchSnapshot s, {String source = 'voice'}) {
    _set(() {
      target = s.target;
      phase = s.phase;
      turn = s.charTurn;
      backend = s.active;
    });
    final to = s.target != null && s.phase != SwitchPhase.rollback
        ? s.target!
        : s.active;
    if (s.charTurn > _seenTurn && to != _visualId) {
      _stray = 0;
      _emit(SwitchEvent(to, _visualId, s.charTurn, source));
      return;
    }
    if (s.charTurn > _seenTurn) _seenTurn = s.charTurn;
    if (s.phase == SwitchPhase.idle && s.active != _visualId && !_engineBusy) {
      if (++_stray >= 2) {
        _stray = 0;
        _emit(
          SwitchEvent(s.active, _visualId, s.charTurn, 'reconcile'),
          force: true,
        );
      }
    } else {
      _stray = 0;
    }
  }

  Future<void> requestSwitch(String target, [String source = 'ui']) async {
    final r = await _api.switchTo(target, source);
    if (r['ok'] == false && r['handled'] == null && r['switched'] == null) {
      _set(() => notice = 'Bridge unreachable.');
      return;
    }
    if (r['switched'] == false || r['handled'] == false) {
      final reply = r['reply'];
      _set(() => notice = reply is String ? reply : null);
      return;
    }
    final snap = r['character'];
    final t = r['char_turn'] ?? (snap is Map ? snap['char_turn'] : null);
    if (t is num) {
      final to = r['target'] is String ? r['target'] as String : target;
      _set(() => notice = null);
      _emit(SwitchEvent(to, _visualId, t.toInt(), source));
      if (snap is Map) {
        final s = SwitchSnapshot.fromJson(snap);
        _set(() {
          this.target = s.target;
          phase = s.phase;
          turn = t.toInt();
        });
      }
    }
  }

  /// A switch the bridge already accepted (typed chat reply).
  void noteSwitch(String target, int turn, [String source = 'text']) =>
      _emit(SwitchEvent(target, _visualId, turn, source));

  void setVisual(String id) => _visualId = id;

  /// Engine landed the transition: `active` follows.
  void commitVisual(String id) {
    _visualId = id;
    _set(() => active = id);
    unawaited(
      Prefs.setRecentCharacter(id).catchError((Object _) {}),
    );
  }

  void setEngineBusy(bool b) => _engineBusy = b;

  Future<void> setEffects(bool on) async {
    _set(() => effects = on);
    try {
      await Prefs.setEffects(on);
    } catch (_) {}
  }

  Future<void> setReducedSetting(bool on) async {
    _set(() => reducedSetting = on);
    try {
      await Prefs.setReducedMotion(on);
    } catch (_) {}
  }

  @override
  void dispose() {
    _disposed = true;
    stop();
    _events.close();
    super.dispose();
  }
}
