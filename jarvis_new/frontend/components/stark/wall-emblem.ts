/**
 * Where the desktop wallpaper's static ASCII emblem sits, so the live orb can
 * coincide with it exactly (the wallpaper IS the orb's resting state).
 *
 * Source: ~/deck/assets/locke-wall-ascii.png (1920x1080), rendered by
 * ~/deck/lib/jarvis_wall.py on a 7 x 15 px cell grid (274 x 72 cells, offset
 * 1,0): the reactor sits on cell (137, 36) with R = 72 * 0.3 = 21.6 rows, so
 *   centre   = (1 + 137*7 + 3.5, 36*15 + 7.5) = (963.5, 547.5) px
 *   ascii R  = 21.6 * 15                      = 324 px
 * (`jarvis_wall.py --dump-cells` writes these into public/wall-cells.json and
 * wall-replica.ts reads them from there; the numbers below are the same ones,
 * for code that must register to the emblem before the JSON has loaded.)
 * Plasma "fill" scales the picture by max(vw/1920, vh/1080), centred; tweak
 * these only if the wallpaper is set to another fill mode.
 */
import { invoke, isTauri } from '@/lib/tauri';

export const WALL_SRC = [1920, 1080] as const;
export const WALL_CENTER_PX = { x: 963.5, y: 547.5 } as const;
/** Radius of the ASCII reactor's field (r = 1 in jarvis_ascii.reactor), px. */
export const WALL_ASCII_RADIUS_PX = 324;
/** Same, as fractions of the 1080 px wallpaper height / 1920 px width. */
export const WALL_EMBLEM_RADIUS_FRAC = WALL_ASCII_RADIUS_PX / WALL_SRC[1];
export const WALL_EMBLEM_CENTER = {
  x: WALL_CENTER_PX.x / WALL_SRC[0],
  y: WALL_CENTER_PX.y / WALL_SRC[1],
} as const;
/** Flat wallpaper background colour after bloom (r, g, b). */
export const WALL_BG_RGB = '5, 23, 38';

/**
 * Where the HUD window sits on its monitor. The wallpaper fills the WHOLE
 * monitor, but the overlay window excludes the panels (e.g. 1920x970 at y=30
 * on a 1920x1080 screen), so wallpaper coordinates must be shifted by the
 * window's offset. Asked from the shell (`overlay_frame`); outside Tauri, or
 * until it answers, the window is assumed to cover the monitor.
 */
export type WallFrame = { x: number; y: number; mw: number; mh: number };
/** Fired on window when the frame changes: re-run any wallpaper placement. */
export const WALL_FRAME_EVENT = 'jarvis-wall-frame';
let frame: WallFrame | null = null;
let asking = false;

export function wallFrame(vw: number, vh: number): WallFrame {
  return frame ?? { x: 0, y: 0, mw: vw, mh: vh };
}

/** Re-ask the shell for the window's monitor offset (cheap; call on resize). */
export function refreshWallFrame(): void {
  if (asking || !isTauri()) return;
  asking = true;
  invoke<{ x: number; y: number; monitorWidth: number; monitorHeight: number }>('overlay_frame')
    .then((f) => {
      const next: WallFrame = { x: f.x, y: f.y, mw: f.monitorWidth, mh: f.monitorHeight };
      const ok = [next.x, next.y, next.mw, next.mh].every(Number.isFinite) && next.mw > 200;
      if (!ok) return;
      const same =
        frame &&
        Math.abs(frame.x - next.x) < 0.5 &&
        Math.abs(frame.y - next.y) < 0.5 &&
        frame.mw === next.mw &&
        frame.mh === next.mh;
      if (same) return;
      frame = next;
      window.dispatchEvent(new Event(WALL_FRAME_EVENT));
    })
    .catch(() => undefined)
    .finally(() => {
      asking = false;
    });
}

/** Plasma fill of a src-sized image on the monitor, in window px. */
export function wallFill(vw: number, vh: number, sw: number, sh: number) {
  const f = wallFrame(vw, vh);
  const s = Math.max(f.mw / sw, f.mh / sh);
  return {
    s,
    /** window px of a wallpaper pixel (px, py) */
    x: (px: number) => f.mw / 2 + s * (px - sw / 2) - f.x,
    y: (py: number) => f.mh / 2 + s * (py - sh / 2) - f.y,
  };
}

/** Emblem centre / ascii radius in window px (Plasma fill, monitor offset). */
export function wallEmblemOnScreen(vw: number, vh: number) {
  const m = wallFill(vw, vh, WALL_SRC[0], WALL_SRC[1]);
  return {
    scale: m.s,
    cx: m.x(WALL_CENTER_PX.x),
    cy: m.y(WALL_CENTER_PX.y),
    r: m.s * WALL_ASCII_RADIUS_PX,
  };
}
