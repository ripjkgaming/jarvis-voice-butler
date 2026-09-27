'use client';

import { ArcReactor } from '@/components/hud/arc-reactor';
import { hash01, useBakedCanvas } from '@/hooks/hud/use-baked-frames';
import { MUTED_COLOR, useJarvisState, useMicMuted } from '@/hooks/hud/use-jarvis-state';

const PARTICLE_CAP = 220;
const PARTICLE_FLOOR = 80;

function particleBudget(width: number, height: number): number {
  // Scale the swarm to the canvas area so small windows stay cheap and
  // large ones stay dense. ~1 particle per 9000 px², clamped.
  const area = Math.max(1, width * height);
  return Math.max(PARTICLE_FLOOR, Math.min(PARTICLE_CAP, Math.round(area / 9000)));
}

/**
 * Particle orb, baked: the full drift loop is pre-rendered once per
 * (color, boosted, size) into stitched frame canvases; playback is a
 * single `drawImage` blit per tick — no per-frame particle math, no
 * gradients at runtime. (KDE GPU-safe 2D canvas, no WebGL.)
 */
export function ParticleOrb() {
  const { color: stateColor, boosted, jarvis } = useJarvisState();
  const { muted } = useMicMuted();
  // Muted mic greys the orb (tray/HUD/mute all funnel through here).
  const color = muted === true ? MUTED_COLOR : stateColor;

  const canvasRef = useBakedCanvas({
    frames: 30,
    // Idle overlay parks on frame 0 (zero wakeups); motion resumes on call.
    fps: 12,
    paused: jarvis === 'idle',
    scale: 0.5,
    seed: 1237,
    bakeKey: `${color}|${boosted ? 'b' : ''}`,
    render: (ctx, w, h, i, n) => {
      // Frame-stable swarm: every particle derives from its index hash,
      // so the drift is identical on every loop and every re-bake.
      const budget = particleBudget(w, h);
      const loopT = 30 / 12;
      const t = (i / n) * loopT;
      const cx = w / 2;
      const cy = h / 2;
      const base = Math.min(w, h) * 0.32;
      const amp = 1 + Math.sin(t * 2.2) * 0.03 + (boosted ? 0.06 : 0);
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, base * 1.6);
      g.addColorStop(0, `${color}55`);
      g.addColorStop(0.55, `${color}18`);
      g.addColorStop(1, 'transparent');
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = color;
      for (let k = 0; k < budget; k += 1) {
        const a = (k / budget) * Math.PI * 2;
        const rr = 0.35 + hash01(k, 1) * 0.6;
        const ss = 0.4 + hash01(k, 2) * 1.2;
        const ww = 1 + hash01(k, 3) * 2;
        const ang = a + t * 0.25 * ss;
        const rad = base * rr * amp * (1 + Math.sin(t * 3 + a * 4) * 0.05);
        const x = cx + Math.cos(ang) * rad;
        const y = cy + Math.sin(ang * 1.3) * rad * 0.9;
        ctx.globalAlpha = 0.35 + 0.45 * Math.abs(Math.sin(t * ss + a));
        ctx.beginPath();
        ctx.arc(x, y, ww, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    },
  });

  return (
    <div
      className="hud-orb im-reactor"
      data-state={jarvis}
      data-muted={muted === true ? 'true' : 'false'}
      data-boosted={boosted ? 'true' : 'false'}
      style={{ ['--jarvis-state' as string]: color }}
    >
      <ArcReactor />
      <canvas ref={canvasRef} className="hud-orb__canvas im-reactor__canvas" aria-hidden="true" />
      <div className="hud-orb__ring" />
      <div className="hud-orb__ticks" aria-hidden="true" />
      <div className="hud-orb__ticks hud-orb__ticks--rev" aria-hidden="true" />
    </div>
  );
}
