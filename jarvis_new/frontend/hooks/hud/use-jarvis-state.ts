'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAgent } from '@livekit/components-react';
import { isTauri, micStatus, setMicMuted } from '@/lib/tauri';

/**
 * Maps the LiveKit agent state machine to the JARVIS status word.
 * Cyan = IDLE, everything else = ACTIVE + sub-state.
 * Priority for orb tint: Orange > Green > Purple > Cyan.
 */
export type JarvisState = 'idle' | 'listening' | 'thinking' | 'speaking';

export const JARVIS_COLORS: Record<JarvisState, string> = {
  idle: '#22d3ee', // Cyan
  listening: '#a855f7', // Purple
  thinking: '#22c55e', // Green
  speaking: '#fb923c', // Orange
};

/** Orb tint while the mic is muted (tray/HUD mute): neutral grey. */
export const MUTED_COLOR = '#6b7280';

export function agentStateToJarvis(agentState: string | undefined): JarvisState {
  switch (agentState) {
    case 'listening':
      return 'listening';
    case 'thinking':
      return 'thinking';
    case 'speaking':
      return 'speaking';
    default:
      return 'idle';
  }
}

export function useJarvisState() {
  const { state: agentState } = useAgent();

  const jarvis = useMemo(() => agentStateToJarvis(agentState), [agentState]);
  const color = JARVIS_COLORS[jarvis];
  const label = jarvis === 'idle' ? '[IDLE] JARVIS' : `[ACTIVE] ${jarvis.toUpperCase()}`;
  const active = jarvis !== 'idle';
  const boosted = jarvis === 'thinking' || jarvis === 'speaking';

  return { jarvis, agentState, color, label, active, boosted };
}

/**
 * Shell mic mute (tray `mute-mic` checkbox / `jarvis mute|unmute` / HUD mic
 * button) via `invoke('set_mic_muted' | 'mic_status')`. Outside Tauri the
 * state stays `null` (unknown) and toggling is a no-op. Fail-soft: an
 * unreachable bridge leaves the last known state in place.
 */
export function useMicMuted() {
  const [muted, setMuted] = useState<boolean | null>(null);

  const refresh = useCallback(async () => {
    if (!isTauri()) return;
    try {
      const status = await micStatus();
      setMuted(status.muted === true);
    } catch {
      /* keep last known state */
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Tray/CLI mutes bypass the HUD: re-query whenever the overlay regains
    // focus (summon flow) so the button + orb never lie for long.
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [refresh]);

  const toggle = useCallback(async () => {
    if (!isTauri()) return;
    const next = !(muted ?? false);
    setMuted(next); // optimistic; corrected below
    try {
      setMuted(await setMicMuted(next));
    } catch {
      await refresh();
    }
  }, [muted, refresh]);

  return { muted, toggle, refresh };
}
