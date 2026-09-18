'use client';

import { useMemo } from 'react';
import { useAgent } from '@livekit/components-react';

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
