'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  DOWNLINK_MS,
  type Dir,
  type HudRect,
  type Packet,
  type Seg,
  UPLINK_MS,
  beamEnd,
  clamp01,
  downlink,
  drawPackets,
  drawSeg,
  easeIn,
  easeInOut,
  easeOut,
  frameClock,
  loadHudRect,
  rgb,
  runTimers,
  stage,
  uplink,
  waitForSchoolScreen,
} from '@/components/hud/school-transition';
import { bridgeSchoolGeom } from '@/lib/bridge';

/* Leaving school mode: the HUD is drafted back like a blueprint.
 *
 *   sink    the bar sinks away, leaving its top edge lit across the screen
 *   split   that edge retracts into its two ends
 *   draft   the ends peel off as tracers, run up the screen sides to the
 *           HUD's bottom corners and draft its outline where it will land
 *           (up the sides, across the top; a branch draws the bottom)
 *   lock    corner brackets snap on, a faint grid and label fill the frame
 *   scan    the shell puts the HUD window there and it scans in top to
 *           bottom behind a bright line, the blueprint fading as it goes
 *
 * As on the way in, the page drives it and the shell only moves the window
 * ("cover-at", then "restore"); school.rs restores directly if it stalls. */

const SINK_MS = 380;
const SPLIT_MS = 300;
/** Tracer speed crossing to the HUD's corner, then drafting (px per ms). */
const TRAVEL_PX_MS = 2.4;
const DRAFT_PX_MS = 2.1;
const LOCK_MS = 320;
const SCAN_MS = 720;
const TAIL_PX = 220;
const BRACKET_PX = 26;
/** Blueprint grid pitch inside the frame (px). */
const GRID_PX = 32;
/** Content size to land on when no entry was ever recorded. */
const DEFAULT_W = 1280;
const DEFAULT_H = 720;

export type ReturnStage = 'bar' | 'sink' | 'draft' | 'scan';
export type SchoolRet = { id: number };

/** A floating panel's gap (px) around the docked bar: school.rs docks it
 *  narrower than the screen by that much on each side. 0 when flush. */
function dockedInset(): number {
  const gap = (window.screen.width - window.innerWidth) / 2;
  return window.innerHeight < 300 && gap >= 2 && gap <= 24 ? Math.round(gap) : 0;
}

/** Listens for the shell's "return" signal and owns the return's state:
 *  which face HudShell shows (`retStage`) and the bar's docked height. */
export function useSchoolReturn() {
  const [ret, setRet] = useState<SchoolRet | null>(null);
  const [retStage, setRetStage] = useState<ReturnStage>('bar');
  const [barPx, setBarPx] = useState(0);
  const [barInset, setBarInset] = useState(0);
  useEffect(() => {
    const onSignal = (e: Event) => {
      const phase = String((e as CustomEvent).detail);
      if (phase === 'return') {
        // Menus grow the window by 480 px; only the actual strip sinks.
        const bar = document.querySelector<HTMLElement>('.sbar');
        setBarPx(bar?.getBoundingClientRect().height || Math.min(140, window.innerHeight));
        setBarInset(dockedInset());
        setRetStage('bar');
        setRet({ id: Date.now() });
      } else if (phase === 'collapse' || phase === 'arrive' || phase === 'expand') {
        setRet(null);
      }
    };
    window.addEventListener('jarvis-school', onSignal);
    return () => window.removeEventListener('jarvis-school', onSignal);
  }, []);
  const done = useCallback(() => setRet(null), []);
  return { ret, retStage, setRetStage, barPx, barInset, done };
}

/* ------------------------------ drafting ------------------------------ */

type Pt = [number, number];

/** One drafting pen: travels `travel` (a fading streak), then draws
 *  `draft` (kept, the blueprint line). Coordinates are relative to the
 *  HUD's content rect. */
type Pen = {
  pts: Pt[];
  cum: number[];
  travelLen: number;
  total: number;
  t0: number;
  travelMs: number;
  draftMs: number;
};

