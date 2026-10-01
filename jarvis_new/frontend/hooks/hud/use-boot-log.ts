'use client';

import { useEffect, useRef, useState } from 'react';
import { useCallState } from '@/hooks/hud/use-room-state';
import { type BootStep } from '@/lib/bridge';

/** How long the finished log stays up after the agent comes online. */
const ONLINE_HOLD_MS = 1400;
/** Rotation pace of the sub-steps under a stage that is still running. */
const SUB_STEP_MS = 650;

export type BootStage = {
  /** Headline, sentence case, no trailing punctuation. */
  label: string;
  /** One or two words for the school strip. */
  short: string;
  /** Mono detail on the right: what is doing the work. */
  detail: string;
  /** What actually happens under this stage, cycled while it runs. */
  sub?: string[];
};

/** Every stage the wake client reports, worded as the real work it is. */
export const BOOT_STAGES: Record<string, BootStage> = {
  wake: { short: 'Wake', label: 'Wake phrase detected', detail: 'on-device' },
  capture: {
    short: 'Listening',
    label: 'Capturing request',
    detail: '48 kHz mic',
    sub: ['Gating on voice activity', 'Waiting for end of speech'],
  },
  transcribe: {
    short: 'Transcribing',
    label: 'Transcribing on device',
    detail: 'whisper base',
    sub: ['Decoding audio', 'Running inference'],
  },
  verify: { short: 'Verified', label: 'Address confirmed', detail: 'local' },
  check: { short: 'Session', label: 'Checking for an open session', detail: 'room service' },
  auth: { short: 'Token', label: 'Signing access token', detail: 'JWT' },
  connect: {
    short: 'Relay',
    label: 'Connecting to voice relay',
    detail: 'wss · TLS',
    sub: [
      'Resolving relay endpoint',
      'TLS handshake',
      'Gathering ICE candidates',
      'DTLS key exchange',
    ],
  },
  uplink: { short: 'Mic uplink', label: 'Publishing microphone', detail: 'Opus 48 kHz' },
  dispatch: {
    short: 'Agent',
    label: 'Dispatching agent',
    detail: 'realtime voice',
    sub: [
      'Worker assigned',
      'Opening model session',
      'Subscribing to mic track',
      'Waiting for first audio frame',
    ],
  },
  online: { short: 'Online', label: 'Agent online', detail: 'link up' },
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
export function bootView(log: BootStep[], tick: number): BootView | null {
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
  const running = BOOT_STAGES[steps[last][0]];
  const sub = !online && running.sub ? running.sub[tick % running.sub.length] : null;
  const short = BOOT_STAGES[steps[last][0]].short;
  return { rows, sub, short, step: steps.length, online };
}

/** The live call-setup log, or null when nothing is starting. */
export function useBootLog(): BootView | null {
  const [log, setLog] = useState<BootStep[] | null>(null);
  const [tick, setTick] = useState(0);
  // Wake stamp (first step's ts) whose finished log was already shown and
  // put away: the waking file outlives setup, so later polls ignore it.
  const dismissed = useRef<number | null>(null);

  // One shared /room poll (use-room-state) instead of a 250 ms loop per
  // mounted copy; this effect reacts whenever the call state changes.
  const call = useCallState();
  const hold = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    if (!call) return; // bridge down: keep the last state
    const next = call.boot && call.boot.length > 0 ? call.boot : null;
    const t0 = next?.[0]?.[1] ?? null;
    if (!next || t0 === dismissed.current) {
      setLog(null);
      return;
    }
    setLog((prev) =>
      prev && prev.length === next.length && prev.at(-1)?.[1] === next.at(-1)?.[1] ? prev : next
    );
    // Once online, let the finished log sit briefly, then put it away.
    if (next.at(-1)?.[0] === 'online' && hold.current === undefined) {
      hold.current = setTimeout(() => {
        hold.current = undefined;
        dismissed.current = t0;
        setLog(null);
      }, ONLINE_HOLD_MS);
    }
  }, [call]);
  useEffect(() => () => clearTimeout(hold.current), []);

  const running = log !== null && log.at(-1)?.[0] !== 'online';
  useEffect(() => {
    if (!running) return;
    setTick(0);
    const timer = setInterval(() => setTick((t) => t + 1), SUB_STEP_MS);
    return () => clearInterval(timer);
  }, [running, log?.length]);

  return log ? bootView(log, tick) : null;
}
