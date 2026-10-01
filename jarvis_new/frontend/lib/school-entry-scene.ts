/** STARK aperture → courier core → taskbar fabrication. The caller owns time,
 * window placement and the real HUD/bar. This layer is purely transparent ink.
 * All glows come from a DPR-matched atlas; draw never queries the DOM, creates
 * gradients, allocates particle objects, or schedules its own animation. */
export type EntryPhase =
  | 'aperture'
  | 'compress'
  | 'transfer-out'
  | 'transfer-in'
  | 'descent'
  | 'forge'
  | 'reveal';
export type EntryDirection = 'left' | 'right' | 'up' | 'down';

const TAU = Math.PI * 2;
const CELL = 64;
const MAX_MOTES = 192;
const COPPER = '#ffb17a';
const WHITE = '#e6fcff';
const clamp = (p: number) => Math.max(0, Math.min(1, p));
const smooth = (p: number) => p * p * (3 - 2 * p);
const out = (p: number) => 1 - Math.pow(1 - p, 3);
const seed = (i: number, salt: number) => {
  const value = Math.sin(i * 127.1 + salt * 311.7) * 43758.5453;
  return value - Math.floor(value);
};
const bezier = (a: number, b: number, c: number, d: number, p: number) => {
  const q = 1 - p;
  return q * q * q * a + 3 * q * q * p * b + 3 * q * p * p * c + p * p * p * d;
};

