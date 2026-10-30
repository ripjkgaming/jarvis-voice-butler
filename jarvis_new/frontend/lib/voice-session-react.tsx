'use client';

import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import {
  type AgentState,
  type ConnectOptions,
  type TranscriptMessage,
  type VoiceSession,
  VoiceSession as VoiceSessionClass,
  type VoiceSnapshot,
  type VoiceStatus,
} from '@/lib/voice-session';

const VoiceSessionContext = createContext<VoiceSession | null>(null);

/**
 * Owns one VoiceSession for a subtree. Pass `session` to share an instance
 * created elsewhere (tests, or a Tauri shell bridge); otherwise one is made.
 */
export function VoiceSessionProvider({
  session,
  children,
}: {
  session?: VoiceSession;
  children: ReactNode;
}) {
  const [own] = useState(() => session ?? new VoiceSessionClass());
  const active = session ?? own;

  useEffect(() => {
    // Only tear down a session this provider created. StrictMode runs this
    // cleanup once on mount; disconnect() is safe to repeat.
    if (session) return;
    return () => own.disconnect();
  }, [own, session]);

  return <VoiceSessionContext.Provider value={active}>{children}</VoiceSessionContext.Provider>;
}

function useVoiceSessionInstance(): VoiceSession {
  const session = useContext(VoiceSessionContext);
  if (!session) throw new Error('useVoiceSession* hooks need <VoiceSessionProvider>');
  return session;
}

function useSnapshot(): VoiceSnapshot {
  const session = useVoiceSessionInstance();
  return useSyncExternalStore(session.watch, session.getSnapshot, session.getSnapshot);
}

export type UseVoiceSessionReturn = {
  session: VoiceSession;
  status: VoiceStatus;
  isConnected: boolean;
  connect: (url: string, token: string, options?: ConnectOptions) => void;
  disconnect: () => void;
  sendChat: (text: string) => boolean;
};

/** Replaces useSession()/useSessionContext(): connection lifecycle and chat send. */
export function useVoiceSession(): UseVoiceSessionReturn {
  const session = useVoiceSessionInstance();
  const snap = useSnapshot();
  const connect = useCallback(
    (url: string, token: string, options?: ConnectOptions) => session.connect(url, token, options),
    [session]
  );
  const disconnect = useCallback(() => session.disconnect(), [session]);
  const sendChat = useCallback((text: string) => session.sendChat(text), [session]);
  return {
    session,
    status: snap.status,
    isConnected: snap.isConnected,
    connect,
    disconnect,
    sendChat,
  };
}

/** Replaces useAgent().state / useVoiceAssistant().state. */
export function useAgentState(): AgentState {
  return useSnapshot().agentState;
}

export type UseMicToggleReturn = {
  enabled: boolean;
  /** Flip, or pass the desired state. Stops sending mic frames when off. */
  toggle: (next?: boolean) => void;
};

/** Replaces useTrackToggle({source: Microphone}). `enabled` is "mic on". */
export function useMicToggle(): UseMicToggleReturn {
  const session = useVoiceSessionInstance();
  const muted = useSnapshot().muted;
  const toggle = useCallback(
    (next?: boolean) => session.setMuted(next === undefined ? !session.getSnapshot().muted : !next),
    [session]
  );
  return { enabled: !muted, toggle };
}

/** Replaces useChat().messages / useSessionMessages(session).messages. */
export function useTranscript(): TranscriptMessage[] {
  return useSnapshot().messages;
}

/**
 * Replaces RoomEvent.DataReceived filtered by topic. The callback receives the
 * parsed JSON payload. The latest callback is used without resubscribing.
 */
export function useDataTopic(topic: string, onPayload: (payload: unknown) => void): void {
  const session = useVoiceSessionInstance();
  const handler = useRef(onPayload);
  useEffect(() => {
    handler.current = onPayload;
  });
  useEffect(
    () => session.subscribe(topic, (payload) => handler.current(payload)),
    [session, topic]
  );
}

/**
 * Replaces the analyser-backed track volume hooks. Returns RMS 0..1, sampled
 * once per animation frame while `enabled`. Only re-renders when the value moves.
 */
export function useAudioLevel(source: 'agent' | 'mic' = 'agent', enabled = true): number {
  const session = useVoiceSessionInstance();
  const [level, setLevel] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setLevel(0);
      return;
    }
    let frame = 0;
    let last = -1;
    const tick = () => {
      const next = session.level(source);
      if (Math.abs(next - last) > 0.01) {
        last = next;
        setLevel(next);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [session, source, enabled]);

  return level;
}
