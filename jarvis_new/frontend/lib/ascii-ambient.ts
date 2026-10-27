import type { AmbientAnchor, AmbientState, AmbientVariant } from './ambient-particles';
import { NOISE, RAMP, glyphAtlas, hash } from './ascii';
import { JARVIS_COLORS, MUTED_COLOR } from './jarvis-palette';

const ACCENT: Record<AmbientState, string> = { ...JARVIS_COLORS, muted: MUTED_COLOR };
const SPEED: Record<AmbientState, number> = {
  idle: 1,
  listening: 1.2,
  thinking: 1.6,
  speaking: 1.3,
  muted: 0.5,
};

/** ASCII replacement for the light-sprite field, same contract as
 * createAmbientScene: a baked dot lattice, a slow brightness wave, sparse
 * falling glyph streams and a scanline. Drawn from one glyph atlas. */
export function createAsciiAmbientScene(
  canvas: HTMLCanvasElement,
  variant: AmbientVariant,
  initial: AmbientState
) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  let state = initial;
  let width = 0;
  let height = 0;
  let dpr = 1;
  let time = 0;
  const cw = variant === 'hud' ? 10 : 8;
  const ch = Math.round(cw * 1.6);
  let atlas = glyphAtlas(cw, ch, 1, ACCENT[state]);
  const lattice = document.createElement('canvas');
  let streams: Float32Array = new Float32Array(0);

  const bake = () => {
    atlas = glyphAtlas(cw, ch, dpr, ACCENT[state]);
    lattice.width = Math.max(1, Math.round(width * dpr));
    lattice.height = Math.max(1, Math.round(height * dpr));
    const l = lattice.getContext('2d');
    if (!l) return;
    l.setTransform(dpr, 0, 0, dpr, 0, 0);
    l.clearRect(0, 0, width, height);
    const step = variant === 'hud' ? 4 : 3;
    for (let y = 0; y * ch < height; y++) {
      for (let x = (y % 2) * 2; x * cw < width; x += step) {
        atlas.draw(l, 2, 0.08 + hash(x, y) * 0.1, x * cw, y * ch);
      }
    }
    const count = variant === 'hud' ? Math.round(width / 140) : variant === 'bar' ? 6 : 5;
    streams = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      streams[i * 3] = Math.floor(hash(i, 1) * Math.max(1, width / cw));
      streams[i * 3 + 1] = hash(i, 2) * (height / ch + 20);
      streams[i * 3 + 2] = 2 + hash(i, 3) * 5;
    }
  };

  const draw = (delta = 0) => {
    if (!width || !height) return;
    time += (delta / 1000) * SPEED[state];
    ctx.clearRect(0, 0, width, height);
    ctx.globalAlpha = 0.85;
    ctx.drawImage(lattice, 0, 0, width, height);
    ctx.globalAlpha = 1;
    const rows = height / ch;
    // brightness wave: a diagonal band re-lights the lattice
    const band = ((time * 0.12) % 1.4) - 0.2;
    const step = variant === 'hud' ? 4 : 3;
    for (let y = 0; y < rows; y++) {
      const by = band * (rows + width / cw) - y;
      for (let x = (y % 2) * 2; x * cw < width; x += step) {
        const d = Math.abs(x * 0.5 - by);
        if (d < 3) atlas.draw(ctx, d < 1 ? 4 : 2, 0.45 - d * 0.12, x * cw, y * ch);
      }
    }
    // falling glyph streams
    for (let i = 0; i < streams.length; i += 3) {
      const col = streams[i];
      const speed = streams[i + 2];
      let head = streams[i + 1] + (delta / 1000) * speed * SPEED[state];
      if (head - 12 > rows) head = -2 - hash(i, Math.floor(time)) * 10;
      streams[i + 1] = head;
      for (let k = 0; k < 10; k++) {
        const y = Math.floor(head) - k;
        if (y < 0 || y > rows) continue;
        const g = RAMP.length + Math.floor(hash(col + k, Math.floor(time * 6) + y) * NOISE.length);
        atlas.draw(ctx, g, k === 0 ? 0.6 : 0.36 - k * 0.03, col * cw, y * ch);
      }
    }
    // scanline
    const sy = ((time * 40) % (height + 40)) - 20;
    ctx.fillStyle = 'rgba(168,235,255,0.035)';
    ctx.fillRect(0, sy, width, 2);
  };

  return {
    draw,
    setState(next: AmbientState) {
      if (next === state) return;
      state = next;
      bake();
    },
    resize(w: number, h: number, ratio: number, anchor: AmbientAnchor | null = null) {
      void anchor; // the ASCII field covers the whole surface
      const r = Math.min(1.5, Number.isFinite(ratio) && ratio > 0 ? ratio : 1);
      if (w === width && h === height && r === dpr) return;
      width = Math.max(0, w);
      height = Math.max(0, h);
      dpr = r;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      bake();
      draw();
    },
    dispose() {
      lattice.width = lattice.height = 1;
      canvas.width = canvas.height = 1;
      width = height = 0;
    },
  };
}
