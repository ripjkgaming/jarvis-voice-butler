'use client';

import { useEffect, useRef } from 'react';
import s from './stark.module.css';

const GLYPHS = '01▓░▒╳#<>/{}[]ABCDEF';

type Live = { stage: number; complete: boolean };

type Bolt = { pts: [number, number][]; life: number };

function bolt(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  jit: number,
  depth: number,
  out: [number, number][]
) {
  if (depth === 0) {
    out.push([x2, y2]);
    return;
  }
  const mx = (x1 + x2) / 2 + (Math.random() - 0.5) * jit;
  const my = (y1 + y2) / 2 + (Math.random() - 0.5) * jit;
  bolt(x1, y1, mx, my, jit / 2, depth - 1, out);
  bolt(mx, my, x2, y2, jit / 2, depth - 1, out);
}

/**
 * VFX layer of the takeover: a noisy red infection front spreading from the
 * reactor with glyph rain inside it, expanding shock rings, lightning from the
 * screen edges, sparks, a rotating target lock, and a pair of glaring Ultron
 * eyes that open at the end. Canvas 2D at 0.75 scale; stops when `on` clears.
 */
export function BreachFx({
  on,
  stage,
  complete,
}: {
  on: boolean;
  stage: number;
  complete: boolean;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useRef<Live>({ stage: 0, complete: false });
  live.current = { stage, complete };

  useEffect(() => {
    if (!on) return;
    const cv = ref.current;
    const ctx = cv?.getContext('2d');
    if (!cv || !ctx) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const SC = 0.75;
    let W = 0;
    let H = 0;
    const resize = () => {
      W = Math.floor(cv.clientWidth * SC);
      H = Math.floor(cv.clientHeight * SC);
      cv.width = W;
      cv.height = H;
    };
    resize();
    window.addEventListener('resize', resize);

    const cols = Array.from({ length: 56 }, (_, i) => ({
      x: i,
      sp: 50 + Math.random() * 170,
      off: Math.random() * 900,
      ch: Math.floor(Math.random() * GLYPHS.length),
    }));
    const rings: { r: number; v: number; life: number; w: number }[] = [];
    const sparks: { x: number; y: number; vx: number; vy: number; l: number }[] = [];
    const bolts: Bolt[] = [];
    let R = 0;
    let lastStage = -1;
    let eyeA = 0;
    let lastBeat = -1;
    let raf = 0;
    const t0 = performance.now();

    const center = () => {
      const orb = document.querySelector<HTMLElement>('[data-ix-orb]');
      const cr = cv.getBoundingClientRect();
      if (orb) {
        const r = orb.getBoundingClientRect();
        return [
          ((r.left + r.width / 2 - cr.left) / cr.width) * W,
          ((r.top + r.height / 2 - cr.top) / cr.height) * H,
        ];
      }
      return [W / 2, H / 2];
    };

    const frame = (now: number) => {
      const t = (now - t0) / 1000;
      const { stage: st, complete: done } = live.current;
      const [cx, cy] = center();
      const diag = Math.hypot(W, H);
      ctx.clearRect(0, 0, W, H);

      if (st !== lastStage) {
        lastStage = st;
        if (st > 0) rings.push({ r: 0, v: diag * (0.9 + st * 0.3), life: 1, w: 8 + st * 4 });
      }
      const beat = Math.floor(t / 1.1);
      if (beat !== lastBeat && st > 0) {
        lastBeat = beat;
        rings.push({ r: 0, v: diag * 0.6, life: 0.8, w: 4 });
      }

      // infection front
      const target = [0, 0.3, 0.65, 1.3][st] * diag;
      R += (target - R) * (reduced ? 0.5 : 0.06);
      if (R > 4) {
        const path = new Path2D();
        const N = 120;
        for (let i = 0; i <= N; i++) {
          const a = (i / N) * Math.PI * 2;
          const r =
            R *
            (1 +
              0.1 * Math.sin(5 * a + t * 3) +
              0.07 * Math.sin(11 * a - t * 5) +
              (Math.random() - 0.5) * 0.035);
          const x = cx + Math.cos(a) * r;
          const y = cy + Math.sin(a) * r;
          if (i) path.lineTo(x, y);
          else path.moveTo(x, y);
        }
        path.closePath();
        ctx.save();
        ctx.clip(path);
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
        g.addColorStop(0, 'rgba(255,20,40,0.30)');
        g.addColorStop(1, 'rgba(120,0,12,0.5)');
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, W, H);
        ctx.font = '700 15px ui-monospace,Menlo,monospace';
        ctx.fillStyle = '#ff3a4c';
        const cw = W / cols.length;
        for (const c of cols) {
          const y0 = ((c.off + t * c.sp) % (H + 200)) - 200;
          for (let k = 0; k < 8; k++) {
            ctx.globalAlpha = 0.5 * (1 - k / 8);
            ctx.fillText(
              GLYPHS[(c.ch + k + Math.floor(t * 9)) % GLYPHS.length],
              c.x * cw,
              y0 + k * 17
            );
          }
        }
        ctx.globalAlpha = 1;
        ctx.restore();
        ctx.strokeStyle = 'rgba(255,70,90,0.95)';
        ctx.lineWidth = 3;
        ctx.shadowColor = '#ff1f33';
        ctx.shadowBlur = 22;
        ctx.stroke(path);
        ctx.shadowBlur = 0;
        if (!reduced) {
          for (let k = 0; k < 4; k++) {
            const a = Math.random() * Math.PI * 2;
            sparks.push({
              x: cx + Math.cos(a) * R,
              y: cy + Math.sin(a) * R,
              vx: (Math.random() - 0.5) * 420,
              vy: (Math.random() - 0.5) * 420,
              l: 0.5,
            });
          }
        }
      }

      // shock rings (dashed, hex-tech)
      ctx.setLineDash([18, 10]);
      for (let i = rings.length - 1; i >= 0; i--) {
        const rg = rings[i];
        rg.r += rg.v / 60;
        rg.life -= 1 / 70;
        if (rg.life <= 0) {
          rings.splice(i, 1);
          continue;
        }
        ctx.beginPath();
        ctx.arc(cx, cy, rg.r, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(255,${60 + Math.random() * 100},${80},${rg.life})`;
        ctx.lineWidth = rg.w * rg.life;
        ctx.shadowColor = '#ff1f33';
        ctx.shadowBlur = 18;
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.shadowBlur = 0;

      // rotating target lock around the reactor
      if (st >= 1) {
        const lr = Math.min(W, H) * 0.24;
        ctx.strokeStyle = 'rgba(255,60,80,0.8)';
        ctx.lineWidth = 2;
        for (let k = 0; k < 4; k++) {
          const a0 = t * (k % 2 ? -1.6 : 1.2) + (k * Math.PI) / 2;
          ctx.beginPath();
          ctx.arc(cx, cy, lr * (1 + k * 0.08), a0, a0 + 0.9);
          ctx.stroke();
        }
        ctx.beginPath();
        ctx.moveTo(cx - lr * 1.5, cy);
        ctx.lineTo(cx - lr * 1.1, cy);
        ctx.moveTo(cx + lr * 1.1, cy);
        ctx.lineTo(cx + lr * 1.5, cy);
        ctx.moveTo(cx, cy - lr * 1.5);
        ctx.lineTo(cx, cy - lr * 1.1);
        ctx.moveTo(cx, cy + lr * 1.1);
        ctx.lineTo(cx, cy + lr * 1.5);
        ctx.stroke();
      }

      // lightning from the edges into the reactor
      if (st >= 2 && !reduced) {
        if (Math.random() < 0.45) {
          const side = Math.floor(Math.random() * 4);
          const sx = side < 2 ? Math.random() * W : side === 2 ? 0 : W;
          const sy = side === 0 ? 0 : side === 1 ? H : Math.random() * H;
          const pts: [number, number][] = [[sx, sy]];
          bolt(
            sx,
            sy,
            cx + (Math.random() - 0.5) * 80,
            cy + (Math.random() - 0.5) * 80,
            diag * 0.12,
            6,
            pts
          );
          bolts.push({ pts, life: 3 });
        }
      }
      for (let i = bolts.length - 1; i >= 0; i--) {
        const b = bolts[i];
        if (--b.life < 0) {
          bolts.splice(i, 1);
          continue;
        }
        ctx.beginPath();
        b.pts.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.strokeStyle = b.life % 2 ? 'rgba(255,255,255,0.95)' : 'rgba(255,90,110,0.9)';
        ctx.lineWidth = 2.2;
        ctx.shadowColor = '#ff1f33';
        ctx.shadowBlur = 20;
        ctx.stroke();
        ctx.shadowBlur = 0;
      }

      // sparks
      for (let i = sparks.length - 1; i >= 0; i--) {
        const p = sparks[i];
        p.x += p.vx / 60;
        p.y += p.vy / 60;
        p.l -= 1 / 60;
        if (p.l <= 0) {
          sparks.splice(i, 1);
          continue;
        }
        ctx.globalAlpha = p.l * 2;
        ctx.fillStyle = Math.random() < 0.5 ? '#ff6b78' : '#fff';
        ctx.fillRect(p.x, p.y, 3, 3);
      }
      ctx.globalAlpha = 1;

      // glaring eyes open when the takeover lands
      eyeA += ((st >= 3 ? 1 : 0) - eyeA) * 0.08;
      if (eyeA > 0.02) {
        const ew = W * 0.2;
        const eh = ew * 0.55;
        const ey = H * 0.2;
        const k = t % 4.6;
        const blink = k > 4.2 ? 1 - Math.abs((k - 4.4) / 0.2) : 0;
        const lid = Math.max(0, Math.min(1, blink));
        const look = Math.sin(t * 1.3) * 0.3;
        ctx.globalAlpha = eyeA * (done ? 1 : 0.9);
        for (const [ex, side] of [
          [W * 0.29, 1],
          [W * 0.71, -1],
        ] as const) {
          ctx.save();
          ctx.translate(ex, ey);
          ctx.scale(side, 1);
          ctx.beginPath();
          ctx.moveTo(-ew / 2, -eh * 0.34);
          ctx.lineTo(ew / 2, eh * 0.04);
          ctx.quadraticCurveTo(ew * 0.05, eh * 0.78, -ew / 2, -eh * 0.28);
          ctx.closePath();
          ctx.save();
          ctx.clip();
          ctx.fillStyle = '#070001';
          ctx.fillRect(-ew, -eh, ew * 2, eh * 2);
          const ix = look * ew * 0.1;
          const iy = eh * 0.08;
          const R2 = eh * 0.5;
          const ig = ctx.createRadialGradient(ix, iy, R2 * 0.1, ix, iy, R2);
          ig.addColorStop(0, '#fff6f7');
          ig.addColorStop(0.2, '#ff6676');
          ig.addColorStop(0.55, '#e0121f');
          ig.addColorStop(1, '#3d0006');
          ctx.beginPath();
          ctx.arc(ix, iy, R2, 0, 7);
          ctx.fillStyle = ig;
          ctx.fill();
          ctx.beginPath();
          ctx.ellipse(ix, iy, R2 * 0.07, R2 * 0.72, 0, 0, 7);
          ctx.fillStyle = '#000';
          ctx.fill();
          if (lid > 0.02) {
            ctx.fillStyle = '#12020a';
            ctx.fillRect(-ew, -eh, ew * 2, eh * (0.2 + lid * 1.1));
          }
          ctx.restore();
          ctx.shadowColor = '#ff1f33';
          ctx.shadowBlur = 26;
          ctx.strokeStyle = '#ff2a3c';
          ctx.lineWidth = 3;
          ctx.stroke();
          ctx.restore();
        }
        ctx.shadowBlur = 0;
        ctx.globalAlpha = 1;
      }

      // rolling scan bar
      const ry = ((t * 360) % (H + 200)) - 100;
      const sg = ctx.createLinearGradient(0, ry, 0, ry + 100);
      sg.addColorStop(0, 'rgba(255,60,80,0)');
      sg.addColorStop(0.5, `rgba(255,120,130,${0.05 + st * 0.03})`);
      sg.addColorStop(1, 'rgba(255,60,80,0)');
      ctx.fillStyle = sg;
      ctx.fillRect(0, ry, W, 100);

      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
    };
  }, [on]);

  return <canvas ref={ref} className={s.breachFx} aria-hidden="true" />;
}
