'use client';

import { useRef } from 'react';
import HoloStage, { type HoloState, type Shape4D, type SimData } from '@/components/hud/holo-stage';
import { bridgeBytes, bridgeGet } from '@/lib/bridge';
import { useSharedPoll } from '@/lib/shared-poll';

/** Holographic 3D projection (src/model3d.py, tools view_3d / edit_3d_object).
 *  Polls GET /model3d/state, streams the newest .glb from GET /model3d/glb and
 *  hands both to <HoloStage/>. Renders nothing until a model is projected.
 *  Voice-only: open/close(dismiss)/rotate/stop/faster/slower/reverse/zoom/
 *  wireframe/solid/explode/assemble/scan/rebuild arrive through state. */
const fetchGlb = () => bridgeBytes('/model3d/glb');
const fetchShape = () => bridgeGet<Shape4D>('/model3d/shape4d');
const fetchSim = () => bridgeGet<SimData>('/model3d/sim');

export default function ModelViewer() {
  const polled = useSharedPoll<HoloState>('/model3d/state', 700);
  // A dropped poll (bridge restart / hiccup) must not dissolve the hologram:
  // keep the last good state; only an explicit open:false closes it.
  const last = useRef<HoloState | null>(null);
  if (polled) last.current = polled;
  return (
    <HoloStage
      state={polled ?? last.current}
      fetchGlb={fetchGlb}
      fetchShape={fetchShape}
      fetchSim={fetchSim}
    />
  );
}
