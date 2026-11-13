'use client';

import { type ReactNode, memo, useEffect, useRef, useState } from 'react';
import { AsciiReactor } from '@/components/ascii/ascii-reactor';
import type { JarvisState } from '@/hooks/hud/use-jarvis-state';
import { subscribeAmbientFrame } from '@/lib/ambient-particles';
import { prefersReducedMotion } from '@/lib/ascii';
import { OrbGL, type OrbInputs, loadOrbData } from '@/lib/orb-gl';
import { getOrbEnergy } from '@/lib/orb-motion';

const SPEED: Record<JarvisState, number> = {
  idle: 1,
  listening: 1.3,
  thinking: 1.9,
  speaking: 1.2,
};

/**
 * The arc-reactor orb: 14 layered shells of line work, each spinning on its own
 * axis and speed (lib/orb-gl.ts). Tinted by the voice state, grey when muted,
 * amber while a warning is lit, assembling shell by shell whenever the HUD
 * appears, throwing a shockwave on wake and on every state change.
 * Falls back to the ASCII reactor if WebGL or the orb data is unavailable.
 */
export const OrbReactor = memo(function OrbReactor({
  color,
  jarvis,
  muted,
  activation,
  energizing,
  warn,
  fallback,
  className,
}: {
  color: string;
  jarvis: JarvisState;
  muted: boolean;
  activation?: number;
  energizing?: boolean;
  /** 0..1 amber warning level (0 = none). */
  warn: number;
  /** Shown instead of the ASCII reactor if WebGL / data fail. */
  fallback?: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  const live = useRef({
    color,
    jarvis,
    muted,
    energizing: !!energizing,
    warn,
    shock: false,
    wake: false,
  });

  useEffect(() => {
    const L = live.current;
    if (L.jarvis !== jarvis || L.muted !== muted) L.shock = true;
    Object.assign(L, { color, jarvis, muted, energizing: !!energizing, warn });
  }, [color, jarvis, muted, energizing, warn]);

  useEffect(() => {
    if (activation) live.current.wake = true;
  }, [activation]);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || failed) return;
    let orb: OrbGL | null = null;
    let dead = false;
    let unsubscribe: (() => void) | null = null;
    const root = document.documentElement;
    let wasHidden = root.classList.contains('hud-hidden');
    let sizes: ResizeObserver | null = null;
    let classes: MutationObserver | null = null;
    const inputs: OrbInputs = {
      color,
      muted,
      speed: 1,
      boost: 0,
      warn: 0,
      energy: 1,
      reduced: false,
    };
    const t0 = performance.now();

    const draw = (delta: number) => {
      if (!orb) return;
      if (orb.lost) {
        setFailed(true);
        return;
      }
      const L = live.current;
      const now = performance.now();
      const t = (now - t0) / 1000;
      if (L.shock || L.wake) {
        orb.shock(now);
        L.shock = false;
        L.wake = false;
      }
      inputs.color = L.color;
      inputs.muted = L.muted;
      inputs.speed = SPEED[L.jarvis];
      inputs.energy = getOrbEnergy();
      inputs.reduced = prefersReducedMotion();
      inputs.warn = L.warn;
      inputs.boost =
        (L.jarvis === 'speaking'
          ? 0.35 + 0.25 * Math.sin(t * 9)
          : L.jarvis === 'listening'
            ? 0.2
            : 0) + (L.energizing ? 0.4 : 0);
      orb.frame(delta || 16.7, inputs, now);
    };

    const sync = () => {
      const visible = !document.hidden && !root.classList.contains('hud-hidden');
      if (visible && !unsubscribe && orb) unsubscribe = subscribeAmbientFrame(draw);
      if (!visible && unsubscribe) {
        unsubscribe();
        unsubscribe = null;
      }
    };
    const rebuild = () => orb?.rebuild();

    loadOrbData()
      .then((data) => {
        if (dead) return;
        orb = new OrbGL({ canvas, data });
        orb.resize();
        orb.rebuild();
        sizes = new ResizeObserver(() => orb?.resize());
        sizes.observe(canvas);
        classes = new MutationObserver(() => {
          const hidden = root.classList.contains('hud-hidden');
          if (wasHidden && !hidden) orb?.rebuild();
          wasHidden = hidden;
          sync();
        });
        classes.observe(root, { attributes: true, attributeFilter: ['class'] });
        window.addEventListener('hud-reactor-rebuild', rebuild);
        document.addEventListener('visibilitychange', sync);
        sync();
      })
      .catch(() => {
        if (!dead) setFailed(true);
      });

    return () => {
      dead = true;
      unsubscribe?.();
      sizes?.disconnect();
      classes?.disconnect();
      window.removeEventListener('hud-reactor-rebuild', rebuild);
      document.removeEventListener('visibilitychange', sync);
      orb?.dispose();
    };
    // color/muted are read live from the ref; the GL context lives once per mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [failed]);

  if (failed) {
    if (fallback) return <>{fallback}</>;
    return (
      <AsciiReactor
        className={className}
        color={color}
        jarvis={jarvis}
        muted={muted}
        activation={activation}
        energizing={energizing}
      />
    );
  }
  return <canvas ref={ref} className={className} data-ascii-reactor="true" aria-hidden="true" />;
});
