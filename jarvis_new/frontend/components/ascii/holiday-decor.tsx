'use client';

import { useEffect, useRef } from 'react';
import { useHoliday } from '@/hooks/hud/use-holiday';
import type { HolidayTheme, ParticleKind } from '@/lib/holidays';
import styles from './holiday-decor.module.css';

/** Holiday overlay (lib/holidays.ts): one canvas of ASCII-glyph particles,
 *  two corner ornaments and a banner. Mounted once by HudShell. Voice/URL
 *  control lives in hooks/hud/use-holiday.ts. Idle cost: ~FPS-capped rAF with
 *  <= 90 glyphs; stops when the tab or HUD is hidden, one static frame when
 *  prefers-reduced-motion is set. */

const FPS = 24;
const FONT = "var(--font-commit-mono), 'Commit Mono', ui-monospace, monospace";

type P = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  life: number; // seconds left; Infinity for ambient
  max: number;
  glyph: string;
  color: string;
  phase: number;
};

const rnd = (a: number, b: number) => a + Math.random() * (b - a);
const pick = <T,>(arr: T[]): T => arr[(Math.random() * arr.length) | 0];

function count(kind: ParticleKind, density: number, w: number, h: number): number {
  const area = Math.min(1, (w * h) / (1280 * 720));
  const base: Record<ParticleKind, number> = {
    snow: 70,
    lanterns: 14,
    fireworks: 0,
    confetti: 48,
    bats: 7,
    leaves: 30,
    hearts: 22,
    diyas: 26,
    stars: 46,
  };
  return Math.max(0, Math.round(base[kind] * density * (0.4 + 0.6 * area)));
}

function spawn(theme: HolidayTheme, w: number, h: number, initial: boolean): P {
  const kind = theme.particle;
  const p: P = {
    x: rnd(0, w),
    y: initial ? rnd(0, h) : -12,
    vx: 0,
    vy: 0,
    size: rnd(10, 15),
    life: Infinity,
    max: Infinity,
    glyph: pick(theme.glyphs),
    color: pick(theme.colors),
    phase: rnd(0, 6.28),
  };
  switch (kind) {
    case 'snow':
      p.vy = rnd(14, 40);
      p.vx = rnd(-6, 6);
      p.size = rnd(12, 20);
      break;
    case 'leaves':
      p.vy = rnd(18, 38);
      p.vx = rnd(8, 22);
      break;
    case 'confetti':
      p.vy = rnd(22, 60);
      p.vx = rnd(-10, 10);
      p.size = rnd(8, 13);
      break;
    case 'lanterns':
    case 'hearts':
    case 'diyas':
      p.y = initial ? rnd(0, h) : h + 14;
      p.vy = -(kind === 'diyas' ? rnd(10, 26) : rnd(8, 24));
      p.vx = rnd(-4, 4);
      p.size = kind === 'lanterns' ? rnd(14, 24) : rnd(10, 16);
      break;
    case 'bats':
      p.y = rnd(h * 0.05, h * 0.5);
      p.x = initial ? rnd(0, w) : -40;
      p.vx = rnd(40, 90);
      p.vy = rnd(-6, 6);
      p.size = rnd(11, 15);
      break;
    case 'stars':
      p.y = rnd(0, h);
      p.size = rnd(8, 15);
      break;
    default:
      break;
  }
  return p;
}

function burst(theme: HolidayTheme, w: number, h: number, out: P[]) {
  const cx = rnd(w * 0.1, w * 0.9);
  const cy = rnd(h * 0.08, h * 0.45);
  const color = pick(theme.colors);
  const n = 26;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + rnd(-0.1, 0.1);
    const s = rnd(50, 120);
    out.push({
      x: cx,
      y: cy,
      vx: Math.cos(a) * s,
      vy: Math.sin(a) * s,
      size: rnd(9, 13),
      life: rnd(1, 1.7),
      max: 1.7,
      glyph: pick(theme.glyphs),
      color,
      phase: 0,
    });
  }
}

function step(kind: ParticleKind, p: P, dt: number, t: number, w: number, h: number): boolean {
  switch (kind) {
    case 'snow':
    case 'leaves':
    case 'confetti':
      p.x += (p.vx + Math.sin(t * 1.3 + p.phase) * (kind === 'confetti' ? 14 : 10)) * dt;
      p.y += p.vy * dt;
      return p.y < h + 16 && p.x > -20 && p.x < w + 20;
    case 'lanterns':
    case 'hearts':
    case 'diyas':
      p.x += (p.vx + Math.sin(t * 0.8 + p.phase) * 8) * dt;
      p.y += p.vy * dt;
      return p.y > -24;
    case 'bats':
      p.x += p.vx * dt;
      p.y += (p.vy + Math.sin(t * 3 + p.phase) * 22) * dt;
      return p.x < w + 50;
    case 'fireworks':
      p.vy += 60 * dt;
      p.vx *= 1 - 0.8 * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.life -= dt;
      return p.life > 0;
    default:
      return true;
  }
}

