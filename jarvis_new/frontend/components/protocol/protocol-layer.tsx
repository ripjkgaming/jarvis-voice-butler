'use client';

import { memo, useEffect, useRef, useState } from 'react';
import stark from '@/components/stark/stark.module.css';
import { useProtocol } from '@/hooks/hud/use-protocol';
import type { ProtocolState } from '@/lib/protocol-layout';
import {
  type MapInfo,
  type OrbAnchor,
  type ProtocolRenderer,
  createProtocolRenderer,
} from '@/lib/protocol-render';
import s from './protocol.module.css';

/** Where the HUD's arc reactor sits, so the red orb spins up in its place. */
function reactorAnchor(): OrbAnchor | null {
  const el = document.querySelector<HTMLElement>(`.${stark.reactor}`);
  const r = el?.getBoundingClientRect();
  if (!r || r.width < 40 || r.height < 40) return null;
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, r: Math.min(r.width, r.height) * 0.4 };
}

const ProtocolCanvas = memo(function ProtocolCanvas({
  run,
  state,
  exiting,
  onExited,
  onMapReady,
  overlay,
}: {
  run: number;
  state: ProtocolState | null;
  exiting: boolean;
  onExited: (run: number) => void;
  onMapReady: (info: MapInfo) => void;
  overlay?: (ctx: CanvasRenderingContext2D, now: number, info: MapInfo) => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const renderer = useRef<ProtocolRenderer | null>(null);
  const cbs = useRef({ onExited, onMapReady, overlay });
  cbs.current = { onExited, onMapReady, overlay };
  const first = useRef(state);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const r = createProtocolRenderer(canvas, {
      run,
      anchor: reactorAnchor,
      onExited: () => cbs.current.onExited(run),
      onMapReady: (info) => cbs.current.onMapReady(info),
      overlay: cbs.current.overlay
        ? (ctx, now, info) => cbs.current.overlay?.(ctx, now, info)
        : undefined,
    });
    r.update(first.current);
    renderer.current = r;
    return () => {
      r.destroy();
      renderer.current = null;
    };
  }, [run]);

  useEffect(() => {
    renderer.current?.update(state);
  }, [state]);

  useEffect(() => {
    if (exiting) renderer.current?.exit();
  }, [exiting]);

  return <canvas ref={ref} className={s.canvas} aria-hidden="true" />;
});

/**
 * Protocol 12: the local-subnet network map (src/netscan.py via the
 * bridge). Phase 1 spins the reactor up into a red protocol frame while
 * the real scan runs; phase 2 morphs it into a terminal holding the device
 * map. Voice-only: no controls, pointer-events none; Esc aborts.
 *
 * Hook for later phases: `onMapReady` fires once the map has fully
 * revealed (phase 'done') with the device box rectangles, and `overlay`
 * draws on the same canvas every frame while the map is held.
 */
export function ProtocolLayer({
  enabled = true,
  onMapReady,
  overlay,
}: {
  enabled?: boolean;
  onMapReady?: (info: MapInfo) => void;
  overlay?: (ctx: CanvasRenderingContext2D, now: number, info: MapInfo) => void;
}) {
  const { state, view, finish, toast, preflightMessage } = useProtocol(enabled);
  const [ready, setReady] = useState<number | null>(null);
  const exiting = !!view?.exiting;

  // Cards fade/blur out (stark.module.css, html[data-protocol]) and back.
  useEffect(() => {
    const root = document.documentElement;
    if (view) root.dataset.protocol = exiting ? 'off' : 'on';
    else delete root.dataset.protocol;
  }, [view, exiting]);
  useEffect(() => () => void delete document.documentElement.dataset.protocol, []);

  const shown = view && state && state.run === view.run ? state : null;

  return (
    <div
      className={s.layer}
      data-protocol-layer={view ? (exiting ? 'exiting' : 'active') : 'idle'}
      data-map-ready={view && ready === view.run ? 'true' : undefined}
    >
      {view ? (
        <ProtocolCanvas
          key={view.run}
          run={view.run}
          state={shown}
          exiting={exiting}
          onExited={finish}
          onMapReady={(info) => {
            setReady(info.run);
            onMapReady?.(info);
          }}
          overlay={overlay}
        />
      ) : null}
      <div className={s.chips}>
        {preflightMessage ? (
          <span className={s.chip} data-tone="warn" role="status">
            <i aria-hidden="true">▲</i>
            {preflightMessage}
          </span>
        ) : null}
        {toast ? (
          <span key={toast.seq} className={s.chip} data-tone="notice" role="status">
            <i aria-hidden="true">■</i>
            {toast.text}
          </span>
        ) : null}
      </div>
    </div>
  );
}
