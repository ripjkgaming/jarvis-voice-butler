// Character-switch data models. Parsing is tolerant: missing or wrong-typed
// keys fall back to defaults and nothing here ever throws.

Map<String, dynamic> _map(Object? v) => v is Map
    ? v.map((k, x) => MapEntry('$k', x))
    : <String, dynamic>{};
String _str(Object? v, [String d = '']) => v is String ? v : d;
int _int(Object? v, int d) => v is num ? v.toInt() : d;
double _dbl(Object? v, double d) => v is num ? v.toDouble() : d;

class CharacterInfo {
  final String id;
  final String displayName;
  final String spokenName;
  final String address;
  final Map<String, List<String>> lines;
  final Map<String, dynamic> themeTokens;
  final String layout;
  final String signature;
  final double intensity;
  final List<String> wakeNames;

  const CharacterInfo({
    required this.id,
    this.displayName = '',
    this.spokenName = '',
    this.address = '',
    this.lines = const {},
    this.themeTokens = const {},
    this.layout = '',
    this.signature = '',
    this.intensity = 1,
    this.wakeNames = const [],
  });

  factory CharacterInfo.fromJson(Object? raw, [String fallbackId = '']) {
    final j = _map(raw);
    final id = _str(j['id'], fallbackId);
    final lines = <String, List<String>>{};
    _map(j['lines']).forEach((k, v) {
      if (v is List) lines[k] = v.whereType<String>().toList();
    });
    final wake = j['wake_names'];
    return CharacterInfo(
      id: id,
      displayName: _str(j['display_name'], id),
      spokenName: _str(j['spoken_name'], id),
      address: _str(j['address']),
      lines: lines,
      themeTokens: _map(j['theme_tokens']),
      layout: _str(j['layout']),
      signature: _str(j['signature']),
      intensity: _dbl(j['intensity'], 1),
      wakeNames: wake is List ? wake.whereType<String>().toList() : const [],
    );
  }
}

/// _motion.yaml `particles`: device-class caps for the shared particle system.
class ParticleTokens {
  final double capDesktop, capLow, retintMs, sizeSteps;
  final int lowCores;
  const ParticleTokens({
    this.capDesktop = 1,
    this.capLow = .45,
    this.lowCores = 4,
    this.retintMs = 120,
    this.sizeSteps = 4,
  });

  factory ParticleTokens.fromJson(Object? raw) {
    final j = _map(raw);
    const d = ParticleTokens();
    return ParticleTokens(
      capDesktop: _dbl(j['cap_desktop'], d.capDesktop),
      capLow: _dbl(j['cap_low'], d.capLow),
      lowCores: _int(j['low_cores'], d.lowCores),
      retintMs: _dbl(j['retint_ms'], d.retintMs),
      sizeSteps: _dbl(j['size_steps'], d.sizeSteps),
    );
  }

  /// Count multiplier ceiling for a device with [cores] logical processors.
  double cap(int cores) => cores <= lowCores ? capLow : capDesktop;
}

/// _motion.yaml `glow`: shader layer budget.
class GlowTokens {
  final double bufferScale, budgetMs;
  final int budgetFrames;
  const GlowTokens({this.bufferScale = .5, this.budgetMs = 2, this.budgetFrames = 90});

  factory GlowTokens.fromJson(Object? raw) {
    final j = _map(raw);
    const d = GlowTokens();
    return GlowTokens(
      bufferScale: _dbl(j['buffer_scale'], d.bufferScale),
      budgetMs: _dbl(j['budget_ms'], d.budgetMs),
      budgetFrames: _int(j['budget_frames'], d.budgetFrames),
    );
  }
}

/// _motion.yaml `layouts[name]`: one HUD arrangement preset.
class LayoutSpec {
  final String flow; // 'columns' | 'cards'
  final List<double> columns;
  final int cards;
  final double topH, gap, reactorScale;
  final bool summary;
  const LayoutSpec({
    this.flow = 'columns',
    this.columns = const [21, 54, 25],
    this.cards = 0,
    this.topH = 64,
    this.gap = 18,
    this.reactorScale = 1,
    this.summary = false,
  });

  static const Map<String, LayoutSpec> defaults = {
    'hud_round': LayoutSpec(),
    'hud_angular': LayoutSpec(
        columns: [15, 59, 26], topH: 48, gap: 8, reactorScale: 1.12),
    'hud_glass': LayoutSpec(
        flow: 'cards', cards: 4, topH: 60, gap: 14, reactorScale: .85, summary: true),
  };

  factory LayoutSpec.fromJson(Object? raw, LayoutSpec d) {
    final j = _map(raw);
    final cols = j['columns'];
    return LayoutSpec(
      flow: _str(j['flow'], d.flow),
      columns: cols is List && cols.length == 3 && cols.every((e) => e is num)
          ? cols.map((e) => (e as num).toDouble()).toList()
          : d.columns,
      cards: _int(j['cards'], d.cards),
      topH: _dbl(j['top_h'], d.topH),
      gap: _dbl(j['gap'], d.gap),
      reactorScale: _dbl(j['reactor_scale'], d.reactorScale),
      summary: j['summary'] is bool ? j['summary'] as bool : d.summary,
    );
  }
}

class MotionTokens {
  final int totalMs;
  final int beatMs;
  final int reducedMs;
  final int readyGateMs;
  final int fastForwardMs;
  final double maxFlashHz;
  final double maxShakePx;
  final double maxGlitchAlpha;
  final Map<String, List<double>> easing;

