'use client';

import { memo, useEffect, useRef } from 'react';
import {
  type AmbientState,
  type AmbientVariant,
  createAmbientScene,
  subscribeAmbientFrame,
} from '@/lib/ambient-particles';

/** Decorative light field. Resizing is observer-driven; animation never
 * updates React state or reads layout. Hidden, offscreen, transition-owned,
 * and reduced-motion surfaces release their shared frame subscription. */
export const AmbientParticles = memo(function AmbientParticles({
  variant,
  state = 'idle',
  className,
  active = true,
  anchorSelector,
}: {
  variant: AmbientVariant;
  state?: AmbientState;
  className?: string;
  active?: boolean;
  anchorSelector?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const settings = useRef({ active, state });
  settings.current = { active, state };
  const update = useRef<(() => void) | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const scene = createAmbientScene(canvas, variant, settings.current.state);
    if (!scene) return;
    const root = document.documentElement;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    let inView = true;
    let unsubscribe: (() => void) | null = null;
    let resolution: MediaQueryList | null = null;

    const sync = () => {
      scene.setState(settings.current.state);
      const visible = !document.hidden && !root.classList.contains('hud-hidden') && inView;
      const running =
        visible &&
        settings.current.active &&
        !reduced.matches &&
        !root.classList.contains('school-tx') &&
        !root.classList.contains('suit-open');
      if (running && !unsubscribe) unsubscribe = subscribeAmbientFrame(scene.draw);
      if (!running && unsubscribe) {
        unsubscribe();
        unsubscribe = null;
      }
      canvas.dataset.ambientRunning = String(running);
      if (visible && !running) scene.draw();
    };
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const target = anchorSelector ? canvas.parentElement?.querySelector(anchorSelector) : null;
      const anchor = target?.getBoundingClientRect();
      scene.resize(
        rect.width,
        rect.height,
        window.devicePixelRatio || 1,
        anchor
          ? {
              x: anchor.left - rect.left,
              y: anchor.top - rect.top,
              width: anchor.width,
              height: anchor.height,
            }
          : null
      );
      sync();
    };
    const densityChanged = () => {
      resolution?.removeEventListener('change', densityChanged);
      resolution = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      resolution.addEventListener('change', densityChanged);
      resize();
    };
    const sizes = new ResizeObserver(resize);
    sizes.observe(canvas);
    const anchor = anchorSelector ? canvas.parentElement?.querySelector(anchorSelector) : null;
    if (anchor) sizes.observe(anchor);
    const visibility = new IntersectionObserver(([entry]) => {
      inView = entry.isIntersecting;
      sync();
    });
    visibility.observe(canvas);
    const classes = new MutationObserver(sync);
    classes.observe(root, { attributes: true, attributeFilter: ['class'] });
    document.addEventListener('visibilitychange', sync);
    window.addEventListener('resize', resize);
    reduced.addEventListener('change', sync);
    update.current = sync;
    densityChanged();
    return () => {
      update.current = null;
      unsubscribe?.();
      sizes.disconnect();
      visibility.disconnect();
      classes.disconnect();
      document.removeEventListener('visibilitychange', sync);
      window.removeEventListener('resize', resize);
      reduced.removeEventListener('change', sync);
      resolution?.removeEventListener('change', densityChanged);
      scene.dispose();
    };
  }, [variant, anchorSelector]);

  useEffect(() => update.current?.(), [state, active]);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      data-ambient-particles={variant}
      aria-hidden="true"
      role="presentation"
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
      }}
    />
  );
});
