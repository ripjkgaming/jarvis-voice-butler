'use client';

import { memo, useEffect, useRef } from 'react';
import { type ReplicaFrame, type WallData, WallReplica } from '@/lib/wall-replica';
import intro from './intro.module.css';
import { type ReplicaPainter, orbMorph } from './orb-morph';
import { WALL_FRAME_EVENT, refreshWallFrame, wallFill } from './wall-emblem';

let dataPromise: Promise<WallData | null> | null = null;
function loadData(): Promise<WallData | null> {
  dataPromise ??= fetch('/wall-cells.json')
    .then((r) => (r.ok ? (r.json() as Promise<WallData>) : null))
    .catch(() => null);
  return dataPromise;
}

/** Wallpaper fill scale and the emblem region's position on screen. Plasma's
 *  "scaled and cropped" fill: scale = max(vw / src.w, vh / src.h), centred. */
export function wallPlacement(rep: WallReplica, vw: number, vh: number) {
  const [sw, sh] = rep.data.src;
  const m = wallFill(vw, vh, sw, sh);
  const s = m.s;
  return {
    s,
    left: m.x(rep.rx0),
    top: m.y(rep.ry0),
    width: rep.rw * s,
    height: rep.rh * s,
    /** emblem centre and ascii radius in window px */
    cx: m.x(rep.cxPx),
    cy: m.y(rep.cyPx),
    r: s * rep.rPx,
  };
}

export type WallPlacement = ReturnType<typeof wallPlacement>;

/** The wallpaper's emblem, rebuilt cell by cell on a canvas that sits exactly
 *  on top of the wallpaper (identical pixels, so invisible while static) and
 *  animates from that pose: see orb-morph.ts. */
export const WallReplicaCanvas = memo(function WallReplicaCanvas() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    let disposed = false;
    let rep: WallReplica | null = null;
    const root = canvas.closest('.hud') ?? document.documentElement;

    const place = () => {
      if (!rep) return;
      const p = wallPlacement(rep, window.innerWidth, window.innerHeight);
      canvas.style.left = `${p.left}px`;
      canvas.style.top = `${p.top}px`;
      canvas.style.width = `${p.width}px`;
      canvas.style.height = `${p.height}px`;
      (window as unknown as { __jarvisAlign?: unknown }).__jarvisAlign = {
        ...(window as unknown as { __jarvisAlign?: object }).__jarvisAlign,
        emblem: { cx: p.cx, cy: p.cy, r: p.r, scale: p.s },
      };
    };

    void loadData().then((data) => {
      const ctx = canvas.getContext('2d');
      if (disposed || !data || !ctx) return;
      const r = new WallReplica(data);
      rep = r;
      canvas.width = r.rw;
      canvas.height = r.rh;
      const full = ctx.createImageData(r.rw, r.rh);
      const box = ctx.createImageData(r.bw, r.bh);
      const glowCanvas = document.createElement('canvas');
      glowCanvas.width = r.dsBW;
      glowCanvas.height = r.dsBH;
      const gctx = glowCanvas.getContext('2d');
      const glowImg = gctx?.createImageData(r.dsBW, r.dsBH);
      const painter: ReplicaPainter = {
        staticT: r.data.reactor.t,
        // bit-exact (Pillow's blur reproduced): identical to the PNG
        drawStatic: () => {
          r.render(r.staticFrame(), full.data, true);
          ctx.globalCompositeOperation = 'source-over';
          ctx.putImageData(full, 0, 0);
        },
        // animated emblem box: CPU glyph raster + GPU bilinear glow
        drawFrame: (f: ReplicaFrame) => {
          if (!gctx || !glowImg) return;
          r.paintBox(f);
          const src = r.boxRgb;
          const dst = box.data;
          for (let i = 0, j = 0, n = r.bw * r.bh; i < n; i++, j += 3) {
            const o = i * 4;
            dst[o] = src[j];
            dst[o + 1] = src[j + 1];
            dst[o + 2] = src[j + 2];
            dst[o + 3] = 255;
          }
          ctx.globalCompositeOperation = 'source-over';
          ctx.putImageData(box, r.bx, r.by);
          glowImg.data.set(r.glowRgba);
          gctx.putImageData(glowImg, 0, 0);
          ctx.globalCompositeOperation = 'screen';
          ctx.imageSmoothingEnabled = true;
          ctx.drawImage(glowCanvas, 0, 0, r.dsBW, r.dsBH, r.bx, r.by, r.bw, r.bh);
          ctx.globalCompositeOperation = 'source-over';
        },
      };
      place();
      painter.drawStatic();
      canvas.dataset.ready = 'true';
      orbMorph.attach(root, painter);
    });

    const onResize = () => {
      place();
      refreshWallFrame();
    };
    refreshWallFrame();
    window.addEventListener('resize', onResize);
    window.addEventListener(WALL_FRAME_EVENT, place);
    return () => {
      disposed = true;
      window.removeEventListener('resize', onResize);
      window.removeEventListener(WALL_FRAME_EVENT, place);
      orbMorph.detach();
    };
  }, []);

  return <canvas ref={ref} className={intro.replica} data-ix-replica aria-hidden="true" />;
});