  /// Each value is [start,end] ms, or a single-element list for a point.
  final Map<String, List<double>> phases;
  final Map<String, Map<String, List<double>>> signatures;
  final ParticleTokens particles;
  final GlowTokens glow;
  final Map<String, LayoutSpec> layouts;

  const MotionTokens({
    required this.totalMs,
    required this.beatMs,
    required this.reducedMs,
    required this.readyGateMs,
    required this.fastForwardMs,
    required this.maxFlashHz,
    required this.maxShakePx,
    required this.maxGlitchAlpha,
    required this.easing,
    required this.phases,
    required this.signatures,
    this.particles = const ParticleTokens(),
    this.glow = const GlowTokens(),
    this.layouts = LayoutSpec.defaults,
  });

  static const Map<String, List<double>> _easing = {
    'out_expo': [.16, 1, .3, 1],
    'in_out_cubic': [.65, 0, .35, 1],
    'snap': [.9, 0, .1, 1],
    'glide': [.22, .61, .36, 1],
  };
  static const Map<String, List<double>> _phases = {
    'out_chrome': [0, 200],
    'out_panels': [80, 320],
    'out_text': [160, 380],
    'out_orb': [240, 500],
    'palette': [150, 900],
    'signature': [500, 1200],
    'in_chrome': [900, 1200],
    'in_panels': [1000, 1400],
    'in_text': [1200, 1500],
    'in_orb': [1100, 1500],
    'greeting_at': [1100],
    'whoosh': [500, 1300],
  };

  static const MotionTokens defaults = MotionTokens(
    totalMs: 1800,
    beatMs: 700,
    reducedMs: 200,
    readyGateMs: 1000,
    fastForwardMs: 150,
    maxFlashHz: 3,
    maxShakePx: 6,
    maxGlitchAlpha: .55,
    easing: _easing,
    phases: _phases,
    signatures: {},
  );

  /// Start of a phase (or its point value) in ms.
  double phaseStart(String key) => (phases[key] ?? const [0]).first;
  double phaseEnd(String key) => (phases[key] ?? const [0]).last;

  static List<double>? _nums(Object? v) {
    if (v is num) return [v.toDouble()];
    if (v is List && v.isNotEmpty && v.every((e) => e is num)) {
      return v.map((e) => (e as num).toDouble()).toList();
    }
    return null;
  }

  factory MotionTokens.fromJson(Object? raw) {
    final j = _map(raw);
    const d = defaults;
    final easing = {...d.easing};
    _map(j['easing']).forEach((k, v) {
      final n = _nums(v);
      if (n != null && n.length == 4) easing[k] = n;
    });
    final phases = {...d.phases};
    _map(j['phases']).forEach((k, v) {
      final n = _nums(v);
      if (n != null) phases[k] = n;
    });
    final sigs = <String, Map<String, List<double>>>{};
    _map(j['signatures']).forEach((s, v) {
      final m = <String, List<double>>{};
      _map(v).forEach((k, x) {
        final n = _nums(x);
        if (n != null) m[k] = n;
      });
      sigs[s] = m;
    });
    return MotionTokens(
      totalMs: _int(j['total_ms'], d.totalMs),
      beatMs: _int(j['beat_ms'], d.beatMs),
      reducedMs: _int(j['reduced_ms'], d.reducedMs),
      readyGateMs: _int(j['ready_gate_ms'], d.readyGateMs),
      fastForwardMs: _int(j['fast_forward_ms'], d.fastForwardMs),
      maxFlashHz: _dbl(j['max_flash_hz'], d.maxFlashHz),
      maxShakePx: _dbl(j['max_shake_px'], d.maxShakePx),
      maxGlitchAlpha: _dbl(j['max_glitch_alpha'], d.maxGlitchAlpha),
      easing: easing,
      phases: phases,
      signatures: sigs,
      particles: ParticleTokens.fromJson(j['particles']),
      glow: GlowTokens.fromJson(j['glow']),
      layouts: {
        for (final e in LayoutSpec.defaults.entries)
          e.key: LayoutSpec.fromJson(_map(j['layouts'])[e.key], e.value),
      },
    );
  }
}

enum SwitchPhase {
  idle,
  preparing,
  exiting,
  signature,
  entering,
  settling,
  rollback;

  static SwitchPhase parse(Object? v) {
    if (v is String) {
      final s = v.toLowerCase();
      for (final p in SwitchPhase.values) {
        if (p.name == s) return p;
      }
    }
    return SwitchPhase.idle;
  }
}

class SwitchSnapshot {
  final String active;
  final String? target;
  final SwitchPhase phase;
  final int charTurn;
  final List<String> characters;

  const SwitchSnapshot({
    this.active = 'jarvis',
    this.target,
    this.phase = SwitchPhase.idle,
    this.charTurn = 0,
    this.characters = const [],
  });

  factory SwitchSnapshot.fromJson(Object? raw) {
    final j = _map(raw);
    final t = j['target'];
    final c = j['characters'];
    return SwitchSnapshot(
      active: _str(j['active'], 'jarvis'),
      target: t is String && t.isNotEmpty ? t : null,
      phase: SwitchPhase.parse(j['phase']),
      charTurn: _int(j['char_turn'], 0),
      characters: c is List ? c.whereType<String>().toList() : const [],
    );
  }
}

class SwitchEvent {
  final String to;
  final String from;
  final int turn;
  final String source;
  const SwitchEvent(this.to, this.from, this.turn, this.source);

  @override
  String toString() => 'SwitchEvent($from->$to #$turn $source)';
}
