import 'dart:async';

import 'package:battery_plus/battery_plus.dart';

/// Phone battery telemetry → POST /phone/telemetry {battery, charging}.
///
/// Payload builders here are pure (unit-tested); platform reads and HTTP
/// live in [PhoneTelemetry] / [LinkApi.phoneTelemetry]. Everything fails
/// silently — a missing battery sensor or unreachable bridge must never
/// surface UI errors.

/// Build the bridge payload. Clamps [battery] into 0..100.
Map<String, dynamic> buildPhoneTelemetryPayload({
  required int battery,
  required bool charging,
}) => {
      'battery': battery.clamp(0, 100),
      'charging': charging,
    };

/// True when the device is on external power (topped-up counts as charging).
bool isChargingState(BatteryState state) =>
    state == BatteryState.charging || state == BatteryState.full;

/// Mono HUD label, e.g. "SUIT POWER 76%" / "SUIT POWER 76% · CHARGING".
/// Null [battery] (sensor unreadable) renders as "SUIT POWER --".
String suitPowerLabel({required int? battery, required bool charging}) {
  final level = battery == null ? '--' : '${battery.clamp(0, 100)}%';
  return charging ? 'SUIT POWER $level · CHARGING' : 'SUIT POWER $level';
}

/// Reads the real battery via battery_plus and pushes it to the bridge
/// on start, every 60s, and immediately when charging state changes.
/// Fail-silent by design: every await is guarded, no UI surface.
class PhoneTelemetry {
  final Future<BatteryLevel> Function() _read;
  final Future<void> Function(int battery, bool charging) _send;
  final void Function(int? battery, bool charging)? onUpdate;

  Timer? _poll;
  StreamSubscription<BatteryState>? _sub;
  bool _started = false;

  PhoneTelemetry._(this._read, this._send, {this.onUpdate});

  /// Wire to the real sensor + bridge. [apiProvider] must return a
  /// configured LinkApi (same base URL + Bearer token as other calls).
  factory PhoneTelemetry.live({
    required Future<PhoneTelemetryTarget> Function() apiProvider,
    Battery? battery,
    void Function(int? level, bool charging)? onUpdate,
  }) {
    final sensor = battery ?? Battery();
    return PhoneTelemetry._(
      () async {
        final level = await sensor.batteryLevel;
        final state = await sensor.batteryState;
        return (level, isChargingState(state));
      },
      (level, charging) async {
        final api = await apiProvider();
        if (api.base.isEmpty) return;
        await api.phoneTelemetry(battery: level, charging: charging);
      },
      onUpdate: onUpdate,
    );
  }

  /// Injectable seam for tests / previews (no platform channels).
  factory PhoneTelemetry.fake({
    required Future<BatteryLevel> Function() read,
    required Future<void> Function(int battery, bool charging) send,
    void Function(int? level, bool charging)? onUpdate,
  }) =>
      PhoneTelemetry._(read, send, onUpdate: onUpdate);

  Stream<BatteryState> get _states => Battery().onBatteryStateChanged;

  /// Send once now, then every 60s + on charging-state flips.
  void start() {
    if (_started) return;
    _started = true;
    _push();
    _poll = Timer.periodic(const Duration(seconds: 60), (_) => _push());
    try {
      _sub = _states.listen((state) => _push(chargingHint: isChargingState(state)));
    } catch (_) {
      // Desktop/test platforms without a battery event channel.
    }
  }

  /// Re-read + report. Errors are swallowed (fail silently).
  Future<void> _push({bool? chargingHint}) async {
    try {
      final (level, charging) = await _read();
      final clamped = level.clamp(0, 100);
      final isCharging = chargingHint ?? charging;
      try {
        await _send(clamped, isCharging);
      } catch (_) {
        // Bridge unreachable — silent.
      }
      try {
        onUpdate?.call(clamped, isCharging);
      } catch (_) {
        // UI callback must never break reporting.
      }
    } catch (_) {
      // Sensor unreadable — silent, keep previous HUD value.
    }
  }

  /// Manual refresh (e.g. RESYNC tile).
  Future<void> refresh() => _push();

  void dispose() {
    _poll?.cancel();
    _sub?.cancel();
    _started = false;
  }
}

/// Bridge endpoint handle (LinkApi) — structural to avoid an import cycle.
abstract class PhoneTelemetryTarget {
  String get base;
  Future<Map<String, dynamic>> phoneTelemetry(
      {required int battery, required bool charging});
}

/// Sensor snapshot: (level 0-100, charging).
typedef BatteryLevel = (int level, bool charging);