function alphaOf(kind: ParticleKind, p: P, t: number): number {
  if (kind === 'fireworks') return Math.max(0, Math.min(1, p.life / (p.max * 0.6)));
  if (kind === 'stars') return 0.2 + 0.7 * (0.5 + 0.5 * Math.sin(t * 1.4 + p.phase * 3));
  if (kind === 'diyas') return 0.45 + 0.4 * Math.sin(t * 7 + p.phase * 5);
  return 0.95;
}

export function HolidayDecor() {
  const theme = useHoliday();
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Tint vars for the rest of the HUD (consume as rgba(var(--holiday-acc), a)).
  useEffect(() => {
    const root = document.documentElement;
    if (!theme) {
      root.removeAttribute('data-holiday');
      root.style.removeProperty('--holiday-acc');
      root.style.removeProperty('--holiday-hot');
      return;
    }
    root.setAttribute('data-holiday', theme.id);
    root.style.setProperty('--holiday-acc', theme.tint.acc);
    root.style.setProperty('--holiday-hot', theme.tint.hot);
    return () => {
      root.removeAttribute('data-holiday');
      root.style.removeProperty('--holiday-acc');
      root.style.removeProperty('--holiday-hot');
    };
  }, [theme]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!theme || !canvas || !ctx) return;
    const kind = theme.particle;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    let w = 0;
    let h = 0;
    let dpr = 1;
    let parts: P[] = [];
    let raf = 0;
    let last = 0;
    let t = 0;
    let nextBurst = 0.4;

    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      const n = count(kind, theme.density, w, h);
      parts = Array.from({ length: n }, () => spawn(theme, w, h, true));
    };

    const draw = () => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      for (const p of parts) {
        ctx.globalAlpha = alphaOf(kind, p, t);
        ctx.fillStyle = p.color;
        let g = p.glyph;
        if (kind === 'bats') g = Math.sin(t * 9 + p.phase) > 0 ? p.glyph : 'v';
        ctx.font = `600 ${p.size}px ${FONT}`;
        ctx.fillText(g, p.x, p.y);
      }
      ctx.globalAlpha = 1;
    };

    const parked = () =>
      document.hidden || document.documentElement.classList.contains('hud-hidden');
    const frame = (now: number) => {
      // Hidden / dissolved HUD: park with no pending frame request (a polling
      // rAF keeps the web process rendering at the display rate). `wake`
      // restarts the loop when the HUD is shown again.
      if (parked()) {
        raf = 0;
        return;
      }
      raf = requestAnimationFrame(frame);
      if (now - last < 1000 / FPS) return;
      const dt = Math.min(0.1, (now - last) / 1000 || 0);
      last = now;
      t += dt;
      if (kind === 'fireworks') {
        nextBurst -= dt;
        if (nextBurst <= 0 && parts.length < 150) {
          burst(theme, w, h, parts);
          nextBurst = rnd(1.4, 3.2) / theme.density;
        }
      }
      const target = count(kind, theme.density, w, h);
      parts = parts.filter((p) => step(kind, p, dt, t, w, h));
      if (kind !== 'fireworks') {
        while (parts.length < target) parts.push(spawn(theme, w, h, false));
      }
      draw();
    };

    const start = () => {
      cancelAnimationFrame(raf);
      if (reduce.matches) {
        // One static frame, no loop.
        if (kind === 'fireworks') for (let i = 0; i < 3; i++) burst(theme, w, h, parts);
        for (const p of parts) p.life = Math.max(p.life, 1);
        draw();
        return;
      }
      last = performance.now();
      raf = requestAnimationFrame(frame);
    };

    resize();
    start();
    const onResize = () => {
      resize();
      start();
    };
    const wake = () => {
      if (raf || reduce.matches || parked()) return;
      last = performance.now();
      raf = requestAnimationFrame(frame);
    };
    const classes = new MutationObserver(wake);
    classes.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('resize', onResize);
    reduce.addEventListener('change', start);
    return () => {
      cancelAnimationFrame(raf);
      classes.disconnect();
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('resize', onResize);
      reduce.removeEventListener('change', start);
    };
  }, [theme]);

  if (!theme) return null;
  const orn = theme.ornament.join('\n');
  return (
    <div className={styles.root} aria-hidden data-holiday={theme.id}>
      <canvas ref={canvasRef} className={styles.canvas} />
      <pre className={`${styles.orn} ${styles.ornTl}`}>{orn}</pre>
      <pre className={`${styles.orn} ${styles.ornBr}`}>{orn}</pre>
      {theme.banner ? <div className={styles.banner}>{theme.banner}</div> : null}
    </div>
  );
}
