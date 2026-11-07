/**
 * Synthesised sound effects for the HUD load-in (use-hud-intro.ts). Pure Web
 * Audio, no asset files. Every cue is scheduled from the same millisecond
 * plan that drives the visuals, so sound and picture share one timeline.
 *
 * Never throws: every entry point swallows errors. Autoplay policy: the
 * context is resumed on start and again on the first pointer / key event; a
 * run that could not start in time is scheduled from where the visuals have
 * got to (or dropped if the intro is already over).
 */

export type IntroSfxBox = {
  /** ms from intro start to this box's descent. */
  at: number;
  d1: number; // descend
  d2: number; // slide
  d3: number; // expand
  glitch: number; // glitch-in
  /** -1 slides left, 1 right, 0 none. */
  dir: number;
};

export type IntroSfxPlan = {
  boxes: IntroSfxBox[];
  /** ms when the last box has finished its glitch-in. */
  boxesEnd: number;
  /** ms when the single dot starts to descend, and how long until it lands
   *  on the reactor centre. */
  orbAt: number;
  dotMs: number;
  /** One entry per orb layer that unfolds: start (ms) and layer index 0..7
   *  (pitch climbs with the index). */
  layers: { at: number; idx: number }[];
  /** ms of the final shockwave + settle flare. */
  flareAt: number;
  /** ms when the whole intro is over (late audio is dropped after this). */
  endAt: number;
  /** Background spawn: grid scan start/length, radial ripple starts. */
  bg: { scanAt: number; scanMs: number; ripples: number[] } | null;
  /** 'wake' = return from hidden: opens on the recorded wake chirp
   *  (public/sfx/hud-wake.mp3) and swaps the box / layer ticks for chirps
   *  cut from the same sample and synthesised to match it. */
  mode?: 'boot' | 'wake';
};

const PREF_KEY = 'jarvis.introSfx';
const MASTER_GAIN = 0.25;

let enabled = readPref();
let ctx: AudioContext | null = null;
let master: GainNode | null = null;
let noiseBuf: AudioBuffer | null = null;
/** Recorded wake chirp (~0.24 s digital blip, 3.4 + 5 kHz rising to 6.6 kHz). */
const WAKE_URL = '/sfx/hud-wake.mp3';
let wakeBuf: AudioBuffer | null = null;
let wakeLoading = false;
let armed = false;
let pending: { plan: IntroSfxPlan; startedAt: number; run: Run } | null = null;
let current: Run | null = null;

type Run = { stopped: boolean; teardown: (() => void) | null };

function readPref(): boolean {
  try {
    return typeof localStorage === 'undefined' || localStorage.getItem(PREF_KEY) !== '0';
  } catch {
    return true;
  }
}

/** Mute / unmute the intro sounds (persisted per browser profile). */
export function setIntroSfxEnabled(on: boolean): void {
  enabled = on;
  try {
    localStorage.setItem(PREF_KEY, on ? '1' : '0');
  } catch {
    /* storage unavailable */
  }
  if (!on) stopRun(current);
}

export function isIntroSfxEnabled(): boolean {
  return enabled;
}

function getCtx(): AudioContext | null {
  try {
    if (ctx) return ctx;
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    ctx = new Ctor();
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -14;
    comp.ratio.value = 6;
    master = ctx.createGain();
    master.gain.value = MASTER_GAIN;
    master.connect(comp);
    comp.connect(ctx.destination);
    const len = Math.floor(ctx.sampleRate * 2);
    noiseBuf = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = noiseBuf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    loadWake(ctx);
    return ctx;
  } catch {
    return null;
  }
}

function loadWake(c: AudioContext): void {
  if (wakeBuf || wakeLoading || typeof fetch === 'undefined') return;
  wakeLoading = true;
  void fetch(WAKE_URL)
    .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(String(r.status)))))
    .then((ab) => c.decodeAudioData(ab))
    .then((b) => {
      wakeBuf = b;
    })
    .catch(() => {
      wakeLoading = false; // retry on the next context use
    });
}