export function createEntryScene(canvas: HTMLCanvasElement, color: string) {
  const ctx = canvas.getContext('2d', { alpha: true });
  const atlas = document.createElement('canvas');
  const ink = atlas.getContext('2d');
  if (!ctx || !ink) return null;

  const motes = new Float32Array(MAX_MOTES * 6);
  for (let i = 0; i < MAX_MOTES; i++) {
    for (let j = 0; j < 6; j++) motes[i * 6 + j] = seed(i + 1, j + 1);
  }
  let width = 0;
  let height = 0;
  let dpr = 1;
  let disposed = false;
  let count = 0;
  let aperture = 0;
  let atlasDpr = 0;

  const buildAtlas = () => {
    atlasDpr = dpr;
    atlas.width = Math.ceil(CELL * 3 * dpr);
    atlas.height = Math.ceil(CELL * dpr);
    ink.setTransform(dpr, 0, 0, dpr, 0, 0);
    for (let row = 0; row < 3; row++) {
      const x = row * CELL + CELL / 2;
      const c = row === 0 ? color : row === 1 ? COPPER : WHITE;
      const glow = ink.createRadialGradient(x, CELL / 2, 0, x, CELL / 2, CELL / 2);
      glow.addColorStop(0, c);
      glow.addColorStop(0.075, c);
      glow.addColorStop(1, 'transparent');
      ink.fillStyle = glow;
      ink.globalAlpha = 0.24;
      ink.fillRect(row * CELL, 0, CELL, CELL);
      ink.globalAlpha = 1;
      ink.fillStyle = c;
      ink.beginPath();
      ink.arc(x, CELL / 2, 1.4, 0, TAU);
      ink.fill();
    }
  };

  const glow = (x: number, y: number, size: number, alpha: number, row = 0) => {
    if (alpha <= 0 || size <= 0) return;
    ctx.globalAlpha = alpha;
    ctx.drawImage(
      atlas,
      row * CELL * dpr,
      0,
      CELL * dpr,
      CELL * dpr,
      x - size / 2,
      y - size / 2,
      size,
      size
    );
  };

  const line = (x1: number, y1: number, x2: number, y2: number) => {
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
  };

  /** Shared path, with rounded corners exactly inside the target rectangle. */
  const plate = (x: number, y: number, w: number, h: number, radius: number) => {
    const r = Math.min(radius, w / 2, h / 2);
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  };

  const core = (x: number, y: number, r: number, spin: number, alpha = 1) => {
    glow(x, y, r * 8, alpha * 0.78);
    glow(x, y, r * 3.8, alpha * 0.9, 2);
    ctx.strokeStyle = color;
    ctx.globalAlpha = alpha * 0.95;
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const angle = (i * TAU) / 6 + spin;
      ctx.moveTo(x + Math.cos(angle) * r, y + Math.sin(angle) * r);
      ctx.arc(x, y, r, angle, angle + (TAU / 6) * 0.7);
    }
    ctx.stroke();
    ctx.strokeStyle = COPPER;
    ctx.globalAlpha = alpha * 0.7;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(x, y, r * 1.4, -spin, -spin + Math.PI * 0.6);
    ctx.moveTo(x + Math.cos(Math.PI - spin) * r * 1.4, y + Math.sin(Math.PI - spin) * r * 1.4);
    ctx.arc(x, y, r * 1.4, Math.PI - spin, Math.PI * 1.6 - spin);
    ctx.stroke();
    ctx.fillStyle = WHITE;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    ctx.arc(x, y, Math.max(2, r * 0.24), 0, TAU);
    ctx.fill();
  };

  const rings = (
    x: number,
    y: number,
    r: number,
    spin: number,
    progress: number,
    alpha: number
  ) => {
    ctx.strokeStyle = color;
    ctx.globalAlpha = alpha;
    ctx.lineWidth = 1.1;
    ctx.beginPath();
    for (let band = 0; band < 3; band++) {
      const radius = r * (0.78 + band * 0.135);
      const direction = band === 1 ? -1 : 1;
      for (let i = 0; i < 24; i++) {
        const reveal = clamp(progress * 1.3 - band * 0.1 - i / 110);
        if (!reveal) continue;
        const angle = (i * TAU) / 24 + spin * direction + band * 0.04;
        ctx.moveTo(x + Math.cos(angle) * radius, y + Math.sin(angle) * radius);
        ctx.arc(x, y, radius, angle, angle + (TAU / 24) * 0.68 * out(reveal));
      }
    }
    ctx.stroke();
    ctx.lineWidth = 0.7;
    ctx.globalAlpha = alpha * 0.42;
    ctx.beginPath();
    for (let i = 0; i < 64; i++) {
      if (i / 64 > progress) break;
      const a = (i * TAU) / 64 - Math.PI / 2;
      const aCos = Math.cos(a);
      const aSin = Math.sin(a);
      const inner = r * 1.11;
      const outer = inner + (i % 4 === 0 ? 9 : 4);
      line(x + aCos * inner, y + aSin * inner, x + aCos * outer, y + aSin * outer);
    }
    ctx.stroke();
    ctx.strokeStyle = COPPER;
    ctx.globalAlpha = alpha * 0.82;
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = (i * TAU) / 3 - spin * 0.6;
      ctx.moveTo(x + Math.cos(a) * r * 0.93, y + Math.sin(a) * r * 0.93);
      ctx.arc(x, y, r * 0.93, a, a + Math.PI * 0.16 * out(progress));
    }
    ctx.stroke();
  };

  const convergence = (x: number, y: number, p: number, compress: boolean) => {
    for (let i = 0; i < count; i++) {
      const k = i * 6;
      const local = clamp(p * 1.5 - motes[k + 3] * 0.5);
      const angle =
        motes[k] * TAU + (local + (compress ? 1 : 0)) * (0.5 + motes[k + 4]) * (i % 2 ? 1 : -1);
      const start = aperture * (0.75 + motes[k + 1] * 1.1);
      const collapsed = compress ? out(local) : 0;
      const radius = compress ? start * (1 - collapsed) + 11 * collapsed : start;
      const alpha = compress
        ? (0.5 + Math.sin(local * Math.PI) * 0.5) * (1 - out(clamp((local - 0.75) / 0.25)))
        : Math.sin((clamp(p * 1.4 - motes[k + 3] * 0.4) * Math.PI) / 2) * 0.5;
      glow(
        x + Math.cos(angle) * radius,
        y + Math.sin(angle) * radius,
        9 + motes[k + 5] * 10,
        alpha,
        i % 9 === 0 ? 1 : i % 5 === 0 ? 2 : 0
      );
    }
  };

  const courier = (p: number, x: number, y: number, incoming: boolean, dir: EntryDirection) => {
    const horizontal = dir === 'left' || dir === 'right';
    const negative = dir === 'left' || dir === 'up';
    const edge = negative !== incoming ? -70 : (horizontal ? width : height) + 70;
    const startX = incoming && horizontal ? edge : x;
    const startY = incoming && !horizontal ? edge : y;
    const endX = !incoming && horizontal ? edge : x;
    const endY = !incoming && !horizontal ? edge : y;
    const bend = Math.min(110, height * 0.1) * (negative ? -1 : 1);
    const q = smooth(p);
    // History is reconstructed analytically, so jumps and reversals need no buffer.
    for (let i = 31; i >= 0; i--) {
      const at = Math.max(0, q - i * 0.012);
      const px = startX + (endX - startX) * at + (!horizontal ? Math.sin(at * Math.PI) * bend : 0);
      const py = startY + (endY - startY) * at + (horizontal ? Math.sin(at * Math.PI) * bend : 0);
      glow(
        px,
        py,
        12 + (1 - i / 32) * 19,
        (1 - i / 32) * 0.35 * Math.min(1, q * 12),
        i % 7 === 0 ? 1 : 0
      );
    }
    const cx = startX + (endX - startX) * q + (!horizontal ? Math.sin(q * Math.PI) * bend : 0);
    const cy = startY + (endY - startY) * q + (horizontal ? Math.sin(q * Math.PI) * bend : 0);
    core(cx, cy, 22, p * TAU);
  };

  const descend = (p: number, x: number, y: number, targetY: number) => {
    const q = smooth(p);
    const targetX = width / 2;
    const bend = Math.min(120, width * 0.09);
    const c1x = x + bend;
    const c1y = y + (targetY - y) * 0.26;
    const c2x = targetX - bend * 0.55;
    const c2y = targetY - Math.min(150, height * 0.18);
    ctx.strokeStyle = color;
    ctx.globalAlpha = Math.sin(p * Math.PI) * 0.17;
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.bezierCurveTo(c1x, c1y, c2x, c2y, targetX, targetY);
    ctx.stroke();
    const wake = Math.sin((Math.min(1, q * 3) * Math.PI) / 2);
    const settled = 1 - out(clamp((p - 0.86) / 0.14));
    for (let i = 41; i >= 0; i--) {
      const at = Math.max(0, q - i * 0.009);
      const px = bezier(x, c1x, c2x, targetX, at);
      const py = bezier(y, c1y, c2y, targetY, at);
      const fade = (1 - i / 42) * wake * settled;
      glow(px, py, 11 + (1 - i / 42) * 12, fade * 0.48, i % 8 === 0 ? 1 : 0);
    }
    core(bezier(x, c1x, c2x, targetX, q), bezier(y, c1y, c2y, targetY, q), 22 - 11 * q, p * TAU);
  };

  const fabricate = (p: number, bar: number, inset: number, revealing: boolean) => {
    const bx = inset;
    const by = height - bar - inset;
    const bw = Math.max(1, width - 2 * inset);
    const cx = width / 2;
    const cy = by + bar / 2;
    const spread = revealing ? 1 : out(clamp(p / 0.68));
    const half = (bw / 2) * spread;
    const strength = revealing ? 1 - smooth(p) : 1;
    const seams = revealing ? out(p) : spread;
    const assembly = revealing ? 1 : clamp((p - 0.13) / 0.87);
    const outerRadius = Math.min(8, half, bar / 2);

    ctx.fillStyle = '#061b2b';
    ctx.globalAlpha = strength * (revealing ? 0.3 : 0.54);
    ctx.beginPath();
    plate(cx - half, by, half * 2, bar, outerRadius);
    ctx.fill();
    // Blueprint rails are batched separately from the individual panel seams.
    ctx.strokeStyle = color;
    ctx.globalAlpha = strength * 0.82;
    ctx.lineWidth = 1;
    ctx.beginPath();
    plate(cx - half, by, half * 2, bar, outerRadius);
    line(cx - half + outerRadius, by + 4, cx + half - outerRadius, by + 4);
    line(cx - half + outerRadius, by + bar - 4, cx + half - outerRadius, by + bar - 4);
    ctx.stroke();

    const columns = Math.max(6, Math.floor(bw / 86));
    const spacing = bw / columns;
    ctx.globalAlpha = strength * 0.23;
    ctx.lineWidth = 0.7;
    ctx.beginPath();
    for (let i = 1; i < columns; i++) {
      const px = bx + spacing * i;
      if (Math.abs(px - cx) > half) continue;
      line(px, by + 9, px, by + bar - 9);
    }
    for (let i = 1; i < 3; i++) line(cx - half, by + (bar * i) / 3, cx + half, by + (bar * i) / 3);
    ctx.stroke();

    // Plates approach from alternating above/below offsets, then knit flush.
    ctx.strokeStyle = color;
    ctx.globalAlpha = strength * 0.58;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    for (let i = 0; i < columns; i++) {
      const px = bx + spacing * i;
      const distance = Math.abs(px + spacing / 2 - cx) / (bw / 2);
      const local = clamp(assembly * 1.8 - distance * 0.75);
      if (local <= 0 || Math.abs(px + spacing / 2 - cx) > half) continue;
      const offset = (1 - out(local)) * (i % 2 ? 14 : -14);
      const piece = spacing - 5;
      plate(px + 2.5, by + 7 + offset, piece, Math.max(1, bar - 14), 3);
    }
    ctx.stroke();

    for (let side = -1; side <= 1; side += 2) {
      const sx = cx + side * (bw / 2 - 1) * seams;
      const pulse = revealing ? Math.sin(p * Math.PI) : Math.sin(clamp(p / 0.9) * Math.PI);
      ctx.globalAlpha = pulse * 0.94;
      ctx.strokeStyle = WHITE;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      line(sx, by + 2, sx, by + bar - 2);
      ctx.stroke();
      glow(sx, cy, Math.max(36, bar * 1.6), pulse * 0.85, 0);
      glow(sx, by + 2, 28, pulse, 2);
      glow(sx, by + bar - 2, 28, pulse * 0.8, 1);
      // Individual weld sparks are deterministic and reset cleanly on reversal.
      for (let i = 0; i < 22; i++) {
        const k = i * 6;
        const age = (p * 4 + motes[k]) % 1;
        const reach = age * (12 + motes[k + 1] * 38);
        const px = sx - side * reach;
        const py =
          cy + (motes[k + 2] - 0.5) * bar + Math.sin(age * Math.PI) * (motes[k + 3] - 0.5) * 28;
        glow(px, py, 8 + motes[k + 4] * 9, (1 - age) * pulse * 0.8, i % 3 === 0 ? 1 : 0);
      }
    }
    if (!revealing)
      core(
        cx,
        cy,
        11 * (1 - out(clamp((p - 0.3) / 0.7))),
        p * TAU,
        1 - smooth(clamp((p - 0.48) / 0.52))
      );
  };

  return {
    resize(w: number, h: number, ratio: number) {
      if (disposed) return;
      const nextWidth = Number.isFinite(w) ? Math.max(0, w) : 0;
      const nextHeight = Number.isFinite(h) ? Math.max(0, h) : 0;
      const nextDpr = Number.isFinite(ratio) && ratio > 0 ? ratio : 1;
      if (nextWidth === width && nextHeight === height && nextDpr === dpr) return;
      width = nextWidth;
      height = nextHeight;
      dpr = nextDpr;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (atlasDpr !== dpr) buildAtlas();
      aperture = Math.min(width * 0.23, height * 0.27);
      count = Math.min(MAX_MOTES, Math.max(96, Math.round((width * height) / 10000)));
    },
    draw(
      phase: EntryPhase,
      progress: number,
      coreX: number,
      coreY: number,
      bar: number,
      inset: number,
      dir: EntryDirection = 'right'
    ) {
      if (disposed || !width || !height) return;
      const p = Number.isFinite(progress) ? clamp(progress) : 0;
      ctx.clearRect(0, 0, width, height);
      if (phase === 'reveal' && p === 1) return;
      ctx.globalCompositeOperation = 'lighter';
      if (phase === 'aperture') {
        rings(coreX, coreY, aperture, p * 0.24, p, out(p) * 0.82);
        convergence(coreX, coreY, p, false);
        glow(coreX, coreY, aperture * 0.9, smooth(p) * 0.45);
      } else if (phase === 'compress') {
        const radius = aperture + (22 - aperture) * smooth(p);
        rings(coreX, coreY, radius, 0.24 + p * 1.25, 1, 0.82 * (1 - smooth(p)));
        convergence(coreX, coreY, p, true);
        glow(coreX, coreY, aperture * 0.9, (1 - smooth(p)) * 0.45);
        core(coreX, coreY, 22 + (1 - smooth(p)) * 12, p * TAU, out(p));
      } else if (phase === 'transfer-out' || phase === 'transfer-in') {
        courier(p, coreX, coreY, phase === 'transfer-in', dir);
      } else if (phase === 'descent') {
        descend(p, coreX, coreY, height - bar / 2 - inset);
      } else {
        fabricate(p, bar, inset, phase === 'reveal');
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'source-over';
    },
    clear() {
      if (!disposed) ctx.clearRect(0, 0, width, height);
    },
    dispose() {
      disposed = true;
      atlas.width = atlas.height = 1;
      canvas.width = canvas.height = 1;
      width = height = count = 0;
    },
  };
}
