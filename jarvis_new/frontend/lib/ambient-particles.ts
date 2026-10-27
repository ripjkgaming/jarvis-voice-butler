import { particleHex, particleSkin } from './character/particle-skin';
import { JARVIS_COLORS, MUTED_COLOR } from './jarvis-palette';

/** Small, additive light sprites: generated once at native DPR, then reused.
 * No per-frame DOM reads, gradients, blur filters, or particle allocations. */
export type AmbientVariant = 'hud' | 'bar' | 'menu';
export type AmbientState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'muted';
export type AmbientAnchor = { x: number; y: number; width: number; height: number };

type Draw = (delta: number) => void;
const subscribers = new Set<Draw>();
let frame = 0;
let previous: number | null = null;

function tick(now: number) {
  frame = 0;
  const delta = previous === null ? 0 : Math.min(34, Math.max(0, now - previous));
  previous = now;
  subscribers.forEach((draw) => draw(delta));
  if (subscribers.size) frame = requestAnimationFrame(tick);
  else previous = null;
}

/** All ambient layers share one refresh-rate clock; no loop remains when
 * the last visible surface unsubscribes. Restart never catches up hidden time. */
export function subscribeAmbientFrame(draw: Draw): () => void {
  subscribers.add(draw);
  if (!frame) frame = requestAnimationFrame(tick);
  return () => {
    subscribers.delete(draw);
    if (!subscribers.size) {
      cancelAnimationFrame(frame);
      frame = 0;
      previous = null;
    }
  };
}

const TAU = Math.PI * 2;
const CELL = 32;
const ACCENT: Record<AmbientState, string> = { ...JARVIS_COLORS, muted: MUTED_COLOR };
const SPEED: Record<AmbientState, number> = {
  idle: 1,
  listening: 1.15,
  thinking: 1.4,
  speaking: 1.25,
  muted: 0.6,
};
const DUST_DRIFT_PERIOD_SECONDS = 12;

/** A tiny periodic drift keeps the cached dust field alive without per-dot work. */
export function ambientDustOffset(timeSeconds: number): { x: number; y: number } {
  const wrapped =
    ((timeSeconds % DUST_DRIFT_PERIOD_SECONDS) + DUST_DRIFT_PERIOD_SECONDS) %
    DUST_DRIFT_PERIOD_SECONDS;
  const phase = (wrapped / DUST_DRIFT_PERIOD_SECONDS) * TAU;
  return {
    x: Math.sin(phase) * 4,
    y: (1 - Math.cos(phase)) * 2,
  };
}

/** Repeatable placement avoids a distracting random reset on resize. */
function seed(i: number, salt: number): number {
  const n = Math.sin(i * 127.1 + salt * 311.7) * 43758.5453;
  return n - Math.floor(n);
}

