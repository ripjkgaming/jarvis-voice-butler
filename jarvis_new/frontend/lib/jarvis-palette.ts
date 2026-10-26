export type JarvisState = 'idle' | 'listening' | 'thinking' | 'speaking';

export const JARVIS_COLORS: Record<JarvisState, string> = {
  idle: '#22d3ee', // Cyan
  listening: '#a855f7', // Purple
  thinking: '#22c55e', // Green
  speaking: '#a855f7', // Purple
};

/** Orb tint while the mic is muted (tray/HUD mute): neutral grey. */
export const MUTED_COLOR = '#6b7280';
