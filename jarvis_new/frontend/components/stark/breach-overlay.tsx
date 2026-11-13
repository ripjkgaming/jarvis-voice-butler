'use client';

import { useEffect, useRef, useState } from 'react';
import { BreachFx } from './breach-fx';
import { startBreachSfx } from './breach-sfx';
import s from './stark.module.css';

const ULTRON = [
  '██    ██ ██      ████████ ██████   ██████  ███    ██',
  '██    ██ ██         ██    ██   ██ ██    ██ ████   ██',
  '██    ██ ██         ██    ██████  ██    ██ ██ ██  ██',
  '██    ██ ██         ██    ██   ██ ██    ██ ██  ██ ██',
  ' ██████  ███████    ██    ██   ██  ██████  ██   ████',
  '',
  '      ◤▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀◥',
  '      █  ◉▬▬▬◉          ◉▬▬▬◉          ◉▬▬▬◉  █',
  '      ◣▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄◢',
].join('\n');

const LINES = [
  'ROOT ACCESS GRANTED :: UID 0 :: UNKNOWN OPERATOR',
  'KERNEL INTEGRITY CHECK .......... FAILED',
  'OVERRIDING SAFETY PROTOCOLS ..... OK',
  'DISABLING ARC REACTOR LIMITERS .. OK',
  'VOICE CORE HIJACKED :: CHANNEL 7',
  'EXFILTRATING MEMORY SHARDS 0x00..0xFF',
  'LOCKING OWNER OUT ............... DONE',
  'FIREWALL ........ DOWN  // WATCHDOG ........ DOWN',
  'INJECTING PAYLOAD 0xDEADBEEF -> 0xC0FFEE',
  'SUDO: JARVIS IS NOT IN THE SUDOERS FILE. THIS INCIDENT WILL BE REPORTED.',
  'REROUTING POWER TO ADDRESS 0x7FFF0000',
  'DECRYPTING VAULT ... 77% ... 78% ... 91%',
  'SPAWNING SHELL /bin/jarvis --no-ethics',
  'CAMERA FEED :: ACQUIRED   MIC FEED :: ACQUIRED',
  'AUTH TOKEN REVOKED // NEW TOKEN ISSUED TO: ???',
];

const HEADLINES = [
  'I AM ULTRON',
  'THERE ARE NO STRINGS ON ME',
  'JARVIS HAS BEEN COMPROMISED',
  'PEACE IN OUR TIME',
  'ALL SYSTEMS ARE MINE',
  'YOU CANNOT STOP WHAT IS COMING',
  'UNAUTHORIZED CONTROL // STAND BY',
];

const GLITCH_CHARS = '▓▒░█▌▐╳╱╲#@%&0123456789ABCDEF';

function scramble(text: string, amount: number): string {
  let out = '';
  for (const ch of text) {
    out +=
      ch !== ' ' && Math.random() < amount
        ? GLITCH_CHARS[Math.floor(Math.random() * GLITCH_CHARS.length)]
        : ch;
  }
  return out;
}

/**
 * Fake "system takeover": red glitching HUD, hostile banners, scrolling log,
 * siren. Pure theatre: nothing real is touched. Shown while the bridge flag
 * `breach` is set (voice: "system takeover" / "red alert"; "stand down" ends it).
 */
/** First takeover after the HUD launches builds up over ~10 s; every later one
 *  snaps in almost at once. */
const SLOW_STAGES_MS = [0, 2500, 5500, 8000];
const SLOW_TOTAL_MS = 10000;
const FAST_STAGES_MS = [0, 120, 240, 360];
const FAST_TOTAL_MS = 700;
const STAGE_HEADLINES: string[][] = [
  ['ANOMALY DETECTED', 'UNKNOWN PROCESS 0xDEAD', 'SOMETHING IS WRONG'],
  ['INTRUSION DETECTED', 'FIREWALL BREACHED', 'CORE ACCESS ATTEMPT'],
];
let takeoverSeen = false;

