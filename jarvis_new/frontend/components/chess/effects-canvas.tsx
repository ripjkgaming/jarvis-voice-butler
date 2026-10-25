'use client';

import { useEffect, useRef } from 'react';

export interface FxEvent {
  id: number;
  kind: 'shatter' | 'promote' | 'mate';
  /** Centre in board fractions (0..1), x right / y down. */
  x: number;
  y: number;
  colour: string;
}

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  max: number;
  size: number;
  colour: string;
  ring?: boolean;
}

/** 2D-canvas particle bursts (GPU-safe on KDE). Idles at zero cost. */
export function EffectsCanvas({ fx, reduced }: { fx: FxEvent | null; reduced: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const parts = useRef<Particle[]>([]);
  const raf = useRef<number | null>(null);
  const last = useRef(0);
  const seen = useRef(0);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const fit = () => {
      const r = canvas.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.max(1, Math.round(r.width * dpr));
      canvas.height = Math.max(1, Math.round(r.height * dpr));
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(canvas);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = ref.current;
    if (!fx || fx.id === seen.current || !canvas || reduced) return;
    seen.current = fx.id;
    const W = canvas.width;
    const H = canvas.height;
    const cx = fx.x * W;
    const cy = fx.y * H;
    const unit = W / 8;
    const add = (n: number, speed: number, life: number, size: number, ring = false) => {
      for (let i = 0; i < n; i++) {
        const a = (Math.PI * 2 * i) / n + Math.random() * 0.6;
        const s = speed * (0.4 + Math.random() * 0.9);
        parts.current.push({
          x: cx,
          y: cy,
          vx: Math.cos(a) * s,
          vy: Math.sin(a) * s,
          life,
          max: life,
          size,
          colour: fx.colour,
          ring,
        });
      }
    };
    if (fx.kind === 'shatter') add(26, unit * 2.6, 620, unit * 0.07);
    if (fx.kind === 'promote') {
      add(36, unit * 3.4, 800, unit * 0.06);
      add(1, 0, 700, unit * 0.2, true);
    }
    if (fx.kind === 'mate') {
      add(90, unit * 5.5, 1400, unit * 0.09);
      add(1, 0, 1200, unit * 0.3, true);
    }
    const frame = (t: number) => {
      const dt = Math.min(48, t - (last.current || t));
      last.current = t;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      parts.current = parts.current.filter((p) => p.life > 0);
      for (const p of parts.current) {
        p.life -= dt;
        const k = Math.max(0, p.life / p.max);
        ctx.globalAlpha = k;
        if (p.ring) {
          ctx.strokeStyle = p.colour;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(p.x, p.y, (1 - k) * unit * 2.2 + 4, 0, Math.PI * 2);
          ctx.stroke();
          continue;
        }
        p.x += (p.vx * dt) / 1000;
        p.y += (p.vy * dt) / 1000;
        p.vx *= 0.985;
        p.vy = p.vy * 0.985 + (unit * 0.9 * dt) / 1000;
        ctx.fillStyle = p.colour;
        ctx.shadowColor = p.colour;
        ctx.shadowBlur = 8;
        ctx.fillRect(p.x - p.size / 2, p.y - p.size / 2, p.size, p.size);
      }
      ctx.globalAlpha = 1;
      if (parts.current.length) raf.current = requestAnimationFrame(frame);
      else {
        raf.current = null;
        last.current = 0;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    };
    if (raf.current === null) raf.current = requestAnimationFrame(frame);
  }, [fx, reduced]);

  useEffect(
    () => () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current);
    },
    []
  );

  return <canvas ref={ref} className="cx-fx" aria-hidden="true" />;
}