/** Play the recorded chirp at `rate` (pitch + speed). Returns false if not loaded. */
function playWakeSample(
  c: AudioContext,
  dest: AudioNode,
  t: number,
  rate: number,
  peak: number,
  srcs?: AudioScheduledSourceNode[]
): boolean {
  if (!wakeBuf) return false;
  const s = c.createBufferSource();
  s.buffer = wakeBuf;
  s.playbackRate.value = rate;
  const g = c.createGain();
  g.gain.value = peak;
  s.connect(g);
  g.connect(dest);
  s.start(t);
  srcs?.push(s);
  return true;
}

/**
 * Synth cousin of the wake sample: two detuned partials (ratio ~0.69, like
 * its 3.4 / 5 kHz pair) gated by a fast digital flutter, holding then sweeping
 * up by `rise` in the back half. `f` is the upper partial in Hz.
 */
function playChirp(
  c: AudioContext,
  dest: AudioNode,
  t: number,
  f: number,
  peak: number,
  opts: { dur?: number; rise?: number; flutter?: number; pan?: number } = {},
  srcs?: AudioScheduledSourceNode[]
): void {
  const dur = opts.dur ?? 0.24;
  const rise = opts.rise ?? 1.33;
  let out: AudioNode = dest;
  if (opts.pan && c.createStereoPanner) {
    const sp = c.createStereoPanner();
    sp.pan.value = Math.max(-1, Math.min(1, opts.pan));
    sp.connect(dest);
    out = sp;
  }
  const amp = c.createGain();
  amp.gain.setValueAtTime(0.0001, t);
  amp.gain.exponentialRampToValueAtTime(peak, t + 0.012);
  amp.gain.setValueAtTime(peak, t + dur * 0.45);
  amp.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  // flutter: square LFO into a gain gives the stepped "data" texture
  const gate = c.createGain();
  gate.gain.value = 0.65;
  const lfo = c.createOscillator();
  lfo.type = 'square';
  lfo.frequency.value = opts.flutter ?? 86;
  const depth = c.createGain();
  depth.gain.value = 0.35;
  lfo.connect(depth);
  depth.connect(gate.gain);
  gate.connect(amp);
  amp.connect(out);
  [
    [f, 1, 'sine'],
    [f * 0.69, 0.7, 'triangle'],
    [f * 1.34, 0.25, 'sine'],
  ].forEach(([hz, lvl, type]) => {
    const o = c.createOscillator();
    o.type = type as OscillatorType;
    o.frequency.setValueAtTime(hz as number, t);
    o.frequency.setValueAtTime(hz as number, t + dur * 0.45);
    o.frequency.exponentialRampToValueAtTime((hz as number) * rise, t + dur);
    const g = c.createGain();
    g.gain.value = lvl as number;
    o.connect(g);
    g.connect(gate);
    o.start(t);
    o.stop(t + dur + 0.03);
    srcs?.push(o);
  });
  lfo.start(t);
  lfo.stop(t + dur + 0.03);
  srcs?.push(lfo);
}

function stopRun(run: Run | null): void {
  if (!run) return;
  run.stopped = true;
  try {
    run.teardown?.();
  } catch {
    /* ignore */
  }
  run.teardown = null;
  if (current === run) current = null;
  if (pending?.run === run) pending = null;
}

/** First user gesture: resume the context and start any run that was waiting. */
function arm(): void {
  if (armed || typeof window === 'undefined') return;
  armed = true;
  const events = ['pointerdown', 'keydown', 'touchstart'] as const;
  const onGesture = () => {
    try {
      void ctx
        ?.resume()
        .then(() => {
          if (ctx?.state === 'running') {
            events.forEach((e) => window.removeEventListener(e, onGesture));
            armed = false;
          }
          flush();
        })
        .catch(() => undefined);
    } catch {
      /* ignore */
    }
  };
  events.forEach((e) => window.addEventListener(e, onGesture, { passive: true }));
}

function flush(): void {
  try {
    const p = pending;
    if (!p || !ctx || ctx.state !== 'running') return;
    pending = null;
    if (p.run.stopped) return;
    const elapsed = (performance.now() - p.startedAt) / 1000;
    if (elapsed * 1000 > p.plan.endAt + 600) return;
    schedule(p.plan, elapsed, p.run);
  } catch {
    /* ignore */
  }
}

/**
 * Start the intro soundtrack. Returns a stop function (fades out and silences
 * everything still scheduled). Safe to call when audio is unavailable.
 */
