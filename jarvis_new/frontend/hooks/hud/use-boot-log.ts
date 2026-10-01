'use client';

import { useEffect, useState } from 'react';
import { useCallState } from '@/hooks/hud/use-room-state';
import { type BootStep } from '@/lib/bridge';

/** How long the finished log stays up after the agent comes online. */
const ONLINE_HOLD_MS = 1400;

export type BootStage = {
  /** Headline, sentence case, no trailing punctuation. */
  label: string;
  /** One or two words for the school strip. */
  short: string;
  /** Mono detail on the right: what is doing the work. */
  detail: string;
};

/** Every stage the wake client reports, worded as the real work it is. */
export const BOOT_STAGES: Record<string, BootStage> = {
  wake: { short: 'Wake', label: 'Voice request received', detail: 'on-device' },
  capture: {
    short: 'Listening',
    label: 'Capturing request',
    detail: '48 kHz mic',
  },
  transcribe: {
    short: 'Transcribing',
    label: 'Transcribing on device',
    detail: 'whisper base',
  },
  verify: { short: 'Verified', label: 'Address confirmed', detail: 'local' },
  check: { short: 'Session', label: 'Checking for an open session', detail: 'room service' },
  auth: { short: 'Token', label: 'Signing access token', detail: 'JWT' },
  connect: {
    short: 'Relay',
    label: 'Connecting to voice relay',
    detail: 'wss · TLS',
  },
  uplink: { short: 'Mic uplink', label: 'Publishing microphone', detail: 'Opus 48 kHz' },
  dispatch: {
    short: 'Agent',
    label: 'Dispatching agent',
    detail: 'realtime voice',
  },
  online: { short: 'Connected', label: 'Voice link established', detail: 'audio track' },
};

export type BootRow = {
  key: string;
  label: string;
  detail: string;
  /** Seconds since the wake word, formatted "+1.24". */
  at: string;
  done: boolean;
};

export type BootView = {
  rows: BootRow[];
  /** Sub-step of the running stage, or null. */
  sub: string | null;
  /** Short name of the latest stage (school strip). */
  short: string;
  /** 1-based step index of the running stage and the total seen so far. */
  step: number;
  online: boolean;
};

/** Build the rows from the raw log. Pure. */
export function bootView(log: BootStep[]): BootView | null {
  const steps = log.filter(([stage]) => stage in BOOT_STAGES);
  if (steps.length === 0) return null;
  const t0 = steps[0][1];
  const last = steps.length - 1;
  const online = steps[last][0] === 'online';
  const rows = steps.map(([stage, ts], i) => ({
    key: `${stage}-${ts}`,
    label: BOOT_STAGES[stage].label,
    detail: BOOT_STAGES[stage].detail,
    at: `+${(ts - t0).toFixed(2)}`,
    done: i < last || online,
  }));
  const sub = null;
  const short = BOOT_STAGES[steps[last][0]].short;
  return { rows, sub, short, step: steps.length, online };
}

/** The live call-setup log, or null when nothing is starting. */
export function useBootLog(): BootView | null {
  const call = useCallState();
  const [dismissed, setDismissed] = useState<number | null>(null);
  const log = call?.boot ?? null;
  const generation = log?.[0]?.[1] ?? null;
  const online = log?.at(-1)?.[0] === 'online';
  useEffect(() => {
    if (!online || generation === null) return;
    // Changing generations cancels the old hold. An old online callback
    // can never dismiss a new wake that started before its 1.4 s expired.
    const timer = setTimeout(() => setDismissed(generation), ONLINE_HOLD_MS);
    return () => clearTimeout(timer);
  }, [generation, online]);
  return log && generation !== dismissed ? bootView(log) : null;
}
