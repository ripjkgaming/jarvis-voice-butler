'use client';

import { useEffect, useRef } from 'react';
import type { HudTaskEvent } from '@/hooks/hud/use-hud-events';

type Node = { id: string; x: number; y: number; heat: number; label: string };

/**
 * Ambient 2D canvas node graph (KDE GPU-safe).
 * Nodes = apps/files/processes named in tool events; edges light
 * sequentially; idle fade after 10s silence.
 */
export function NodeGraph({ events }: { events: HudTaskEvent[] }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<Map<string, Node>>(new Map());
  const lastEvRef = useRef<number>(Date.now());

  useEffect(() => {
    lastEvRef.current = Date.now();
    const map = nodesRef.current;
    for (const ev of events.slice(-12)) {
      const key = (ev.tool ?? ev.label ?? 'agent').slice(0, 24);
      if (!key) continue;
      const n = map.get(key);
      if (n) {
        n.heat = 1;
      } else {
        if (map.size > 14) {
          const first = map.keys().next().value;
          if (first) map.delete(first);
        }
        map.set(key, {
          id: key,
          x: 0.15 + Math.random() * 0.7,
          y: 0.2 + Math.random() * 0.6,
          heat: 1,
          label: key,
        });
      }
    }
  }, [events]);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    let raf = 0;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const resize = () => {
      const r = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(r.width * dpr));
      canvas.height = Math.max(1, Math.floor(r.height * dpr));
    };
    resize();
    window.addEventListener('resize', resize);

    const draw = () => {
      const { width: W, height: H } = canvas;
      ctx.clearRect(0, 0, W, H);
      const idleMs = Date.now() - lastEvRef.current;
      const fade = idleMs > 10_000 ? Math.max(0.15, 1 - (idleMs - 10_000) / 8000) : 1;
      const nodes = [...nodesRef.current.values()];
      nodes.forEach((n) => {
        n.heat = Math.max(0, n.heat - 0.008);
      });

      ctx.strokeStyle = `rgba(34, 211, 238, ${0.25 * fade})`;
      ctx.lineWidth = 1 * dpr;
      for (let i = 1; i < nodes.length; i++) {
        const a = nodes[i - 1];
        const b = nodes[i];
        ctx.globalAlpha = Math.max(a.heat, b.heat, 0.2) * fade;
        ctx.beginPath();
        ctx.moveTo(a.x * W, a.y * H);
        ctx.lineTo(b.x * W, b.y * H);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      for (const n of nodes) {
        const x = n.x * W;
        const y = n.y * H;
        const r = (5 + n.heat * 7) * dpr;
        const g = ctx.createRadialGradient(x, y, 0, x, y, r * 2.4);
        g.addColorStop(0, `rgba(34,211,238,${(0.35 + n.heat * 0.5) * fade})`);
        g.addColorStop(1, 'transparent');
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(x, y, r * 2.4, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = `rgba(224, 251, 255, ${(0.5 + n.heat * 0.5) * fade})`;
        ctx.beginPath();
        ctx.arc(x, y, 2.2 * dpr, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = `rgba(148, 220, 235, ${0.75 * fade})`;
        ctx.font = `${10 * dpr}px monospace`;
        ctx.fillText(n.label, x + 8 * dpr, y + 3 * dpr);
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
    };
  }, []);

  return (
    <div className="hud-graph">
      <div className="hud-graph__head">
        <span>WORKFLOW</span>
        <span className="hud-graph__hint">data-channel · 10s fade</span>
      </div>
      <canvas ref={ref} className="hud-graph__canvas" aria-label="Task node graph" />
    </div>
  );
}
