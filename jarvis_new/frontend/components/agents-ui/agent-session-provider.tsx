'use client';

import { useEffect } from 'react';
import { type VoiceSession } from '@/lib/voice-session';
import { VoiceSessionProvider } from '@/lib/voice-session-react';

/**
 * Props for the AgentSessionProvider component.
 */
export type AgentSessionProviderProps = {
  /**
   * The voice session to provide.
   */
  session: VoiceSession;
  /**
   * The volume (0-1) of agent playback.
   */
  volume?: number;
  /**
   * Whether to mute agent playback locally.
   */
  muted?: boolean;
  /**
   * The children to render.
   */
  children: React.ReactNode;
};

/**
 * A provider component for agent sessions. Shares one VoiceSession with the
 * subtree and applies local playback settings (playback lives in the core).
 *
 * @example
 * ```tsx
 * <AgentSessionProvider session={voiceSession}>
 *   <AgentControlBar />
 *   <AgentChatTranscript />
 * </AgentSessionProvider>
 * ```
 */
export function AgentSessionProvider({
  session,
  volume,
  muted = false,
  children,
}: AgentSessionProviderProps) {
  useEffect(() => {
    session.setPlaybackMuted(muted);
  }, [session, muted]);

  useEffect(() => {
    if (volume !== undefined) session.setPlaybackVolume(volume);
  }, [session, volume]);

  return <VoiceSessionProvider session={session}>{children}</VoiceSessionProvider>;
}
