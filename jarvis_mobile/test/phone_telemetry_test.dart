import 'package:battery_plus/battery_plus.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/phone_telemetry.dart';

void main() {
  group('buildPhoneTelemetryPayload', () {
    test('passes through a normal level', () {
      expect(
        buildPhoneTelemetryPayload(battery: 76, charging: false),
        {'battery': 76, 'charging': false},
      );
    });

    test('marks charging', () {
      expect(
        buildPhoneTelemetryPayload(battery: 100, charging: true),
        {'battery': 100, 'charging': true},
      );
    });

    test('clamps out-of-range levels into 0..100', () {
      expect(
        buildPhoneTelemetryPayload(battery: 142, charging: false)['battery'],
        100,
      );
      expect(
        buildPhoneTelemetryPayload(battery: -8, charging: true)['battery'],
        0,
      );
    });

    test('payload has exactly the bridge keys', () {
      final payload =
          buildPhoneTelemetryPayload(battery: 50, charging: true);
      expect(payload.keys.toSet(), {'battery', 'charging'});
      expect(payload['battery'], isA<int>());
      expect(payload['charging'], isA<bool>());
    });
  });

  group('isChargingState', () {
    test('charging + full count as charging', () {
      expect(isChargingState(BatteryState.charging), isTrue);
      expect(isChargingState(BatteryState.full), isTrue);
    });

    test('everything else counts as unplugged', () {
      expect(isChargingState(BatteryState.discharging), isFalse);
      expect(isChargingState(BatteryState.connectedNotCharging), isFalse);
      expect(isChargingState(BatteryState.unknown), isFalse);
    });
  });

  group('suitPowerLabel', () {
    test('formats level + charging state', () {
      expect(
        suitPowerLabel(battery: 76, charging: false),
        'SUIT POWER 76%',
      );
      expect(
        suitPowerLabel(battery: 76, charging: true),
        'SUIT POWER 76% · CHARGING',
      );
    });

    test('unknown sensor renders dashes', () {
      expect(
        suitPowerLabel(battery: null, charging: false),
        'SUIT POWER --',
      );
    });
  });
}
