'use client';

import { memo, useEffect, useRef } from 'react';
import type { JarvisState } from '@/hooks/hud/use-jarvis-state';
import { subscribeAmbientFrame } from '@/lib/ambient-particles';
import {
  type GlyphAtlas,
  RAMP,
  type ReactorFrame,
  glyphAtlas,
  prefersReducedMotion,
  reactorField,
} from '@/lib/ascii';

const SPEED: Record<JarvisState, number> = {
  idle: 1,
  listening: 1.3,
  thinking: 1.9,
  speaking: 1.2,
};

/** The arc reactor drawn as a live ASCII field. It assembles ring by ring
 * whenever the HUD appears, spins with the voice state, flares while Jarvis
 * speaks, and throws a glyph shockwave on wake and on every state change. */
export const AsciiReactor = memo(function AsciiReactor({
  color,
  jarvis,
  muted,
  activation,
  energizing,
  className,
}: {
  color: string;
  jarvis: JarvisState;
  muted: boolean;
  activation?: number;
  energizing?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useRef({
    color,
    jarvis,
    muted,
    energizing: !!energizing,
    shockAt: -10,
    rebuild: true,
  });

  useEffect(() => {
    const L = live.current;
    if (L.color !== color) L.rebuild = true;
    if (L.jarvis !== jarvis || L.muted !== muted) L.shockAt = performance.now();
    Object.assign(L, { color, jarvis, muted, energizing: !!energizing });
  }, [color, jarvis, muted, energizing]);

  useEffect(() => {
    if (activation) live.current.shockAt = performance.now();
  }, [activation]);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const root = document.documentElement;
    let w = 0;
    let h = 0;
    let dpr = 1;
    let cw = 9;
    let ch = 15;
    let atlas: GlyphAtlas | null = null;
    let start = performance.now();
    let unsubscribe: (() => void) | null = null;
    const frame: ReactorFrame = { build: 0, t: 0, boost: 0, speed: 1, shock: null };

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      w = Math.max(1, rect.width);
      h = Math.max(1, rect.height);
      dpr = Math.min(1.5, window.devicePixelRatio || 1);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cw = Math.max(5, Math.round(Math.min(w, h) / 64));
      ch = Math.round(cw * 1.6);
      live.current.rebuild = true;
      draw();
    };

    const draw = () => {
      const L = live.current;
      if (!w) return;
      if (L.rebuild || !atlas) {
        atlas = glyphAtlas(cw, ch, dpr, L.color);
        L.rebuild = false;
      }
      const now = performance.now();
      const reduced = prefersReducedMotion();
      const t = (now - start) / 1000;
      frame.t = reduced ? 3 : t;
      frame.build = reduced ? 3 : t;
      frame.speed = L.muted ? 0.35 : SPEED[L.jarvis];
      const talk =
        L.jarvis === 'speaking'
          ? 0.35 + 0.25 * Math.sin(t * 9)
          : L.jarvis === 'listening'
            ? 0.2
            : 0;
      const since = (now - L.shockAt) / 1000;
      frame.boost = talk + (L.energizing ? 0.4 : 0) + (since < 1 ? 1.1 * Math.exp(-since * 3) : 0);
      frame.shock = since < 0.9 ? [0.3 + since * 1.4, (1 - since / 0.9) * 0.9] : null;
      ctx.clearRect(0, 0, w, h);
      const cx = w / 2;
      const cy = h / 2;
      const R = (Math.min(w, h) / 2) * 0.92;
      const aa = (0.55 * ch) / R;
      const cols = Math.ceil(w / cw);
      const rows = Math.ceil(h / ch);
      const reach = frame.shock ? Math.max(1.1, frame.shock[0] + 0.12) : 1.1;
      for (let y = 0; y < rows; y++) {
        const ny = ((y + 0.5) * ch - cy) / R;
        if (Math.abs(ny) > reach) continue;
        for (let x = 0; x < cols; x++) {
          const nx = ((x + 0.5) * cw - cx) / R;
          if (nx * nx + ny * ny > reach * reach) continue;
          const [I, hot] = reactorField(nx, ny, frame, aa);
          if (I < 0.08) continue;
          const Ic = I < 1 ? I : 1;
          // ▓█ are reserved for the white-hot core; everything else stays in .·:-=+*#%
          const glyph =
            hot > 0.9
              ? RAMP.length - 1
              : hot > 0.6
                ? RAMP.length - 2
                : 1 + Math.floor(Ic * (RAMP.length - 3.01));
          atlas!.draw(ctx, glyph, Math.min(1, Ic * 0.85 + hot * 0.5), x * cw, y * ch);
        }
      }
    };

    const sync = () => {
      const visible = !document.hidden && !root.classList.contains('hud-hidden');
      if (visible && !unsubscribe) unsubscribe = subscribeAmbientFrame(draw);
      if (!visible && unsubscribe) {
        unsubscribe();
        unsubscribe = null;
      }
    };
    // The reactor reassembles each time the HUD is brought back.
    let wasHidden = root.classList.contains('hud-hidden');
    const classes = new MutationObserver(() => {
      const hidden = root.classList.contains('hud-hidden');
      if (wasHidden && !hidden) start = performance.now();
      wasHidden = hidden;
      sync();
    });
    classes.observe(root, { attributes: true, attributeFilter: ['class'] });
    const sizes = new ResizeObserver(resize);
    sizes.observe(canvas);
    document.addEventListener('visibilitychange', sync);
    resize();
    sync();
    return () => {
      unsubscribe?.();
      classes.disconnect();
      sizes.disconnect();
      document.removeEventListener('visibilitychange', sync);
      canvas.width = canvas.height = 1;
    };
  }, []);

  return <canvas ref={ref} className={className} data-ascii-reactor="true" aria-hidden="true" />;
});