function makePen(travel: Pt[], draft: Pt[], t0: number): Pen {
  const pts = [...travel, ...draft.slice(travel.length ? 1 : 0)];
  const cum = [0];
  for (let i = 1; i < pts.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  }
  let travelLen = 0;
  for (let i = 1; i < travel.length; i++) {
    travelLen += Math.hypot(travel[i][0] - travel[i - 1][0], travel[i][1] - travel[i - 1][1]);
  }
  const total = cum[cum.length - 1];
  return {
    pts,
    cum,
    travelLen,
    total,
    t0,
    travelMs: travelLen / TRAVEL_PX_MS,
    draftMs: (total - travelLen) / DRAFT_PX_MS,
  };
}

function penAt(p: Pen, d: number): Pt {
  const dd = Math.max(0, Math.min(p.total, d));
  let i = 1;
  while (i < p.cum.length - 1 && p.cum[i] < dd) i++;
  const seg = p.cum[i] - p.cum[i - 1] || 1;
  const f = (dd - p.cum[i - 1]) / seg;
  const a = p.pts[i - 1];
  const b = p.pts[i];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

/** Distance drawn so far: the crossing eases in and out, the drafting
 *  runs on steadily and settles into the end. */
function penHead(p: Pen, now: number): number {
  const t = now - p.t0;
  if (t <= 0) return 0;
  if (t < p.travelMs) return p.travelLen * easeInOut(t / p.travelMs);
  return p.travelLen + (p.total - p.travelLen) * easeOut(clamp01((t - p.travelMs) / p.draftMs));
}

const penDone = (p: Pen, now: number) => now - p.t0 >= p.travelMs + p.draftMs;

type Scene = {
  /** Screen offset of the content rect (0,0 once the window is the HUD). */
  ox: number;
  oy: number;
  w: number;
  h: number;
  hair: Seg[];
  pens: Pen[];
  /** Blueprint line strength, brackets, grid and label (0-1 each). */
  outline: number;
  lock: number;
  grid: number;
  label: number;
  /** Scan line, 0-1 down the frame; null before the scan. */
  scan: number | null;
  onResize: ((w: number, h: number) => void) | null;
  /** The data beam when the HUD returns to another monitor. */
  packets: Packet[];
};

function drawPen(ctx: CanvasRenderingContext2D, p: Pen, now: number, outline: number, c: string) {
  const head = penHead(p, now);
  if (head <= 0) return;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = c;
  // The drafted outline stays.
  if (head > p.travelLen && outline > 0.001) {
    const path = new Path2D();
    const start = penAt(p, p.travelLen);
    path.moveTo(start[0], start[1]);
    for (let i = 1; i < p.pts.length; i++) {
      if (p.cum[i] <= p.travelLen) continue;
      if (p.cum[i] >= head) break;
      path.lineTo(p.pts[i][0], p.pts[i][1]);
    }
    const end = penAt(p, head);
    path.lineTo(end[0], end[1]);
    ctx.globalAlpha = 0.14 * outline;
    ctx.lineWidth = 6;
    ctx.stroke(path);
    ctx.globalAlpha = 0.9 * outline;
    ctx.lineWidth = 1.5;
    ctx.stroke(path);
  }
  // The crossing leaves a fading streak.
  const from = Math.max(0, head - TAIL_PX);
  const to = Math.min(head, p.travelLen);
  if (to > from) {
    const steps = Math.max(2, Math.ceil((to - from) / 4));
    let prev = penAt(p, from);
    for (let k = 1; k <= steps; k++) {
      const d = from + ((to - from) * k) / steps;
      const pt = penAt(p, d);
      const f = 1 - (head - d) / TAIL_PX;
      ctx.globalAlpha = f ** 1.3;
      ctx.lineWidth = 0.6 + 2.6 * f;
      ctx.beginPath();
      ctx.moveTo(prev[0], prev[1]);
      ctx.lineTo(pt[0], pt[1]);
      ctx.stroke();
      prev = pt;
    }
  }
  // The pen tip, fading once it has finished drawing.
  const fade = penDone(p, now) ? 1 - clamp01((now - p.t0 - p.travelMs - p.draftMs) / 220) : 1;
  if (fade > 0.01) {
    const [hx, hy] = penAt(p, head);
    const g = ctx.createRadialGradient(hx, hy, 0, hx, hy, 13);
    g.addColorStop(0, 'rgba(255,255,255,0.95)');
    g.addColorStop(0.3, c);
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.globalAlpha = 0.9 * fade;
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(hx, hy, 13, 0, Math.PI * 2);
    ctx.fill();
  }
}

/** Corner brackets snapping in from further out, a faint grid inside the
 *  frame, and a small label. */
function drawBlueprint(ctx: CanvasRenderingContext2D, s: Scene, c: string) {
  const { w, h } = s;
  if (s.grid > 0.001) {
    const [r, g, b] = rgb(c);
    ctx.strokeStyle = `rgb(${r},${g},${b})`;
    ctx.globalAlpha = 0.07 * s.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = GRID_PX; x < w; x += GRID_PX) {
      ctx.moveTo(x + 0.5, 0);
      ctx.lineTo(x + 0.5, h);
    }
    for (let y = GRID_PX; y < h; y += GRID_PX) {
      ctx.moveTo(0, y + 0.5);
      ctx.lineTo(w, y + 0.5);
    }
    ctx.stroke();
  }
  if (s.lock > 0.001) {
    const out = 6 + 22 * (1 - s.lock);
    const arm = BRACKET_PX;
    ctx.strokeStyle = c;
    ctx.lineCap = 'square';
    const corners: [number, number, number, number][] = [
      [-out, -out, 1, 1],
      [w + out, -out, -1, 1],
      [-out, h + out, 1, -1],
      [w + out, h + out, -1, -1],
    ];
    for (const [width, alpha] of [
      [7, 0.16],
      [2.5, 1],
    ] as const) {
      ctx.globalAlpha = alpha * s.lock;
      ctx.lineWidth = width;
      ctx.beginPath();
      for (const [x, y, dx, dy] of corners) {
        ctx.moveTo(x + dx * arm, y);
        ctx.lineTo(x, y);
        ctx.lineTo(x, y + dy * arm);
      }
      ctx.stroke();
    }
  }
  if (s.label > 0.001) {
    ctx.globalAlpha = 0.85 * s.label;
    ctx.fillStyle = c;
    ctx.font = '600 10px ui-monospace, monospace';
    ctx.fillText('J.A.R.V.I.S.   //   RESTORING', 14, 20);
    ctx.globalAlpha = 0.5 * s.label;
    ctx.fillText(`${Math.round(w)} × ${Math.round(h)}`, w - 90, h - 12);
  }
}