export function playIntroSfx(plan: IntroSfxPlan): () => void {
  const run: Run = { stopped: false, teardown: null };
  try {
    if (!enabled || typeof window === 'undefined') return () => undefined;
    stopRun(current);
    const c = getCtx();
    if (!c) return () => undefined;
    current = run;
    arm();
    pending = { plan, startedAt: performance.now(), run };
    if (c.state === 'running') flush();
    else
      void c
        .resume()
        .then(flush)
        .catch(() => undefined);
  } catch {
    /* fail silently */
  }
  return () => stopRun(run);
}

/* ------------------------------------------------------------ synthesis */

function schedule(plan: IntroSfxPlan, elapsed: number, run: Run): void {
  const c = ctx;
  const out = master;
  const nb = noiseBuf;
  if (!c || !out || !nb) return;
  const bus = c.createGain();
  bus.connect(out);
  const srcs: AudioScheduledSourceNode[] = [];
  run.teardown = () => {
    const now = c.currentTime;
    bus.gain.cancelScheduledValues(now);
    bus.gain.setValueAtTime(bus.gain.value, now);
    bus.gain.linearRampToValueAtTime(0, now + 0.08);
    srcs.forEach((s) => {
      try {
        s.stop(now + 0.1);
      } catch {
        /* already stopped */
      }
    });
    setTimeout(() => {
      try {
        bus.disconnect();
      } catch {
        /* ignore */
      }
    }, 300);
  };

  const base = c.currentTime + 0.03;
  /** Context time for a plan time in ms; null when it is already past. */
  const when = (ms: number, slackS = 0.05): number | null => {
    const s = ms / 1000 - elapsed;
    return s < -slackS ? null : base + Math.max(0, s);
  };

  const env = (g: GainNode, t: number, a: number, peak: number, d: number) => {
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), t + a);
    g.gain.exponentialRampToValueAtTime(0.0001, t + a + Math.max(0.01, d));
  };

  const pan = (dest: AudioNode, p: number): AudioNode => {
    if (!p || !c.createStereoPanner) return dest;
    const sp = c.createStereoPanner();
    sp.pan.value = Math.max(-1, Math.min(1, p));
    sp.connect(dest);
    return sp;
  };

  const osc = (
    type: OscillatorType,
    f0: number,
    f1: number,
    t: number,
    dur: number,
    peak: number,
    attack: number,
    dest: AudioNode = bus
  ) => {
    const o = c.createOscillator();
    o.type = type;
    o.frequency.setValueAtTime(Math.max(1, f0), t);
    o.frequency.exponentialRampToValueAtTime(Math.max(1, f1), t + dur);
    const g = c.createGain();
    env(g, t, attack, peak, dur - attack);
    o.connect(g);
    g.connect(dest);
    o.start(t);
    o.stop(t + dur + 0.05);
    srcs.push(o);
  };

  const noise = (
    t: number,
    dur: number,
    kind: BiquadFilterType,
    f0: number,
    f1: number,
    q: number,
    peak: number,
    attack: number,
    dest: AudioNode = bus,
    crush = 0
  ) => {
    const s = c.createBufferSource();
    s.buffer = nb;
    s.loop = true;
    const f = c.createBiquadFilter();
    f.type = kind;
    f.Q.value = q;
    f.frequency.setValueAtTime(Math.max(20, f0), t);
    f.frequency.exponentialRampToValueAtTime(Math.max(20, f1), t + dur);
    const g = c.createGain();
    env(g, t, attack, peak, dur - attack);
    let node: AudioNode = s;
    if (crush > 0) {
      const ws = c.createWaveShaper();
      const curve = new Float32Array(256);
      for (let i = 0; i < 256; i++) {
        const x = (i / 255) * 2 - 1;
        curve[i] = Math.round(x * crush) / crush;
      }
      ws.curve = curve;
      s.connect(ws);
      node = ws;
    }
    node.connect(f);
    f.connect(g);
    g.connect(dest);
    s.start(t, Math.random() * 1.5);
    s.stop(t + dur + 0.05);
    srcs.push(s);
  };

  const wake = plan.mode === 'wake';

  // Wake from hidden: the recorded chirp opens it, a lower cousin answers.
  if (wake) {
    const w0 = when(0, 0.3);
    if (w0 !== null) {
      if (!playWakeSample(c, bus, w0, 1, 1.1, srcs)) playChirp(c, bus, w0, 4950, 0.09, {}, srcs);
      playChirp(c, bus, w0 + 0.16, 3300, 0.035, { dur: 0.3, rise: 1.5, flutter: 64 }, srcs);
    }
  }

  // Sub-rumble + riser under the whole descent.
  const t0 = when(0, 10);
  if (t0 !== null) {
    const dur = Math.max(0.5, plan.boxesEnd / 1000 - elapsed + 0.3);
    osc('sine', 30, 58, t0, dur, 0.55, 0.5);
    const lp = c.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.value = 170;
    lp.connect(bus);
    osc('sawtooth', 46, 82, t0, dur, 0.16, 0.6, lp);
    noise(t0, dur, 'bandpass', 140, 1100, 1.2, 0.05, dur * 0.6);
  }

  plan.boxes.forEach((b, i) => {
    const jit = (i % 5) * 0.012;
    const down = when(b.at);
    if (down !== null) noise(down, b.d1 / 1000, 'bandpass', 2000, 520, 2.5, 0.045, 0.05);

    const land = when(b.at + b.d1);
    if (land !== null) {
      const lp = c.createBiquadFilter();
      lp.type = 'lowpass';
      lp.frequency.value = 2400;
      lp.connect(bus);
      osc('sawtooth', 300 + (i % 6) * 55, 980 + (i % 4) * 90, land + jit, 0.17, 0.06, 0.012, lp);
      noise(land + jit, 0.05, 'highpass', 5200, 5200, 0.7, 0.05, 0.004);
    }

    const slide = when(b.at + b.d1);
    if (slide !== null && b.dir !== 0) {
      noise(slide, b.d2 / 1000, 'bandpass', 560, 2700, 5, 0.1, b.d2 / 2000, pan(bus, b.dir * 0.75));
    }

    const expand = when(b.at + b.d1 + b.d2);
    if (expand !== null) osc('triangle', 520, 1500, expand, 0.14, 0.08, 0.01);

    const gl = when(b.at + b.d1 + b.d2 + b.d3);
    if (gl !== null && wake) {
      // each card glitches in on a chirp: sample re-pitched, synth on alternates
      noise(gl, Math.min(0.12, b.glitch / 4000), 'highpass', 2400, 2400, 0.8, 0.06, 0.004, bus, 5);
      const rate = 0.82 + (i % 5) * 0.11;
      if (i % 2 === 1 || !playWakeSample(c, pan(bus, b.dir * 0.5), gl, rate, 0.45, srcs))
        playChirp(c, bus, gl, 4200 + (i % 4) * 420, 0.03, { dur: 0.16, pan: b.dir * 0.5 }, srcs);
    } else if (gl !== null) {
      noise(gl, Math.min(0.2, b.glitch / 3000), 'highpass', 1800, 1800, 0.8, 0.13, 0.004, bus, 5);
      [1900, 2700, 1200, 3200].forEach((f, k) =>
        osc('square', f, f * 0.96, gl + k * 0.035, 0.03, 0.03, 0.004)
      );
    }
  });

  // Background spawn: a low sweep with ticks while the grid scan draws down,
  // and a radial blip of ticks for each ripple.
  const bg = plan.bg;
  if (bg) {
    const sc = when(bg.scanAt);
    const sd = bg.scanMs / 1000;
    if (sc !== null) {
      osc('sine', 52, 150, sc, sd + 0.2, 0.26, 0.25);
      noise(sc, sd, 'bandpass', 180, 2200, 3, 0.03, sd * 0.5);
      for (let k = 0; k < 9; k++) {
        const tk = when(bg.scanAt + (k / 8) * bg.scanMs);
        if (tk !== null) osc('square', 1500 + k * 140, 1400 + k * 140, tk, 0.02, 0.025, 0.003);
      }
    }
    bg.ripples.forEach((at, r) => {
      const rt = when(at);
      if (rt === null) return;
      osc('sine', 120 - r * 20, 46, rt, 0.6, 0.3 + r * 0.1, 0.01);
      noise(rt, 0.45, 'bandpass', 400, 2600, 2, 0.03, 0.12);
      for (let k = 0; k < 6; k++)
        osc('square', 2600 - k * 260, 2500 - k * 260, rt + 0.05 + k * 0.07, 0.018, 0.02, 0.003);
    });
  }

  // The wake-up: a tiny spark at the nucleus, then a spin-up whir that rises
  // as the static emblem starts to move (replaces the old dot descent).
  const dotS = plan.dotMs / 1000;
  const dd = when(plan.orbAt);
  if (dd !== null) {
    osc('sine', 1800, 2400, dd, dotS, 0.035, dotS * 0.5);
    const lp = c.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.setValueAtTime(220, dd);
    lp.frequency.exponentialRampToValueAtTime(1400, dd + dotS + 1.6);
    lp.connect(bus);
    osc('sawtooth', 48, 190, dd, dotS + 1.7, 0.12, dotS * 0.8, lp);
    osc('sine', 220, 1320, dd + dotS * 0.5, dotS + 1.5, 0.045, 0.5);
    noise(dd, dotS + 1.2, 'bandpass', 400, 3200, 3, 0.02, dotS * 1.2);
  }

  // Hand-over ping: the live nucleus takes over from the spark.
  const land = when(plan.orbAt + plan.dotMs);
  if (land !== null) {
    osc('sine', 3136, 3090, land, 0.6, 0.06, 0.004);
    osc('sine', 62, 44, land, 0.3, 0.16, 0.005);
  }

  // Each layer unfolding: a stacked chime plus a servo whirr, pitch climbing.
  const SCALE = [1, 9 / 8, 5 / 4, 3 / 2, 5 / 3, 2, 9 / 4, 5 / 2];
  plan.layers.forEach((l, k) => {
    const lt = when(l.at);
    if (lt === null) return;
    const f = 262 * (SCALE[Math.min(SCALE.length - 1, l.idx)] ?? 1);
    osc('triangle', f, f * 1.01, lt, 0.7, 0.07, 0.008);
    osc('sine', f * 2, f * 2, lt + 0.015, 0.55, 0.035, 0.01);
    osc('sine', f * 3, f * 3, lt + 0.03, 0.4, 0.015 + k * 0.001, 0.01);
    const lp = c.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.setValueAtTime(260, lt);
    lp.frequency.exponentialRampToValueAtTime(900 + l.idx * 180, lt + 0.3);
    lp.connect(bus);
    osc('sawtooth', f / 4, f / 2, lt, 0.3, 0.05, 0.05, lp);
    noise(lt, 0.12, 'highpass', 4200, 4200, 0.7, 0.03, 0.004);
    if (wake) playWakeSample(c, bus, lt + 0.02, 0.7 + l.idx * 0.09, 0.22, srcs);
  });

  // Shockwave + settle flare: bass impact and shimmer.
  const hit = when(plan.flareAt);
  if (hit !== null) {
    osc('sine', 96, 30, hit, 0.95, 1.0, 0.004);
    osc('sine', 44, 38, hit, 1.5, 0.5, 0.01);
    noise(hit, 0.5, 'lowpass', 220, 90, 0.7, 0.55, 0.003);
    [2093, 2637, 3136, 3951, 5274].forEach((f, k) =>
      osc('sine', f * (1 + k * 0.002), f * 0.995, hit + 0.02 + k * 0.025, 1.9, 0.035, 0.03)
    );
    if (wake) {
      // full HUD reached: chirp ladder up, then the sample an octave-ish up
      [3300, 4400, 5600].forEach((f, k) =>
        playChirp(c, bus, hit + 0.12 + k * 0.09, f, 0.03, { dur: 0.18, rise: 1.2 }, srcs)
      );
      playWakeSample(c, bus, hit + 0.42, 1.25, 0.6, srcs);
    }
  }
}