export function createAmbientScene(
  canvas: HTMLCanvasElement,
  variant: AmbientVariant,
  initialState: AmbientState = 'idle'
) {
  const ctx = canvas.getContext('2d', { alpha: true });
  if (!ctx) return null;
  const atlas = document.createElement('canvas');
  const glow = atlas.getContext('2d');
  if (!glow) return null;
  const background = document.createElement('canvas');
  const backgroundCtx = background.getContext('2d');
  if (!backgroundCtx) return null;

  let width = 0;
  let height = 0;
  let dpr = 1;
  let state = initialState;
  let time = 0;
  let speed = SPEED[state];
  let strength = state === 'muted' ? 0.55 : 1;
  let anchor: AmbientAnchor | null = null;
  let count = 0;
  let data = new Float32Array(0);
  let drawBudgetMs = 0;
  let stroke = '#a8ebff';
  let seen = particleSkin.version;

  const buildAtlas = () => {
    seen = particleSkin.version;
    stroke = particleHex();
    atlas.width = Math.ceil(CELL * 3 * dpr);
    atlas.height = Math.ceil(CELL * 3 * dpr);
    glow.setTransform(dpr, 0, 0, dpr, 0, 0);
    const colors = [
      state === 'muted' ? '#7294a5' : particleHex(),
      particleHex(true),
      state === 'muted' ? ACCENT.muted : particleHex(state === 'listening'),
    ];
    colors.forEach((color, row) => {
      for (let shape = 0; shape < 3; shape++) {
        const x = shape * CELL + CELL / 2;
        const y = row * CELL + CELL / 2;
        const haze = glow.createRadialGradient(x, y, 0, x, y, 14);
        haze.addColorStop(0, `${color}a0`);
        haze.addColorStop(0.2, `${color}50`);
        haze.addColorStop(1, `${color}00`);
        glow.fillStyle = haze;
        glow.fillRect(x - 14, y - 14, 28, 28);
        glow.strokeStyle = color;
        glow.fillStyle = color;
        glow.lineWidth = 1.2;
        glow.beginPath();
        if (shape === 0) {
          glow.arc(x, y, 1.8, 0, TAU);
          glow.fill();
        } else if (shape === 1) {
          glow.moveTo(x, y - 4);
          glow.lineTo(x + 4, y);
          glow.lineTo(x, y + 4);
          glow.lineTo(x - 4, y);
          glow.closePath();
          glow.stroke();
        } else {
          glow.moveTo(x - 5, y);
          glow.lineTo(x + 5, y);
          glow.moveTo(x, y - 5);
          glow.lineTo(x, y + 5);
          glow.stroke();
        }
      }
    });
  };

  const sprite = (
    target: CanvasRenderingContext2D,
    x: number,
    y: number,
    size: number,
    alpha: number,
    shape: number,
    color: number
  ) => {
    target.globalAlpha = alpha;
    target.drawImage(
      atlas,
      shape * CELL * dpr,
      color * CELL * dpr,
      CELL * dpr,
      CELL * dpr,
      x - size / 2,
      y - size / 2,
      size,
      size
    );
  };

  // The faint general-purpose dust field is deliberately stationary. Baking
  // it once removes 70–150 per-particle trigonometry and sprite blits from
  // every animation tick while the reactor orbit and edge packets still move.
  const bakeBackground = () => {
    background.width = canvas.width;
    background.height = canvas.height;
    backgroundCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    backgroundCtx.globalCompositeOperation = 'lighter';
    const strength = state === 'muted' ? 0.55 : 1;
    for (let i = 0; i < count; i++) {
      const at = i * 5;
      const a = data[at];
      const b = data[at + 1];
      const phase = data[at + 3];
      const fade = Math.sin(phase * Math.PI);
      const x = variant === 'bar' ? a * (width + 40) - 20 : a * width;
      const y =
        variant === 'bar'
          ? 5 + b * Math.max(1, height - 10)
          : ((b * (height + 24) + height + 24) % (height + 24)) - 12;
      const shape = i % 13 === 0 ? 1 : i % 19 === 0 ? 2 : 0;
      const color = i % 7 === 0 ? 2 : i % 5 === 0 ? 1 : 0;
      sprite(
        backgroundCtx,
        x,
        y,
        data[at + 4],
        (0.18 + 0.6 * fade * fade) * strength,
        shape,
        color
      );
    }
    backgroundCtx.globalAlpha = 1;
    backgroundCtx.globalCompositeOperation = 'source-over';
  };

  const draw = (delta = 0) => {
    if (!width || !height) return;
    const dt = Math.min(34, Math.max(0, delta));
    if (seen !== particleSkin.version) {
      buildAtlas();
      bakeBackground();
    }
    speed += (SPEED[state] - speed) * (1 - Math.exp(-dt / 600));
    const targetStrength =
      state === 'muted' ? 0.55 : state === 'speaking' ? 1.35 : state === 'listening' ? 1.15 : 1;
    strength += (targetStrength - strength) * (1 - Math.exp(-dt / 350));
    time += (dt / 1000) * speed * particleSkin.drift;
    // Keep the low idle raster budget; fluid 60 FPS is reserved for active
    // voice states on the main reactor. Small bar/menu surfaces stay at 30.
    const fps = variant === 'hud' && state !== 'idle' && state !== 'muted' ? 60 : 30;
    drawBudgetMs += dt;
    if (dt > 0 && drawBudgetMs < 1000 / fps) return;
    drawBudgetMs %= 1000 / fps;
    ctx.clearRect(0, 0, width, height);
    ctx.globalCompositeOperation = 'lighter';
    const offset = ambientDustOffset(time);
    // Tile only the narrow exposed edge strips as the pre-rendered dust drifts.
    // The wrapped copies make the texture periodic with no blank edge or seam.
    ctx.drawImage(background, offset.x, offset.y, width, height);
    const left = offset.x > 0 ? offset.x - width : offset.x + width;
    const top = offset.y > 0 ? offset.y - height : offset.y + height;
    if (offset.x > 0) ctx.drawImage(background, left, offset.y, width, height);
    if (offset.x < 0) ctx.drawImage(background, left, offset.y, width, height);
    if (offset.y > 0) ctx.drawImage(background, offset.x, top, width, height);
    if (offset.y < 0) ctx.drawImage(background, offset.x, top, width, height);
    if (offset.x !== 0 && offset.y !== 0) {
      ctx.drawImage(background, left, top, width, height);
    }

    if (variant === 'hud') {
      // The orbit is anchored to the actual reactor, including solo mode.
      const cx = anchor ? anchor.x + anchor.width / 2 : width * 0.48;
      const cy = anchor ? anchor.y + anchor.height / 2 : height * 0.43;
      const radius = anchor ? anchor.width * (292 / 880) : Math.min(width * 0.22, height * 0.31);
      for (let i = 0; i < 28; i++) {
        const band = i % 3;
        const r = radius * (1.03 + band * 0.065);
        const angle = seed(i, 11) * TAU + time * (band === 1 ? -0.12 : 0.08 + band * 0.035);
        const alpha = (0.34 + 0.38 * Math.pow(Math.sin(time * 0.55 + i), 2)) * strength;
        if (i % 4 === 0) {
          ctx.globalAlpha = alpha * 0.35;
          ctx.strokeStyle = stroke;
          ctx.lineWidth = 0.8;
          ctx.beginPath();
          ctx.arc(cx, cy, r, angle - 0.065, angle);
          ctx.stroke();
        }
        sprite(
          ctx,
          cx + Math.cos(angle) * r,
          cy + Math.sin(angle) * r,
          12 + (i % 4) * 2,
          alpha,
          i % 9 === 0 ? 1 : 0,
          i % 6 === 0 ? 2 : 1
        );
      }
    } else {
      // Small telemetry packets skim the bar rails / menu frame edges.
      const rails = variant === 'bar' ? 8 : 4;
      for (let i = 0; i < rails; i++) {
        const upper = i % 2 === 0;
        const travel = ((seed(i, 23) * width + time * (22 + seed(i, 24) * 18)) % (width + 56)) - 28;
        const x = upper ? travel : width - travel;
        const y = upper ? 2.5 : height - 2.5;
        ctx.globalAlpha = 0.3 * strength;
        ctx.strokeStyle = stroke;
        ctx.lineWidth = 0.8;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + (upper ? -18 : 18), y);
        ctx.stroke();
        sprite(ctx, x, y, 16, 0.8 * strength, 0, 1);
      }
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
  };

  return {
    draw,
    setState(next: AmbientState) {
      if (next === state) return;
      state = next;
      buildAtlas();
      bakeBackground();
    },
    resize(w: number, h: number, ratio: number, nextAnchor: AmbientAnchor | null = null) {
      const requestedDpr = Number.isFinite(ratio) && ratio > 0 ? ratio : 1;
      anchor = nextAnchor;
      if (w === width && h === height && Math.min(1.5, requestedDpr) === dpr) return;
      width = Math.max(0, w);
      height = Math.max(0, h);
      // Fine glow sprites upscale cleanly. Full device DPR made this large,
      // mostly transparent canvas several times larger than the visible UI.
      dpr = Math.min(1.5, requestedDpr);
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      buildAtlas();
      const wanted =
        variant === 'hud'
          ? Math.max(70, Math.min(150, Math.round((width * height) / 12000)))
          : variant === 'bar'
            ? Math.max(26, Math.min(80, Math.round(width / 26)))
            : Math.max(18, Math.min(36, Math.round((width * height) / 6500)));
      if (wanted !== count) {
        count = wanted;
        data = new Float32Array(count * 5);
        for (let i = 0; i < count; i++) {
          const at = i * 5;
          for (let j = 0; j < 4; j++) data[at + j] = seed(i + 1, j + 1);
          data[at + 4] = 7 + seed(i + 1, 7) * (variant === 'bar' ? 8 : 11);
        }
      }
      bakeBackground();
      draw();
    },
    dispose() {
      atlas.width = atlas.height = 1;
      background.width = background.height = 1;
      canvas.width = canvas.height = 1;
      data = new Float32Array(0);
      width = height = 0;
    },
  };
}
