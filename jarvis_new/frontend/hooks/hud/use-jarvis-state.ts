'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useCallState, useRoomState } from '@/hooks/hud/use-room-state';
import { bridgeHudState, bridgeSetHudState } from '@/lib/bridge';
import { JARVIS_COLORS, type JarvisState } from '@/lib/jarvis-palette';
import { isTauri, micStatus, setMicMuted } from '@/lib/tauri';
import { useAgentState } from '@/lib/voice-session-react';

/** Canonical colour rule: execution green, active call purple, no call cyan.
 * Tauri consumes the leased bridge state; browser voice sessions use the voice link.
 * Speech and quiet listening share a colour, while retaining their labels.
 */
export { JARVIS_COLORS, MUTED_COLOR, type JarvisState } from '@/lib/jarvis-palette';

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
  const agentState = useAgentState();
  const inCall = useCallState()?.live ?? false;
  const room = useRoomState();

  const jarvis = useMemo<JarvisState>(() => {
    if (isTauri()) {
      if (!inCall) return 'idle';
      const state = agentStateToJarvis(room?.agent_state ?? undefined);
      return state === 'idle' ? 'listening' : state;
    }
    const state = agentStateToJarvis(agentState);
    if (state !== 'idle') return state;
    if (!inCall) return 'idle';
    const bridgeState = agentStateToJarvis(room?.agent_state ?? undefined);
    return bridgeState === 'idle' ? 'listening' : bridgeState;
  }, [agentState, inCall, room?.agent_state]);
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

/**
 * HUD-only mute: set by the voice commands "mute" / "unmute" (agent, wake
 * listener or bridge) through ~/.jarvis/hud_state.json. Polled cheaply; the
 * microphone is a separate state (useMicMuted) and is never affected.
 */
export function useHudMuted() {
  const [muted, setMuted] = useState(false);
  const [breach, setBreach] = useState(false);

  useEffect(() => {
    let live = true;
    const tick = async () => {
      const v = await bridgeHudState();
      if (!live || v === null) return;
      setMuted(v.muted);
      setBreach(v.breach);
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1000);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, []);

  const toggle = useCallback(async () => {
    const next = !muted;
    setMuted(next);
    const v = await bridgeSetHudState({ muted: next });
    if (v !== null) setMuted(v.muted);
  }, [muted]);

  /** Click-through to end the fake takeover (also "stand down" by voice). */
  const standDown = useCallback(async () => {
    setBreach(false);
    const v = await bridgeSetHudState({ breach: false });
    if (v !== null) setBreach(v.breach);
  }, []);

  return { muted, toggle, breach, standDown };
}
