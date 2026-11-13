'use client';

import { useEffect, useRef } from 'react';
import HoloStage, { type HoloState, type Shape4D, type SimData } from '@/components/hud/holo-stage';
import { bridgeBytes, bridgeGet } from '@/lib/bridge';
import { useSharedPoll } from '@/lib/shared-poll';
import { invoke, isTauri } from '@/lib/tauri';

/** Separate hologram window (not part of the HUD). The shell keeps it hidden;
 *  this page asks the shell to show it while a model is projected and to hide
 *  it again once the de-rez finishes. Voice-only, no controls. */
const fetchGlb = () => bridgeBytes('/model3d/glb');
const fetchShape = () => bridgeGet<Shape4D>('/model3d/shape4d');
const fetchSim = () => bridgeGet<SimData>('/model3d/sim');
const DEREZ_MS = 4000;

export default function HologramApp() {
  const polled = useSharedPoll<HoloState>('/model3d/state', 700);
  const last = useRef<HoloState | null>(null);
  if (polled) last.current = polled;
  const state = polled ?? last.current;
  const want = !!state?.open && !!state?.has_glb;

  useEffect(() => {
    if (!isTauri()) return;
    if (want) {
      invoke('show_holo_window')
        .then(() => bridgeGet('/model3d/place')) // fullscreen on the emptiest monitor
        .catch(() => undefined);
      return;
    }
    const id = setTimeout(() => invoke('hide_holo_window').catch(() => undefined), DEREZ_MS);
    return () => clearTimeout(id);
  }, [want]);

  return (
    <main
      style={{
        position: 'fixed',
        inset: 0,
        overflow: 'hidden',
        background: 'radial-gradient(ellipse at 50% 62%, #06202e 0%, #03101a 45%, #010508 100%)',
      }}
    >
      <HoloStage state={state} fetchGlb={fetchGlb} fetchShape={fetchShape} fetchSim={fetchSim} />
    </main>
  );
}
