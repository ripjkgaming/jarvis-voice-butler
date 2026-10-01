'use client';

import { useEffect, useRef, useState } from 'react';
import { hash01, prefersReducedMotion, useBakedCanvas } from '@/hooks/hud/use-baked-frames';
import type { HudTaskEvent } from '@/hooks/hud/use-hud-events';
import { useJarvisState } from '@/hooks/hud/use-jarvis-state';

type Node = { id: string; x: number; y: number; heat: number; label: string };

/** Stable pseudo-random position per key so nodes stop jumping around. */
function hashKey(key: string): number {
  return hash01(
    [...key].reduce((a, c) => a + c.charCodeAt(0) * 31, 7),
    key.length * 131
  );
}

/** One baked radial glow stamp; nodes are stamped copies, never gradients. */
function bakeGlowSprite(color: string): HTMLCanvasElement {
  const s = 64;
  const c = document.createElement('canvas');
  c.width = s;
  c.height = s;
  const ctx = c.getContext('2d');
  if (ctx) {
    const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    g.addColorStop(0, color);
    g.addColorStop(1, 'transparent');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, s, s);
  }
  return c;
}

/**
 * Ambient 2D node graph (KDE GPU-safe), baked: the background drift loop
 * is pre-rendered once and blit-played; nodes are stamped from one baked
 * glow sprite plus live labels. The overlay repaints at 4Hz (heat decay)
 * instead of a 60fps scene loop.
 */
export function NodeGraph({ events }: { events: HudTaskEvent[] }) {
  const { jarvis } = useJarvisState();
  const nodesRef = useRef<Map<string, Node>>(new Map());
  const lastEvRef = useRef<number>(Date.now());
  const frontRef = useRef<HTMLCanvasElement>(null);
  const spriteRef = useRef<HTMLCanvasElement | null>(null);
  const paintRef = useRef<() => void>(() => {});
  const settledRef = useRef(false);
  const [, setTick] = useState(0);

  const backRef = useBakedCanvas({
    frames: 24,
    fps: 8,
    scale: 0.5,
    seed: 4242,
    paused: jarvis === 'idle',
    render: (ctx, w, h, i, n) => {
      // Slow drifting mote field + vignette; nodes live on the overlay.
      const t = (i / n) * (24 / 8);
      const g = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.7);
      g.addColorStop(0, 'rgba(34,211,238,0.05)');
      g.addColorStop(1, 'transparent');
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = 'rgba(34,211,238,0.5)';
      for (let k = 0; k < 40; k += 1) {
        const x = hash01(k, 11) * w;
        const y = ((hash01(k, 12) + t * 0.01 * (0.3 + hash01(k, 13))) % 1) * h;
        ctx.globalAlpha = 0.1 + 0.25 * Math.abs(Math.sin(t * 0.8 + k));
        ctx.fillRect(x, y, 1.5, 1.5);
      }
      ctx.globalAlpha = 1;
    },
  });

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
          x: 0.15 + hashKey(`x:${key}`) * 0.7,
          y: 0.2 + hashKey(`y:${key}`) * 0.6,
          heat: 1,
          label: key,
        });
      }
    }
    setTick((v) => v + 1);
    settledRef.current = false;
    // Reduced motion has no repaint interval — repaint once per batch.
    if (prefersReducedMotion()) paintRef.current();
  }, [events]);

  // 4Hz overlay repaint (heat decay + fade); parked when hidden.
  useEffect(() => {
    if (!spriteRef.current) spriteRef.current = bakeGlowSprite('rgba(34,211,238,0.85)');
    const canvas = frontRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const fit = () => {
      const r = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(r.width * dpr));
      canvas.height = Math.max(1, Math.floor(r.height * dpr));
    };
    fit();
    const paint = () => {
      const { width: W, height: H } = canvas;
      ctx.clearRect(0, 0, W, H);
      const idleMs = Date.now() - lastEvRef.current;
      const fade = idleMs > 10_000 ? Math.max(0.15, 1 - (idleMs - 10_000) / 8000) : 1;
      const decay = prefersReducedMotion() ? 1 : 0.06;
      const nodes = [...nodesRef.current.values()];
      nodes.forEach((nd) => {
        nd.heat = Math.max(0, nd.heat - decay);
      });
      const sprite = spriteRef.current;
      ctx.lineWidth = 1 * dpr;
      for (let k = 1; k < nodes.length; k += 1) {
        const a = nodes[k - 1];
        const b = nodes[k];
        if (!a || !b) continue;
        ctx.strokeStyle = '#22d3ee';
        ctx.globalAlpha = Math.max(a.heat, b.heat, 0.2) * fade * 0.5;
        ctx.beginPath();
        ctx.moveTo(a.x * W, a.y * H);
        ctx.lineTo(b.x * W, b.y * H);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      for (const nd of nodes) {
        const x = nd.x * W;
        const y = nd.y * H;
        const r = (5 + nd.heat * 7) * dpr;
        if (sprite) {
          ctx.globalAlpha = (0.35 + nd.heat * 0.5) * fade;
          const s = r * 4.8;
          ctx.drawImage(sprite, x - s / 2, y - s / 2, s, s);
        }
        ctx.globalAlpha = (0.5 + nd.heat * 0.5) * fade;
        ctx.fillStyle = '#e0fbff';
        ctx.beginPath();
        ctx.arc(x, y, 2.2 * dpr, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 0.75 * fade;
        ctx.fillStyle = '#94dceb';
        ctx.font = `${10 * dpr}px monospace`;
        ctx.fillText(nd.label, x + 8 * dpr, y + 3 * dpr);
      }
      ctx.globalAlpha = 1;
    };
    paint();
    paintRef.current = paint;
    if (prefersReducedMotion()) return;
    const timer = setInterval(() => {
      if (document.hidden) return;
      // Calm mode: the HUD root reports idle when no call is active —
      // skip repaints then (static graph costs nothing to keep).
      const root = document.querySelector('.hud');
      if (root && root.getAttribute('data-state') === 'idle') return;
      // Quiescence gate: all cold + long faded = one final floor frame,
      // then parked until new events arrive (settledRef reset above).
      const cold = [...nodesRef.current.values()].every((nd) => nd.heat <= 0.01);
      if (cold && Date.now() - lastEvRef.current > 18_000) {
        if (settledRef.current) return;
        settledRef.current = true;
      }
      paint();
    }, 250);
    window.addEventListener('resize', fit);
    return () => {
      clearInterval(timer);
      window.removeEventListener('resize', fit);
    };
  }, []);

  return (
    <div className="hud-graph">
      <div className="hud-graph__head">
        <span>WORKFLOW</span>
      </div>
      <div className="hud-graph__stage">
        <canvas ref={backRef} className="hud-graph__canvas" aria-hidden="true" />
        <canvas
          ref={frontRef}
          className="hud-graph__canvas hud-graph__front"
          aria-label="Task node graph"
        />
      </div>
      {/* Screen-reader / keyboard fallback: the canvas is decorative. */}
      <ul className="hud-sr" aria-label="Recent workflow nodes">
        {[...nodesRef.current.values()].slice(-8).map((n) => (
          <li key={n.id}>{n.label}</li>
        ))}
      </ul>
    </div>
  );
}