function drawScan(ctx: CanvasRenderingContext2D, s: Scene, c: string) {
  if (s.scan === null) return;
  const y = s.scan * s.h;
  const g = ctx.createLinearGradient(0, y - 60, 0, y);
  g.addColorStop(0, 'rgba(0,0,0,0)');
  g.addColorStop(1, c);
  ctx.globalAlpha = 0.18;
  ctx.fillStyle = g;
  ctx.fillRect(0, y - 60, s.w, 60);
  ctx.globalAlpha = 1;
  ctx.strokeStyle = c;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(0, y);
  ctx.lineTo(s.w, y);
  ctx.stroke();
  ctx.globalAlpha = 0.7;
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 0.8;
  ctx.stroke();
}

function drawScene(
  canvas: HTMLCanvasElement,
  ctx: CanvasRenderingContext2D,
  s: Scene,
  now: number,
  c: string
) {
  const dpr = window.devicePixelRatio || 1;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  if (canvas.width !== Math.round(vw * dpr) || canvas.height !== Math.round(vh * dpr)) {
    canvas.width = Math.round(vw * dpr);
    canvas.height = Math.round(vh * dpr);
    s.onResize?.(vw, vh);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = 'source-over';
  ctx.clearRect(0, 0, vw, vh);
  ctx.globalCompositeOperation = 'lighter';
  for (const seg of s.hair) drawSeg(ctx, seg, c);
  drawPackets(ctx, s.packets, now, c);
  ctx.setTransform(dpr, 0, 0, dpr, dpr * s.ox, dpr * s.oy);
  drawBlueprint(ctx, s, c);
  for (const p of s.pens) drawPen(ctx, p, now, s.outline, c);
  drawScan(ctx, s, c);
  ctx.globalCompositeOperation = 'source-over';
  ctx.globalAlpha = 1;
}

/** Direction from screen `a` to screen `b` (x, y, w, h), or null when they
 *  are the same screen (or `a` is unknown). */
function beamDir(a: number[] | undefined, b: number[]): Dir | null {
  if (!a || a.every((v, i) => Math.abs(v - b[i]) < 2)) return null;
  const dx = b[0] + b[2] / 2 - (a[0] + a[2] / 2);
  const dy = b[1] + b[3] / 2 - (a[1] + a[3] / 2);
  if (Math.abs(dx) >= Math.abs(dy)) return dx < 0 ? 'left' : 'right';
  return dy < 0 ? 'up' : 'down';
}

/* ------------------------------ the run ------------------------------ */

export function SchoolReturn({
  ret,
  color,
  barPx,
  barInset,
  onStage,
  onDone,
}: {
  ret: SchoolRet;
  color: string;
  /** The docked bar's height when the return began. */
  barPx: number;
  /** Its floating-panel gap from the screen's edges (px). */
  barInset: number;
  onStage: (s: ReturnStage) => void;
  onDone: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const colorRef = useRef(color);
  colorRef.current = color;

  useEffect(() => {
    const canvas = canvasRef.current;
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas?.getContext('2d') ?? null;
    } catch {
      // A lost/unavailable canvas must still restore the native window.
    }
    let alive = true;
    let finishing = false;
    let completed = false;
    const active = () => alive && !finishing;
    const pending = new Set<() => void>();
    const timedOut = Symbol('return-timeout');
    // Native IPC can remain unresolved even after the shell's watchdog has
    // restored the window. Bound it here so React also releases the return face.
    const bounded = <T,>(work: Promise<T>, ms: number): Promise<T | typeof timedOut> =>
      new Promise((resolve) => {
        let settled = false;
        const finish = (value: T | typeof timedOut) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          pending.delete(cancel);
          resolve(value);
        };
        const cancel = () => finish(timedOut);
        const timer = setTimeout(cancel, ms);
        pending.add(cancel);
        void work.then(finish, cancel);
      });
    const saved = loadHudRect();
    let restoreFrame = saved?.frame;
    let restoring: Promise<unknown> | undefined;
    const requestRestore = () =>
      (restoring ??= bounded(stage('restore', undefined, restoreFrame), 1800));
    const advance = (name: string, nonce?: string, rect?: number[]) =>
      active() ? bounded(stage(name, nonce, rect), 1800) : Promise.resolve(timedOut);
    const root = document.documentElement;
    root.classList.add('school-tx');
    const scene: Scene = {
      ox: 0,
      oy: 0,
      w: 0,
      h: 0,
      hair: [],
      pens: [],
      outline: 1,
      lock: 0,
      grid: 0,
      label: 0,
      scan: null,
      onResize: null,
      packets: [],
    };
    let scanHud: HTMLElement | null = null;
    const clock = frameClock();
    let raf = 0;
    const stopMotion = () => {
      cancelAnimationFrame(raf);
      raf = 0;
      clock.dispose();
    };
    const release = () => {
      clearTimeout(deadline);
      stopMotion();
      pending.forEach((cancel) => cancel());
      root.classList.remove('school-tx', 'stx-scan');
      root.style.removeProperty('--stx-scan');
      scanHud?.style.removeProperty('clip-path');
    };
    const complete = () => {
      if (!alive || completed) return;
      completed = true;
      finishing = true;
      release();
      onDone();
    };
    const finishRestore = async () => {
      if (!active()) return;
      finishing = true;
      clearTimeout(deadline);
      stopMotion();
      // Reuse an in-flight restore; a timeout must not dispatch it twice.
      await requestRestore();
      complete();
    };
    const frame = (now: number) => {
      raf = 0;
      if (!active() || !canvas || !ctx) return;
      try {
        drawScene(canvas, ctx, scene, clock.tick(now), colorRef.current);
        raf = requestAnimationFrame(frame);
      } catch {
        void finishRestore();
      }
    };
    const deadline = setTimeout(() => void finishRestore(), 15000);

    const { sleep, until, untilT, waitT, tween, settle } = runTimers(clock, active);
    /** Where to land: the rect recorded on the way in, else a default-size
     *  HUD centred on the bar's screen (measured now). */
    const target = (): Promise<HudRect | null> =>
      saved
        ? Promise.resolve(saved)
        : bounded(
            (async () => {
              const nonce = Math.random().toString(36).slice(2, 12);
              await advance('measure', nonce);
              while (active()) {
                const g = await bridgeSchoolGeom(nonce).catch(() => null);
                if (g) {
                  const o = g.out;
                  const w = Math.min(DEFAULT_W, o.w - 80);
                  const h = Math.min(DEFAULT_H, o.h - 120);
                  const x = o.x + Math.round((o.w - w) / 2);
                  const y = o.y + Math.round((o.h - h) / 2);
                  return {
                    frame: [x, y, w, h],
                    content: [x, y, w, h],
                    screen: [o.x, o.y, o.w, o.h],
                  };
                }
                await sleep(70);
              }
              return null;
            })(),
            1600
          ).then((result) => (result === timedOut ? null : result));

    const run = async () => {
      if (!canvas || !ctx) {
        await finishRestore();
        return;
      }
      const t = await target();
      if (!active()) return;
      restoreFrame = t?.frame;
      if (!t || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        await finishRestore();
        return;
      }
      raf = requestAnimationFrame(frame);
      const [cx, cy, cw, ch] = t.content;
      const [sx, sy, sw, sh] = t.screen;
      const screen = { x: sx, y: sy, w: sw, h: sh };

      // Which way the HUD's screen lies from the bar's (null: same one).
      const cross = beamDir(t.primary, t.screen);
      let hair: Seg;
      if (cross) {
        // Sink on the bar's screen, then beam the edge across as data...
        if ((await advance('primary')) === timedOut) {
          await finishRestore();
          return;
        }
        const primary = await waitForSchoolScreen('primary', active);
        if (!active()) return;
        if (!primary) {
          await finishRestore();
          return;
        }
        await settle();
        if (!active()) return;
        const pw = window.innerWidth;
        const ph = window.innerHeight;
        const ey = ph - barInset - barPx;
        const edge: Seg = { x0: barInset, y0: ey, x1: pw - barInset, y1: ey, a: 0 };
        scene.hair = [edge];
        if (!active()) return;
        onStage('sink');
        await tween(SINK_MS, (e) => (edge.a = e), easeOut);
        if (!active()) return;
        onStage('draft');
        scene.packets = uplink([edge], cross, pw, ph, clock.now);
        const outEnd = beamEnd(scene.packets);
        await tween(UPLINK_MS, (e) => (edge.a = 1 - e), easeIn);
        await untilT(() => clock.now >= outEnd, 1500);
        scene.hair = [];
        scene.packets = [];
        if ((await advance('cover-at', undefined, t.content)) === timedOut) {
          await finishRestore();
          return;
        }
        const arrived = await waitForSchoolScreen(screen, active);
        if (!active()) return;
        if (!arrived) {
          await finishRestore();
          return;
        }
        await settle();
        if (!active()) return;
        // ...packing back into the bottom edge on the HUD's screen.
        const W0 = window.innerWidth;
        const H0 = window.innerHeight;
        hair = { x0: 0, y0: H0 - barPx, x1: W0, y1: H0 - barPx, a: 0 };
        scene.hair = [hair];
        scene.packets = downlink(hair, cross, W0, H0, clock.now);
        const inEnd = beamEnd(scene.packets);
        await tween(DOWNLINK_MS, (e) => (hair.a = e), easeIn);
        await untilT(() => clock.now >= inEnd, 1500);
        scene.packets = [];
      } else {
        // Cover the HUD's screen; the bar stays put at the bottom and
        // sinks away, its top edge left glowing.
        if ((await advance('cover-at', undefined, t.content)) === timedOut) {
          await finishRestore();
          return;
        }
        const arrived = await waitForSchoolScreen(screen, active);
        if (!active()) return;
        if (!arrived) {
          await finishRestore();
          return;
        }
        await settle();
        if (!active()) return;
        const W0 = window.innerWidth;
        const H0 = window.innerHeight;
        const hy = H0 - barInset - barPx;
        hair = { x0: barInset, y0: hy, x1: W0 - barInset, y1: hy, a: 0 };
        scene.hair = [hair];
        if (!active()) return;
        onStage('sink');
        await tween(SINK_MS, (e) => (hair.a = e), easeOut);
        if (!active()) return;
        onStage('draft');
      }
      if (!active()) return;
      const W = window.innerWidth;
      const H = window.innerHeight;
      const top = H - barPx;
      scene.ox = cx - sx;
      scene.oy = cy - sy;
      scene.w = cw;
      scene.h = ch;

      // Split: the edge retracts into its two ends.
      const left: Seg = { ...hair };
      const right: Seg = { ...hair };
      scene.hair = [left, right];
      await tween(
        SPLIT_MS,
        (e) => {
          left.x1 = W / 2 + (hair.x0 - W / 2) * e;
          right.x0 = W / 2 + (hair.x1 - W / 2) * e;
        },
        easeIn
      );
      scene.hair = [];
      if (!active()) return;

      // Draft: from each end up the screen side to the HUD's bottom
      // corner, then its outline; a branch draws the bottom edge.
      const lx = -scene.ox + 1.5;
      const rx = W - scene.ox - 1.5;
      const yb = top - scene.oy;
      const now = clock.now;
      const leftPen = makePen(
        [
          [lx, yb],
          [lx, ch],
          [0, ch],
        ],
        [
          [0, ch],
          [0, 0],
          [cw / 2, 0],
        ],
        now
      );
      const rightPen = makePen(
        [
          [rx, yb],
          [rx, ch],
          [cw, ch],
        ],
        [
          [cw, ch],
          [cw, 0],
          [cw / 2, 0],
        ],
        now
      );
      const branchL = makePen(
        [],
        [
          [0, ch],
          [cw / 2, ch],
        ],
        now + leftPen.travelMs
      );
      const branchR = makePen(
        [],
        [
          [cw, ch],
          [cw / 2, ch],
        ],
        now + rightPen.travelMs
      );
      scene.pens = [leftPen, rightPen, branchL, branchR];
      const longest = Math.max(...scene.pens.map((p) => p.t0 - now + p.travelMs + p.draftMs));
      await waitT(longest + 60);
      if (!active()) return;

      // Lock: brackets snap on; the grid and label fill the frame.
      await tween(
        LOCK_MS,
        (e) => {
          scene.lock = e;
          scene.grid = e;
          scene.label = e;
        },
        easeOut
      );
      if (!active()) return;

      // Scan: the HUD window lands in the frame and scans in.
      root.style.setProperty('--stx-scan', '0%');
      root.classList.add('stx-scan');
      onStage('scan');
      scene.onResize = (vw, vh) => {
        if (Math.abs(vw - cw) <= 4 && Math.abs(vh - ch) <= 4) {
          scene.ox = 0;
          scene.oy = 0;
        }
      };
      if ((await requestRestore()) === timedOut) {
        await finishRestore();
        return;
      }
      await until(
        () => Math.abs(window.innerWidth - cw) <= 4 && Math.abs(window.innerHeight - ch) <= 4,
        2500
      );
      await settle();
      if (!active()) return;
      scene.onResize?.(window.innerWidth, window.innerHeight);
      scene.onResize = null;
      scene.ox = 0;
      scene.oy = 0;
      scene.w = window.innerWidth;
      scene.h = window.innerHeight;
      root.classList.remove('school-tx');
      scanHud = document.querySelector<HTMLElement>('.hud');
      await tween(
        SCAN_MS,
        (e) => {
          scene.scan = e;
          // clip-path is not inherited: update only the HUD layer instead
          // of invalidating every descendant through a root CSS variable.
          if (scanHud) scanHud.style.clipPath = `inset(0 0 ${((1 - e) * 100).toFixed(2)}% 0)`;
          const late = clamp01((e - 0.55) / 0.45);
          scene.outline = 1 - late;
          scene.lock = 1 - clamp01((e - 0.65) / 0.35);
          scene.grid = 1 - late;
          scene.label = 1 - clamp01(e / 0.4);
        },
        (u) => u
      );
      scene.scan = null;
      if (active()) complete();
    };
    void run().catch(() => void finishRestore());

    return () => {
      alive = false;
      release();
    };
  }, [ret, barPx, barInset, onStage, onDone]);

  return (
    <div className="stx stx--return" aria-hidden="true">
      <canvas ref={canvasRef} className="stx-canvas" />
    </div>
  );
}