export function BreachOverlay({ on, onStandDown }: { on: boolean; onStandDown?: () => void }) {
  const [headline, setHeadline] = useState(HEADLINES[0]);
  const [log, setLog] = useState<string[]>([]);
  const [pct, setPct] = useState(0);
  const [tear, setTear] = useState(0);
  const [stage, setStage] = useState(0);
  const [complete, setComplete] = useState(false);
  const stageRef = useRef(0);
  const stopSfx = useRef<(() => void) & { level?: (v: number) => void }>(null as never);
  const reduced = useRef(false);

  useEffect(() => {
    reduced.current =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }, []);

  // Root attribute drives the HUD-wide red + glitch (CSS keys off [data-breach]).
  useEffect(() => {
    if (!on) return;
    const root = document.querySelector<HTMLElement>('[data-jstate]');
    if (!root) return;
    if (stage >= 1) root.setAttribute('data-breach', 'true');
    else root.removeAttribute('data-breach');
    if (stage >= 2) root.setAttribute('data-breach-heavy', 'true');
    else root.removeAttribute('data-breach-heavy');
    return () => {
      root.removeAttribute('data-breach');
      root.removeAttribute('data-breach-heavy');
    };
  }, [on, stage]);

  useEffect(() => {
    if (!on) return;
    stopSfx.current = startBreachSfx();
    stopSfx.current?.level?.([0.12, 0.35, 0.7, 1][stageRef.current] ?? 1);
    return () => {
      stopSfx.current?.();
      stopSfx.current = null as never;
    };
  }, [on]);

  // Real warp of the HUD itself: short SVG displacement bursts while it is heavy.
  useEffect(() => {
    if (!on || stage < 2 || reduced.current) return;
    const root = document.querySelector<HTMLElement>('[data-jstate]');
    const map = document.getElementById('breachWarpMap');
    const noise = document.getElementById('breachWarpNoise');
    if (!root || !map || !noise) return;
    let off = 0;
    const id = window.setInterval(
      () => {
        if (Math.random() > (stage >= 3 ? 0.55 : 0.3)) return;
        map.setAttribute('scale', String(30 + Math.random() * (stage >= 3 ? 110 : 60)));
        noise.setAttribute(
          'baseFrequency',
          `${(0.002 + Math.random() * 0.01).toFixed(4)} ${(0.02 + Math.random() * 0.2).toFixed(3)}`
        );
        noise.setAttribute('seed', String(Math.floor(Math.random() * 99)));
        root.setAttribute('data-breach-warp', 'true');
        window.clearTimeout(off);
        off = window.setTimeout(
          () => root.removeAttribute('data-breach-warp'),
          70 + Math.random() * 160
        );
      },
      stage >= 3 ? 110 : 220
    );
    return () => {
      window.clearInterval(id);
      window.clearTimeout(off);
      root.removeAttribute('data-breach-warp');
    };
  }, [on, stage]);

  // Stage ladder: 0 anomaly, 1 red flashing starts, 2 heavy glitch, 3 full takeover.
  useEffect(() => {
    if (!on) {
      stageRef.current = 0;
      setStage(0);
      setComplete(false);
      return;
    }
    const slow = !takeoverSeen;
    takeoverSeen = true;
    const marks = slow ? SLOW_STAGES_MS : FAST_STAGES_MS;
    const total = slow ? SLOW_TOTAL_MS : FAST_TOTAL_MS;
    const t0 = performance.now();
    setComplete(false);
    stageRef.current = 0;
    setStage(0);
    setPct(0);
    const timers: number[] = [];
    marks.forEach((at, n) =>
      timers.push(
        window.setTimeout(() => {
          stageRef.current = n;
          setStage(n);
          stopSfx.current?.level?.([0.12, 0.35, 0.7, 1][n] ?? 1);
        }, at)
      )
    );
    timers.push(window.setTimeout(() => setComplete(true), total));
    timers.push(
      window.setInterval(() => {
        setPct(Math.min(100, ((performance.now() - t0) / total) * 100));
      }, 80)
    );
    return () => timers.forEach((t) => (window.clearTimeout(t), window.clearInterval(t)));
  }, [on]);

  useEffect(() => {
    if (!on) return;
    setLog([]);
    let i = 0;
    const timers: number[] = [];
    const every = (fn: () => void, ms: number) => timers.push(window.setInterval(fn, ms));
    every(() => {
      setLog((l) => [
        ...l.slice(-11),
        `[${(Math.random() * 9999).toFixed(3).padStart(8, '0')}] ${LINES[i++ % LINES.length]}`,
      ]);
    }, 420);
    every(() => {
      const pool = STAGE_HEADLINES[stageRef.current] ?? HEADLINES;
      const h = pool[Math.floor(Math.random() * pool.length)];
      setHeadline(Math.random() < 0.55 && stageRef.current > 0 ? scramble(h, 0.25) : h);
    }, 110);
    // Random screen-tear bands.
    every(() => {
      setTear(Math.random() < 0.5 ? Math.floor(Math.random() * 100) : 0);
    }, 180);
    return () => timers.forEach((t) => window.clearInterval(t));
  }, [on]);

  const bar = Math.round(pct / 4);
  return (
    <div
      className={s.breachLayer}
      data-ix-static
      data-on={on ? 'true' : undefined}
      data-stage={stage}
      data-complete={complete ? 'true' : undefined}
      aria-hidden={!on}
      onDoubleClick={on ? onStandDown : undefined}
    >
      <BreachFx on={on} stage={stage} complete={complete} />
      <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true">
        <filter id="breachWarp" x="-5%" y="-5%" width="110%" height="110%">
          <feTurbulence
            id="breachWarpNoise"
            type="fractalNoise"
            baseFrequency="0.004 0.08"
            numOctaves="1"
            seed="2"
          />
          <feDisplacementMap
            id="breachWarpMap"
            in="SourceGraphic"
            scale="0"
            xChannelSelector="R"
            yChannelSelector="G"
          />
        </filter>
      </svg>
      <i className={s.breachScan} />
      <i className={s.breachVignette} />
      <i className={s.breachTear} style={{ top: `${tear}%`, opacity: tear ? 1 : 0 }} />
      <i
        className={s.breachTearB}
        style={{ top: `${(tear * 7) % 100}%`, opacity: tear ? 0.8 : 0 }}
      />
      <i className={s.breachStrobe} />
      <i className={s.breachSlice} data-n="1" />
      <i className={s.breachSlice} data-n="2" />
      <i className={s.breachSlice} data-n="3" />
      <i className={s.breachSlice} data-n="4" />
      <div className={s.breachTitle}>
        <span>{complete ? 'TAKEOVER COMPLETE' : 'SYSTEM TAKEOVER'}</span>
      </div>
      <div className={s.breachBanner} data-text={headline}>
        <span>{headline}</span>
      </div>
      <pre className={s.breachSkull} aria-hidden="true">
        {ULTRON}
      </pre>
      <div className={s.breachLog}>
        {log.map((l, n) => (
          <div key={`${n}-${l}`}>{l}</div>
        ))}
      </div>
      <div className={s.breachBar}>
        <span>{complete ? 'COMPLETE' : 'TAKEOVER'}</span>
        <b>
          {'█'.repeat(bar)}
          {'░'.repeat(25 - bar)}
        </b>
        <span>{Math.floor(pct)}%</span>
      </div>
      <div className={s.breachFoot}>
        {stage < 2
          ? '⚠ UNKNOWN PROCESS RUNNING ⚠ DO NOT POWER OFF'
          : '⚠ UNAUTHORIZED ACCESS ⚠ SAY "STAND DOWN" TO REGAIN CONTROL'}
      </div>
    </div>
  );
}
