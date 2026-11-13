/**
 * Sound for the fake "system takeover" state: alarm siren, sub drone, digital
 * glitch stutters and static bursts. WebAudio only, no assets. Starts with
 * `startBreachSfx()`, ends with the returned stop function (fades out fast).
 * Respects the HUD mute (voice "mute" turns the sound off).
 */
import { isIntroSfxEnabled } from './intro-sfx';

const MASTER = 0.5;

export type BreachSfx = (() => void) & { level?: (v: number) => void };

export function startBreachSfx(): BreachSfx {
  if (!isIntroSfxEnabled()) return () => {};
  let c: AudioContext;
  try {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return () => {};
    c = new Ctor();
  } catch {
    return () => {};
  }
  void c.resume().catch(() => {});

  const comp = c.createDynamicsCompressor();
  comp.threshold.value = -12;
  comp.ratio.value = 8;
  const master = c.createGain();
  master.gain.value = 0;
  master.gain.linearRampToValueAtTime(MASTER, c.currentTime + 0.25);
  master.connect(comp);
  comp.connect(c.destination);

  const nodes: AudioNode[] = [];
  const timers: number[] = [];

  // Siren: two detuned saws swept by a 1.4 Hz triangle LFO, through a bandpass.
  const siren = c.createOscillator();
  siren.type = 'sawtooth';
  siren.frequency.value = 660;
  const siren2 = c.createOscillator();
  siren2.type = 'square';
  siren2.frequency.value = 664;
  const lfo = c.createOscillator();
  lfo.type = 'triangle';
  lfo.frequency.value = 1.4;
  const lfoDepth = c.createGain();
  lfoDepth.gain.value = 260;
  lfo.connect(lfoDepth);
  lfoDepth.connect(siren.frequency);
  lfoDepth.connect(siren2.frequency);
  const band = c.createBiquadFilter();
  band.type = 'bandpass';
  band.frequency.value = 900;
  band.Q.value = 0.8;
  const sirenGain = c.createGain();
  sirenGain.gain.value = 0.16;
  siren.connect(band);
  siren2.connect(band);
  band.connect(sirenGain);
  sirenGain.connect(master);

  // Sub drone with slow wobble.
  const sub = c.createOscillator();
  sub.type = 'sine';
  sub.frequency.value = 46;
  const sub2 = c.createOscillator();
  sub2.type = 'sawtooth';
  sub2.frequency.value = 23;
  const subGain = c.createGain();
  subGain.gain.value = 0.38;
  const subLp = c.createBiquadFilter();
  subLp.type = 'lowpass';
  subLp.frequency.value = 140;
  sub.connect(subGain);
  sub2.connect(subGain);
  subGain.connect(subLp);
  subLp.connect(master);

  for (const n of [siren, siren2, lfo, sub, sub2]) n.start();
  nodes.push(siren, siren2, lfo, sub, sub2);

  // Noise buffer for static / glitch bursts.
  const len = Math.floor(c.sampleRate * 1);
  const noise = c.createBuffer(1, len, c.sampleRate);
  const d = noise.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;

  const burst = () => {
    const t = c.currentTime;
    const dur = 0.03 + Math.random() * 0.16;
    const src = c.createBufferSource();
    src.buffer = noise;
    src.loop = true;
    const bp = c.createBiquadFilter();
    bp.type = Math.random() < 0.5 ? 'highpass' : 'bandpass';
    bp.frequency.value = 800 + Math.random() * 5200;
    bp.Q.value = 1 + Math.random() * 6;
    const g = c.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(0.25 + Math.random() * 0.3, t + 0.004);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(bp);
    bp.connect(g);
    g.connect(master);
    src.start(t);
    src.stop(t + dur + 0.02);
  };

  // Bit-crushed zap: a fast descending square chirp.
  const zap = () => {
    const t = c.currentTime;
    const o = c.createOscillator();
    o.type = 'square';
    const f0 = 400 + Math.random() * 2400;
    o.frequency.setValueAtTime(f0, t);
    o.frequency.exponentialRampToValueAtTime(Math.max(40, f0 / (4 + Math.random() * 10)), t + 0.12);
    const g = c.createGain();
    g.gain.setValueAtTime(0.18, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.14);
    o.connect(g);
    g.connect(master);
    o.start(t);
    o.stop(t + 0.16);
  };

  // Warning beep: hard 3-note klaxon pattern every few seconds.
  const klaxon = () => {
    const t = c.currentTime;
    [0, 0.22, 0.44].forEach((off, i) => {
      const o = c.createOscillator();
      o.type = 'square';
      o.frequency.value = i === 1 ? 1180 : 880;
      const g = c.createGain();
      g.gain.setValueAtTime(0.0001, t + off);
      g.gain.linearRampToValueAtTime(0.2, t + off + 0.01);
      g.gain.setValueAtTime(0.2, t + off + 0.14);
      g.gain.exponentialRampToValueAtTime(0.0001, t + off + 0.18);
      o.connect(g);
      g.connect(master);
      o.start(t + off);
      o.stop(t + off + 0.2);
    });
  };

  const loop = (fn: () => void, min: number, spread: number) => {
    const tick = () => {
      fn();
      timers.push(window.setTimeout(tick, min + Math.random() * spread));
    };
    timers.push(window.setTimeout(tick, min * Math.random()));
  };
  const thump = (dt: number, g: number) => {
    const t = c.currentTime + dt;
    const o = c.createOscillator();
    const v = c.createGain();
    o.type = 'sine';
    o.frequency.setValueAtTime(80, t);
    o.frequency.exponentialRampToValueAtTime(28, t + 0.22);
    v.gain.setValueAtTime(g, t);
    v.gain.exponentialRampToValueAtTime(0.001, t + 0.26);
    o.connect(v);
    v.connect(master);
    o.start(t);
    o.stop(t + 0.3);
  };
  const hb = window.setInterval(() => {
    thump(0, 0.85);
    thump(0.2, 0.55);
  }, 1100);
  timers.push(hb);
  loop(burst, 120, 520);
  loop(zap, 300, 1300);
  loop(klaxon, 3200, 2200);
  burst();
  klaxon();

  let stopped = false;
  const stop: BreachSfx = () => {
    if (stopped) return;
    stopped = true;
    for (const id of timers) window.clearTimeout(id);
    try {
      const t = c.currentTime;
      master.gain.cancelScheduledValues(t);
      master.gain.setValueAtTime(master.gain.value, t);
      master.gain.linearRampToValueAtTime(0, t + 0.2);
    } catch {
      /* context already closed */
    }
    window.setTimeout(() => {
      for (const n of nodes) {
        try {
          (n as OscillatorNode).stop();
        } catch {
          /* already stopped */
        }
      }
      void c.close().catch(() => {});
    }, 260);
  };
  stop.level = (v: number) => {
    if (stopped) return;
    try {
      const t = c.currentTime;
      master.gain.cancelScheduledValues(t);
      master.gain.setValueAtTime(master.gain.value, t);
      master.gain.linearRampToValueAtTime(MASTER * v, t + 0.3);
    } catch {
      /* closed */
    }
  };
  return stop;
}
