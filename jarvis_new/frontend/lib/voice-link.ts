import type { BridgeRoomState } from './bridge';

export type VoiceLinkPhase =
  | 'idle'
  | 'request'
  | 'relay'
  | 'agent'
  | 'ready'
  | 'failed'
  | 'stalled';
export type VoiceRequest = { at: number; baseline: number | null; failed: boolean };
export type VoiceLink = {
  phase: VoiceLinkPhase;
  word: string;
  detail: string;
  step: number;
  joining: boolean;
  /** Next wall-clock boundary; no per-frame React timer. */
  until: number | null;
};

const READY_MS = 1100;
const ACK_MS = 10000;
const STALE_MS = 45000;
const SLOW_MS = 8000;
const IDLE: VoiceLink = {
  phase: 'idle',
  word: '',
  detail: '',
  step: -1,
  joining: false,
  until: null,
};
const make = (
  phase: VoiceLinkPhase,
  word: string,
  detail: string,
  step: number,
  until: number | null = null
): VoiceLink => ({
  phase,
  word,
  detail,
  step,
  until,
  joining: ['request', 'relay', 'agent'].includes(phase),
});

/** Observed wake stages only. Room publication is not agent readiness;
 * `online` means the audio track was subscribed, not that a model spoke. */
export function voiceLinkFrom(
  raw: BridgeRoomState | null,
  request: VoiceRequest | null,
  now = Date.now()
): VoiceLink {
  const boot = Array.isArray(raw?.boot) ? raw.boot : [];
  const first = boot[0]?.[1] ?? null;
  const last = boot.at(-1);
  const acknowledged =
    !!last && first !== request?.baseline && (first ?? 0) * 1000 >= (request?.at ?? 0) - 1000;
  if (request && !acknowledged) {
    if (request.failed)
      return make(
        'failed',
        'LINK UNAVAILABLE',
        'VOICE REQUEST COULD NOT REACH JARVIS · TRY AGAIN',
        -1
      );
    if (now >= request.at + ACK_MS)
      return make(
        'stalled',
        'AWAITING CONFIRMATION',
        'NO VOICE STATUS RECEIVED · PRESS NUMPAD ENTER TO RETRY',
        -1
      );
    return make('request', 'ACQUIRING', 'VOICE REQUEST SENT', 0, request.at + ACK_MS);
  }
  if (!last || !Number.isFinite(last[1])) return IDLE;
  const [stage, stamp] = last;
  if (stage === 'online') {
    const until = stamp * 1000 + READY_MS;
    return now < until
      ? make('ready', 'LINK ESTABLISHED', 'VOICE CHANNEL CONNECTED', 3, until)
      : IDLE;
  }
  const until = stamp * 1000 + STALE_MS;
  if (now >= until) return make('stalled', 'CHECKING LINK', 'NO RECENT CONNECTION UPDATE', -1);
  // A slower wait changes the explanation, never the completed stage. Use one
  // wall-clock boundary, not a timer that repeatedly updates elapsed seconds.
  const slowAt = stamp * 1000 + SLOW_MS;
  const slow = now >= slowAt;
  const next = slow ? until : slowAt;
  if (stage === 'dispatch')
    return make(
      'agent',
      'CONTACTING JARVIS',
      slow ? 'STILL WAITING FOR THE AGENT AUDIO LINK' : 'WAITING FOR THE AGENT AUDIO LINK',
      2,
      next
    );
  if (['check', 'auth', 'connect', 'uplink'].includes(stage)) {
    const details: Record<string, string> = {
      check: 'CHECKING THE VOICE SERVICE',
      auth: 'AUTHENTICATING YOUR VOICE CHANNEL',
      connect: 'CONNECTING YOUR VOICE CHANNEL',
      uplink: 'CONNECTING THE MICROPHONE UPLINK',
    };
    return make(
      'relay',
      'ESTABLISHING LINK',
      slow ? 'STILL CONNECTING YOUR VOICE CHANNEL' : details[stage],
      1,
      next
    );
  }
  if (['wake', 'verify', 'capture', 'transcribe'].includes(stage)) {
    const detail =
      stage === 'capture'
        ? 'CAPTURING YOUR REQUEST'
        : stage === 'transcribe'
          ? 'READING YOUR REQUEST ON DEVICE'
          : 'VOICE REQUEST RECEIVED';
    return make('request', 'ACQUIRING', detail, 0, until);
  }
  return IDLE;
}
