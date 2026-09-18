'use client';

import { useEffect, useRef } from 'react';
import { useJarvisState } from '@/hooks/hud/use-jarvis-state';

const PARTICLES = 220;

/**
 * Canvas 2D particle orb (KDE GPU-safe, no WebGL).
 * Audio FFT would drive amplitude via the aura visualizer; here an
 * idle drift + state-tinted glow stands in and scales ~15% on Green/Orange.
 */
export function ParticleOrb() {
  const ref = useRef<HTMLCanvasElement>(null);
  const { color, boosted, jarvis } = useJarvisState();

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    let raf = 0;
    let t = 0;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const resize = () => {
      const r = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(r.width * dpr));
      canvas.height = Math.max(1, Math.floor(r.height * dpr));
    };
    resize();
    window.addEventListener('resize', resize);

    const seeds = Array.from({ length: PARTICLES }, (_, i) => ({
      a: (i / PARTICLES) * Math.PI * 2,
      r: 0.35 + Math.random() * 0.6,
      s: 0.4 + Math.random() * 1.2,
      w: 1 + Math.random() * 2,
    }));

    const draw = () => {
      t += 0.016;
      const { width: W, height: H } = canvas;
      ctx.clearRect(0, 0, W, H);
      const cx = W / 2;
      const cy = H / 2;
      const base = Math.min(W, H) * 0.32;
      const amp = 1 + Math.sin(t * 2.2) * 0.03 + (boosted ? 0.06 : 0);

      // core glow
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, base * 1.6);
      g.addColorStop(0, `${color}55`);
      g.addColorStop(0.55, `${color}18`);
      g.addColorStop(1, 'transparent');
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);

      ctx.fillStyle = color;
      for (const p of seeds) {
        const ang = p.a + t * 0.25 * p.s;
        const rad = base * p.r * amp * (1 + Math.sin(t * 3 + p.a * 4) * 0.05);
        const x = cx + Math.cos(ang) * rad;
        const y = cy + Math.sin(ang * 1.3) * rad * 0.9;
        ctx.globalAlpha = 0.35 + 0.45 * Math.abs(Math.sin(t * p.s + p.a));
        ctx.beginPath();
        ctx.arc(x, y, p.w * dpr, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
    };
  }, [color, boosted]);

  return (
    <div
      className="hud-orb"
      data-state={jarvis}
      data-boosted={boosted ? 'true' : 'false'}
      style={{ ['--jarvis-state' as string]: color }}
    >
      <canvas ref={ref} className="hud-orb__canvas" aria-hidden="true" />
      <div className="hud-orb__ring" />
    </div>
  );
}
