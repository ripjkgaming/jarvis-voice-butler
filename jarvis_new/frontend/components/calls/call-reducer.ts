/** Pure call-event reducer (no React): parse and fold the contract's
 *  call events (docs/calls/CONTRACT.md) into a CallView. */
import type { CallEvent, CallView, TranscriptLine } from './types';

const MAX_LINES = 60;
const STATES = ['ringing', 'handling', 'hold', 'owner', 'ended'];

/** Python emits epoch seconds (time.time()); accept ms too. */
export function toMs(t: unknown, fallback: number): number {
  if (typeof t !== 'number' || !Number.isFinite(t) || t <= 0) return fallback;
  return t < 1e12 ? t * 1000 : t;
}

/** Narrow an arbitrary data-channel payload to a call event, or null. */
export function parseCallEvent(raw: unknown): CallEvent | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = raw as Record<string, unknown>;
  if (o.type !== 'call' || typeof o.event !== 'string') return null;
  if (typeof o.call_id !== 'string' && typeof o.call_id !== 'number') return null;
  const ev = { ...o, call_id: String(o.call_id) } as Record<string, unknown>;
  switch (o.event) {
    case 'state':
      return typeof o.state === 'string' && STATES.includes(o.state)
        ? (ev as unknown as CallEvent)
        : null;
    case 'transcript':
      return typeof o.text === 'string' && typeof o.speaker === 'string'
        ? (ev as unknown as CallEvent)
        : null;
    case 'speaking':
      return typeof o.level === 'number' ? (ev as unknown as CallEvent) : null;
    case 'escalation':
      return { ...ev, reason: String(o.reason ?? '') } as unknown as CallEvent;
    case 'calendar':
      return typeof o.action === 'string' && typeof o.status === 'string'
        ? (ev as unknown as CallEvent)
        : null;
    case 'summary':
      return {
        ...ev,
        summary: String(o.summary ?? ''),
        outcome: String(o.outcome ?? ''),
      } as unknown as CallEvent;
    default:
      return null;
  }
}

let lineSeq = 0;

/** Transcript markers the HUD writes on state changes. */
const MARKS: Record<string, (dir: string) => string> = {
  ringing: (d) => (d === 'out' ? '☏ DIALING OUT' : '☏ INCOMING · WHATSAPP'),
  handling: () => '◢ JARVIS ON THE LINE',
  hold: () => '‖ CALLER ON HOLD',
  owner: () => '◆ OWNER TOOK THE LINE',
  ended: () => '■ CALL ENDED',
};

/** Pure reducer: fold one call event into the view. `now` is epoch ms
 *  (the demo passes scripted time; live use passes Date.now()). Speaking
 *  events are ignored here; they go to the level ref. */
export function reduceCall(view: CallView | null, ev: CallEvent, now: number): CallView | null {
  if (ev.event === 'speaking') return view;
  // A new call id replaces whatever was showing (the old one ended or was missed).
  const same = view && view.callId === ev.call_id;
  if (ev.event === 'state') {
    const base: CallView = same
      ? view
      : {
          callId: ev.call_id,
          state: ev.state,
          direction: ev.direction ?? 'in',
          caller: null,
          goal: '',
          startedAt: toMs(ev.started_at, now),
          stateSince: now,
          endedAt: null,
          mode: 'jarvis',
          aiDisclosed: false,
          recording: false,
          transcript: [],
          escalation: null,
          escalations: [],
          calendar: [],
          summary: null,
        };
    const changed = !same || base.state !== ev.state;
    const mark = changed ? MARKS[ev.state]?.(ev.direction ?? base.direction) : null;
    const transcript = mark
      ? [
          ...base.transcript,
          {
            id: ++lineSeq,
            speaker: 'jarvis' as const,
            text: mark,
            partial: false,
            ts: now,
            mark: true,
          },
        ].slice(-MAX_LINES)
      : base.transcript;
    return {
      ...base,
      state: ev.state,
      direction: ev.direction ?? base.direction,
      caller: ev.caller ?? base.caller,
      goal: ev.goal ?? base.goal,
      startedAt: toMs(ev.started_at, base.startedAt),
      stateSince: changed ? now : base.stateSince,
      endedAt: ev.state === 'ended' ? (base.endedAt ?? now) : null,
      mode: ev.mode ?? base.mode,
      aiDisclosed: !!ev.ai_disclosed,
      recording: !!ev.recording,
      // Any state transition resolves a pending escalation (the owner took
      // over, Jarvis put the caller on hold, or the call closed).
      escalation: changed ? null : base.escalation,
      transcript,
    };
  }
  if (!view || !same) return view;
  if (ev.event === 'transcript') {
    const ts = toMs(ev.ts, now);
    const lines = view.transcript.slice();
    // The newest partial from this speaker is replaced by each update and
    // by its final; other speakers' lines are left alone (overlapping talk).
    let i = lines.length - 1;
    while (i >= 0 && !(lines[i].speaker === ev.speaker && lines[i].partial)) i--;
    const line: TranscriptLine = {
      id: i >= 0 ? lines[i].id : ++lineSeq,
      speaker: ev.speaker,
      text: ev.text,
      partial: !!ev.partial,
      ts: i >= 0 ? lines[i].ts : ts,
    };
    if (i >= 0) lines[i] = line;
    else lines.push(line);
    return { ...view, transcript: lines.slice(-MAX_LINES) };
  }
  if (ev.event === 'escalation') {
    const transcript = [
      ...view.transcript,
      {
        id: ++lineSeq,
        speaker: 'jarvis' as const,
        text: '▲ ESCALATED TO OWNER',
        partial: false,
        ts: now,
        mark: true,
      },
    ].slice(-MAX_LINES);
    const esc = { reason: ev.reason, at: now };
    return {
      ...view,
      escalation: esc,
      escalations: [...view.escalations, esc].slice(-10),
      transcript,
    };
  }
  if (ev.event === 'calendar') {
    return { ...view, calendar: [...view.calendar, { ev, at: now }].slice(-12) };
  }
  if (ev.event === 'summary')
    return { ...view, summary: { summary: ev.summary, outcome: ev.outcome } };
  return view;
}
