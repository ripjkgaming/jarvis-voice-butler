/** Protocol 12 canvas renderer: one rAF loop on one full-screen canvas.
 *
 *  Phase 1 (transition, runs alongside the real scan): a red ASCII orb at
 *  the reactor's spot spins up, its speed a function of
 *  max(scan progress, elapsed / MIN_MS), inside a red protocol frame that
 *  reports the real progress, phase, devices found and notes.
 *  At the peak (phase 'done' and MIN_MS elapsed) the orb decelerates and
 *  morphs, circle → rounded rect, into a terminal window.
 *  Phase 2: the terminal hosts the device map (lib/protocol-layout),
 *  rendered once to an offscreen canvas and auto-scrolled when it
 *  overflows. When it has fully revealed, `onMapReady` fires with the laid
 *  out box rectangles and the map holds until `exit()`.
 *
 *  Per frame: a few drawImage calls of cached layers plus ~300 glyph
 *  sprites for the orb. Static text is re-rendered only when the data or
 *  the viewport changes. Paused while the document is hidden. */
import {
  type GlyphAtlas,
  NOISE,
  RAMP,
  TAU,
  asciiFontFamily,
  clamp,
  fit,
  glyphAtlas,
  prefersReducedMotion,
  sstep,
} from '@/lib/ascii';
import {
  type DeviceBox,
  type Grid,
  HOST_TAG,
  LABEL_W,
  type ProtocolState,
  boxWidthFor,
  deviceBox,
  deviceCountLine,
  deviceTitle,
  layoutGrid,
  sortDevices,
  wrap,
} from '@/lib/protocol-layout';

export type Rect = { x: number; y: number; w: number; h: number };
export type OrbAnchor = { x: number; y: number; r: number };

export type MapBoxRect = Rect & { title: string; ip?: string; host: boolean };

/** Handed to `onMapReady` / `overlay` for later phases built on the map. */
export type MapInfo = {
  run: number;
  /** The terminal window and its scrolling body, viewport CSS px. */
  terminal: Rect;
  body: Rect;
  /** Device boxes in map-content px (add body.x, body.y - scrollY()). */
  content: MapBoxRect[];
  contentHeight: number;
  scrollY: () => number;
  /** Device boxes in viewport px at the current scroll offset. */
  boxes: () => MapBoxRect[];
  /** Terminal cell size, CSS px. */
  cell: { w: number; h: number };
};

export type ProtocolRendererOptions = {
  run: number;
  anchor: () => OrbAnchor | null;
  onMapReady?: (info: MapInfo) => void;
  onExited?: () => void;
  /** Drawn every frame on top of the held map (hook for later phases). */
  overlay?: (ctx: CanvasRenderingContext2D, now: number, info: MapInfo) => void;
};

export const MIN_MS = 6000;
const FADE_IN_MS = 900;
const MORPH_MS = 1700;
const REVEAL_MS = 1300;
const EXIT_MS = 750;
const PAUSE_MS = 2600;
const SCROLL_PX_S = 26;

const RED = '#ff3b4e';
const RED_HOT = '#ff4d5e';
const RED_DIM = 'rgba(255, 77, 94, 0.55)';
const RED_FAINT = 'rgba(255, 59, 78, 0.22)';
const GREEN = '#6dffb3';
const GREEN_HOT = '#d4ffe8';
const GREEN_DIM = 'rgba(109, 255, 179, 0.5)';
const WHITE_RED = '#ffd6da';

const CHARS = RAMP + NOISE;
const gi = (c: string) => Math.max(0, CHARS.indexOf(c));
const G = {
  dot: gi('.'),
  mid: gi('·'),
  colon: gi(':'),
  dash: gi('-'),
  eq: gi('='),
  plus: gi('+'),
  star: gi('*'),
  hash: gi('#'),
  pct: gi('%'),
  shade3: gi('▓'),
  shade2: gi('▒'),
  shade1: gi('░'),
  full: gi('█'),
};

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const easeIn = (t: number) => t * t * t;
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