/* ----------------------------------------------------- presence (appear / fade) */

export type PresenceSfxKind = 'appear' | 'fade' | 'settle';

/**
 * Modest cues for the presence mode (use-hud-presence.ts): a rising shimmer as
 * Jarvis appears from the wallpaper (stretched to `ms`, the expected load
 * time), a soft down-sweep as he dissolves, and a small settle tick when the
 * appearance completes. Never throws; silent when the intro sounds are muted.
 */
export function playPresenceSfx(kind: PresenceSfxKind, ms = 2500): void {
  try {
    if (!enabled || typeof window === 'undefined') return;
    const c = getCtx();
    const out = master;
    const nb = noiseBuf;
    if (!c || !out || !nb) return;
    const go = () => {
      try {
        const t = c.currentTime + 0.03;
        const dur = Math.max(0.6, Math.min(6, ms / 1000));
        const bus = c.createGain();
        bus.gain.value = 0.7;
        bus.connect(out);
        const srcs: AudioScheduledSourceNode[] = [];
        const env = (g: GainNode, at: number, a: number, peak: number, d: number) => {
          g.gain.setValueAtTime(0.0001, at);
          g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), at + a);
          g.gain.exponentialRampToValueAtTime(0.0001, at + a + Math.max(0.01, d));
        };
        const tone = (
          type: OscillatorType,
          f0: number,
          f1: number,
          at: number,
          d: number,
          peak: number,
          attack: number
        ) => {
          const o = c.createOscillator();
          o.type = type;
          o.frequency.setValueAtTime(Math.max(1, f0), at);
          o.frequency.exponentialRampToValueAtTime(Math.max(1, f1), at + d);
          const g = c.createGain();
          env(g, at, attack, peak, d - attack);
          o.connect(g);
          g.connect(bus);
          o.start(at);
          o.stop(at + d + 0.05);
          srcs.push(o);
        };
        const hiss = (
          at: number,
          d: number,
          f0: number,
          f1: number,
          peak: number,
          attack: number
        ) => {
          const src = c.createBufferSource();
          src.buffer = nb;
          src.loop = true;
          const f = c.createBiquadFilter();
          f.type = 'bandpass';
          f.Q.value = 2.5;
          f.frequency.setValueAtTime(f0, at);
          f.frequency.exponentialRampToValueAtTime(f1, at + d);
          const g = c.createGain();
          env(g, at, attack, peak, d - attack);
          src.connect(f);
          f.connect(g);
          g.connect(bus);
          src.start(at, Math.random() * 1.5);
          src.stop(at + d + 0.05);
          srcs.push(src);
        };
        if (kind === 'appear') {
          // mid-fade return: open on the wake chirp too
          if (!playWakeSample(c, bus, t, 1, 1.2, srcs)) playChirp(c, bus, t, 4950, 0.09, {}, srcs);
          tone('sine', 196, 1568, t, dur, 0.05, dur * 0.7);
          tone('triangle', 392, 3136, t + dur * 0.1, dur * 0.9, 0.022, dur * 0.7);
          tone('sine', 988, 4186, t + dur * 0.3, dur * 0.7, 0.012, dur * 0.6);
          hiss(t, dur, 600, 5200, 0.014, dur * 0.8);
        } else if (kind === 'fade') {
          const d = Math.max(2, Math.min(7, ms / 1000));
          tone('sine', 880, 110, t, d, 0.035, 0.25);
          tone('triangle', 1320, 220, t, d * 0.8, 0.012, 0.3);
          hiss(t, d, 3600, 300, 0.008, 0.4);
          // going hidden: the wake chirp turned downward
          playChirp(c, bus, t, 5600, 0.03, { dur: 0.32, rise: 0.6, flutter: 70 }, srcs);
          playWakeSample(c, bus, t + 0.2, 0.62, 0.4, srcs);
        } else {
          tone('sine', 1568, 1560, t, 0.5, 0.04, 0.004);
          tone('sine', 2352, 2340, t + 0.02, 0.4, 0.015, 0.004);
          playChirp(c, bus, t + 0.05, 5600, 0.025, { dur: 0.18, rise: 1.2 }, srcs);
        }
        setTimeout(
          () => {
            try {
              bus.disconnect();
            } catch {
              /* ignore */
            }
          },
          (kind === 'settle' ? 1 : Math.max(dur, 7) + 1) * 1000 + 500
        );
      } catch {
        /* fail silently */
      }
    };
    if (c.state === 'running') go();
    else
      void c
        .resume()
        .then(() => {
          if (c.state === 'running') go();
        })
        .catch(() => undefined);
  } catch {
    /* fail silently */
  }
}