function layer(w: number, h: number, dpr: number) {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(w * dpr));
  c.height = Math.max(1, Math.round(h * dpr));
  const ctx = c.getContext('2d')!;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.textBaseline = 'top';
  return { c, ctx };
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
) {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

/** Point at arc length `s` along a rounded rect centred on 0,0. */
function perimeterPoint(hx: number, hy: number, r: number, s: number): [number, number] {
  const sx = 2 * (hx - r);
  const sy = 2 * (hy - r);
  const q = (Math.PI / 2) * r;
  const segs = [sx, q, sy, q, sx, q, sy, q];
  const total = 2 * sx + 2 * sy + 4 * q;
  let u = ((s % total) + total) % total;
  let i = 0;
  while (i < 7 && u > segs[i]) u -= segs[i++];
  switch (i) {
    case 0:
      return [-hx + r + u, -hy];
    case 1: {
      const a = -Math.PI / 2 + u / r;
      return [hx - r + Math.cos(a) * r, -hy + r + Math.sin(a) * r];
    }
    case 2:
      return [hx, -hy + r + u];
    case 3: {
      const a = u / r;
      return [hx - r + Math.cos(a) * r, hy - r + Math.sin(a) * r];
    }
    case 4:
      return [hx - r - u, hy];
    case 5: {
      const a = Math.PI / 2 + u / r;
      return [-hx + r + Math.cos(a) * r, hy - r + Math.sin(a) * r];
    }
    case 6:
      return [-hx, hy - r - u];
    default: {
      const a = Math.PI + u / r;
      return [-hx + r + Math.cos(a) * r, -hy + r + Math.sin(a) * r];
    }
  }
}

function rrPerimeter(hx: number, hy: number, r: number) {
  return 4 * (hx - r) + 4 * (hy - r) + TAU * r;
}

type Font = { font: string; cw: number; lh: number; size: number };

function measure(size: number, weight = 500, lineH = 1.42): Font {
  const font = `${weight} ${size}px ${asciiFontFamily()}`;
  const probe = document.createElement('canvas').getContext('2d')!;
  probe.font = font;
  const cw = probe.measureText('MMMMMMMMMM').width / 10 || size * 0.6;
  return { font, cw, lh: Math.round(size * lineH), size };
}

function phaseLabel(s: ProtocolState | null): string {
  switch (s?.phase) {
    case 'discovering':
      return 'DISCOVERING';
    case 'scanning':
      return 'SCANNING';
    case 'done':
      return 'COMPLETE';
    case 'error':
      return 'ERROR';
    case 'aborted':
      return 'ABORTED';
    default:
      return '';
  }
}

function subnetLine(s: ProtocolState | null): string {
  const bits: string[] = [];
  if (s?.iface) bits.push(`IFACE ${s.iface}`);
  if (s?.mode) bits.push(`MODE ${s.mode.toUpperCase()}`);
  return bits.join(' · ');
}

function noteLines(s: ProtocolState | null): string[] {
  return (s?.notes ?? []).filter((n) => typeof n === 'string' && n.trim()).map((n) => n.trim());
}

export function createProtocolRenderer(canvas: HTMLCanvasElement, opts: ProtocolRendererOptions) {
  const ctx = canvas.getContext('2d')!;
  const reduced = prefersReducedMotion();
  let state: ProtocolState | null = null;
  let vw = 0;
  let vh = 0;
  let dpr = 1;
  let anchor: OrbAnchor = { x: 0, y: 0, r: 120 };
  let term: Rect = { x: 0, y: 0, w: 0, h: 0 };
  let body: Rect = { x: 0, y: 0, w: 0, h: 0 };
  let atlas: GlyphAtlas | null = null;
  let gcw = 9;
  let gch = 14;
  let backdrop: ReturnType<typeof layer> | null = null;
  let frameLayer: ReturnType<typeof layer> | null = null;
  /** Notes and the found-devices list: fade out as the terminal grows. */
  let colsLayer: ReturnType<typeof layer> | null = null;
  let chrome: ReturnType<typeof layer> | null = null;
  let status: ReturnType<typeof layer> | null = null;
  let map: ReturnType<typeof layer> | null = null;
  let mapGrid: Grid | null = null;
  let mapBoxes: DeviceBox[] = [];
  let mapFont: Font = { font: '13px monospace', cw: 8, lh: 18, size: 13 };
  let uiFont: Font = mapFont;
  let smallFont: Font = mapFont;
  let frameKey = '';
  let statusKey = '';
  let mapKey = '';
  let contentH = 0;

  const t0 = performance.now();
  let last = t0;
  let theta = 0;
  let omega = 0;
  let peakAt: number | null = null;
  let peakOmega = 0;
  let readyFired = false;
  let revealDoneAt: number | null = null;
  let exitAt: number | null = null;
  let exitedFired = false;
  let raf = 0;
  let scrollNow = 0;
  let info: MapInfo | null = null;
  let destroyed = false;

  /* ---------------- static layers ---------------- */

  function buildBackdrop() {
    backdrop = layer(vw, vh, dpr);
    const b = backdrop.ctx;
    b.fillStyle = '#070103';
    b.fillRect(0, 0, vw, vh);
    const g = b.createRadialGradient(
      anchor.x,
      anchor.y,
      0,
      anchor.x,
      anchor.y,
      Math.max(vw, vh) * 0.7
    );
    g.addColorStop(0, 'rgba(92, 8, 20, 0.42)');
    g.addColorStop(0.45, 'rgba(40, 3, 9, 0.28)');
    g.addColorStop(1, 'rgba(0, 0, 0, 0)');
    b.fillStyle = g;
    b.fillRect(0, 0, vw, vh);
    b.fillStyle = 'rgba(255, 59, 78, 0.028)';
    for (let y = 0; y < vh; y += 3) b.fillRect(0, y, vw, 1);
    const v = b.createRadialGradient(
      vw / 2,
      vh / 2,
      Math.min(vw, vh) * 0.35,
      vw / 2,
      vh / 2,
      Math.max(vw, vh) * 0.75
    );
    v.addColorStop(0, 'rgba(0,0,0,0)');
    v.addColorStop(1, 'rgba(0,0,0,0.6)');
    b.fillStyle = v;
    b.fillRect(0, 0, vw, vh);
  }

  const inset = () => Math.round(Math.max(16, Math.min(vw, vh) * 0.026));

  /** The protocol frame: corner brackets, header, progress, devices, notes. */
  function buildFrame() {
    const s = state;
    const key = JSON.stringify([
      vw,
      vh,
      s?.phase,
      s?.subnet,
      s?.iface,
      s?.mode,
      Math.round(clamp(s?.progress ?? 0) * 1000),
      (s?.devices ?? []).map((d) => [d.ip, deviceTitle(d)]),
      s?.notes,
      s?.phase === 'error' ? s.message : '',
    ]);
    if (key === frameKey && frameLayer) return;
    frameKey = key;
    frameLayer = layer(vw, vh, dpr);
    colsLayer = layer(vw, vh, dpr);
    let c = frameLayer.ctx;
    const pad = inset();
    const f = uiFont;
    const sm = smallFont;
    // corner brackets, glyph style
    c.font = f.font;
    c.fillStyle = RED;
    const arm = Math.max(4, Math.round((Math.min(vw, vh) * 0.07) / f.cw));
    const hz = '━'.repeat(arm);
    const x0 = pad;
    const y0 = pad;
    const x1 = vw - pad;
    const y1 = vh - pad - f.lh;
    c.textAlign = 'left';
    c.fillText('┏' + hz, x0, y0);
    c.fillText('┗' + hz, x0, y1);
    c.textAlign = 'right';
    c.fillText(hz + '┓', x1, y0);
    c.fillText(hz + '┛', x1, y1);
    const vert = Math.max(2, Math.round(arm / 3));
    for (let i = 1; i <= vert; i++) {
      c.textAlign = 'left';
      c.fillText('┃', x0, y0 + i * f.lh);
      c.fillText('┃', x0, y1 - i * f.lh);
      c.textAlign = 'right';
      c.fillText('┃', x1, y0 + i * f.lh);
      c.fillText('┃', x1, y1 - i * f.lh);
    }
    // faint frame rule between the brackets
    c.fillStyle = RED_FAINT;
    c.fillRect(x0 + (arm + 2) * f.cw, y0 + f.lh / 2, vw - 2 * (x0 + (arm + 2) * f.cw), 1);
    c.fillRect(x0 + (arm + 2) * f.cw, y1 + f.lh / 2, vw - 2 * (x0 + (arm + 2) * f.cw), 1);

    // header
    const hx = x0 + 2 * f.cw;
    const hy = y0 + f.lh * 1.4;
    c.textAlign = 'left';
    c.font = `700 ${Math.round(f.size * 1.18)}px ${asciiFontFamily()}`;
    c.fillStyle = RED_HOT;
    c.fillText(`PROTOCOL 12 · LOCAL SUBNET${s?.subnet ? ' ' + s.subnet : ''}`, hx, hy);
    c.font = sm.font;
    c.fillStyle = RED_DIM;
    const sub = subnetLine(s);
    if (sub) c.fillText(sub, hx, hy + f.lh * 1.25);
    const label = phaseLabel(s);
    if (label) {
      c.textAlign = 'right';
      c.font = `700 ${f.size}px ${asciiFontFamily()}`;
      c.fillStyle = s?.phase === 'error' ? WHITE_RED : RED_HOT;
      c.fillText(`■ ${label}`, x1 - 2 * f.cw, hy);
    }

    // left column: notes (real backend notes only)
    c = colsLayer.ctx;
    const notes = noteLines(s);
    const colW = Math.max(18, Math.floor((anchor.x - anchor.r * 1.3 - hx) / sm.cw));
    let ny = hy + f.lh * 3.2;
    c.textAlign = 'left';
    if (notes.length && colW > 12) {
      c.font = sm.font;
      c.fillStyle = RED_DIM;
      c.fillText('NOTES', hx, ny);
      ny += sm.lh * 1.3;
      c.fillStyle = WHITE_RED;
      for (const n of notes) {
        for (const line of wrap(n, colW - 2)) {
          if (ny > y1 - f.lh * 4) break;
          c.fillText(line, hx, ny);
          ny += sm.lh;
        }
        ny += sm.lh * 0.4;
      }
    }

    // right column: devices found so far (count is devices.length)
    const devs = sortDevices(s?.devices ?? []);
    const rx = x1 - 2 * f.cw;
    let ry = hy + f.lh * 3.2;
    c.textAlign = 'right';
    c.font = sm.font;
    c.fillStyle = RED_DIM;
    c.fillText('DEVICES FOUND', rx - 4 * f.cw, ry);
    c.font = `700 ${Math.round(f.size * 1.18)}px ${asciiFontFamily()}`;
    c.fillStyle = RED_HOT;
    c.fillText(String(devs.length).padStart(2, '0'), rx, ry - 2);
    ry += sm.lh * 1.6;
    c.font = sm.font;
    const room = Math.max(0, Math.floor((y1 - f.lh * 4 - ry) / sm.lh));
    const listW = Math.max(14, Math.min(34, Math.floor((rx - anchor.x - anchor.r * 1.3) / sm.cw)));
    devs.slice(0, room).forEach((d, i) => {
      c.fillStyle = i % 2 ? RED_DIM : 'rgba(255, 140, 150, 0.8)';
      const t = deviceTitle(d);
      const line = d.ip && t !== d.ip ? `${t}  ${d.ip}` : t;
      c.fillText(fit(line + (d.isHost ? ' ◂' : ''), listW, 'r').trimStart(), rx, ry + i * sm.lh);
    });
    if (devs.length > room && room > 0) {
      c.fillStyle = RED_DIM;
      c.fillText(`+${devs.length - room}`, rx, ry + room * sm.lh);
    }

    // bottom: real progress bar
    c = frameLayer.ctx;
    const p = clamp(s?.progress ?? 0);
    const barCells = Math.max(10, Math.min(64, Math.floor((vw * 0.46) / f.cw)));
    const lit = Math.round(p * barCells);
    const pctText = `${String(Math.round(p * 100)).padStart(3, ' ')}%`;
    const by = y1 - f.lh * 1.6;
    const total = (barCells + 2 + 10 + 6) * f.cw;
    let bx = vw / 2 - total / 2;
    c.textAlign = 'left';
    c.font = sm.font;
    c.fillStyle = RED_DIM;
    c.fillText('PROGRESS', bx, by + (f.lh - sm.lh) / 2);
    bx += 10 * f.cw;
    c.font = f.font;
    c.fillStyle = RED_FAINT;
    c.fillText('[' + '░'.repeat(barCells) + ']', bx, by);
    c.fillStyle = RED_HOT;
    c.fillText('█'.repeat(lit), bx + f.cw, by);
    c.fillStyle = WHITE_RED;
    c.fillText(pctText, bx + (barCells + 3) * f.cw, by);

    if (s?.phase === 'error' && s.message) {
      c.textAlign = 'center';
      c.font = `700 ${f.size}px ${asciiFontFamily()}`;
      c.fillStyle = WHITE_RED;
      c.fillText(`! ${s.message}`, vw / 2, by - f.lh * 1.4);
    }
  }

  /** Terminal window chrome (fill, border, title bar). */
  function buildChrome() {
    chrome = layer(term.w, term.h, dpr);
    const c = chrome.ctx;
    const r = 12;
    roundRect(c, 0.5, 0.5, term.w - 1, term.h - 1, r);
    c.fillStyle = '#0c0205';
    c.fill();
    const g = c.createLinearGradient(0, 0, 0, term.h);
    g.addColorStop(0, 'rgba(255, 59, 78, 0.07)');
    g.addColorStop(1, 'rgba(255, 59, 78, 0.015)');
    c.fillStyle = g;
    c.fill();
    c.lineWidth = 1.5;
    c.strokeStyle = 'rgba(255, 77, 94, 0.75)';
    c.stroke();
    const tb = titleBarH();
    c.save();
    roundRect(c, 0.5, 0.5, term.w - 1, term.h - 1, r);
    c.clip();
    c.fillStyle = 'rgba(255, 59, 78, 0.14)';
    c.fillRect(0, 0, term.w, tb);
    c.restore();
    c.fillStyle = 'rgba(255, 77, 94, 0.5)';
    c.fillRect(0, tb, term.w, 1);
    c.font = uiFont.font;
    c.textBaseline = 'middle';
    c.fillStyle = RED;
    c.textAlign = 'left';
    c.fillText('● ● ●', 14, tb / 2 + 1);
    c.textAlign = 'center';
    c.fillStyle = WHITE_RED;
    c.font = `600 ${uiFont.size}px ${asciiFontFamily()}`;
    c.fillText('root@jarvis ~ # netmap', term.w / 2, tb / 2 + 1);
    // faint scanlines inside the body
    c.fillStyle = 'rgba(255, 59, 78, 0.025)';
    for (let y = tb + 2; y < term.h - 4; y += 3) c.fillRect(2, y, term.w - 4, 1);
  }

  const titleBarH = () => Math.round(uiFont.lh * 1.55);

  /** Header strip and footer inside the terminal; sets `body`. */
  function buildStatus(force = false) {
    const s = state;
    const notes = noteLines(s);
    const key = JSON.stringify([
      term,
      s?.devices?.length ?? 0,
      s?.subnet,
      s?.iface,
      s?.mode,
      notes,
      s?.phase,
      s?.phase === 'error' ? s.message : '',
      s?.startedAt,
      s?.finishedAt,
    ]);
    if (!force && key === statusKey && status) return;
    statusKey = key;
    status = layer(term.w, term.h, dpr);
    const c = status.ctx;
    const f = mapFont;
    const px = Math.round(f.cw * 2);
    let y = titleBarH() + Math.round(f.lh * 0.6);
    c.font = `700 ${f.size}px ${asciiFontFamily()}`;
    c.fillStyle = RED_HOT;
    c.fillText(deviceCountLine(s?.devices), px, y);
    const sub = [s?.subnet ? `SUBNET ${s.subnet}` : '', subnetLine(s)].filter(Boolean).join(' · ');
    c.font = f.font;
    c.fillStyle = RED_DIM;
    if (sub) c.fillText(sub, px + 14 * f.cw, y);
    y += f.lh;
    const cols = Math.floor((term.w - 2 * px) / f.cw);
    c.fillStyle = WHITE_RED;
    for (const n of notes) {
      for (const line of wrap(`! ${n}`, cols)) {
        c.fillText(line, px, y);
        y += f.lh;
      }
    }
    c.fillStyle = 'rgba(255, 77, 94, 0.35)';
    c.fillText('─'.repeat(Math.max(0, cols)), px, y);
    y += f.lh;
    const footY = term.h - Math.round(f.lh * 1.5);
    c.fillStyle = 'rgba(255, 77, 94, 0.35)';
    c.fillText('─'.repeat(Math.max(0, cols)), px, footY - f.lh * 0.8);
    c.fillStyle = RED_DIM;
    if (s?.phase === 'done') {
      const dur =
        typeof s.startedAt === 'number' &&
        typeof s.finishedAt === 'number' &&
        s.finishedAt >= s.startedAt
          ? ` IN ${(s.finishedAt - s.startedAt).toFixed(1)}S`
          : '';
      c.fillText(`SCAN COMPLETE${dur}`, px, footY);
      c.textAlign = 'right';
      c.font = `700 ${f.size}px ${asciiFontFamily()}`;
      c.fillStyle = RED_HOT;
      c.fillText('STAND DOWN OR ESC TO EXIT', term.w - px, footY);
      c.textAlign = 'left';
    } else if (s?.phase === 'error') {
      c.font = `700 ${f.size}px ${asciiFontFamily()}`;
      c.fillStyle = WHITE_RED;
      c.fillText(fit(`! ${s.message || 'ERROR'}`, cols), px, footY);
    }
    body = {
      x: term.x + px,
      y: term.y + y + Math.round(f.lh * 0.3),
      w: term.w - 2 * px,
      h: Math.max(f.lh, footY - f.lh * 1.1 - y - Math.round(f.lh * 0.3)),
    };
  }

  /** The device grid, cached offscreen at full content height. */
  function buildMap() {
    const devs = sortDevices(state?.devices ?? []);
    const f = mapFont;
    const cols = Math.max(10, Math.floor(body.w / f.cw));
    const key = JSON.stringify([cols, f.font, devs]);
    if (key === mapKey && map) return;
    mapKey = key;
    const w = boxWidthFor(cols);
    mapBoxes = devs.map((d) => deviceBox(d, Math.min(w, cols)));
    mapGrid = layoutGrid(mapBoxes, cols);
    const offX = Math.max(0, Math.floor((cols - mapGrid.width) / 2)) * f.cw;
    contentH = Math.max(f.lh, mapGrid.height * f.lh);
    const maxH = Math.floor(16000 / dpr);
    map = layer(body.w, Math.min(contentH, maxH), dpr);
    const c = map.ctx;
    c.font = f.font;
    mapGrid.cells.forEach((cell, i) => {
      const box = mapBoxes[i];
      const x = offX + cell.x * f.cw;
      const y = cell.y * f.lh;
      if (y > maxH) return;
      if (box.host) {
        c.fillStyle = 'rgba(109, 255, 179, 0.06)';
        c.fillRect(
          x + f.cw / 2,
          y + f.lh / 2,
          (box.width - 1) * f.cw,
          (box.lines.length - 1) * f.lh
        );
      }
      const edge = box.host ? GREEN : GREEN_DIM;
      let field = '';
      box.lines.forEach((line, k) => {
        const ly = y + k * f.lh;
        if (k === box.lines.length - 1) {
          c.fillStyle = edge;
          c.fillText(line, x, ly);
          return;
        }
        if (k === 0) {
          // ╭─ title ───── [THIS HOST] ─╮ in three tones
          const tagLen = box.host ? HOST_TAG.length + 2 : 0;
          const end = box.title
            ? 4 + Math.min(box.title.length, Math.max(1, box.width - 7 - tagLen))
            : 2;
          c.fillStyle = edge;
          c.fillText(line.slice(0, 2), x, ly);
          c.fillText(line.slice(end), x + end * f.cw, ly);
          c.font = `700 ${f.size}px ${asciiFontFamily()}`;
          c.fillStyle = box.host ? GREEN_HOT : GREEN;
          c.fillText(line.slice(2, end), x + 2 * f.cw, ly);
          if (box.host) {
            const at = line.lastIndexOf(HOST_TAG);
            c.fillStyle = GREEN_HOT;
            if (at > 0) c.fillText(HOST_TAG, x + at * f.cw, ly);
          }
          c.font = f.font;
          return;
        }
        const row = box.rows[k - 1];
        if (row.label) field = row.label;
        c.fillStyle = edge;
        c.fillText('│', x, ly);
        c.fillText('│', x + (box.width - 1) * f.cw, ly);
        c.fillStyle = GREEN_DIM;
        if (row.label) c.fillText(row.label, x + 2 * f.cw, ly);
        c.fillStyle = field === 'PORT' ? GREEN : GREEN_HOT;
        c.fillText(line.slice(2 + LABEL_W, box.width - 2).trimEnd(), x + (2 + LABEL_W) * f.cw, ly);
      });
    });
    const content: MapBoxRect[] = mapGrid.cells.map((cell, i) => ({
      x: offX + cell.x * f.cw,
      y: cell.y * f.lh,
      w: cell.w * f.cw,
      h: cell.h * f.lh,
      title: mapBoxes[i].title,
      ip: mapBoxes[i].ip,
      host: mapBoxes[i].host,
    }));
    info = {
      run: opts.run,
      terminal: { ...term },
      body: { ...body },
      content,
      contentHeight: contentH,
      scrollY: () => scrollNow,
      boxes: () => content.map((b) => ({ ...b, x: b.x + body.x, y: b.y + body.y - scrollNow })),
      cell: { w: f.cw, h: f.lh },
    };
  }

  function layout() {
    const rect = canvas.getBoundingClientRect();
    vw = Math.max(1, rect.width || window.innerWidth);
    vh = Math.max(1, rect.height || window.innerHeight);
    dpr = Math.min(1.5, window.devicePixelRatio || 1);
    canvas.width = Math.round(vw * dpr);
    canvas.height = Math.round(vh * dpr);
    const a = opts.anchor();
    const rMax = Math.min(vw, vh) * 0.3;
    anchor = a
      ? { x: a.x, y: a.y, r: clamp(a.r, 80, rMax) }
      : { x: vw / 2, y: vh / 2, r: clamp(Math.min(vw, vh) * 0.24, 80, rMax) };
    const base = clamp(Math.round(Math.min(vw / 110, vh / 56)), 11, 16);
    uiFont = measure(base, 500);
    smallFont = measure(Math.max(10, base - 2), 500);
    mapFont = measure(clamp(Math.round(Math.min(vw / 128, vh / 62)), 10, 15), 500, 1.38);
    gcw = Math.max(7, Math.round(anchor.r / 15));
    gch = Math.round(gcw * 1.5);
    atlas = glyphAtlas(gcw, gch, dpr, RED);
    const pad = inset();
    const top = pad + uiFont.lh * 4.4;
    const bottom = pad + uiFont.lh * 3.4;
    const mx = Math.max(pad * 2, vw * 0.06);
    term = {
      x: Math.round(mx),
      y: Math.round(top),
      w: Math.round(vw - 2 * mx),
      h: Math.round(Math.max(200, vh - top - bottom)),
    };
    frameKey = '';
    statusKey = '';
    mapKey = '';
    buildBackdrop();
    buildFrame();
    buildChrome();
    buildStatus(true);
    buildMap();
    if (readyFired && info) opts.onMapReady?.(info);
  }

  /* ---------------- per-frame ---------------- */

  function drawRing(
    cx: number,
    cy: number,
    r: number,
    slots: number,
    rot: number,
    alpha: number,
    pick: (u: number) => [number, number] | null
  ) {
    if (!atlas || alpha <= 0.01) return;
    const hw = gcw / 2;
    const hh = gch / 2;
    for (let i = 0; i < slots; i++) {
      const u = i / slots;
      const g = pick(u);
      if (!g) continue;
      const a = u * TAU + rot;
      atlas.draw(ctx, g[0], g[1] * alpha, cx + Math.cos(a) * r - hw, cy + Math.sin(a) * r - hh);
    }
  }

  /** The red ASCII orb: rim dashes, a comet, tick ring, two counter-
   *  rotating inner rings and a pulsing core. `k` fades/shrinks it. */
  function drawOrb(now: number, cx: number, cy: number, R: number, k: number, energy: number) {
    if (k <= 0.01 || !atlas) return;
    const frac = (x: number) => x - Math.floor(x);
    const t = (now - t0) / 1000;
    const slotsFor = (r: number) => Math.max(16, Math.round((TAU * r) / (gcw * 1.05)));
    // tick ring
    drawRing(cx, cy, R * 1.14, 60, -theta * 0.35, k * 0.8, (u) => {
      const i = Math.round(u * 60);
      return i % 5 === 0 ? [G.colon, 0.95] : [G.dot, 0.45];
    });
    // rim: six dashes
    drawRing(cx, cy, R, slotsFor(R), theta, k, (u) =>
      frac(u * 6) < 0.72 ? [G.eq, 0.75 + 0.25 * energy] : [G.mid, 0.3]
    );
    // comet riding the rim
    const head = theta * 1.6;
    for (let i = 0; i < 14; i++) {
      const a = head - i * 0.055;
      const b = 1 - i / 14;
      const g = i < 2 ? G.full : i < 5 ? G.shade3 : i < 9 ? G.shade2 : G.shade1;
      atlas.draw(ctx, g, b * k, cx + Math.cos(a) * R - gcw / 2, cy + Math.sin(a) * R - gch / 2);
    }
    // middle ring: counter-rotating, three bright arcs
    const r2 = R * 0.74;
    drawRing(cx, cy, r2, slotsFor(r2), -theta * 1.45, k, (u) => {
      const f = frac(u * 3);
      return f < 0.42 ? [G.hash, 0.85] : f < 0.5 ? null : [G.dash, 0.4];
    });
    // inner ring
    const r3 = R * 0.47;
    drawRing(cx, cy, r3, slotsFor(r3), theta * 2.3, k, (u) => {
      const f = frac(u * 4);
      return f < 0.3 ? [G.pct, 0.9] : f < 0.55 ? [G.plus, 0.5] : null;
    });
    // core
    const pulse = 0.82 + 0.18 * Math.sin(t * (6 + energy * 10));
    const cr = R * 0.2 * (0.9 + 0.2 * energy);
    for (let y = -cr; y <= cr; y += gch) {
      for (let x = -cr; x <= cr; x += gcw) {
        const d = Math.hypot(x, y) / cr;
        if (d > 1) continue;
        const g = d < 0.35 ? G.full : d < 0.65 ? G.shade3 : d < 0.85 ? G.star : G.colon;
        atlas.draw(ctx, g, (1 - d * 0.55) * pulse * k, cx + x - gcw / 2, cy + y - gch / 2);
      }
    }
  }

  /** Glyph outline along a rounded rect, the orb's morph silhouette. */
  function drawOutline(cx: number, cy: number, hx: number, hy: number, r: number, alpha: number) {
    if (!atlas || alpha <= 0.01) return;
    const per = rrPerimeter(hx, hy, r);
    const n = Math.max(24, Math.round(per / (gcw * 1.1)));
    const rot = theta * 40;
    for (let i = 0; i < n; i++) {
      const [x, y] = perimeterPoint(hx, hy, r, (i / n) * per + rot);
      const f = (i / n) * 8 - Math.floor((i / n) * 8);
      atlas.draw(
        ctx,
        f < 0.7 ? G.eq : G.mid,
        (f < 0.7 ? 0.85 : 0.35) * alpha,
        cx + x - gcw / 2,
        cy + y - gch / 2
      );
    }
  }

  function scrollFor(now: number): number {
    if (!revealDoneAt) return 0;
    const over = contentH - body.h;
    if (over <= 2) return 0;
    const leg = Math.max(2000, (over / SCROLL_PX_S) * 1000);
    const cycle = 2 * (PAUSE_MS + leg);
    const u = (now - revealDoneAt) % cycle;
    if (u < PAUSE_MS) return 0;
    if (u < PAUSE_MS + leg) return over * sstep(0, 1, (u - PAUSE_MS) / leg);
    if (u < 2 * PAUSE_MS + leg) return over;
    return over * (1 - sstep(0, 1, (u - 2 * PAUSE_MS - leg) / leg));
  }

  function frame(now: number) {
    raf = 0;
    if (destroyed) return;
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    const elapsed = now - t0;
    const s = state;
    const progress = clamp(s?.progress ?? 0);

    // spin: accelerate with max(progress, elapsed / MIN_MS); decelerate in the morph
    const drive = clamp(Math.max(progress, elapsed / MIN_MS));
    if (peakAt === null && s?.phase === 'done' && elapsed >= MIN_MS) {
      peakAt = now;
      peakOmega = omega;
    }
    const morph = peakAt === null ? 0 : clamp((now - peakAt) / (reduced ? 350 : MORPH_MS));
    const em = easeInOut(morph);
    if (reduced) omega = peakAt === null ? 0.5 : 0.5 * (1 - em);
    else if (peakAt === null) omega = 0.5 + 6.2 * easeIn(drive);
    else omega = peakOmega * (1 - em) * (1 - em);
    theta += omega * dt;

    const fade = reduced ? 1 : sstep(0, FADE_IN_MS, elapsed);
    const exitK = exitAt === null ? 1 : 1 - clamp((now - exitAt) / EXIT_MS);

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, vw, vh);
    ctx.globalAlpha = fade * exitK * 0.97;
    if (backdrop) ctx.drawImage(backdrop.c, 0, 0, vw, vh);

    // protocol frame
    ctx.globalAlpha = fade * exitK;
    if (frameLayer) ctx.drawImage(frameLayer.c, 0, 0, vw, vh);
    if (colsLayer && morph < 0.6) {
      ctx.globalAlpha = fade * exitK * (1 - sstep(0, 0.6, morph));
      ctx.drawImage(colsLayer.c, 0, 0, vw, vh);
      ctx.globalAlpha = fade * exitK;
    }
    // live sweep line across the frame while scanning
    if (peakAt === null && !reduced) {
      const sy = inset() + ((elapsed / 3200) % 1) * (vh - 2 * inset());
      ctx.fillStyle = 'rgba(255, 59, 78, 0.06)';
      ctx.fillRect(inset(), sy, vw - 2 * inset(), 2);
    }

    // orb → rounded rect → terminal
    const tcx = term.x + term.w / 2;
    const tcy = term.y + term.h / 2;
    const cx = lerp(anchor.x, tcx, em);
    const cy = lerp(anchor.y, tcy, em);
    const R = anchor.r;
    const energy = peakAt === null ? easeIn(drive) : 1 - em;
    const orbK = fade * exitK * (1 - sstep(0, 0.55, morph));
    drawOrb(now, cx, cy, R * (1 - 0.25 * em), orbK, energy);
    if (morph > 0) {
      const hx = lerp(R, term.w / 2, em);
      const hy = lerp(R, term.h / 2, em);
      const rr = lerp(R, 12, em);
      ctx.globalAlpha = exitK * sstep(0, 0.4, morph) * (1 - sstep(0.85, 1, morph));
      roundRect(ctx, cx - hx, cy - hy, hx * 2, hy * 2, rr);
      ctx.fillStyle = 'rgba(12, 2, 5, 0.9)';
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = RED_HOT;
      ctx.stroke();
      ctx.globalAlpha = 1;
      drawOutline(cx, cy, hx, hy, rr, exitK * sstep(0, 0.3, morph) * (1 - sstep(0.8, 1, morph)));
    }

    if (morph >= 0.8 && chrome && status) {
      const k = sstep(0.8, 1, morph) * exitK;
      ctx.globalAlpha = k;
      ctx.drawImage(chrome.c, term.x, term.y, term.w, term.h);
      const reveal =
        peakAt === null ? 0 : clamp((now - peakAt - MORPH_MS) / (reduced ? 1 : REVEAL_MS));
      ctx.globalAlpha = k * sstep(0, 0.25, reveal);
      ctx.drawImage(status.c, term.x, term.y, term.w, term.h);
      if (map && reveal > 0) {
        if (reveal >= 1 && revealDoneAt === null) revealDoneAt = now;
        scrollNow = scrollFor(now);
        const visH = Math.min(body.h, contentH) * (reveal < 1 ? easeInOut(reveal) : 1);
        const srcH = Math.min(visH, map.c.height / dpr - scrollNow);
        if (srcH > 0) {
          ctx.globalAlpha = k;
          ctx.drawImage(
            map.c,
            0,
            scrollNow * dpr,
            body.w * dpr,
            srcH * dpr,
            body.x,
            body.y,
            body.w,
            srcH
          );
          // typing cursor on the reveal edge
          if (reveal < 1) {
            ctx.fillStyle = GREEN;
            ctx.fillRect(body.x, body.y + srcH - 2, body.w * 0.25, 2);
          }
        }
        if (contentH > body.h) {
          // scroll gutter: position of the window over the content
          const gh = Math.max(16, (body.h * body.h) / contentH);
          const gy = body.y + (scrollNow / (contentH - body.h)) * (body.h - gh);
          ctx.fillStyle = 'rgba(109, 255, 179, 0.35)';
          ctx.fillRect(term.x + term.w - 6, gy, 2, gh);
        }
      }
      if (reveal >= 1 && info && exitAt === null) {
        if (!readyFired) {
          readyFired = true;
          opts.onMapReady?.(info);
        }
        if (opts.overlay) {
          ctx.globalAlpha = 1;
          opts.overlay(ctx, now, info);
        }
      }
    }
    ctx.globalAlpha = 1;

    if (exitAt !== null && exitK <= 0) {
      if (!exitedFired) {
        exitedFired = true;
        ctx.clearRect(0, 0, vw, vh);
        opts.onExited?.();
      }
      return;
    }
    // A held map that fits needs no frames until data, resize or exit.
    if (readyFired && exitAt === null && !opts.overlay && contentH <= body.h + 2) return;
    schedule();
  }

  function schedule() {
    if (!raf && !destroyed && !document.hidden) raf = requestAnimationFrame(frame);
  }

  const onVisibility = () => {
    if (document.hidden) {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    } else {
      last = performance.now();
      schedule();
    }
  };
  const onResize = () => {
    layout();
    schedule();
  };
  const sizes = new ResizeObserver(onResize);
  sizes.observe(canvas);
  document.addEventListener('visibilitychange', onVisibility);
  layout();
  // Commit Mono may still be loading: re-render the text layers once it is.
  void document.fonts?.ready.then(() => {
    if (!destroyed) layout();
  });
  schedule();

  return {
    update(next: ProtocolState | null) {
      state = next;
      if (!vw) return;
      buildFrame();
      buildStatus();
      // Once the map is handed out its rectangles stay put (resize aside).
      if (!readyFired) buildMap();
      else if (info) info.body = { ...body };
      schedule();
    },
    exit() {
      if (exitAt === null) exitAt = performance.now();
      schedule();
    },
    mapInfo: () => info,
    destroy() {
      destroyed = true;
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      sizes.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
      canvas.width = canvas.height = 1;
    },
  };
}

export type ProtocolRenderer = ReturnType<typeof createProtocolRenderer>;
